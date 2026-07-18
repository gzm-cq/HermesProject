"""CLI 入口 — typer 实现"""

import json
import logging
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import numpy as np
import typer

# Optional GPU deps
TORCH_AVAILABLE = False
try:
    import torch

    TORCH_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore[assignment]

from clustering_analysis.adapters.database import DatabaseAdapter
from clustering_analysis.config import AppConfig, load_config
from clustering_analysis.core.clustering import (
    _detect_causal_in_group,
    _detect_causal_in_group_incremental,
    adaptive_hdbscan_params,
    dedup_memory_links,
    match_new_to_existing,
    merge_similar_entities,
    process_clusters,
    run_hdbscan_clustering,
)
from clustering_analysis.core.dedup import HAS_DATASKETCH, dedup_memories as _dedup_memories_core
from clustering_analysis.core.embeddings import batch_embed
from clustering_analysis.core.quality import batch_score_memories

# Entity merge thresholds
_MERGE_SIMILAR_THRESHOLD = 0.88  # 新实体间合并阈值
_MERGE_EXISTING_THRESHOLD = 0.85  # 新实体匹配已有实体阈值

# CLI 显示/采样阈值
_LOW_QUALITY_PREVIEW_LIMIT = 10
_MAX_BAR_LEN = 20
_ENTITY_MATCH_THRESHOLD = 0.75
_MAX_FULL_MEMBERS_FOR_CAUSAL = 50
_MAX_SAMPLE_OLD_FOR_CAUSAL = 30

# ========== JSON Logger ==========


class JSONFormatter(logging.Formatter):
    """统一 JSON 日志格式器"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """初始化日志系统"""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
    )


# ========== CLI App ==========

# ========== Audit Log ==========

AUDIT_LOG_PATH = os.path.expanduser(
    "~/.hermes/plugins/knowledge-navigation/clustering_audit.log"
)


# ========== 记忆去重预处理 ==========


def _log_clustering_run(
    *,
    total_units: int,
    processed_units: int,
    entity_count: int,
    cluster_count: int,
    silhouette: float | None,
    memory_links: int,
    noise_units: int,
    max_group_size: int,
    min_llm_size: int,
    bank_id: str,
    duration_sec: float,
) -> None:
    """将当次聚类运行元数据追加到审计日志文件。"""
    # 审计日志以 JSONL 格式写入文件（每行一个 JSON 对象），
    # 可用 `jq -s '.' <file>` 转换为 JSON 数组后分析。
    record = {
        "event": "clustering_run",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bank_id": bank_id,
        "total_units": total_units,
        "processed_units": processed_units,
        "noise_units": noise_units,
        "entity_count": entity_count,
        "cluster_count": cluster_count,
        "silhouette": silhouette,
        "memory_links": memory_links,
        "max_group_size": max_group_size,
        "min_llm_size": min_llm_size,
        "duration_sec": round(duration_sec, 2),
    }
    try:
        log_path = Path(AUDIT_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"   📋 审计日志已追加: {AUDIT_LOG_PATH}")
    except Exception as exc:
        print(f"   ⚠️  审计日志写入失败: {exc}")


app = typer.Typer(
    name="clustering-analysis",
    help="因果链聚类分析",
    add_completion=False,
)


def _load_embedding_config(config: dict) -> tuple[str, str, str]:
    """从配置或 daemon.env 加载 embedding 配置"""
    embed_base_url = config.get("embed_base_url")
    embed_model = config.get("embed_model")
    embed_api_key = config.get("embed_api_key")

    if not embed_base_url or not embed_model or not embed_api_key:
        try:
            env_path = "/root/.hindsight/daemon.env"
            if os.path.exists(env_path):
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith("HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL="):
                            embed_base_url = line.strip().split("=", 1)[1].strip()
                        elif line.strip().startswith("HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL="):
                            embed_model = line.strip().split("=", 1)[1].strip()
                        elif line.strip().startswith("HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY="):
                            embed_api_key = line.strip().split("=", 1)[1].strip()
        except Exception as e:
            print(f"   [WARN] Failed to load daemon.env for embedding: {e}")

    if not embed_base_url or not embed_model or not embed_api_key:
        raise RuntimeError(
            "❌ Embedding config missing. Please set embed_base_url/embed_model/embed_api_key "
            "in config, or ensure ~/.hindsight/daemon.env contains "
            "HINDSIGHT_API_EMBEDDINGS_* variables."
        )

    return str(embed_base_url), str(embed_model), str(embed_api_key)


# ========== 记忆去重预处理 ==========


@app.command()
def dedup_memories(
    dry_run: bool = typer.Option(False, '--dry-run', help='仅预览，不修改 DB'),
    threshold: float = typer.Option(0.85, '--threshold', help='Jaccard 相似度阈值'),
    batch_size: int = typer.Option(500, '--batch-size', help='每次扫描条数'),
    db_url: str = typer.Option('', help='PG 连接串（默认从 CLUSTERING_DB_URL 读取）'),
    minhash: bool = typer.Option(True, '--minhash/--no-minhash', help='使用 MinHash LSH 去重（默认开启）'),
) -> None:
    """扫描 memory_units，合并 text 高度相似的重复记忆。

    保留最早创建的那条，其余标记为 redundant（设置 text = "[redundant]"）。
    治本方案：一次性的数据清洗，后续聚类时自动避免重复。
    """
    if not db_url:
        db_url = os.environ.get('CLUSTERING_DB_URL', '')
        if not db_url:
            print('❌ 请设置 CLUSTERING_DB_URL 环境变量或传入 --db-url')
            raise typer.Exit(1)

    use_minhash = minhash and HAS_DATASKETCH
    if minhash and not HAS_DATASKETCH:
        print('⚠️  datasketch 未安装，降级为 Jaccard O(n²) 比较')
        print('   (pip install datasketch 可大幅提速)')
    method_label = 'MinHash LSH' if use_minhash else 'Jaccard O(n²)'
    print(f'🔧 去重方式: {method_label}')

    try:
        import psycopg2 as _pg
        conn = _pg.connect(db_url)
        cursor = conn.cursor()

        offset = 0
        total_merged = 0
        total_scanned = 0
        total_time = 0.0

        while True:
            cursor.execute(
                'SELECT id, text, created_at FROM memory_units '
                "WHERE text IS NOT NULL AND text != '[redundant]' " 
                'ORDER BY id ASC LIMIT %s OFFSET %s',
                (batch_size, offset),
            )
            rows = cursor.fetchall()
            if not rows:
                break
            total_scanned += len(rows)

            memories = [
                {'id': r[0], 'text': r[1] or '', 'created_at': r[2]}
                for r in rows
            ]

            t0 = time.time()
            deduped, removed_count, method = _dedup_memories_core(
                memories,
                threshold=threshold,
                use_minhash=use_minhash,
            )
            batch_time = time.time() - t0
            total_time += batch_time

            deduped_ids = {str(m['id']) for m in deduped}
            merged_in_batch = 0

            for mem in memories:
                if str(mem['id']) not in deduped_ids:
                    if not dry_run:
                        cursor.execute(
                            "UPDATE memory_units SET text = '[redundant]' WHERE id = %s",
                            (mem['id'],),
                        )
                    merged_in_batch += 1

            total_merged += merged_in_batch
            if merged_in_batch:
                print(f'   批次 {offset}~{offset + len(rows)}: 合并 {merged_in_batch} 条 ({batch_time:.2f}s, {method})')
            offset += batch_size

        if not dry_run:
            conn.commit()

        print(f"   📊 扫描 {total_scanned} 条，合并 {total_merged} 条重复记忆，总耗时 {total_time:.2f}s")
        print(f"   🔧 使用方式: {method_label}")
        if dry_run:
            print('   🔍 dry-run 模式，未实际修改')

        cursor.close()
        conn.close()

    except Exception as e:
        print(f'   ❌ 记忆去重失败: {e}')
        raise


@app.command()
def quality_score(
    sample_size: int = typer.Option(0, '--sample-size', help='采样数量，0 表示全量'),
    min_score: float = typer.Option(0.0, '--min-score', help='最低分过滤（仅输出低于此分数的记忆）'),
    dry_run: bool = typer.Option(False, '--dry-run', help='仅生成报告，不写入数据库'),
    use_llm: bool = typer.Option(False, '--use-llm/--heuristic', help='使用 LLM 评分（默认启发式）'),
    db_url: str = typer.Option('', help='PG 连接串（默认从 CLUSTERING_DB_URL 读取）'),
    bank_id: str = typer.Option('hermes', '--bank-id', help='记忆库 ID'),
    config_path: str = typer.Option('config/default.yaml', '--config', help='配置文件路径'),
) -> None:
    """全库记忆语义质量评分，生成质量分布报告。

    根据 enable_quality_scoring 配置决定是否启用（Feature Flag）。
    Feature Flag 关闭时输出提示并退出。
    默认使用启发式快速估算；开启 --use-llm 可调用 LLM 精确评分。
    """
    from collections import Counter

    # 加载配置
    if config_path:
        config = load_config(config_path)
    else:
        config = AppConfig()

    # Feature Flag 检查
    if not config.enable_quality_scoring:
        print(
            '⚠️  全库质量评分已禁用。\n'
            '   启用方式：设置 CLUSTERING_ENABLE_QUALITY_SCORING=true\n'
            '   或在配置文件中设置 enable_quality_scoring: true'
        )
        raise typer.Exit(code=0)

    cfg = AppConfig.from_dict(config)

    if not db_url:
        db_url = os.environ.get('CLUSTERING_DB_URL', '')
        if not db_url:
            print('❌ 请设置 CLUSTERING_DB_URL 环境变量或传入 --db-url')
            raise typer.Exit(1)

    batch_size = config.get('quality_score_batch_size', cfg.quality_score_batch_size)
    llm_api_url = config.get('llm_api_url', cfg.llm_api_url)
    llm_api_key = config.get('llm_api_key', cfg.llm_api_key)
    llm_model = config.get('quality_score_model', cfg.quality_score_model)

    method_label = 'LLM 精确评分' if use_llm else '启发式快速估算'
    print(f'📊 记忆质量评分')
    print(f'   方式: {method_label}')
    print(f'   批大小: {batch_size}')

    try:
        adapter = DatabaseAdapter(db_url)
        units = adapter.fetch_memory_units(sample_size, bank_id=bank_id)
        total = len(units)
        print(f'   待评分记忆数: {total} (bank={bank_id})')

        if total == 0:
            print('   ⚠️  没有可评分的记忆')
            adapter.close()
            return

        memories = [
            {'id': str(u[0]), 'text': u[2] or ''}
            for u in units
        ]

        t0 = time.time()
        scored = batch_score_memories(
            memories,
            batch_size=batch_size,
            api_url=llm_api_url,
            api_key=llm_api_key,
            model=llm_model,
            use_llm=use_llm,
        )
        duration = time.time() - t0

        scores = [m['quality_score'] for m in scored]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        buckets = [
            ('0.0-0.2', 0.0, 0.2),
            ('0.2-0.4', 0.2, 0.4),
            ('0.4-0.6', 0.4, 0.6),
            ('0.6-0.8', 0.6, 0.8),
            ('0.8-1.0', 0.8, 1.01),
        ]
        bucket_counts: dict[str, int] = {}
        for label, lo, hi in buckets:
            count = sum(1 for s in scores if lo <= s < hi)
            bucket_counts[label] = count

        print(f'\n📈 质量分布统计')
        print(f'   平均分: {avg_score:.3f}')
        print(f'   最高分: {max(scores):.3f}')
        print(f'   最低分: {min(scores):.3f}')
        print(f'   评分耗时: {duration:.2f}s')
        print(f'\n   各分段占比:')
        for label, _, _ in buckets:
            count = bucket_counts[label]
            pct = count / total * 100 if total > 0 else 0
            bar = '█' * int(pct * _MAX_BAR_LEN / 100)
            print(f'     {label}: {count:5d} ({pct:5.1f}%) {bar}')

        low_quality = [m for m in scored if m['quality_score'] < min_score] if min_score > 0 else []
        if min_score > 0:
            print(f'\n🔻 低质量记忆（< {min_score}）: {len(low_quality)} 条')
            for i, mem in enumerate(low_quality[:_LOW_QUALITY_PREVIEW_LIMIT]):
                text_preview = mem['text'][:80].replace('\n', ' ')
                print(f'   [{i+1}] id={mem["id"]}, score={mem["quality_score"]:.3f}')
                print(f'        {text_preview}...')
            if len(low_quality) > _LOW_QUALITY_PREVIEW_LIMIT:
                print(f'   ... 还有 {len(low_quality) - _LOW_QUALITY_PREVIEW_LIMIT} 条')

        if not dry_run:
            updates = [
                (m['id'], m['quality_score'], m['quality_details'])
                for m in scored
            ]
            updated = adapter.batch_update_quality_scores(updates)
            if updated > 0:
                print(f'\n💾 已写入数据库: {updated} 条')
            else:
                print(f'\nℹ️  数据库无 quality_score 字段，跳过写入（仅生成报告）')

        adapter.close()

    except Exception as e:
        print(f'   ❌ 质量评分失败: {e}')
        raise


def run(
    apply: bool = typer.Option(False, "--apply", help="实际写入 PG（默认 dry-run）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行模式（不写入数据库）"),
    cleanup: bool = typer.Option(False, "--cleanup", help="先清理旧的聚类关联数据"),
    force: bool = typer.Option(False, "--force", help="跳过确认提示"),
    skip_entity: bool = typer.Option(False, "--skip-entity", help="跳过实体提取阶段（LLM 调用慢）"),
    config_path: str = typer.Option("config/default.yaml", "--config", help="配置文件路径"),
) -> None:
    """多轮聚类（实体挂靠 → HDBSCAN 聚类）"""
    if dry_run:
        apply = False

    run_start = time.time()

    # 加载配置
    config = load_config(config_path)
    cfg = AppConfig.from_dict(config)

    # 覆盖运行时参数
    db_url = config.get("db_url", cfg.db_url)
    sample_size = config.get("sample_size", cfg.sample_size)
    epsilon_range = config.get("epsilon_range", cfg.epsilon_range)
    min_samples = config.get("min_samples", cfg.min_samples)
    entity_boost_factor = config.get("entity_boost_factor", cfg.entity_boost_factor)
    bank_id = config.get("bank_id", cfg.bank_id)
    max_group_size = config.get("max_group_size", cfg.max_group_size)
    min_llm_size = config.get("min_llm_size", cfg.min_llm_size)
    llm_api_url = config.get("llm_api_url", cfg.llm_api_url)
    llm_api_key = config.get("llm_api_key", cfg.llm_api_key)
    llm_model = config.get("llm_model", cfg.llm_model)
    hdbscan_adaptive = config.get("hdbscan_adaptive", cfg.hdbscan_adaptive)
    hdbscan_min_samples_min = config.get("hdbscan_min_samples_min", cfg.hdbscan_min_samples_min)
    hdbscan_min_samples_max = config.get("hdbscan_min_samples_max", cfg.hdbscan_min_samples_max)
    causal_incremental = config.get("causal_incremental", cfg.causal_incremental)
    causal_new_only = config.get("causal_new_only", cfg.causal_new_only)

    # Embedding 配置
    embed_base_url, embed_model, embed_api_key = _load_embedding_config(config)
    embed_batch_size = config.get("embed_batch_size", cfg.embed_batch_size)
    _embed_fn = partial(
        batch_embed,
        base_url=embed_base_url,
        model=embed_model,
        api_key=embed_api_key,
        batch_size=embed_batch_size,
    )

    # GPU 检测
    if TORCH_AVAILABLE and torch.cuda.is_available():
        use_gpu = True
        print(f"   GPU 加速：✅ {torch.cuda.get_device_name(0)} ({torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f}GB)")
    else:
        use_gpu = False
        print("   GPU 加速：❌ 使用 CPU")

    # ========== Phase 1: 拉取数据 ==========
    print("📥 Phase 1: 拉取数据...")

    adapter = DatabaseAdapter(db_url)
    units = adapter.fetch_memory_units(sample_size, bank_id=bank_id)
    n = len(units)
    print(f"   采样记忆数：{n} (bank={bank_id})")

    # 拉取实体关联
    unit_ids_list = [str(u[0]) for u in units]
    rows = adapter.fetch_unit_entities(unit_ids_list)
    unit_entities_map: defaultdict = defaultdict(set)
    for row in rows:
        unit_entities_map[row[0]].add(row[1])
    print(f"   有实体关联的记忆：{len(unit_entities_map)}个")

    # 提取数据
    embeddings_list = []
    unit_ids = []
    unit_texts = []
    unit_entity_sets = []
    for u in units:
        try:
            emb = np.array(json.loads(u[3]), dtype=np.float32)
        except (ValueError, json.JSONDecodeError, MemoryError) as _e:
            print(f"     [WARN] embedding 格式异常，跳过 unit_id={u[0]}: {_e}")
            continue
        embeddings_list.append(emb)
        unit_ids.append(u[0])
        unit_texts.append(u[2] if u[2] else "")
        unit_entity_sets.append(unit_entities_map.get(u[0], set()))

    # 全局 unit_id → local_index 映射，Phase 2 因果链增强与 Phase 4 embedding 更新共用。
    # 使用 str key，避免 psycopg2 UUID 对象与字符串 ID 混用导致查找失败。
    uid_to_local_idx: dict[str, int] = {str(uid): i for i, uid in enumerate(unit_ids)}

    if not embeddings_list:
        print("   ❌ 没有有效 embedding，终止")
        sys.exit(1)

    embeddings = np.array(embeddings_list)
    n = len(embeddings)
    print(f"   Embedding 维度：{embeddings.shape[1]}")

    # ============================================================
    # Phase 2: 多轮聚类（实体挂靠 → HDBSCAN 聚类）
    # ============================================================
    print("\n🔢 Phase 2: 多轮聚类（实体挂靠 → HDBSCAN 聚类）...")

    # 累积各轮的写入计划
    all_entity_plans: list = []
    all_unit_entity_plans: list = []
    all_memory_link_plans: list = []
    all_enriched_texts: dict[str, list[str]] = {}

    # 全局索引跟踪：已处理（有实体 或 已匹配到实体 或 已语义聚类）
    processed = np.zeros(n, dtype=bool)
    # 已有实体的 unit 标记为已处理
    for i, s in enumerate(unit_entity_sets):
        if s:
            processed[i] = True

    with_entity_count = processed.sum()
    without_entity_count = n - with_entity_count
    print(f"   已有实体: {with_entity_count}，无实体: {without_entity_count}")

    # ----------------------------------------------------------
    # Round 1: 无实体记忆 → 挂靠已有实体（embeddings 余弦相似度）
    # ----------------------------------------------------------
    print("\n--- Round 1/2: 挂靠已有实体 ---")

    existing_entities = adapter.fetch_existing_entities()
    has_existing = len(existing_entities) > 0
    print(f"   已有实体数: {len(existing_entities)}")

    entity_centroids: dict[str, np.ndarray] = {}
    # 记录哪些已有实体在 Round 1 新增了成员（需要后续增强因果链）
    entities_with_new_members: set[str] = set()
    round1_plans: list[dict] = []

    if has_existing and without_entity_count > 0:
        # 计算每个已有实体的质心 embedding
        all_member_ids = list({uid for members in existing_entities.values() for uid in members})
        member_embs = adapter.fetch_embeddings_by_ids(all_member_ids)

        for eid, members in existing_entities.items():
            embs = [member_embs[uid] for uid in members if uid in member_embs]
            if embs:
                entity_centroids[eid] = np.mean(embs, axis=0)
        print(f"   有质心的实体: {len(entity_centroids)}")

        if entity_centroids:
            centroid_matrix = np.stack(list(entity_centroids.values()))  # (n_entities, dim)
            centroid_ids = list(entity_centroids.keys())

            # 找到无实体的 unit 索引
            unassociated_idx = np.where(~processed)[0]
            unassociated_embs = embeddings[unassociated_idx]

            # 批量余弦相似度
            norms_centroids = np.linalg.norm(centroid_matrix, axis=1, keepdims=True)
            norms_units = np.linalg.norm(unassociated_embs, axis=1, keepdims=True)
            sim_matrix = (unassociated_embs @ centroid_matrix.T) / (norms_units @ norms_centroids.T + 1e-10)

            # 每条记忆 → 最佳实体
            best_entity_idx = np.argmax(sim_matrix, axis=1)
            best_sim = sim_matrix[np.arange(len(unassociated_idx)), best_entity_idx]

            round1_plans = []
            matched_count = 0
            for k, sim in enumerate(best_sim):
                if sim >= _ENTITY_MATCH_THRESHOLD:
                    global_idx = unassociated_idx[k]
                    match_eid = centroid_ids[best_entity_idx[k]]
                    round1_plans.append({
                        "unit_id": unit_ids[global_idx],
                        "entity_id": match_eid,
                    })
                    processed[global_idx] = True
                    matched_count += 1
                    entities_with_new_members.add(match_eid)

            if round1_plans:
                all_unit_entity_plans.append(round1_plans)
            print(f"   Round 1 挂靠: {matched_count}/{without_entity_count}（涉及 {len(entities_with_new_members)} 个已有实体）")
        else:
            print("   无有效质心，跳过 Round 1")
    else:
        if not has_existing:
            print("   无已有实体，跳过 Round 1")
        else:
            print("   所有 unit 已有关联，跳过 Round 1")

    # ----------------------------------------------------------
    # Round 2: HDBSCAN 聚类（替代原 Rounds 2-4 三次 DBSCAN）
    # ----------------------------------------------------------
    remaining = np.where(~processed)[0]
    hdbscan_silhouette_val: float | None = None
    hdbscan_cluster_count: int = 0

    if hdbscan_adaptive:
        hdb_min_cluster_size, hdb_min_samples = adaptive_hdbscan_params(
            len(remaining),
            min_samples_min=hdbscan_min_samples_min,
            min_samples_max=hdbscan_min_samples_max,
        )
        print(f"   [自适应] HDBSCAN 参数: n_samples={len(remaining)}, "
              f"min_cluster_size={hdb_min_cluster_size}, min_samples={hdb_min_samples}")
    else:
        hdb_min_cluster_size = min_samples
        hdb_min_samples = None

    min_samples_threshold = hdb_min_cluster_size if hdbscan_adaptive else min_samples

    if len(remaining) > min_samples_threshold:
        print(f"\n--- Round 2: HDBSCAN 聚类（剩余 {len(remaining)} 条） ---")

        sub_emb = embeddings[remaining]
        sub_ids = [unit_ids[i] for i in remaining]
        sub_texts = [unit_texts[i] for i in remaining]
        sub_entity_sets = [unit_entity_sets[i] for i in remaining]

        r_labels, _, hdbscan_silhouette_val = run_hdbscan_clustering(
            sub_emb,
            min_cluster_size=hdb_min_cluster_size,
            min_samples=hdb_min_samples,
            cluster_selection_method="eom",
        )
        hdbscan_cluster_count = len(set(r_labels) - {-1})

        new_clustered = r_labels != -1
        if new_clustered.sum() > 0:
            ep, uep, clp, et = process_clusters(
                labels=r_labels,
                unit_ids=sub_ids,
                unit_texts=sub_texts,
                unit_entity_sets=sub_entity_sets,
                skip_entity=skip_entity,
                llm_api_url=llm_api_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                min_llm_size=min_llm_size,
                max_group_size=max_group_size,
                label_prefix="r2",
            )
            all_entity_plans.append(ep)
            all_unit_entity_plans.append(uep)
            all_memory_link_plans.append(clp)
            all_enriched_texts.update(et)

            # Bug fix [P1]: 只标记实际写入 unit_entity 的 items
            # 大簇被 max_group_size 跳过 → process_clusters 返回空 ep
            if ep:
                processed_uids: set[str] = set()
                for ue in uep:
                    processed_uids.add(str(ue.get("unit_id", "")))
                processed_mask = np.zeros(len(remaining), dtype=bool)
                for local_idx, uid in enumerate(sub_ids):
                    if str(uid) in processed_uids:
                        processed_mask[local_idx] = True
                processed[remaining[processed_mask]] = True
                newly_processed = int(processed_mask.sum())
            else:
                newly_processed = 0

            print(f"   Round 2 新增聚类: {newly_processed}/{len(remaining)}" + (" (全部被 max_group_size 跳过)" if not ep else ""))
        else:
            print(f"   Round 2 无有效簇")
    else:
        print(f"   剩余 {len(remaining)} 条，小于 min_cluster_size={min_samples_threshold}，跳过 Round 2")

    # 剩余未处理的标记为噪声

    # 合并各轮计划
    entity_write_plan = [item for plan in all_entity_plans for item in plan]
    unit_entity_write_plan = [item for plan in all_unit_entity_plans for item in plan]
    memory_link_plan = [item for plan in all_memory_link_plans for item in plan]
    enriched_texts = all_enriched_texts

    total_handled = processed.sum()
    print(f"\n   总计：{total_handled}/{n} 条已处理（含已有实体），{n - total_handled} 条噪声")

    # ----------------------------------------------------------
    # 实体合并：相似实体质心合并（两步法，避免 n×n 大矩阵）
    # ----------------------------------------------------------
    total_entity_ids = len(entity_centroids) + len(entity_write_plan)
    if total_entity_ids <= 1:
        print(f"\n🔀 实体合并跳过（共 {total_entity_ids} 个实体，无需合并）")
    else:
        print(f"\n🔀 实体合并检查（新 {len(entity_write_plan)} / 已有 {len(entity_centroids)}，共 {total_entity_ids} 个实体）...")

        # 构建新实体质心
        new_centroids: dict[str, np.ndarray] = {}
        new_ids = [e["entity_id"] for e in entity_write_plan]
        if new_ids:
            new_member_ids = list({uid for e in entity_write_plan for uid in e.get("member_ids", [])})
            new_embs = adapter.fetch_embeddings_by_ids(new_member_ids) if new_member_ids else {}
            for e in entity_write_plan:
                embs = [new_embs[uid] for uid in e.get("member_ids", []) if uid in new_embs]
                if embs:
                    new_centroids[e["entity_id"]] = np.mean(embs, axis=0)

        merge_map: dict[str, str] = {}

        # Step A: 新实体之间的合并（小矩阵，O(m²)）
        if len(new_centroids) > 1:
            print(f"   Step A: 新实体间合并（{len(new_centroids)} 个）...")
            merge_map.update(merge_similar_entities(new_centroids, threshold=_MERGE_SIMILAR_THRESHOLD))

        # Step B: 新实体 vs 已有实体（单向比对，O(m × k)，不构造全量 n×n）
        if entity_centroids and new_centroids:
            # 排除 Step A 已被合并的新实体
            remaining_new = {eid: c for eid, c in new_centroids.items() if eid not in merge_map}
            if remaining_new:
                print(f"   Step B: 新实体匹配已有实体（{len(remaining_new)} → {len(entity_centroids)}）...")
                merge_map.update(match_new_to_existing(remaining_new, entity_centroids, threshold=_MERGE_EXISTING_THRESHOLD))

        # 应用合并结果
        if merge_map:
            print(f"   合并 {len(merge_map)} 个实体")
            # 修正 unit_entity_write_plan 中被合并实体的 entity_id
            for ue in unit_entity_write_plan:
                while ue["entity_id"] in merge_map:
                    ue["entity_id"] = merge_map[ue["entity_id"]]
            # 从 entity_write_plan 移除被合并的实体
            merged_eids = set(merge_map.keys())
            entity_write_plan = [e for e in entity_write_plan if e["entity_id"] not in merged_eids]
        else:
            print("   无相似实体需要合并")

    # ----------------------------------------------------------
    # 因果链增强：已有实体新增成员后，重新检测完整成员的因果链
    # ----------------------------------------------------------
    if entities_with_new_members:
        print(f"\n🔗 因果链增强（{len(entities_with_new_members)} 个实体有新成员）...")
        print(f"   增量模式: {'开启' if causal_incremental else '关闭'}" + (f"（仅新成员相关）" if causal_incremental and causal_new_only else ""))

        # 构建已有因果链的去重集合（包含本轮 + DB 历史）
        existing_seen: set = set()
        for link in memory_link_plan:
            existing_seen.add((str(link.get("from_id", "")), str(link.get("to_id", "")), str(link.get("link_type", ""))))
        # 加载 DB 已有的全部链接，避免多次 apply 重复检测同一因果对
        print(f"   加载已有因果链用于去重（bank={bank_id}）...")
        existing_seen.update(adapter.fetch_all_links(bank_id=bank_id))
        print(f"   去重集合: {len(existing_seen)} 条已知链接")

        enhanced_links: list[dict] = []
        enhanced_enriched: dict[str, list[str]] = {}
        total_pairs_checked = 0
        causal_enhance_start = time.time()

        # 大型实体只对新增成员 + 采样旧成员做因果检测，避免 n² 爆炸

        for eid in entities_with_new_members:
            old_members = existing_entities.get(eid, [])
            new_members = [ue["unit_id"] for ue in round1_plans if ue["entity_id"] == eid]

            if len(new_members) == 0:
                continue

            # 确定参与因果检测的成员范围
            if not causal_incremental or not causal_new_only:
                # 非增量模式 或 全量模式：全量 old + new（小实体）或 new + 采样 old（大实体）
                if len(old_members) <= _MAX_FULL_MEMBERS_FOR_CAUSAL:
                    all_members = old_members + new_members
                    sampled_old_for_log = old_members
                else:
                    sampled_old = random.sample(old_members, min(_MAX_SAMPLE_OLD_FOR_CAUSAL, len(old_members)))
                    all_members = sampled_old + new_members
                    sampled_old_for_log = sampled_old
            else:
                # 增量 + 仅新成员相关：检测 new×new + new×old
                # 小实体：用全部 old；大实体：用采样 old
                if len(old_members) <= _MAX_FULL_MEMBERS_FOR_CAUSAL:
                    sampled_old_for_log = old_members
                else:
                    sampled_old_for_log = random.sample(old_members, min(_MAX_SAMPLE_OLD_FOR_CAUSAL, len(old_members)))
                all_members = sampled_old_for_log + new_members

            if len(all_members) < 2:
                continue

            # 统计检测对数（用于日志）
            if causal_incremental and causal_new_only:
                n_new = len(new_members)
                n_old_eff = min(len(old_members), _MAX_SAMPLE_OLD_FOR_CAUSAL if len(old_members) > _MAX_FULL_MEMBERS_FOR_CAUSAL else len(old_members))
                total_pairs_checked += n_new * (n_new - 1) // 2 + n_new * n_old_eff
            else:
                n_all = len(all_members)
                total_pairs_checked += n_all * (n_all - 1) // 2

            # 分离：当前 batch 内 vs 需要从 DB 查的
            local_indices: list[int] = []
            external_ids: list[str] = []
            for uid in all_members:
                if uid in uid_to_local_idx and unit_texts[uid_to_local_idx[uid]]:
                    local_indices.append(uid_to_local_idx[uid])
                else:
                    external_ids.append(uid)

            # 从 DB 批量取外部成员文本
            ext_texts = adapter.fetch_unit_texts_batch(external_ids) if external_ids else {}

            # 构建完整的 unit_ids / unit_texts 数组，并区分 new / old 的索引
            full_uids: list[str] = []
            full_texts: list[str] = []
            uid_to_full_idx: dict[str, int] = {}

            # 先放旧成员（采样后的）
            old_full_indices: list[int] = []
            for old_uid in sampled_old_for_log:
                if old_uid in uid_to_local_idx and unit_texts[uid_to_local_idx[old_uid]]:
                    full_idx = len(full_uids)
                    full_uids.append(unit_ids[uid_to_local_idx[old_uid]])
                    full_texts.append(unit_texts[uid_to_local_idx[old_uid]])
                    uid_to_full_idx[old_uid] = full_idx
                    old_full_indices.append(full_idx)
                elif old_uid in ext_texts:
                    full_idx = len(full_uids)
                    full_uids.append(old_uid)
                    full_texts.append(ext_texts[old_uid])
                    uid_to_full_idx[old_uid] = full_idx
                    old_full_indices.append(full_idx)

            # 再放新成员
            new_full_indices: list[int] = []
            for new_uid in new_members:
                if new_uid in uid_to_local_idx and unit_texts[uid_to_local_idx[new_uid]]:
                    full_idx = len(full_uids)
                    full_uids.append(unit_ids[uid_to_local_idx[new_uid]])
                    full_texts.append(unit_texts[uid_to_local_idx[new_uid]])
                    uid_to_full_idx[new_uid] = full_idx
                    new_full_indices.append(full_idx)
                elif new_uid in ext_texts:
                    full_idx = len(full_uids)
                    full_uids.append(new_uid)
                    full_texts.append(ext_texts[new_uid])
                    uid_to_full_idx[new_uid] = full_idx
                    new_full_indices.append(full_idx)

            if len(new_full_indices) + len(old_full_indices) < 2:
                continue

            try:
                if causal_incremental and causal_new_only:
                    # 增量模式：只检测 new×new 和 new×old
                    new_links = _detect_causal_in_group_incremental(
                        group_label=eid,
                        new_members=new_full_indices,
                        old_members=old_full_indices,
                        unit_ids=full_uids,
                        unit_texts=full_texts,
                        seen_pairs=existing_seen,
                        group_prefix="",
                    )
                else:
                    # 非增量或全量模式：全量两两比较
                    full_members = list(range(len(full_uids)))
                    new_links = _detect_causal_in_group(
                        group_label=eid,
                        members=full_members,
                        unit_ids=full_uids,
                        unit_texts=full_texts,
                        seen_pairs=existing_seen,
                        group_prefix="",
                    )
                if new_links:
                    enhanced_links.extend(new_links)
                    for link in new_links:
                        if "enriched_to_text" in link and link["enriched_to_text"]:
                            uid = str(link["to_id"])
                            if uid not in enhanced_enriched:
                                enhanced_enriched[uid] = []
                            enhanced_enriched[uid].append(link["enriched_to_text"])
            except Exception as exc:
                print(f"   ⚠ 实体 {eid} 因果链增强失败: {exc}")

        # 去重保护
        if enhanced_links:
            before_dedup = len(enhanced_links)
            enhanced_links = dedup_memory_links(enhanced_links)
            after_dedup = len(enhanced_links)
            if before_dedup != after_dedup:
                print(f"   去重: {before_dedup} → {after_dedup}（移除 {before_dedup - after_dedup} 条重复）")

            memory_link_plan.extend(enhanced_links)
            for uid, texts in enhanced_enriched.items():
                if uid in enriched_texts:
                    enriched_texts[uid].extend(texts)
                else:
                    enriched_texts[uid] = texts

            causal_enhance_duration = time.time() - causal_enhance_start
            print(f"   ✅ 增强 {len(enhanced_links)} 条因果链（{len(enhanced_enriched)} 个 enrichment）")
            print(f"   📊 检测约 {total_pairs_checked} 对，耗时 {causal_enhance_duration:.2f}s")
        else:
            causal_enhance_duration = time.time() - causal_enhance_start
            print(f"   无新增因果链（检测约 {total_pairs_checked} 对，耗时 {causal_enhance_duration:.2f}s）")

    # ============================================================
    # Phase 3: 写入数据库
    # ============================================================
    print("\n💾 Phase 3: 写入数据库...")

    if not apply:
        print("   📝 DRY-RUN MODE: 不写入数据库，仅打印计划")
        print(f"     entities 计划: {len(entity_write_plan)} 条")
        print(f"     unit_entities 计划: {len(unit_entity_write_plan)} 条")
        print(f"     memory_links 计划: {len(memory_link_plan)} 条")
        print(f"     enriched_texts 计划: {len(enriched_texts)} 个 memory_unit")
        adapter.close()
        return

    # Cleanup first (if requested)
    if cleanup:
        adapter.cleanup_old_clusters(force=force, bank_id=bank_id)

    # 构建预取文本字典，消除 apply_to_db 内部重复查询
    prefetched_texts: dict[str, str] = {}
    for i, uid in enumerate(unit_ids):
        prefetched_texts[str(uid)] = unit_texts[i]

    # 剥离过程数据 member_ids（只用于质心计算，下游不需要）
    member_ids_removed = sum(len(e.get("member_ids", [])) for e in entity_write_plan)
    for e in entity_write_plan:
        e.pop("member_ids", None)
    if member_ids_removed:
        print(f"   剥离 member_ids: 移除 {member_ids_removed} 个关联 ID，减少管道传输数据量")

    # Apply
    adapter.apply_to_db(
        entity_write_plan=entity_write_plan,
        unit_entity_write_plan=unit_entity_write_plan,
        memory_link_plan=memory_link_plan,
        enriched_texts=enriched_texts,
        bank_id=bank_id,
        prefetched_texts=prefetched_texts,
    )

    # ============================================================
    # 审计日志：记录当次聚类运行元数据
    # ============================================================
    total_handled_audit = int(processed.sum())
    _log_clustering_run(
        total_units=n,
        processed_units=total_handled_audit,
        noise_units=n - total_handled_audit,
        entity_count=len(entity_write_plan),
        cluster_count=hdbscan_cluster_count,
        silhouette=hdbscan_silhouette_val,
        memory_links=len(memory_link_plan),
        max_group_size=max_group_size,
        min_llm_size=min_llm_size,
        bank_id=bank_id,
        duration_sec=time.time() - run_start,
    )

    # ============================================================
    # Phase 3.5: 自动标记记忆（--apply 时运行）
    # ============================================================
    if apply and not skip_entity:
        try:
            import importlib
            _mark_module = importlib.import_module("mark_memory")
            mark_keyword_memories = _mark_module.mark_keyword_memories
            print("\n🏷️ Phase 3.5: 标记记忆...")
            result = mark_keyword_memories(adapter, dry_run=False)
            marked = result.get("total_marked", 0)
            if marked:
                print(f"   ✅ 标记 {marked} 条记忆")
        except Exception as e:
            print(f"   ⚠️  自动标记跳过: {e}")

    # ============================================================
    # Phase 4: 更新 embedding
    # ============================================================
    print("\n🔄 Phase 4: 更新 memory_units.embedding...")

    embed_unit_ids: set[str] = set()
    for uid in enriched_texts.keys():
        embed_unit_ids.add(str(uid))

    if embed_unit_ids:
        try:
            from pgvector.psycopg2 import register_vector

            register_vector(adapter.conn)
        except ImportError:
            pass

        texts_to_embed = []
        id_order = []
        for uid in sorted(embed_unit_ids):
            # 优先走内存文本索引，避免 O(N) DB 往返
            text = unit_texts[uid_to_local_idx[uid]] if uid in uid_to_local_idx else adapter.fetch_unit_text(uid)
            if text:
                texts_to_embed.append(text)
                id_order.append(uid)
        if texts_to_embed:
            new_embeddings = _embed_fn(texts_to_embed)
            if new_embeddings:
                if len(new_embeddings) != len(texts_to_embed):
                    print(f"   ⚠️  batch_embed 数量不匹配: 输入 {len(texts_to_embed)} 条, 返回 {len(new_embeddings)} 条, 跳过本轮更新")
                else:
                    # 批量更新 embedding，一次 execute_values 完成
                    updates = [(emb, uid) for uid, emb in zip(id_order, new_embeddings)]
                    updated = adapter.batch_update_embeddings(updates)
                    print(f"   ✅ embedding 更新完成: {updated} 条")
            else:
                print("   ⚠️  embedding 更新失败：batch_embed returned None")
        else:
            print("   ⚠️  embedding 更新跳过：无待嵌入文本")
    else:
        print("   ⚠️  embedding 更新跳过：无需更新的 unit_id")

    adapter.close()
    print("\n🎉 Clustering analysis completed successfully.")


def main() -> None:
    """CLI 主入口"""
    app()


if __name__ == "__main__":
    main()
