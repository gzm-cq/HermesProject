"""阶段4: 树定位 — 领域匹配 + 科目匹配 + 树写入

将准入后的知识点归入知识树的正确层级（领域→科目→知识点）。

领域匹配策略：不维护规则表。LLM 根据文章标题+摘要判断领域，
从已有领域中选择或创建新领域。有多少领域就入多少，没有就新建。
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Callable

from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.core.embeddings import batch_embed, cosine_similarity
from knowledge_tree_builder.models import AtomicKnowledge, EMBEDDING_DIM

logger = logging.getLogger(__name__)


# ========== 领域匹配（纯 LLM，无规则表）==========


def _match_domain_via_llm(
    title: str,
    content_summary: str,
    existing_domains: list[str],
    llm_fn: Callable[[str, list[str]], str] | None,
) -> str:
    """LLM 领域匹配：从已有领域中选择或创建新领域。

    Args:
        title: 文章标题
        content_summary: 阶段1 产出的内容摘要
        existing_domains: 知识树已有领域列表
        llm_fn: LLM 判断函数 (title+summary, [existing_domains]) → domain

    Returns:
        domain 字符串（如 "mlops/clustering"）
    """
    if llm_fn is None:
        # 无 LLM 降级时直接创建新领域
        return _derive_domain_from_title(title)

    return llm_fn(title + " " + content_summary, existing_domains)


def _derive_domain_from_title(title: str) -> str:
    """从标题推领域（LLM 不可用时的兜底）。"""
    # 简单提取：取标题前 2-3 个有意义的词转小写
    words = re.findall(r"[a-zA-Z\-]+|[\u4e00-\u9fff]+", title)
    if not words:
        return "general"
    return words[0].lower().strip("-")


# ========== 科目匹配（不变）==========


def _match_or_create_subject(
    knowledge_text: str,
    domain: str,
    existing_subjects: list[dict[str, Any]],
    embed_fn: Callable,
    cosine_sim_fn: Callable,
    threshold: float = 0.70,
    knowledge_vector: list[float] | None = None,
) -> tuple[str, bool]:
    """科目匹配：cosine > 0.7 匹配已有科目，否则标记为创建。

    Args:
        knowledge_text: 知识点文本
        domain: 所属领域
        existing_subjects: 已有科目列表 [{id, name, k_vector?}]
        embed_fn: embedding 函数
        cosine_sim_fn: 余弦相似度函数
        threshold: 匹配阈值
        knowledge_vector: 已计算的知识点 embedding；传入时不再调用 embed_fn

    Returns:
        (subject_name, is_new)
    """
    if knowledge_vector is None:
        emb = embed_fn([knowledge_text])
        if not emb:
            return "其他", True
        vec = emb[0]
    else:
        vec = knowledge_vector
    best_subject = "其他"
    best_sim = 0.0

    for subj in existing_subjects:
        if subj.get("k_vector"):
            sim = cosine_sim_fn(vec, subj["k_vector"])
            if sim > best_sim:
                best_sim = sim
                best_subject = subj.get("name", "其他")

    if best_sim > threshold:
        return best_subject, False

    return "新科目", True


# ========== 主函数 ==========


class PlacementResult:
    """阶段4 产出"""
    records: list[dict[str, Any]]
    stats: dict[str, int]
    review_items: list[dict[str, Any]]


def place_knowledge(
    admitted_list: list[AtomicKnowledge],
    article_title: str,
    content_summary: str,
    *,
    db_adapter: DatabaseAdapter | None = None,
    embed_fn: Callable = batch_embed,
    cosine_sim_fn: Callable = cosine_similarity,
    llm_domain_fn: Callable[[str, list[str]], str] | None = None,
    write_db: bool = True,
) -> PlacementResult:
    """阶段4: 将准入的知识点归入知识树。

    Args:
        admitted_list: 阶段3 准入的知识点列表
        article_title: 文章标题（用于领域匹配）
        content_summary: 内容摘要（用于领域匹配）
        db_adapter: PG 适配器（用于查询已有树结构）
        embed_fn: embedding 函数
        cosine_sim_fn: 余弦相似度函数
        llm_domain_fn: LLM 领域判断函数 (title+summary, [existing_domains]) → domain

    Returns:
        PlacementResult
    """
    result = PlacementResult()
    result.records = []
    result.stats = {"total": 0, "placed": 0, "new_subjects": 0, "errors": 0, "orphaned": 0}
    result.review_items = []

    if not admitted_list:
        return result

    result.stats["total"] = len(admitted_list)

    # 1. 查询已有领域
    existing_domains: list[str] = []
    if db_adapter:
        try:
            existing_domains = db_adapter.get_all_domains()
        except Exception as e:
            logger.warning("查询已有领域失败: %s", e)

    # 2. LLM 领域匹配（无规则表）
    domain = _match_domain_via_llm(
        article_title, content_summary, existing_domains,
        llm_fn=llm_domain_fn,
    )

    # 3. 查询已有科目
    existing_subjects: list[dict[str, Any]] = []
    subject_count = 0
    if db_adapter:
        try:
            existing_subjects = db_adapter.get_subjects_by_domain(domain)
            subject_count = len(existing_subjects)
        except Exception as e:
            logger.warning("查询已有科目失败: %s", e)

    is_cold_start = subject_count < 3

    # 4. 逐条知识点匹配/创建科目（k_vector 批量生成）
    new_subject_names: set[str] = set()
    placement_records: list[dict[str, Any]] = []

    # 一次性批量计算所有知识的 embedding（含重试 + 降级）
    all_texts = [a["text"] for a in admitted_list]
    all_k_vectors: list[list[float] | None] = [None] * len(admitted_list)
    embedding_error: str | None = None
    for attempt in range(3):
        try:
            embeddings = embed_fn(all_texts)
            if embeddings:
                all_k_vectors = [list(e) if e is not None else None for e in embeddings]
            embedding_error = None
            break
        except Exception as e:
            embedding_error = str(e)
            logger.warning("batch_embed 第 %d/3 次失败: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)  # 退避: 1s, 2s
    if embedding_error:
        logger.warning("batch_embed 三次重试均失败，使用文本哈希降级向量: %s", embedding_error)
        # 降级：用文本的确定性哈希作为向量，确保 k_vector 不为 NULL
        for i, text in enumerate(all_texts):
            h = hashlib.md5(text.encode("utf-8")).digest()
            # 将 16 字节 md5 展开为 EMBEDDING_DIM 维伪向量（与 BGE-M3 对齐）
            vec = [(b / 255.0) * 2 - 1 for b in h * (EMBEDDING_DIM // len(h))]
            norm = (sum(x * x for x in vec) ** 0.5) or 1.0
            all_k_vectors[i] = [x / norm for x in vec]

    for i, atomic in enumerate(admitted_list):
        if is_cold_start:
            subject_name = f"{domain}/root"
        else:
            subject_name, is_new = _match_or_create_subject(
                atomic["text"], domain,
                existing_subjects, embed_fn, cosine_sim_fn,
                knowledge_vector=all_k_vectors[i],
            )
            if is_new:
                new_subject_names.add(subject_name)

        record = {
            "knowledge_text": atomic["text"],
            "type": atomic["type"],
            "domain": domain,
            "subject": subject_name,
            "parent_knowledge_id": None,
            "quality_confidence": 0.85,
            "source_article": article_title,
            "source_ids": [0],  # 来源文章 ID（0 = 离线管线）
            "k_vector": all_k_vectors[i] if i < len(all_k_vectors) else None,
            "entities": atomic.get("entities", []),  # 命名实体列表
            "valid_from": atomic.get("valid_from"),   # P3-9: 时态信息
            "valid_until": atomic.get("valid_until"),  # P3-9: 时态信息
        }
        placement_records.append(record)

    result.stats["placed"] = len(placement_records)
    result.stats["new_subjects"] = len(new_subject_names)
    result.records = placement_records

    # 5. 写入 PG
    if db_adapter and write_db:
        written = _write_to_db(placement_records, domain, db_adapter)
        result.stats["errors"] = written.get("errors", 0)

    return result


def _write_to_db(
    records: list[dict[str, Any]],
    domain: str,
    adapter: DatabaseAdapter,
) -> dict[str, int]:
    """批量写入 PG（executemany + 批量去重查询）。"""
    stats = {"nodes": 0, "points": 0, "errors": 0}

    # find_or_create_subject 等内部方法自己 commit，无需外包装事务。
    # 所有操作都是幂等的（查找先于插入），部分写入也不影响重跑。
    try:
        root_id = adapter.find_or_create_subject(domain, parent_id=None)
        stats["nodes"] += 1

        cursor = adapter.cursor

        # 1. 批量去重查询：一次查出所有已存在的文本
        all_texts = [r["knowledge_text"] for r in records]
        cursor.execute(
            "SELECT text FROM knowledge_point_texts WHERE text = ANY(%s)",
            (all_texts,),
        )
        existing_texts = {row[0] for row in cursor.fetchall()}

        # 2. 一次查出或创建所有科目（先收集唯一科目名）
        subject_names = list({r["subject"] for r in records})
        subject_ids: dict[str, int] = {}
        for name in subject_names:
            subject_ids[name] = adapter.find_or_create_subject(name, parent_id=root_id)
        stats["nodes"] += len(subject_ids)

        # 3. 批量插入知识点和文本（executemany）
        # 保存 k_vector 与每个待插入记录对应，插入后补写
        to_insert: list[tuple[str, int, list[int] | None, str, list[float] | None, list[str], str | None, str | None]] = []
        for rec in records:
            if rec["knowledge_text"] in existing_texts:
                stats["points"] += 1
                continue
            to_insert.append((
                rec["knowledge_text"][:30],
                subject_ids[rec["subject"]],
                rec.get("source_ids"),
                rec["knowledge_text"],
                rec.get("k_vector"),  # 保存 k_vector 供插入后补写
                rec.get("entities", []),  # 保存 entities 供插入后补写
                rec.get("valid_from"),    # P3-9: valid_from
                rec.get("valid_until"),   # P3-9: valid_until
            ))

        if to_insert:
            # 逐条 INSERT ... RETURNING id（executemany 不支持 RETURNING，
            # 且 ORDER BY id ASC 在有历史节点时取不对）
            node_ids: list[tuple[int, str]] = []
            for rec in to_insert:
                cursor.execute(
                    "INSERT INTO knowledge_tree (name, node_type, parent_id, display_order, source_ids) "
                    "VALUES (%s, 'knowledge_point', %s, 0, %s) RETURNING id",
                    (rec[0], rec[1], rec[2]),
                )
                row = cursor.fetchone()
                if row:
                    node_ids.append((row[0], rec[0]))

            # 批量插入 point_texts
            cursor.executemany(
                "INSERT INTO knowledge_point_texts (tree_node_id, text) VALUES (%s, %s)",
                [(n[0], to_insert[i][3]) for i, n in enumerate(node_ids)],
            )

            # 补写 k_vector（每个新节点的 embedding）
            for i, n in enumerate(node_ids):
                k_vec = to_insert[i][4]  # k_vector
                if k_vec is not None:
                    adapter.update_k_vector(
                        node_id=n[0],
                        k_vector=k_vec,
                        placement_count=1,
                    )

            # 补写实体 kt_entity_links
            for i, n in enumerate(node_ids):
                entities: list[str] = to_insert[i][5]
                if entities:
                    cursor.executemany(
                        "INSERT INTO kt_entity_links (kp_id, entity) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        [(n[0], e) for e in entities],
                    )

            # P3-9: 补写 temporal 信息（valid_from / valid_until）
            for i, n in enumerate(node_ids):
                vf = to_insert[i][6]
                vu = to_insert[i][7]
                if vf and vu:
                    cursor.execute(
                        "UPDATE knowledge_tree SET valid_from = %s::DATE, valid_until = %s::DATE, updated_at = NOW() WHERE id = %s",
                        (vf, vu, n[0]),
                    )
                elif vf:
                    cursor.execute(
                        "UPDATE knowledge_tree SET valid_from = %s::DATE, updated_at = NOW() WHERE id = %s",
                        (vf, n[0]),
                    )
                elif vu:
                    cursor.execute(
                        "UPDATE knowledge_tree SET valid_until = %s::DATE, updated_at = NOW() WHERE id = %s",
                        (vu, n[0]),
                    )

            stats["points"] += len(to_insert)

    except Exception as e:
        logger.warning("写入 PG 失败: %s", e)
        stats["errors"] += 1

    return stats
