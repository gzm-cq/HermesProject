"""PG 数据库适配器 — 知识树相关表读写"""

from __future__ import annotations

import logging
try:
    from pgvector.psycopg2 import register_vector
    PGVECTOR_AVAILABLE = True
except ImportError:
    register_vector = None
    PGVECTOR_AVAILABLE = False

from typing import Any

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_STATUS = "pending_review"
LEGACY_REVIEW_STATUS = "pending"


def _parse_k_vector(raw: object) -> list[float] | None:
    """将 pgvector 的 k_vector 安全转为 Python list。

    无论 pgvector psycopg2 适配器是否生效（VECTOR 可能被读为字符串），
    都能正确解析。返回 None 表示无法解析。
    """
    if raw is None:
        return None
    # pgvector 适配器生效时：list[float]
    if isinstance(raw, list):
        return raw
    # pgvector 适配器生效时：tuple/numpy array
    if hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes)):
        try:
            return [float(x) for x in raw]
        except (ValueError, TypeError):
            return None
    # pgvector 适配器未生效时：字符串 '[1.0, 2.0, ...]'
    if isinstance(raw, (str, bytes)):
        import json as _json
        try:
            parsed = _json.loads(str(raw))
            if isinstance(parsed, list) and all(isinstance(x, (int, float)) for x in parsed):
                return [float(x) for x in parsed]
        except (_json.JSONDecodeError, ValueError, TypeError):
            pass
    return None


class DatabaseAdapter:
    """知识树数据库适配器"""

    def __init__(self, db_url: str) -> None:
        self.conn = psycopg2.connect(db_url)
        # 先确保 autocommit=True，再注册 pgvector 类型。
        # register_vector 内部调用 set_session，在活跃事务中会触发
        # "set_session cannot be used inside a transaction" 错误。
        # connect() 默认 autocommit=True，但显式设置一次彻底消除环境差异。
        self.conn.autocommit = True
        if PGVECTOR_AVAILABLE:
            try:
                register_vector(self.conn)
            except Exception as e:
                logger.error("pgvector register_vector 失败（k_vector 将被读为字符串而非 list）: %s", e)
        self.conn.autocommit = False
        self.cursor = self.conn.cursor()
        self._ensure_indexes()

    # ========== Source Articles ==========

    def insert_article(self, wiki_path: str, title: str, source_type: str = "wiki_note") -> int:
        """登记一篇输入文章，返回 ID"""
        self.cursor.execute(
            "INSERT INTO source_articles (wiki_path, title, source_type, extracted_at) "
            "VALUES (%s, %s, %s, NOW()) RETURNING id",
            (wiki_path, title, source_type),
        )
        self.conn.commit()
        return int(self.cursor.fetchone()[0])

    # ========== Knowledge Tree Nodes ==========

    def insert_node(
        self,
        name: str,
        node_type: str,
        parent_id: int | None = None,
        display_order: int = 0,
        source_ids: list[int] | None = None,
    ) -> int:
        """插入一个树节点，返回 ID"""
        self.cursor.execute(
            "INSERT INTO knowledge_tree (name, node_type, parent_id, display_order, source_ids) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, node_type, parent_id, display_order, source_ids or None),
        )
        self.conn.commit()
        return int(self.cursor.fetchone()[0])

    def _ensure_indexes(self) -> None:
        """确保关键索引存在（幂等执行）。"""
        try:
            self.cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_subject_name_parent
                ON knowledge_tree (name, COALESCE(parent_id, -1))
                WHERE node_type = 'subject'
            """)
            self.conn.commit()
        except Exception as e:
            logger.warning("创建 subject 唯一索引失败（不影响运行，仅降级防御）: %s", e)
            self.conn.rollback()

    def get_leaf_nodes(self) -> list[dict[str, Any]]:
        """获取所有叶子节点（knowledge_point 类型）"""
        self.cursor.execute(
            "SELECT id, name, k_vector FROM knowledge_tree WHERE node_type = 'knowledge_point'"
        )
        rows = self.cursor.fetchall()
        return [
            {"id": r[0], "name": r[1], "k_vector": r[2] if len(r) > 2 else None}
            for r in rows
        ]

    def get_sibling_points(self, node_id: int) -> list[dict[str, Any]]:
        """获取同父节点下的所有兄弟知识点"""
        self.cursor.execute(
            "SELECT parent_id FROM knowledge_tree WHERE id = %s",
            (node_id,),
        )
        row = self.cursor.fetchone()
        if not row or row[0] is None:
            return []
        parent_id = row[0]

        self.cursor.execute(
            "SELECT id, name, k_vector FROM knowledge_tree "
            "WHERE parent_id = %s AND node_type = 'knowledge_point'",
            (parent_id,),
        )
        rows = self.cursor.fetchall()
        return [
            {"id": r[0], "name": r[1], "k_vector": r[2] if len(r) > 2 else None}
            for r in rows
        ]

    def update_source_ids(self, node_id: int, new_source_id: int) -> None:
        """追加来源 ID 到已有节点的 source_ids（防重）"""
        self.cursor.execute(
            "UPDATE knowledge_tree SET source_ids = "
            "CASE WHEN %s = ANY(COALESCE(source_ids, ARRAY[]::INT[])) "
            "THEN source_ids "
            "ELSE array_append(COALESCE(source_ids, ARRAY[]::INT[]), %s) "
            "END WHERE id = %s",
            (new_source_id, new_source_id, node_id),
        )
        self.conn.commit()

    def insert_review(
        self, new_text: str, existing_node_id: int, existing_text: str,
        conflict_type: str, similarity: float,
    ) -> int:
        """插入矛盾检测记录到 review_queue。"""
        self.cursor.execute(
            "INSERT INTO knowledge_review_queue "
            "(new_text, existing_node_id, existing_text, conflict_type, similarity, status) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (new_text, existing_node_id, existing_text, conflict_type, similarity, DEFAULT_REVIEW_STATUS),
        )
        self.conn.commit()
        return int(self.cursor.fetchone()[0])

    def insert_edge(
        self, from_node_id: int, to_node_id: int, relation_type: str = "related",
    ) -> int:
        """插入跨科链接边（防重）。"""
        self.cursor.execute(
            "INSERT INTO knowledge_tree_edges "
            "(from_node_id, to_node_id, relation_type, cooccurrence_count) "
            "VALUES (%s, %s, %s, 1) "
            "ON CONFLICT (from_node_id, to_node_id, relation_type) "
            "DO UPDATE SET cooccurrence_count = knowledge_tree_edges.cooccurrence_count + 1 "
            "RETURNING id",
            (from_node_id, to_node_id, relation_type),
        )
        self.conn.commit()
        return int(self.cursor.fetchone()[0])

    def log_use(self, session_id: str, node_ids: list[int], query: str = "") -> int:
        """记录一次知识使用事件。"""
        self.cursor.execute(
            "INSERT INTO knowledge_use_log (session_id, node_ids, query) "
            "VALUES (%s, %s, %s) RETURNING id",
            (session_id, node_ids, query),
        )
        self.conn.commit()
        return int(self.cursor.fetchone()[0])

    def get_node_embedding(self, node_id: int) -> list[float] | None:
        """获取节点的 K 向量"""
        self.cursor.execute(
            "SELECT k_vector FROM knowledge_tree WHERE id = %s", (node_id,)
        )
        row = self.cursor.fetchone()
        if row and row[0] is not None:
            return _parse_k_vector(row[0])
        return None

    def query_cooccurrence(self, subject_id: int, days_back: int = 30) -> dict[int, float]:
        """查询某个科目与其他科目的共现率（基于 knowledge_use_log）。

        逻辑：找到所有回召了该科目下知识点的会话，在该会话中还回召了哪些其他科目。

        Args:
            subject_id: 科目 ID
            days_back: 回溯天数

        Returns:
            {其他科目 ID: 共现率}
        """
        self.cursor.execute(
            """
            WITH target_sessions AS (
                -- 找到所有回召了 subject_id 下知识点的会话
                SELECT DISTINCT kul.session_id
                FROM knowledge_use_log kul
                CROSS JOIN LATERAL unnest(kul.node_ids) AS node_id
                JOIN knowledge_tree kt ON kt.id = node_id
                WHERE (kt.parent_id = %s OR kt.id = %s)
                  AND kul.created_at > NOW() - INTERVAL '1 day' * %s
            ),
            subject_pairs AS (
                -- 在那些会话中，还回召了哪些其他科目
                SELECT DISTINCT kul2.session_id,
                  CASE
                    WHEN kt2.node_type = 'subject' THEN kt2.id
                    ELSE kt2.parent_id
                  END as other_subject_id
                FROM knowledge_use_log kul2
                JOIN target_sessions ts ON kul2.session_id = ts.session_id
                CROSS JOIN LATERAL unnest(kul2.node_ids) AS node_id2
                JOIN knowledge_tree kt2 ON kt2.id = node_id2
                WHERE kt2.node_type IN ('subject', 'knowledge_point')
                  AND kt2.id != %s
            )
            SELECT other_subject_id, COUNT(*)::int as cooc_cnt
            FROM subject_pairs
            WHERE other_subject_id IS NOT NULL AND other_subject_id != %s
            GROUP BY other_subject_id
            ORDER BY cooc_cnt DESC
            LIMIT 20
            """,
            (subject_id, subject_id, days_back, subject_id, subject_id),
        )
        rows = self.cursor.fetchall()
        total = max((r[1] for r in rows), default=1)
        return {r[0]: round(r[1] / total, 4) for r in rows}

    def update_local_offset(self, node_id: int, local_offset: list[float]) -> None:
        """更新科目的局部偏移向量。"""
        self.cursor.execute(
            "UPDATE knowledge_tree SET local_offset = %s, updated_at = NOW() "
            "WHERE id = %s",
            (local_offset, node_id),
        )
        self.conn.commit()

    # ========== Knowledge Point Texts ==========

    def insert_point_text(self, tree_node_id: int, text: str, source_id: int | None = None) -> int:
        """插入一条知识点原文，返回 ID"""
        self.cursor.execute(
            "INSERT INTO knowledge_point_texts (tree_node_id, text, source_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (tree_node_id, text, source_id),
        )
        self.conn.commit()
        return int(self.cursor.fetchone()[0])

    def get_point_texts_by_node(self, node_id: int) -> list[str]:
        """获取某个节点下的所有知识点原文"""
        self.cursor.execute(
            "SELECT text FROM knowledge_point_texts WHERE tree_node_id = %s",
            (node_id,),
        )
        return [r[0] for r in self.cursor.fetchall()]

    def search_point_texts(self, text: str) -> list[dict[str, Any]]:
        """搜索知识点原文（精确匹配），用于去重检查"""
        self.cursor.execute(
            "SELECT kpt.id, kpt.text, kpt.tree_node_id "
            "FROM knowledge_point_texts kpt "
            "WHERE kpt.text = %s",
            (text,),
        )
        rows = self.cursor.fetchall()
        return [
            {"id": r[0], "text": r[1], "tree_node_id": r[2]}
            for r in rows
        ]
    # ========== Schema ==========

    def create_tables(self) -> None:
        """创建知识树相关表（首次部署用）"""
        # 确保 pgvector 扩展已安装（VECTOR(1024) 依赖）
        self.cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.commit()
        statements = [
            """
            CREATE TABLE IF NOT EXISTS source_articles (
                id SERIAL PRIMARY KEY,
                wiki_path TEXT,
                title TEXT,
                source_type VARCHAR(16),
                extracted_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_tree (
                id SERIAL PRIMARY KEY,
                parent_id INT REFERENCES knowledge_tree(id),
                name VARCHAR(128),
                node_type VARCHAR(16),
                display_order INT,
                source_ids INTEGER[],
                k_vector VECTOR(1024),
                placement_count INT DEFAULT 0,
                k_updated_at TIMESTAMPTZ,
                local_offset VECTOR(1024),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_point_texts (
                id SERIAL PRIMARY KEY,
                tree_node_id INT REFERENCES knowledge_tree(id),
                text TEXT NOT NULL,
                source_id INT REFERENCES source_articles(id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_tree_edges (
                id SERIAL PRIMARY KEY,
                from_node_id INT REFERENCES knowledge_tree(id),
                to_node_id INT REFERENCES knowledge_tree(id),
                relation_type VARCHAR(32),
                cooccurrence_count INT DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(from_node_id, to_node_id, relation_type)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_use_log (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(64),
                node_ids INTEGER[],
                query TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_review_queue (
                id SERIAL PRIMARY KEY,
                new_text TEXT NOT NULL,
                existing_node_id INT REFERENCES knowledge_tree(id),
                existing_text TEXT,
                conflict_type VARCHAR(32),
                similarity FLOAT,
                status VARCHAR(16) DEFAULT 'pending_review',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                reviewed_at TIMESTAMPTZ,
                target_knowledge_id INT REFERENCES knowledge_tree(id)
            )
            """,
        ]
        for stmt in statements:
            self.cursor.execute(stmt)
        self.conn.commit()

    # ========== Phase 4: Tree Placement ==========

    def get_all_domains(self) -> list[str]:
        """获取知识树中所有顶级领域名。"""
        self.cursor.execute(
            "SELECT DISTINCT name FROM knowledge_tree "
            "WHERE parent_id IS NULL AND node_type = 'subject' "
            "ORDER BY name"
        )
        return [row[0] for row in self.cursor.fetchall()]

    def get_subjects_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """获取指定领域下的所有科目。"""
        self.cursor.execute(
            "SELECT kt.id, kt.name, kt.k_vector "
            "FROM knowledge_tree kt "
            "JOIN knowledge_tree domain ON kt.parent_id = domain.id "
            "WHERE domain.name = %s AND domain.node_type = 'subject' "
            "AND kt.node_type = 'subject'",
            (domain,),
        )
        rows = self.cursor.fetchall()
        return [
            {"id": str(r[0]), "name": r[1], "k_vector": r[2] if len(r) > 2 else None}
            for r in rows
        ]

    def find_or_create_subject(self, name: str, parent_id: int | None = None) -> int:
        """查找或创建科目节点。返回节点 ID。"""
        # 1. 精确匹配：按 name + parent_id 查找
        if parent_id is None:
            self.cursor.execute(
                "SELECT id FROM knowledge_tree "
                "WHERE name = %s AND parent_id IS NULL AND node_type = 'subject'",
                (name,),
            )
        else:
            self.cursor.execute(
                "SELECT id FROM knowledge_tree "
                "WHERE name = %s AND parent_id = %s AND node_type = 'subject'",
                (name, parent_id),
            )
        row = self.cursor.fetchone()
        if row:
            return row[0]

        # 2. 安全网：检查同名的 subject 是否在别的层级已存在
        #    （防止跨 run 因 parent_id 不对而创建出孤儿副本）
        self.cursor.execute(
            "SELECT id, parent_id FROM knowledge_tree "
            "WHERE name = %s AND node_type = 'subject'",
            (name,),
        )
        existing = self.cursor.fetchone()
        if existing:
            logger.warning(
                "科目 '%s' 已在 parent=%s (id=%s) 存在，重用（阻止创建孤儿副本）",
                name, existing[1], existing[0],
            )
            return existing[0]

        # 3. 新建
        if parent_id is None:
            self.cursor.execute(
                "INSERT INTO knowledge_tree (name, node_type, parent_id, display_order) "
                "VALUES (%s, 'subject', NULL, 0) RETURNING id",
                (name,),
            )
        else:
            self.cursor.execute(
                "INSERT INTO knowledge_tree (name, node_type, parent_id, display_order) "
                "VALUES (%s, 'subject', %s, 0) RETURNING id",
                (name, parent_id),
            )
        new_id = self.cursor.fetchone()[0]
        self.conn.commit()
        return new_id

    def update_k_vector(
        self, node_id: int, k_vector: list[float],
        placement_count: int | None = None,
    ) -> None:
        """更新节点的 k_vector 和（可选）placement_count。

        使用 ::vector 显式类型转换，不依赖 pgvector 的 psycopg2 适配器。
        （适配器可能因版本问题未注册，导致 numpy.ndarray 类型适配失败）

        Note:
            保留自 explain 的 k_updated_at = NOW() 更新和时间戳提交。
        """
        # 转为 PostgreSQL vector 字面量字符串：'[1.0, 2.0, ...]'
        import numpy as np
        vec = np.array(k_vector, dtype=np.float32)
        if not np.all(np.isfinite(vec)):
            logger.warning("k_vector 包含 NaN/inf，跳过节点 %d", node_id)
            return
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        if placement_count is not None:
            self.cursor.execute(
                "UPDATE knowledge_tree SET k_vector = %s::vector, placement_count = %s, "
                "k_updated_at = NOW() WHERE id = %s",
                (vec_str, placement_count, node_id),
            )
        else:
            self.cursor.execute(
                "UPDATE knowledge_tree SET k_vector = %s::vector, "
                "k_updated_at = NOW() WHERE id = %s",
                (vec_str, node_id),
            )
        self.conn.commit()

    # ========== Consolidation ==========

    def update_retrieval_confidence(self, knowledge_id: int, confidence: float) -> None:
        """更新知识的检索 confidence。"""
        self.cursor.execute(
            "UPDATE knowledge_tree SET retrieval_confidence = %s WHERE id = %s",
            (confidence, knowledge_id),
        )

    def get_all_nodes_with_confidence(self) -> list[dict[str, Any]]:
        """获取所有节点及其 retrieval_confidence。"""
        self.cursor.execute(
            "SELECT id, retrieval_confidence FROM knowledge_tree "
            "WHERE node_type = 'knowledge_point'"
        )
        return [
            {"id": r[0], "retrieval_confidence": r[1] if r[1] is not None else 1.0}
            for r in self.cursor.fetchall()
        ]

    def get_recent_use_logs(self, days: int = 30) -> list[dict[str, Any]]:
        """获取近期使用日志。"""
        self.cursor.execute(
            "SELECT id, session_id, node_ids, query, created_at "
            "FROM knowledge_use_log "
            "WHERE created_at > NOW() - INTERVAL '1 day' * %s",
            (days,),
        )
        rows = self.cursor.fetchall()
        return [
            {
                "knowledge_id": r[2][0] if r[2] else None,
                "node_ids": r[2] or [],
                "session_id": r[1],
                "recalled": bool(r[2]),
                "clicked": False,
                "user_feedback": None,
                "timestamp": r[4].isoformat() if r[4] else "",
            }
            for r in rows
        ]

    # ========== Review Queue ==========

    def list_review_queue(
        self, review_type: str | None = None, status: str = DEFAULT_REVIEW_STATUS
    ) -> list[dict[str, Any]]:
        """列出审查队列。兼容历史 pending 状态。"""
        statuses = [status]
        if status == DEFAULT_REVIEW_STATUS:
            statuses.append(LEGACY_REVIEW_STATUS)
        if review_type:
            self.cursor.execute(
                "SELECT id, new_text, existing_text, conflict_type, similarity, "
                "status, created_at "
                "FROM knowledge_review_queue "
                "WHERE status = ANY(%s) AND conflict_type = %s "
                "ORDER BY created_at",
                (statuses, review_type),
            )
        else:
            self.cursor.execute(
                "SELECT id, new_text, existing_text, conflict_type, similarity, "
                "status, created_at "
                "FROM knowledge_review_queue "
                "WHERE status = ANY(%s) "
                "ORDER BY created_at",
                (statuses,),
            )
        return [
            {
                "id": r[0], "new_text": r[1], "existing_text": r[2],
                "type": r[3], "similarity": r[4],
                "status": r[5], "created_at": str(r[6]) if r[6] else "",
            }
            for r in self.cursor.fetchall()
        ]

    def get_review_item(self, review_id: int) -> dict[str, Any] | None:
        """获取单条审查项。"""
        self.cursor.execute(
            "SELECT id, new_text, existing_text, conflict_type, similarity, "
            "status, created_at, target_knowledge_id "
            "FROM knowledge_review_queue WHERE id = %s",
            (review_id,),
        )
        r = self.cursor.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "new_text": r[1], "existing_text": r[2],
            "type": r[3], "similarity": r[4],
            "status": r[5], "created_at": str(r[6]) if r[6] else "",
            "target_knowledge_id": r[7],
        }

    def update_review_status(self, review_id: int, status: str) -> None:
        """更新审查项状态。"""
        self.cursor.execute(
            "UPDATE knowledge_review_queue SET status = %s, reviewed_at = NOW() "
            "WHERE id = %s",
            (status, review_id),
        )

    def delete_node(self, node_id: int) -> None:
        """删除知识树节点。"""
        self.cursor.execute("DELETE FROM knowledge_point_texts WHERE tree_node_id = %s", (node_id,))
        self.cursor.execute("DELETE FROM knowledge_tree WHERE id = %s", (node_id,))

    def close(self) -> None:
        try:
            self.cursor.close()
        except Exception:
            pass
        self.conn.close()

    def __enter__(self) -> "DatabaseAdapter":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        self.close()
        return False
