"""SQLite session 数据库适配器 — FTS 查询原始对话片段。"""

import logging
import re
import sqlite3
from pathlib import Path

from memory_cleanup.config import AppConfig, CONFIG
from memory_cleanup.core.utils import KEYWORD_PATTERN

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "the", "this", "that", "with", "from", "were", "when", "what", "which",
    "have", "been", "after", "before", "about", "into", "over", "than", "also",
    "very", "just", "more", "some", "such", "not", "its", "was", "are", "can",
    "for", "and", "but", "has", "had",
}

# FTS 查询使用的关键词数量上限（P2-MC-032）
_FTS_KEYWORD_LIMIT = 3


class SessionDB:
    """Hermes session 数据库 FTS 查询适配器。

    数据库不存在时优雅降级，返回空结果。
    """

    def __init__(self, config: AppConfig = CONFIG) -> None:
        self._db_path = Path(config.session_db_path)

    def search(self, text: str) -> dict[str, object]:
        """从 state.db FTS 检索与 text 最相关的对话片段。

        返回:
            {"found": True, "title": ..., "snippet": ..., "time": ..., "confidence": float}
            或 {"found": False, "confidence": 0.0}
        """
        if not self._db_path.exists():
            return {"found": False, "confidence": 0.0}

        kw = KEYWORD_PATTERN.findall(text)
        kw = [w for w in kw if w.lower() not in _STOP_WORDS]
        if not kw:
            return {"found": False, "confidence": 0.0}

        # 先 AND 精准匹配，搜不到则 OR 降级（应对关键词不全匹配的情况）
        queries = [" AND ".join(kw[:_FTS_KEYWORD_LIMIT])]
        if len(kw) > 1:
            queries.append(" OR ".join(kw[:_FTS_KEYWORD_LIMIT]))
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                c = conn.cursor()
                for q in queries:
                    c.execute(
                        """SELECT s.title, substr(m.content,1,400), datetime(m.timestamp,'unixepoch'),
                                  m.timestamp
                        FROM messages_fts f JOIN messages m ON f.rowid = m.id
                        LEFT JOIN sessions s ON m.session_id = s.id
                        WHERE messages_fts MATCH ? ORDER BY m.timestamp DESC LIMIT 1""",
                        (q,),
                    )
                    row = c.fetchone()
                    if row:
                        snippet = row[1]
                        # 计算关键词重叠度作为 confidence
                        snippet_lower = snippet.lower()
                        hits = sum(1 for w in kw if w.lower() in snippet_lower)
                        confidence = hits / len(kw) if kw else 0.0
                        return {
                            "found": True,
                            "title": row[0] or "",
                            "snippet": snippet,
                            "time": row[2],
                            "timestamp": int(row[3]) if row[3] else 0,
                            "confidence": round(confidence, 2),
                        }
                return {"found": False, "confidence": 0.0}
            finally:
                conn.close()
        except Exception as e:
            logger.debug("session_db search error: %s", e)
            return {"found": False, "confidence": 0.0}
