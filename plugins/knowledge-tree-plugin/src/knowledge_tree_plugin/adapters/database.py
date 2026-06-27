"""DatabaseAdapter 包装层 — 在 knowledge_tree_builder 的 DatabaseAdapter 上加插件专用方法。

本模块包装 knowledge_tree_builder.adapters.database.DatabaseAdapter，
增加插件专用的查询方法：
- search_subjects_by_keywords: ILIKE 模糊匹配科目名称
- get_domain_nodes: 获取顶层领域节点
- get_child_nodes: 获取子节点（含 k_vector, text）
- get_placement_count: 获取放置次数
"""

from __future__ import annotations

from typing import Any

from knowledge_tree_builder import DatabaseAdapter
from knowledge_tree_builder.adapters.database import _parse_k_vector


def _escape_ilike(s: str) -> str:
    """转义 ILIKE 通配符特殊字符（%, _, \\）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PluginDatabaseAdapter:
    """插件专用的 PG 适配器。

    包装 DatabaseAdapter，补充插件需要的专用查询方法。
    所有委托方法直接转发给 _inner。
    """

    def __init__(self, db_url: str) -> None:
        self._inner = DatabaseAdapter(db_url)

    # ========== 委托方法（直接转发） ==========

    @property
    def cursor(self) -> Any:
        """暴露内部 cursor 供 search_subjects_by_keywords 等使用。"""
        return self._inner.cursor

    def insert_node(
        self,
        name: str,
        node_type: str,
        parent_id: int | None = None,
        display_order: int = 0,
        source_ids: list[int] | None = None,
    ) -> int:
        return self._inner.insert_node(name, node_type, parent_id, display_order, source_ids)

    def insert_point_text(
        self,
        tree_node_id: int,
        text: str,
        source_id: int | None = None,
    ) -> int:
        return self._inner.insert_point_text(tree_node_id, text, source_id)

    def batch_insert_nodes(
        self,
        names: list[str],
        parent_id: int | None = None,
        node_type: str = "knowledge_point",
    ) -> list[int]:
        """批量插入知识树节点，返回 ID 列表。

        用单条 multi-row INSERT + RETURNING id，大幅减少 PG 往返。

        Args:
            names: 节点名称列表
            parent_id: 父节点 ID（所有节点同父）
            node_type: 节点类型

        Returns:
            插入的节点 ID 列表（顺序与 names 一致）
        """
        if not names:
            return []
        cursor = self._inner.cursor
        placeholders = ", ".join(
            f"(%s, %s, %s, %s, %s)" for _ in names
        )
        flat_params: list[Any] = []
        for name in names:
            flat_params.extend([name, node_type, parent_id, 0, None])
        try:
            cursor.execute(
                f"INSERT INTO knowledge_tree (name, node_type, parent_id, display_order, source_ids) "
                f"VALUES {placeholders} RETURNING id",
                flat_params,
            )
            ids = [r[0] for r in cursor.fetchall()]
            self._inner.conn.commit()
            return ids
        except Exception:
            self._inner.conn.rollback()
            raise

    def batch_insert_point_texts(
        self,
        records: list[tuple[int, str]],
    ) -> None:
        """批量插入知识点原文。

        Args:
            records: (tree_node_id, text) 列表
        """
        if not records:
            return
        cursor = self._inner.cursor
        placeholders = ", ".join(
            f"(%s, %s)" for _ in records
        )
        flat_params: list[Any] = []
        for node_id, text in records:
            flat_params.extend([node_id, text])
        try:
            cursor.execute(
                f"INSERT INTO knowledge_point_texts (tree_node_id, text) "
                f"VALUES {placeholders}",
                flat_params,
            )
            self._inner.conn.commit()
        except Exception:
            self._inner.conn.rollback()
            raise

    def batch_insert_knowledge_points(
        self,
        records: list[tuple[str, str]],
        parent_id: int | None = None,
        k_vectors: list[list[float]] | None = None,
    ) -> list[int]:
        """单事务批量插入知识点节点和原文，避免孤儿节点。

        Args:
            records: (name, text) 列表
            parent_id: 父节点 ID
            k_vectors: 与 records 同序的知识点向量；提供时写入节点自身 k_vector

        Returns:
            插入的节点 ID 列表，顺序与 records 一致。
        """
        if not records:
            return []
        if k_vectors is not None and len(k_vectors) != len(records):
            raise ValueError("k_vectors length must match records length")
        cursor = self._inner.cursor
        try:
            if k_vectors is None:
                node_placeholders = ", ".join(
                    "(%s, %s, %s, %s, %s)" for _ in records
                )
                node_params: list[Any] = []
                for name, _text in records:
                    node_params.extend([name, "knowledge_point", parent_id, 0, None])
                cursor.execute(
                    f"INSERT INTO knowledge_tree (name, node_type, parent_id, display_order, source_ids) "
                    f"VALUES {node_placeholders} RETURNING id",
                    node_params,
                )
            else:
                node_placeholders = ", ".join(
                    "(%s, %s, %s, %s, %s, %s::vector)" for _ in records
                )
                node_params = []
                for (name, _text), k_vector in zip(records, k_vectors):
                    node_params.extend([name, "knowledge_point", parent_id, 0, None, k_vector])
                cursor.execute(
                    f"INSERT INTO knowledge_tree (name, node_type, parent_id, display_order, source_ids, k_vector) "
                    f"VALUES {node_placeholders} RETURNING id",
                    node_params,
                )
            node_ids = [r[0] for r in cursor.fetchall()]

            text_placeholders = ", ".join("(%s, %s)" for _ in records)
            text_params: list[Any] = []
            for node_id, (_name, text) in zip(node_ids, records):
                text_params.extend([node_id, text])
            cursor.execute(
                f"INSERT INTO knowledge_point_texts (tree_node_id, text) VALUES {text_placeholders}",
                text_params,
            )
            self._inner.conn.commit()
            return node_ids
        except Exception:
            self._inner.conn.rollback()
            raise

    def get_leaf_nodes(self) -> list[dict[str, Any]]:
        nodes = self._inner.get_leaf_nodes()
        for node in nodes:
            node["k_vector"] = _parse_k_vector(node.get("k_vector"))
        return nodes

    def get_sibling_points(self, node_id: int) -> list[dict[str, Any]]:
        return self._inner.get_sibling_points(node_id)

    def get_node_embedding(self, node_id: int) -> list[float] | None:
        return _parse_k_vector(self._inner.get_node_embedding(node_id))

    def update_k_vector(
        self,
        node_id: int,
        k_vector: list[float],
        placement_count: int,
    ) -> None:
        self._inner.update_k_vector(node_id, k_vector, placement_count)

    def insert_review(
        self,
        new_text: str,
        existing_node_id: int,
        existing_text: str,
        conflict_type: str,
        similarity: float,
    ) -> int:
        """插入人工审查记录。"""
        # DatabaseAdapter 可能没有此方法，用原始 SQL 实现
        cursor = self._inner.cursor
        try:
            cursor.execute(
                "INSERT INTO knowledge_review_queue (new_text, existing_node_id, existing_text,"
                " conflict_type, similarity, created_at) "
                "VALUES (%s, %s, %s, %s, %s, NOW()) RETURNING id",
                (new_text, existing_node_id, existing_text, conflict_type, similarity),
            )
            row = cursor.fetchone()
            self._inner.conn.commit()
            return row[0]
        except Exception:
            self._inner.conn.rollback()
            raise

    def log_use(
        self,
        session_id: str,
        node_ids: list[int],
        query: str = "",
    ) -> None:
        self._inner.log_use(session_id, node_ids, query)

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> "PluginDatabaseAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ========== 插件专用方法 ==========

    def search_subjects_by_keywords(
        self,
        keywords: list[str],
    ) -> list[dict[str, Any]]:
        """通过关键词模糊匹配科目名称。

        使用 SQL ILIKE 做模糊匹配，返回匹配的科目节点。
        按路径深度降序排列（最细粒度的优先）。

        Args:
            keywords: 关键词列表

        Returns:
            匹配的科目列表，每项含 {id, name, node_type, parent_id,
            k_vector, local_offset, depth}
        """
        if not keywords:
            return []

        cursor = self._inner.cursor
        # 构建 ILIKE 条件（OR 连接），转义通配符特殊字符
        params = [f"%{_escape_ilike(kw)}%" for kw in keywords]
        conditions = " OR ".join(
            "kt.name ILIKE %s ESCAPE '\\'" for _ in keywords
        )
        query = f"""
            WITH RECURSIVE tree_depth AS (
                SELECT id, parent_id, 0 AS depth
                FROM knowledge_tree
                WHERE parent_id IS NULL
                UNION ALL
                SELECT kt.id, kt.parent_id, td.depth + 1
                FROM knowledge_tree kt
                JOIN tree_depth td ON kt.parent_id = td.id
            )
            SELECT kt.id, kt.name, kt.node_type, kt.parent_id,
                   kt.k_vector, kt.local_offset, td.depth
            FROM knowledge_tree kt
            JOIN tree_depth td ON kt.id = td.id
            WHERE ({conditions})
              AND kt.node_type IN ('domain', 'subject')
            ORDER BY td.depth DESC
            LIMIT 10
        """
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "node_type": r[2],
                "parent_id": r[3],
                "k_vector": _parse_k_vector(r[4]),
                "local_offset": _parse_k_vector(r[5]),
                "depth": r[6],
            }
            for r in rows
        ]

    def get_domain_nodes(self) -> list[dict[str, Any]]:
        """获取所有顶层领域节点。

        Returns:
            领域节点列表，每项含 {id, name, k_vector}。
            按 display_order 排序。
        """
        cursor = self._inner.cursor
        cursor.execute(
            "SELECT id, name, k_vector FROM knowledge_tree "
            "WHERE parent_id IS NULL AND node_type = 'subject' "
            "ORDER BY display_order"
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "k_vector": _parse_k_vector(r[2]),
            }
            for r in rows
        ]

    def get_child_nodes(self, parent_id: int) -> list[dict[str, Any]]:
        """递归获取指定节点下所有 knowledge_point 类型的子节点。

        使用 WITH RECURSIVE CTE 遍历所有层级，适配 3 层树结构：
        domain → domain/root(subject) → knowledge_point。
        左连接 knowledge_point_texts 获取知识点原文。

        Args:
            parent_id: 父节点 ID

        Returns:
            knowledge_point 节点列表，每项含 {id, name, node_type, parent_id,
            k_vector, local_offset, text}
        """
        cursor = self._inner.cursor
        cursor.execute(
            """
            WITH RECURSIVE descendants AS (
                SELECT id, name, node_type, parent_id, k_vector, local_offset, display_order
                FROM knowledge_tree WHERE parent_id = %s
                UNION ALL
                SELECT kt.id, kt.name, kt.node_type, kt.parent_id,
                       kt.k_vector, kt.local_offset, kt.display_order
                FROM knowledge_tree kt
                JOIN descendants d ON kt.parent_id = d.id
            )
            SELECT d.id, d.name, d.node_type, d.parent_id,
                   d.k_vector, d.local_offset, kpt.text
            FROM descendants d
            LEFT JOIN knowledge_point_texts kpt
                   ON kpt.tree_node_id = d.id
            WHERE d.node_type = 'knowledge_point'
            ORDER BY d.display_order
            """,
            (parent_id,),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "node_type": r[2],
                "parent_id": r[3],
                "k_vector": _parse_k_vector(r[4]),
                "local_offset": _parse_k_vector(r[5]),
                "text": r[6] or "",
            }
            for r in rows
        ]

    def insert_entity_links(self, kp_id: int, entities: list[str]) -> None:
        """写入知识点实体关系到 kt_entity_links。

        Args:
            kp_id: knowledge_point 的 knowledge_tree.id
            entities: 实体名称列表
        """
        if not entities:
            return
        cursor = self._inner.cursor
        values = ", ".join("(%s, %s)" for _ in entities)
        params: list[Any] = []
        for entity in entities:
            params.extend([kp_id, entity])
        cursor.execute(
            f"INSERT INTO kt_entity_links (kp_id, entity) VALUES {values} "
            "ON CONFLICT DO NOTHING",
            params,
        )

    def get_placement_count(self, node_id: int) -> int:
        """获取节点的放置次数。

        Args:
            node_id: 节点 ID

        Returns:
            放置次数，节点不存在时返回 0
        """
        cursor = self._inner.cursor
        cursor.execute(
            "SELECT placement_count FROM knowledge_tree WHERE id = %s",
            (node_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else 0
