"""时态感知模块 — P3-9

知识点的 valid_from / valid_until 时间范围提取、存储、和查询辅助。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TemporalRange:
    """时间范围（ISO 格式字符串，方便存 JSON/DB）。"""
    valid_from: str | None = None
    valid_until: str | None = None

    @property
    def is_unbounded(self) -> bool:
        return self.valid_from is None and self.valid_until is None


_DATE_PATTERNS = [
    (re.compile(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})[日号]?"), "ymd"),
    (re.compile(r"(\d{4})[年\-](\d{1,2})月?"), "ym"),
    (re.compile(r"(\d{4})年"), "y"),
    (re.compile(r"v?(\d+\.\d+(?:\.\d+)?)"), "version"),
]

_KEYWORD_UNTIL = ["之前", "以前", "为止", "截至", "到...为止", "旧版", "老版本", "已废弃", "已过时"]
_KEYWORD_FROM = ["起", "开始", "之后", "以后", "从", "新版", "新版本", "现版本", "现行"]


def _iso_date(y: int, m: int | None = None, d: int | None = None) -> str:
    if m and d:
        return f"{y:04d}-{m:02d}-{d:02d}"
    if m:
        return f"{y:04d}-{m:02d}-01"
    return f"{y:04d}-01-01"


def extract_temporal_from_text(text: str) -> TemporalRange:
    """从知识点文本中启发式提取时间范围。

    仅作为 LLM 提取失败或关闭时的 fallback；不保证精确。
    """
    if not text:
        return TemporalRange()

    rng = TemporalRange()
    lowered = text

    for pat, kind in _DATE_PATTERNS:
        m = pat.search(lowered)
        if not m:
            continue
        if kind == "ymd":
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            iso = _iso_date(y, mo, d)
        elif kind == "ym":
            y, mo = int(m.group(1)), int(m.group(2))
            iso = _iso_date(y, mo)
        elif kind == "y":
            y = int(m.group(1))
            iso = _iso_date(y)
        else:
            continue

        pos = m.start()
        before = text[:pos]
        after = text[m.end():]

        has_until_kw = any(kw in before for kw in _KEYWORD_UNTIL)
        has_from_kw = any(kw in after for kw in _KEYWORD_FROM)

        if has_until_kw:
            rng.valid_until = iso
        elif has_from_kw:
            rng.valid_from = iso
        else:
            rng.valid_from = iso
        break

    return rng


def parse_llm_temporal(data: dict[str, Any] | None) -> TemporalRange:
    """解析 LLM 返回的 temporal 字段。"""
    if not data:
        return TemporalRange()
    vf = data.get("valid_from") or data.get("from") or data.get("start")
    vu = data.get("valid_until") or data.get("until") or data.get("end")
    return TemporalRange(
        valid_from=str(vf) if vf else None,
        valid_until=str(vu) if vu else None,
    )


def ensure_temporal_columns(adapter) -> bool:
    """确保 knowledge_tree 表有 valid_from / valid_until 列（幂等）。

    Args:
        adapter: DatabaseAdapter 实例

    Returns:
        True 表示列已存在或新建成功
    """
    try:
        adapter.cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'knowledge_tree' AND column_name IN ('valid_from', 'valid_until')
            """
        )
        existing = {r[0] for r in adapter.cursor.fetchall()}
    except Exception as e:
        logger.warning("时态列存在性查询失败: %s", e)
        return False

    missing = {"valid_from", "valid_until"} - existing
    if not missing:
        return True

    try:
        for col in sorted(missing):
            adapter.cursor.execute(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'knowledge_tree' AND column_name = '{col}'
                    ) THEN
                        ALTER TABLE knowledge_tree ADD COLUMN {col} DATE;
                    END IF;
                END $$;
                """
            )
        adapter.conn.commit()
        return True
    except Exception as e:
        logger.warning("添加时态列失败: %s", e)
        adapter.conn.rollback()
        return False


def update_node_temporal(adapter, node_id: int, temporal: TemporalRange) -> bool:
    """更新单个节点的 valid_from / valid_until。

    只更新非 None 的字段，避免意外清空已有值。

    Args:
        adapter: DatabaseAdapter 实例
        node_id: 节点 ID
        temporal: TemporalRange 对象

    Returns:
        是否成功
    """
    try:
        vf = temporal.valid_from
        vu = temporal.valid_until

        if vf is not None and vu is not None:
            adapter.cursor.execute(
                """
                UPDATE knowledge_tree
                SET valid_from = %s, valid_until = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (vf, vu, node_id),
            )
        elif vf is not None:
            adapter.cursor.execute(
                """
                UPDATE knowledge_tree
                SET valid_from = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (vf, node_id),
            )
        elif vu is not None:
            adapter.cursor.execute(
                """
                UPDATE knowledge_tree
                SET valid_until = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (vu, node_id),
            )
        else:
            return True

        adapter.conn.commit()
        return True
    except Exception as e:
        logger.warning("更新节点时态信息失败 (id=%d): %s", node_id, e)
        adapter.conn.rollback()
        return False
