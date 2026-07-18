"""数据库适配器 — 封装所有 PostgreSQL 操作"""

from collections import defaultdict
from typing import Any

import json
import numpy as np
import psycopg2
import psycopg2.extras

DEFAULT_BANK_ID = "hermes"
_DEFAULT_LINK_WEIGHT = 0.5


class DatabaseAdapter:
    """PostgreSQL 数据库操作适配器"""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self._conn: Any = None
        self._has_quality_score_column: bool | None = None

    @property
    def conn(self) -> Any:
        """获取或创建数据库连接"""
        if self._conn is None:
            self._conn = psycopg2.connect(self.db_url)
        return self._conn

    def fetch_memory_units(self, sample_size: int, bank_id: str | None = None) -> list[tuple]:
        """获取记忆单元数据

        Args:
            sample_size: 采样数，<=0 表示不限量全量获取。
            bank_id: 按银行ID过滤，None 时使用默认 bank_id。
        """
        bid = bank_id or self.bank_id
        with self.conn.cursor() as cur:
            sql = """
                SELECT id, bank_id, text, embedding
                FROM memory_units
                WHERE embedding IS NOT NULL AND text IS NOT NULL
                  AND bank_id = %s
                ORDER BY created_at DESC
            """
            if sample_size > 0:
                sql += " LIMIT %s"
                cur.execute(sql, (bid, sample_size))
            else:
                cur.execute(sql, (bid,))
            return cur.fetchall()

    def fetch_unit_entities(self, unit_ids: list[str]) -> list[tuple]:
        """获取单元-实体关联"""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT unit_id, entity_id FROM unit_entities
                WHERE unit_id::text = ANY(%s)
                """,
                (unit_ids,),
            )
            return cur.fetchall()

    def fetch_unit_text(self, unit_id: str) -> str | None:
        """获取单个记忆单元的文本"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT text FROM memory_units WHERE id = %s",
                (unit_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def fetch_unit_texts_batch(self, unit_ids: list[str]) -> dict[str, str]:
        """批量获取记忆单元文本

        Returns:
            {unit_id: text, ...}，不存在的 unit_id 不包含
        """
        if not unit_ids:
            return {}
        result: dict[str, str] = {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, text FROM memory_units WHERE id::text = ANY(%s)",
                (unit_ids,),
            )
            for uid, text in cur.fetchall():
                result[uid] = text
        return result

    def fetch_existing_entities(self) -> dict[str, list[str]]:
        """获取已有实体及其成员 unit_id 列表。

        Returns:
            {entity_id: [unit_id, ...], ...}
        """
        result: dict[str, list[str]] = {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT entity_id, unit_id::text FROM unit_entities ORDER BY entity_id"
            )
            for entity_id, unit_id in cur.fetchall():
                result.setdefault(entity_id, []).append(unit_id)
        return result

    def fetch_embeddings_by_ids(self, unit_ids: list[str]) -> dict[str, np.ndarray]:
        """批量获取 memory_units 的 embedding

        Returns:
            {unit_id: np.ndarray, ...}，无 embedding 的不包含
        """
        result: dict[str, np.ndarray] = {}
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, embedding FROM memory_units WHERE id::text = ANY(%s) AND embedding IS NOT NULL",
                (unit_ids,),
            )
            for uid, emb_str in cur.fetchall():
                try:
                    result[uid] = np.array(json.loads(emb_str), dtype=np.float32)
                except (ValueError, json.JSONDecodeError):
                    pass
        return result

    def fetch_all_links(self, bank_id: str = DEFAULT_BANK_ID) -> set[tuple[str, str, str]]:
        """获取已有因果链的去重集合，用于跨运行跳过重复检测。

        Returns:
            {(from_id, to_id, link_type), ...}
        """
        result: set[tuple[str, str, str]] = set()
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT from_unit_id::text, to_unit_id::text, link_type FROM memory_links WHERE bank_id = %s",
                (bank_id,),
            )
            for row in cur.fetchall():
                result.add((row[0], row[1], row[2]))
        return result

    def cleanup_old_clusters(
        self,
        *,
        force: bool = False,
        bank_id: str = DEFAULT_BANK_ID,
    ) -> None:
        """保留所有聚类数据（实体、关联、因果链均不清除）。

        实体和记忆关联是稳定知识锚点；因果链每次增强而非重建。
        """
        print(f"🧹 Cleanup: 保留全部聚类数据 (bank={bank_id})")

    def apply_to_db(
        self,
        entity_write_plan: list[dict[str, Any]],
        unit_entity_write_plan: list[dict[str, Any]],
        memory_link_plan: list[dict[str, Any]],
        enriched_texts: dict[str, list[str]],
        *,
        bank_id: str = DEFAULT_BANK_ID,
        prefetched_texts: dict[str, str] | None = None,
    ) -> None:
        """将聚类结果写入数据库（实体、unit 关联、因果链、富化文本）

        优化设计：所有数据库操作合并为 4 轮往返（而非 N+6 轮）。
        """
        cur = self.conn.cursor()

        # ============================================================
        # 第 1 轮：批量写入 entities → 一次性收集 UUID 映射
        # ============================================================
        # Bug fix: 按 canonical_name 去重，避免同一批次中多条相同 canonical_name
        # 触发 PostgreSQL "ON CONFLICT DO UPDATE command cannot affect row a second time"
        canonical_to_eids: dict[str, list[str]] = {}
        for e in entity_write_plan:
            canonical_to_eids.setdefault(e["canonical_name"], []).append(e["entity_id"])

        # 取每个 canonical_name 的最大 member_count，保留最完整的信息
        canonical_member_count: dict[str, int] = {}
        for e in entity_write_plan:
            name = e["canonical_name"]
            prev = canonical_member_count.get(name, 0)
            canonical_member_count[name] = max(prev, e.get("member_count", 0))

        entity_params = [
            (
                name,
                json.dumps({
                    "group_id": canonical_to_eids[name][0],
                    "group_size": canonical_member_count.get(name, 0),
                    "type": "clustering_group",
                }),
                bank_id,
            )
            for name in canonical_to_eids
        ]

        entity_uuid_map: dict[str, str] = {}
        if entity_params:
            try:
                result = psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO entities (canonical_name, metadata, bank_id)
                    VALUES %s
                    ON CONFLICT (bank_id, LOWER(canonical_name)) DO UPDATE
                    SET metadata = EXCLUDED.metadata, last_seen = now(), mention_count = entities.mention_count + 1
                    RETURNING id::text
                    """,
                    entity_params,
                    template="(%s, %s, %s)",
                    fetch=True,
                )
                # 映射所有 entity_id（含去重前的）到同一个 UUID
                for (entity_uuid,), name in zip(result, canonical_to_eids):
                    for eid in canonical_to_eids[name]:
                        entity_uuid_map[eid] = entity_uuid
            except Exception as ex:
                raise RuntimeError(
                    f"entities 批量写入失败，apply 终止。原因: {ex}\n"
                    "  请修复根因后重试，不要降级写入，避免错误实体进入数据库。"
                ) from ex

        print(f"   写入 entities: {len(entity_uuid_map)} 条（去重后 {len(entity_params)} 条）")

        # 预建 entity_id → canonical_name 查询表，避免 O(n*m) 线性查找
        entity_name_map: dict[str, str] = {
            e["entity_id"]: e["canonical_name"]
            for e in entity_write_plan
            if e.get("canonical_name")
        }

        # ============================================================
        # 第 2 轮：批量写入 unit_entities + 收集实体文本更新
        # ============================================================
        ue_params: list[tuple] = []
        entity_text_updates: dict[str, list[str]] = defaultdict(list)
        for ue in unit_entity_write_plan:
            entity_uuid = entity_uuid_map.get(ue["entity_id"])
            if entity_uuid is None:
                # 第 1 轮匹配的 entity 可能已是 UUID（已有实体）
                if "-" in ue["entity_id"] and len(ue["entity_id"]) == 36:
                    entity_uuid = ue["entity_id"]
                else:
                    print(f"     [WARN] entity_id '{ue['entity_id']}' 未找到对应 UUID")
                    continue
            ue_params.append((ue["unit_id"], entity_uuid))
            # 收集 canonical_name
            name = entity_name_map.get(ue["entity_id"])
            if name:
                entity_text_updates[ue["unit_id"]].append(name)

        ue_written = 0
        if ue_params:
            try:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO unit_entities (unit_id, entity_id) VALUES %s ON CONFLICT DO NOTHING",
                    ue_params,
                    template="(%s, %s::uuid)",
                )
                ue_written = cur.rowcount
            except Exception as ex:
                # 失败时整体回滚（与第 1 轮 entities 写入保持原子性）
                self.conn.rollback()
                raise RuntimeError(
                    f"unit_entities 批量写入失败，apply 整体回滚。原因: {ex}\n"
                    "  请修复根因后重试，避免 entities 写入但 unit_entities 漏写的脏数据。"
                ) from ex

        print(f"   写入 unit_entities: {ue_written} 条")

        # ============================================================
        # 第 3 轮：合并文本 UPDATE（entity 回写 + 富化文本，一次预取一次写入）
        # ============================================================
        text_updated = 0
        all_text_unit_ids = set(entity_text_updates.keys()) | set(enriched_texts.keys())
        if all_text_unit_ids:
            # 只预取一次文本
            if prefetched_texts is not None:
                current_texts = {
                    uid: prefetched_texts[uid]
                    for uid in all_text_unit_ids
                    if uid in prefetched_texts
                }
                missing = [uid for uid in all_text_unit_ids if uid not in prefetched_texts]
                if missing:
                    current_texts.update(self.fetch_unit_texts_batch(missing))
            else:
                current_texts = self.fetch_unit_texts_batch(list(all_text_unit_ids))

            # 合并构建 UPDATE 参数：保持与原逻辑一致
            #   1) entity 文本追加（仅在无富化文本时生效）
            #   2) 富化文本替换（优先级高，覆盖 entity 追加）
            text_update_params: list[tuple] = []
            for unit_id in all_text_unit_ids:
                current = current_texts.get(unit_id, "")
                new_text = current

                # 实体 canonical_name 追加
                if unit_id in entity_text_updates:
                    names = entity_text_updates[unit_id]
                    append_text = "\n[聚类实体: " + ", ".join(names) + "]"
                    if current and not current.rstrip().endswith(append_text.rstrip()):
                        new_text = current + append_text

                # 富化文本追加（保留实体标签，不再覆盖）
                if unit_id in enriched_texts:
                    # Bug fix [P2]: 相同内容只保留一个，防止多链路重复堆积
                    unique_texts = list(dict.fromkeys(enriched_texts[unit_id]))
                    combined = "\n" + "\n".join(unique_texts)
                    if combined not in new_text:
                        new_text = new_text + combined

                if new_text != current:
                    text_update_params.append((new_text, unit_id))

            if text_update_params:
                try:
                    psycopg2.extras.execute_values(
                        cur,
                        "UPDATE memory_units AS mu SET text = v.text FROM (VALUES %s) AS v(text, id) WHERE mu.id = v.id::uuid",
                        text_update_params,
                        template="(%s, %s)",
                    )
                    text_updated = cur.rowcount
                except Exception as ex:
                    # 失败时整体回滚（与第 1 轮 entities 写入保持原子性）
                    self.conn.rollback()
                    raise RuntimeError(
                        f"批量回写文本失败，apply 整体回滚。原因: {ex}\n"
                        "  请修复根因后重试，避免 entities/unit_entities 已写但文本未回写的不一致状态。"
                    ) from ex

        print(f"   回写实体文本 + 富化文本: {text_updated} 条")

        # ============================================================
        # 第 4 轮：批量写入 memory_links
        # ============================================================
        link_params: list[tuple] = []
        for link in memory_link_plan:
            entity_str = link.get("entity_id", "")
            e_uuid = (
                entity_uuid_map.get(entity_str)
                if entity_str in entity_uuid_map
                else None
            )
            link_params.append((
                link["from_id"],
                link["to_id"],
                link.get("link_type", "causes"),
                min(1.0, float(link.get("weight", _DEFAULT_LINK_WEIGHT))),
                e_uuid,
                bank_id,
            ))

        links_written = 0
        if link_params:
            try:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO memory_links (from_unit_id, to_unit_id, link_type, weight, entity_id, bank_id)
                    VALUES %s
                    ON CONFLICT (from_unit_id, to_unit_id, link_type, COALESCE(entity_id, '00000000-0000-0000-0000-000000000000'::uuid))
                    DO UPDATE SET
                      weight = GREATEST(EXCLUDED.weight, memory_links.weight),
                      entity_id = EXCLUDED.entity_id
                    """,
                    link_params,
                    template="(%s, %s, %s, %s, %s::uuid, %s)",
                )
                links_written = cur.rowcount
            except Exception as ex:
                # 失败时整体回滚（与第 1 轮 entities 写入保持原子性）
                self.conn.rollback()
                raise RuntimeError(
                    f"memory_links 批量写入失败，apply 整体回滚。原因: {ex}\n"
                    "  请修复根因后重试，避免部分写入导致不一致状态。"
                ) from ex

        print(f"   写入 memory_links: {links_written} 条")

        cur.close()
        self.conn.commit()

    @property
    def bank_id(self) -> str:
        """默认 bank_id"""
        return DEFAULT_BANK_ID

    def update_embedding(self, unit_id: str, embedding: list[float]) -> None:
        """更新单个记忆单元的 embedding"""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE memory_units SET embedding = %s WHERE id = %s",
                (embedding, unit_id),
            )
        self.conn.commit()

    def batch_update_embeddings(self, updates: list[tuple[list[float], str]]) -> int:
        """批量更新 memory_units 的 embedding，一次 execute_values 完成"""
        if not updates:
            return 0
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                "UPDATE memory_units AS mu SET embedding = v.emb FROM (VALUES %s) AS v(emb, id) WHERE mu.id = v.id::uuid",
                updates,
                template="(%s::vector, %s)",
            )
            updated = cur.rowcount
        self.conn.commit()
        return updated

    def _check_quality_score_column(self) -> bool:
        """检查 memory_units 表是否存在 quality_score 字段（结果缓存）。"""
        if self._has_quality_score_column is not None:
            return self._has_quality_score_column
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='memory_units' AND column_name='quality_score'
                """,
            )
            self._has_quality_score_column = cur.fetchone() is not None
        return self._has_quality_score_column

    def update_quality_score(
        self,
        memory_id: str,
        quality_score: float,
        quality_details: dict[str, float] | None = None,
    ) -> bool:
        """更新记忆单元的质量评分。

        若 memory_units 表中没有 quality_score 字段，则不写入，返回 False。
        仅当字段存在且更新成功时返回 True。

        Args:
            memory_id: 记忆单元 ID
            quality_score: 综合质量分数（0-1）
            quality_details: 各维度详细分数（可选）

        Returns:
            是否成功更新
        """
        try:
            if not self._check_quality_score_column():
                return False

            with self.conn.cursor() as cur:
                details_json = json.dumps(quality_details) if quality_details else None
                cur.execute(
                    "UPDATE memory_units SET quality_score = %s, quality_details = %s WHERE id = %s",
                    (quality_score, details_json, memory_id),
                )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            return False

    def batch_update_quality_scores(
        self,
        updates: list[tuple[str, float, dict[str, float] | None]],
    ) -> int:
        """批量更新记忆单元的质量评分。

        若 memory_units 表中没有 quality_score 字段，则不写入，返回 0。

        Args:
            updates: [(memory_id, quality_score, quality_details), ...]

        Returns:
            成功更新的条数
        """
        if not updates:
            return 0

        try:
            if not self._check_quality_score_column():
                return 0

            with self.conn.cursor() as cur:
                params = [
                    (uid, score, json.dumps(details) if details else None)
                    for uid, score, details in updates
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    UPDATE memory_units AS mu
                    SET quality_score = v.score, quality_details = v.details
                    FROM (VALUES %s) AS v(id, score, details)
                    WHERE mu.id = v.id::uuid
                    """,
                    params,
                    template="(%s, %s, %s)",
                )
                updated = cur.rowcount
            self.conn.commit()
            return updated
        except Exception:
            self.conn.rollback()
            return 0

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
