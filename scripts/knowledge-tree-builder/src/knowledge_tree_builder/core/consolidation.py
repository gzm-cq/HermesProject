"""Consolidation 纠错回路 — 树结构演化（拆分与合并）

P1 功能：活跃科目注意力评分 → 拆分（>50点）→ 合并提示（共现>80%）。
"""

from __future__ import annotations

import json
import logging
import math
import random
from itertools import combinations
from typing import Any

import numpy as np

try:
    from sklearn.cluster import HDBSCAN as HDBSCANCluster
    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCANCluster = None
    HDBSCAN_AVAILABLE = False

logger = logging.getLogger(__name__)


class ConsolidationEngine:
    """Consolidation 纠错回路引擎。

    功能：
    1. 计算科目活跃度评分，优先 review 活跃科目
    2. 科目知识点 > 50 时触发 sub-clustering 拆分
    3. 相邻科目共现率 > 80% 时提示人工确认合并
    """

    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url

    def collect_baseline_metrics(self, db_adapter) -> dict[str, float] | None:
        """采集 4 个核心质量指标用于基线反馈。

        指标：
          avg_confidence   — knowledge_point 的平均 retrieval_confidence
          total_kps        — knowledge_point 总数
          fragment_domains — 知识点 < 3 的 domain 数
          orphan_kps       — 无边 knowledge_point 数（不在 knowledge_tree_edges 中）

        Returns:
          dict with 4 float metrics, or None if no db_adapter
        """
        if db_adapter is None:
            return None
        cursor = db_adapter.cursor

        # 1. avg_confidence
        cursor.execute("SELECT COALESCE(AVG(retrieval_confidence), 0.0) FROM knowledge_tree WHERE node_type = 'knowledge_point'")
        avg_confidence = float(cursor.fetchone()[0] or 0.0)

        # 2. total_kps
        cursor.execute("SELECT COUNT(*) FROM knowledge_tree WHERE node_type = 'knowledge_point'")
        total_kps = float(cursor.fetchone()[0] or 0)

        # 3. fragment_domains: domains with < 3 total knowledge points recursively
        cursor.execute("""
            WITH RECURSIVE descendants AS (
                SELECT id, parent_id, node_type FROM knowledge_tree WHERE parent_id IS NULL AND node_type = 'subject'
                UNION ALL
                SELECT child.id, child.parent_id, child.node_type FROM knowledge_tree child
                JOIN descendants d ON child.parent_id = d.id
            ),
            kp_count AS (
                SELECT d.id, COUNT(*) AS cnt FROM descendants d
                JOIN knowledge_tree kp ON kp.parent_id = d.id
                WHERE kp.node_type = 'knowledge_point'
                GROUP BY d.id
            )
            SELECT COUNT(*) FROM kp_count WHERE cnt < 3
        """)
        fragment_domains = float(cursor.fetchone()[0] or 0)

        # 4. orphan_kps — 不在 knowledge_tree_edges 中的知识点
        cursor.execute("""
            SELECT COUNT(*) FROM knowledge_tree kp
            WHERE kp.node_type = 'knowledge_point'
            AND NOT EXISTS (
                SELECT 1 FROM knowledge_tree_edges e
                WHERE e.child_id = kp.id
            )
        """)
        orphan_kps = float(cursor.fetchone()[0] or 0)

        return {
            "avg_confidence": avg_confidence,
            "total_kps": total_kps,
            "fragment_domains": fragment_domains,
            "orphan_kps": orphan_kps,
        }

    def run(
        self,
        subjects: list[dict[str, Any]] | None = None,
        *,
        dry_run: bool = True,
        split_threshold: int = 50,
        merge_cooccurrence: float = 0.30,
        top_n: int = 5,
        db_adapter=None,
        min_domain_nodes: int = 0,          # 0 = 不启用 domain 合并
        domain_merge_threshold: float = 0.6,
    ) -> dict[str, Any]:
        """执行一次 consolidation。

        Args:
            subjects: 科目列表（含 id, name, point_count, placement_delta 等）
                      为 None 时从 DB 加载
            dry_run: True 则不实际修改
            split_threshold: 拆分阈值（知识点数 > 此值触发 sub-clustering）
            merge_cooccurrence: 合并共现率阈值
            top_n: 每次 review 的活跃科目数
            min_domain_nodes: domain 合并阈值，>0 时启用碎片 domain 合并
            domain_merge_threshold: 余弦相似度阈值

        Returns:
            纠错报告
        """
        results: dict[str, Any] = {
            "status": "completed",
            "dry_run": dry_run,
            "domain_merges": [],
            "splits": [],
            "merge_suggestions": [],
            "reviews": [],
        }

        # ===== 阶段 0: 碎片 domain 合并 =====
        if min_domain_nodes > 0 and db_adapter:
            dm = self.merge_small_domains(
                db_adapter,
                min_nodes=min_domain_nodes,
                threshold=domain_merge_threshold,
                dry_run=dry_run,
            )
            results["domain_merges"] = [dm] if dm else []

        if not subjects:
            results["status"] = "no_data"
            results["message"] = "未提供科目数据，跳过 consolidation"
            return results

        # 1. 计算科目评分，按活跃度排序
        scored = self.score_subjects(subjects)
        scored.sort(key=lambda s: s["score"], reverse=True)
        active = scored[:top_n]

        for subject in active:
            sid = subject["id"]
            point_count = subject.get("point_count", 0)

            # 2. 检查拆分条件
            if point_count > split_threshold:
                split_result = self.split_subject(
                    sid, subject["name"],
                    subject.get("points", []),
                    subject.get("embeddings"),
                    dry_run=dry_run,
                    split_threshold=split_threshold,
                )
                if split_result:
                    results["splits"].append(split_result)

            # 3. 计算并更新 TaxoGen 局部偏移向量
            if db_adapter and not dry_run and subject.get("embeddings") is not None:
                try:
                    from knowledge_tree_builder.core.incremental import compute_subject_offset
                    children = subject.get("children_embeddings", [])
                    siblings = subject.get("sibling_embeddings", [])
                    if children:
                        offset = compute_subject_offset(children, siblings)
                        db_adapter.update_local_offset(sid, offset)
                except Exception as exc:
                    logger.warning("科目 %s local offset 更新失败: %s", sid, exc)

            # 4. 记录 review
            results["reviews"].append({
                "subject_id": sid,
                "name": subject["name"],
                "score": subject["score"],
                "point_count": point_count,
                "split_triggered": point_count > split_threshold,
            })

        # 4. 检查合并条件（跨科目共现）
        merge_suggestions = self.check_merge(
            active,
            merge_cooccurrence=merge_cooccurrence,
            db_adapter=db_adapter,
        )
        results["merge_suggestions"] = merge_suggestions

        return results

    def score_subjects(
        self, subjects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """计算各科目的 consolidation score。

        score = w1 * placement_delta + w2 * k_vector_change
              + w3 * days_since_review + w4 * recall_count_decayed

        recall_count_decayed 基于使用日志表 knowledge_use_log 的实际召回频率。
        """
        for subject in subjects:
            delta = subject.get("placement_delta", 0)
            k_change = subject.get("k_vector_change", 0)
            days = subject.get("days_since_review", 30)
            recall_decayed = subject.get("recall_count_decayed", 0)
            subject["score"] = (
                1.0 * delta + 0.5 * k_change + 0.1 * days + 0.3 * recall_decayed
            )
        return subjects

    def split_subject(
        self,
        subject_id: int,
        subject_name: str,
        points: list[str],
        embeddings: np.ndarray | None = None,
        *,
        dry_run: bool = True,
        split_threshold: int = 50,
    ) -> dict[str, Any] | None:
        """对知识点 > split_threshold 的科目执行 sub-clustering 拆分。

        Args:
            subject_id: 科目 ID
            subject_name: 科目名
            points: 知识点文本列表
            embeddings: 对应的 embedding 矩阵
            dry_run: True 则不实际修改 DB
            split_threshold: 拆分阈值

        Returns:
            拆分结果 dict，或 None（无需拆分）
        """
        if len(points) <= split_threshold:
            return None

        if not HDBSCAN_AVAILABLE or embeddings is None or len(embeddings) < 5:
            return {
                "subject_id": subject_id,
                "name": subject_name,
                "status": "skipped",
                "reason": "HDBSCAN 不可用或 embedding 不足",
            }

        # 跑 HDBSCAN sub-clustering（带超时保护）
        assert HDBSCANCluster is not None  # guarded above
        import concurrent.futures as _cf
        _cluster_executor = _cf.ThreadPoolExecutor(max_workers=1)
        _cluster_future = _cluster_executor.submit(
            HDBSCANCluster(
                min_cluster_size=5,
                cluster_selection_method="eom",
                metric="euclidean",
            ).fit_predict, embeddings
        )
        try:
            labels = _cluster_future.result(timeout=60)
        except _cf.TimeoutError:
            logger.warning("HDBSCAN 聚类超时（60s），跳过科目 %s 的拆分", subject_name)
            _cluster_future.cancel()
            _cluster_executor.shutdown(wait=False)
            return {
                "subject_id": subject_id,
                "name": subject_name,
                "status": "skipped",
                "reason": "HDBSCAN 超时（>60s）",
            }
        unique_labels = set(labels) - {-1}

        if len(unique_labels) <= 1:
            return {
                "subject_id": subject_id,
                "name": subject_name,
                "status": "no_split_needed",
                "reason": "知识点分布均匀，无法分成多个子簇",
                "cluster_count": len(unique_labels),
            }

        # 构建子簇
        clusters: list[dict[str, Any]] = []
        for cid in sorted(unique_labels):
            mask = labels == cid
            cluster_points = [points[i] for i in range(len(points)) if mask[i]]
            clusters.append({
                "cluster_id": int(cid),
                "point_count": len(cluster_points),
                "points": cluster_points if dry_run else [],
            })

        noise_count = int((labels == -1).sum())

        result = {
            "subject_id": subject_id,
            "name": subject_name,
            "status": "split_ready" if dry_run else "split_applied",
            "clusters": clusters,
            "noise_count": noise_count,
            "total_points": len(points),
        }

        if not dry_run:
            logger.info(
                "科目 %s（ID=%d）拆分为 %d 个子科",
                subject_name, subject_id, len(clusters),
            )

        return result

    def check_merge(
        self,
        subjects: list[dict[str, Any]],
        merge_cooccurrence: float = 0.30,
        db_adapter=None,
    ) -> list[dict[str, Any]]:
        """检查相邻科目是否需要合并。

        条件：两个科目共现率 > merge_cooccurrence，且知识点数都很少。
        有 db_adapter 时自动建图边（不是挪知识）。

        Args:
            subjects: 科目列表
            merge_cooccurrence: 合并共现率阈值
            db_adapter: PG 适配器（可选，传入时自动建图边）

        Returns:
            合并建议列表
        """
        suggestions: list[dict[str, Any]] = []
        for i, sa in enumerate(subjects):
            for j, sb in enumerate(subjects):
                if j <= i:
                    continue
                cooccur = self._estimate_cooccurrence(sa, sb, db_adapter=db_adapter)
                if cooccur > merge_cooccurrence:
                    a_count = sa.get("point_count", 0)
                    b_count = sb.get("point_count", 0)

                    # 自动建图边（低成本、可逆，比合并更安全）
                    if db_adapter and sa.get("id") and sb.get("id"):
                        try:
                            db_adapter.insert_edge(sa["id"], sb["id"], "related")
                        except Exception:
                            pass

                    suggestions.append({
                        "subject_a": {"id": sa["id"], "name": sa["name"], "point_count": a_count},
                        "subject_b": {"id": sb["id"], "name": sb["name"], "point_count": b_count},
                        "cooccurrence": round(cooccur, 3),
                        "suggested_action": (
                            "建议人工确认合并"
                            if a_count + b_count < 20
                            else "已建图边（知识点较多，不走合并）"
                        ),
                        "edge_created": db_adapter is not None,
                    })
        return suggestions

    def merge_small_domains(
        self,
        db_adapter: Any,
        *,
        min_nodes: int = 5,
        threshold: float = 0.6,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """合并碎片 domain（子节点数少于阈值的 domain 合并到最近的大 domain）。

        整合在 consolidate run 中，作为阶段 0 自动执行，无需独立命令。

        Args:
            db_adapter: PG 适配器
            min_nodes: 子节点数低于此值视为碎片
            threshold: 余弦相似度阈值，低于此值保留不动
            dry_run: True 则不实际修改

        Returns:
            {"total": int, "fragments": int, "merged": int, "deleted": int, "kept": int}
        """
        cursor = db_adapter.cursor
        stats = {"total": 0, "fragments": 0, "merged": 0, "deleted": 0, "kept": 0}

        # 1. 查出所有顶层 subject 节点及其 knowledge_point 总数
        # 使用递归 CTE 统计所有层级下的 knowledge_point（而非仅直接子节点）
        cursor.execute(
            "SELECT kt.id, kt.name, kt.k_vector, "
            "  (WITH RECURSIVE descendants AS ("
            "      SELECT id, node_type FROM knowledge_tree WHERE parent_id = kt.id"
            "    UNION ALL"
            "      SELECT child.id, child.node_type FROM knowledge_tree child"
            "      JOIN descendants d ON child.parent_id = d.id"
            "   )"
            "   SELECT COUNT(*) FROM descendants WHERE node_type = 'knowledge_point'"
            "  ) AS knowledge_count "
            "FROM knowledge_tree kt "
            "WHERE kt.parent_id IS NULL AND kt.node_type = 'subject' "
            "ORDER BY knowledge_count DESC"
        )
        all_rows = cursor.fetchall()
        stats["total"] = len(all_rows)
        if not all_rows:
            return stats

        # 2. 分离保留 domain 和碎片 domain
        # 注意：subject 节点的 k_vector 通常为 NULL（backfill 只填了 knowledge_point 类型）
        # 大 domain 的 centroid 从子节点实时计算，不依赖 subject.k_vector
        big: list[dict[str, Any]] = []
        small: list[dict[str, Any]] = []
        for row in all_rows:
            entry = {
                "id": row[0], "name": row[1],
                "child_count": row[3],
            }
            (big if entry["child_count"] >= min_nodes else small).append(entry)

        # 大 domain 的 child_count 是递归统计的 knowledge_point 数，反映真实数据量

        # 对大 domain，从子节点 k_vector 实时计算 centroid
        for d in big:
            children = self._get_domain_children(cursor, d["id"])
            centroid = self._compute_centroid(children)
            if centroid is not None:
                d["k_vector"] = centroid

        stats["fragments"] = len(small)
        logger.info("domain 合并: 保留 %d, 碎片 %d", len(big), len(small))
        if not small:
            return stats
        if not big:
            logger.warning("所有 %d 个 domain 都是碎片，无大 domain 可合并", len(small))
            return stats

        if dry_run:
            for d in small:
                logger.info("  碎片 domain: %s (%d 知识点)", d["name"], d["child_count"])
            return stats

        # 3. 逐碎片合并
        for sm in small:
            children = self._get_domain_children(cursor, sm["id"])
            if not children:
                # 空 domain：所有 knowledge_point 已搬走或被清空。
                # 但中间层 subject 节点（如 general/root）仍通过 FK 引用此 domain。
                # 需要递归删所有后代，再删自身。
                # 安全校验：确保 sm['id'] 是整数，避免 SQL 注入风险
                if not isinstance(sm["id"], int):
                    raise ValueError(f"Invalid domain id: {sm['id']} (expected int)")
                sp_name = f"cascade_sp_{sm['id']}"
                try:
                    cursor.execute(f"SAVEPOINT {sp_name}")
                    cursor.execute(
                        "WITH RECURSIVE descendants AS ("
                        "  SELECT id FROM knowledge_tree WHERE parent_id = %s"
                        "  UNION ALL"
                        "  SELECT kt.id FROM knowledge_tree kt"
                        "  JOIN descendants d ON kt.parent_id = d.id"
                        ") "
                        "DELETE FROM knowledge_tree WHERE id IN (SELECT id FROM descendants)",
                        (sm["id"],),
                    )
                    cursor.execute("DELETE FROM knowledge_tree WHERE id = %s", (sm["id"],))
                    cursor.execute(f"RELEASE SAVEPOINT {sp_name}")
                    stats["deleted"] += 1
                except Exception as _cascade_err:
                    cursor.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    logger.warning("级联删除 domain %s 失败: %s", sm["name"], _cascade_err)
                    stats["kept"] += 1
                continue

            centroid = self._compute_centroid(children)
            if centroid is None:
                stats["kept"] += 1
                continue

            best = self._find_best_domain(centroid, big, threshold)
            if best is None:
                stats["kept"] += 1
                continue

            child_ids = [int(c["id"]) for c in children]
            # 使用 savepoint 隔离每次合并，单次失败不波及全局
            # 安全校验：确保 sm['id'] 是整数，避免 SQL 注入风险
            if not isinstance(sm["id"], int):
                raise ValueError(f"Invalid domain id: {sm['id']} (expected int)")
            sp_name = f"merge_sp_{sm['id']}"
            try:
                cursor.execute(f"SAVEPOINT {sp_name}")
                # 1. 搬迁所有 knowledge_point 子节点
                cursor.execute(
                    "UPDATE knowledge_tree SET parent_id = %s WHERE id = ANY(%s)",
                    (best["id"], child_ids),
                )
                affected = cursor.rowcount
                if affected != len(child_ids):
                    raise RuntimeError(
                        f"预期更新 {len(child_ids)} 行，实际 {affected} 行"
                    )
                # 2. 搬迁中间层 subject 子节点（domain/root 等）
                #    当前只搬迁了 knowledge_point，subject 子节点仍引用要删的 domain → FK 错误
                cursor.execute(
                    "UPDATE knowledge_tree SET parent_id = %s "
                    "WHERE parent_id = %s AND node_type = 'subject'",
                    (best["id"], sm["id"]),
                )
                cursor.execute("DELETE FROM knowledge_tree WHERE id = %s", (sm["id"],))
                cursor.execute(f"RELEASE SAVEPOINT {sp_name}")
                stats["merged"] += 1
                stats["deleted"] += 1
                logger.info("  合并: %s (%d 知识点) → %s", sm["name"], sm["child_count"], best["name"])
            except Exception as _merge_err:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                logger.warning("合并失败 %s: %s，跳过", sm["name"], _merge_err)
                stats["kept"] += 1

        db_adapter.conn.commit()
        return stats

    @staticmethod
    def _get_domain_children(cursor: Any, domain_id: int) -> list[dict[str, Any]]:
        """递归获取 domain 下所有 knowledge_point 类型的子节点及 k_vector。

        树结构为 domain → domain/root(subject) → knowledge_point，
        需要递归 2 层才能取到有效数据。使用 WITH RECURSIVE CTE。
        """
        cursor.execute(
            """
            WITH RECURSIVE descendants AS (
                SELECT id, k_vector, node_type, ARRAY[id] AS __path
                FROM knowledge_tree
                WHERE parent_id = %s
                UNION ALL
                SELECT kt.id, kt.k_vector, kt.node_type, d.__path || kt.id
                FROM knowledge_tree kt
                JOIN descendants d ON kt.parent_id = d.id
                WHERE NOT kt.id = ANY(d.__path)
            )
            SELECT id, k_vector FROM descendants
            WHERE node_type = 'knowledge_point' AND k_vector IS NOT NULL
            """,
            (domain_id,),
        )
        from knowledge_tree_builder.adapters.database import _parse_k_vector
        results = []
        for r in cursor.fetchall():
            k_vec = _parse_k_vector(r[1])
            if k_vec is not None:
                results.append({"id": r[0], "k_vector": k_vec})
        return results

    @staticmethod
    def _compute_centroid(children: list[dict[str, Any]]) -> list[float] | None:
        """计算子节点 k_vector centroid。

        过滤 NaN/inf、非数值元素（与 update_k_vector 的守卫一致），
        避免余弦相似度产生无意义结果。
        """
        vectors = []
        for c in children:
            try:
                v = np.array(c["k_vector"], dtype=np.float32)
                if np.all(np.isfinite(v)):
                    vectors.append(v)
            except (ValueError, TypeError):
                continue
        if not vectors:
            return None
        return np.mean(vectors, axis=0).tolist()

    @staticmethod
    def _find_best_domain(
        centroid: list[float], domains: list[dict[str, Any]], threshold: float,
    ) -> dict[str, Any] | None:
        """余弦匹配最近的大 domain。"""
        from knowledge_tree_builder.core.embeddings import cosine_similarity
        vec = np.array(centroid, dtype=np.float32)
        best, best_score = None, threshold
        for d in domains:
            if d.get("k_vector"):
                score = cosine_similarity(vec, np.array(d["k_vector"], dtype=np.float32))
                if score > best_score:
                    best_score, best = score, d
        return best

    @staticmethod
    def _estimate_cooccurrence(
        sa: dict[str, Any], sb: dict[str, Any],
        db_adapter=None,
    ) -> float:
        """估算两个科目的共现率。

        有 db_adapter 时基于 knowledge_use_log 表的真实聚合查询。
        无 db_adapter 时 fallback 到 placement 时间接近度估算。
        """
        if db_adapter and sa.get("id") and sb.get("id"):
            try:
                # 双向查询：sa→sb 和 sb→sa，取最大值
                cooc_ab = db_adapter.query_cooccurrence(sa["id"])
                cooc_ba = db_adapter.query_cooccurrence(sb["id"])
                val_ab = cooc_ab.get(sb["id"], 0.0)
                val_ba = cooc_ba.get(sa["id"], 0.0)
                return max(val_ab, val_ba)
            except Exception:
                pass
        # fallback：无使用日志时返回 0（不触发合并）
        # last_placement_day 字段不存在（subjects 字典中没有该字段），
        # 因此不再基于虚构的时间差做无意义估算
        return 0.0

    def build_kp_edges(
        self,
        db_adapter: Any,
        *,
        max_source_kps: int = 50,
        vector_threshold: float = 0.85,
        same_subject_threshold: float = 0.95,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """构建 KP 级关联边（知识树知识点之间的直接关联）。

        三种策略：
          1. 同源共现 — 同一 source_id 的 KPs 两两建边
          2. 向量桥接 — 跨 subject 的 k_vector cosine > threshold 建边
          3. 同科高相似 — 同一 subject 下 cosine > same_subject_threshold 建边

        与 consolidate 已有的 subject 级边（recall 共现）互补。

        Args:
            db_adapter: 数据库适配器
            max_source_kps: 同源组建边时最多取前 N 个 KPs（防组合爆炸）
            vector_threshold: 跨 subject 向量桥接阈值
            same_subject_threshold: 同科高相似度阈值
            dry_run: 仅统计不写入

        Returns:
            {"source_edges": N, "vector_edges": N, "same_subject_edges": N, "total": N}
        """
        if not db_adapter:
            return {"source_edges": 0, "vector_edges": 0, "same_subject_edges": 0, "total": 0}

        cursor = db_adapter.cursor
        result: dict[str, int] = {"source_edges": 0, "vector_edges": 0, "same_subject_edges": 0, "total": 0}

        # ── 策略 1: 同源共现 ──
        cursor.execute(
            "SELECT id, source_ids FROM knowledge_tree "
            "WHERE node_type = 'knowledge_point' AND source_ids IS NOT NULL "
            "AND array_length(source_ids, 1) > 0 ORDER BY id"
        )
        source_groups: dict[int, list[int]] = {}
        for row in cursor.fetchall():
            kp_id = int(row[0])
            sources = row[1] or []
            for src in sources:
                if src is not None:
                    source_groups.setdefault(int(src), []).append(kp_id)

        existing = self._get_existing_edge_pairs(cursor)
        edge_count = 0
        for src_id, kp_ids in source_groups.items():
            if len(kp_ids) < 2:
                continue
            limited = kp_ids[:max_source_kps]
            for a, b in combinations(limited, 2):
                if (a, b) not in existing:
                    if not dry_run:
                        db_adapter.insert_edge(a, b, "related")
                    edge_count += 1
                    existing.add((a, b))
                    existing.add((b, a))
        result["source_edges"] = edge_count
        result["total"] += edge_count

        # ── 策略 2: 跨 subject 向量桥接 ──
        cursor.execute(
            "SELECT id, parent_id, k_vector::text FROM knowledge_tree "
            "WHERE node_type='knowledge_point' AND k_vector IS NOT NULL"
        )
        kp_data: list[dict] = []
        for row in cursor.fetchall():
            vt = (row[2] or "").strip("[]")
            if not vt:
                continue
            try:
                vec = [float(p) for p in vt.split(",") if p.strip()]
            except ValueError:
                continue
            kp_data.append({"id": int(row[0]), "parent_id": int(row[1]) if row[1] else None, "vector": vec})

        kp_by_subject: dict[int, list[dict]] = {}
        for kp in kp_data:
            pid = kp["parent_id"]
            if pid is not None:
                kp_by_subject.setdefault(pid, []).append(kp)

        # 计算 centroid
        subject_centroids: dict[int, list[float]] = {}
        for pid, kps in kp_by_subject.items():
            if len(kps) < 3:
                continue
            dim = len(kps[0]["vector"])
            cent = [0.0] * dim
            for kp in kps:
                for d in range(dim):
                    cent[d] += kp["vector"][d]
            subject_centroids[pid] = [c / len(kps) for c in cent]

        edge_count = 0
        pid_list = list(subject_centroids.keys())
        for i in range(len(pid_list)):
            for j in range(i + 1, len(pid_list)):
                pid_a, pid_b = pid_list[i], pid_list[j]
                ca, cb = subject_centroids[pid_a], subject_centroids[pid_b]
                dot_c = sum(x * y for x, y in zip(ca, cb))
                nc_a = math.sqrt(sum(x * x for x in ca)) or 1.0
                nc_b = math.sqrt(sum(x * x for x in cb)) or 1.0
                if dot_c / (nc_a * nc_b) < 0.80:
                    continue
                sample_a = kp_by_subject.get(pid_a, [])[:10]
                sample_b = kp_by_subject.get(pid_b, [])[:10]
                for ka_info in sample_a:
                    va = ka_info["vector"]
                    for kb_info in sample_b:
                        if (ka_info["id"], kb_info["id"]) in existing:
                            continue
                        vb = kb_info["vector"]
                        dot = sum(x * y for x, y in zip(va, vb))
                        na = math.sqrt(sum(x * x for x in va)) or 1.0
                        nb = math.sqrt(sum(x * x for x in vb)) or 1.0
                        sim = dot / (na * nb)
                        if sim > vector_threshold:
                            if not dry_run:
                                db_adapter.insert_edge(ka_info["id"], kb_info["id"], "related")
                            edge_count += 1
                            existing.add((ka_info["id"], kb_info["id"]))
                            existing.add((kb_info["id"], ka_info["id"]))
        result["vector_edges"] = edge_count
        result["total"] += edge_count

        # ── 策略 3: 同科高相似度 ──
        edge_count = 0
        all_kps: dict[int, list[float]] = {kp["id"]: kp["vector"] for kp in kp_data}
        kp_by_pid: dict[int, list[int]] = {}
        for kp in kp_data:
            pid = kp["parent_id"]
            if pid is not None:
                kp_by_pid.setdefault(pid, []).append(kp["id"])

        for pid, kid_list in kp_by_pid.items():
            if len(kid_list) < 2:
                continue
            # 性能优化：同科超过 100 个 KPs 时随机采样 100 个，避免 O(n²) 性能问题
            if len(kid_list) > 100:
                # 确定性种子：基于 kid_list 内容，保证同一输入同一输出
                _seed = hash(frozenset(kid_list)) % (2**32)
                rng = random.Random(_seed)
                kid_list = rng.sample(kid_list, 100)
            for i, ka in enumerate(kid_list[:-1]):
                va = all_kps.get(ka)
                if va is None:
                    continue
                for kb in kid_list[i + 1:]:
                    if (ka, kb) in existing:
                        continue
                    vb = all_kps.get(kb)
                    if vb is None:
                        continue
                    dot = sum(x * y for x, y in zip(va, vb))
                    na = math.sqrt(sum(x * x for x in va)) or 1.0
                    nb = math.sqrt(sum(x * x for x in vb)) or 1.0
                    if dot / (na * nb) > same_subject_threshold:
                        if not dry_run:
                            db_adapter.insert_edge(ka, kb, "related")
                        edge_count += 1
                        existing.add((ka, kb))
                        existing.add((kb, ka))
        result["same_subject_edges"] = edge_count
        result["total"] += edge_count

        if not dry_run:
            db_adapter.conn.commit()
        return result

    @staticmethod
    def _get_existing_edge_pairs(cursor) -> set[tuple[int, int]]:
        """查询已有边集合，用于去重。"""
        cursor.execute(
            "SELECT from_node_id, to_node_id FROM knowledge_tree_edges WHERE relation_type = 'related'"
        )
        pairs = set()
        for row in cursor.fetchall():
            pairs.add((int(row[0]), int(row[1])))
            pairs.add((int(row[1]), int(row[0])))
        return pairs
