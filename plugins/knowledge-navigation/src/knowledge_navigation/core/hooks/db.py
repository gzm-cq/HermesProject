"""知识导航 Hook — 数据库连接管理。

拆分自 hooks.py 的 DB 相关代码：
- PG 连接 thread-local 缓存
- embedding 和因果链 boost 功能
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

# 顶层可选依赖
try:
    import requests as _requests  # noqa: N816
except ImportError:
    _requests = None  # type: ignore[assignment]

try:
    import psycopg2 as _psycopg2  # noqa: N816
except ImportError:
    _psycopg2 = None  # type: ignore[assignment]

from knowledge_navigation.config import CONFIG
from knowledge_navigation.core.env_loader import get_env

logger = logging.getLogger(__name__)

__all__ = [
    "_batch_embed",
    "_causal_boost",
    "_get_cached_conn",
    "_pg_conn_local",
    "_PG_CONN_TTL",
    "_psycopg2",
    "_requests",
]

# PG 连接缓存：thread-local 存储，每个线程拥有独立连接
# psycopg2 connection 不是线程安全的，多线程并发使用同一连接会导致竞态
# _causal_boost 使用，避免每次 recall 新建连接
_pg_conn_local = threading.local()
_PG_CONN_TTL = 300  # 5 分钟无使用自动关闭


def _get_cached_conn(db_url: str, pg_module) -> "psycopg2.extensions.connection | None":
    """从 thread-local 获取复用 PG 连接，超时未用的自动关闭。

    使用 threading.local() 确保每个线程拥有独立连接，
    避免 psycopg2 connection 被多线程并发使用的竞态问题。
    """
    now = time.time()

    # 获取当前线程的连接缓存
    if not hasattr(_pg_conn_local, "conn_map"):
        _pg_conn_local.conn_map = {}  # type: ignore[attr-defined]

    conn_map = _pg_conn_local.conn_map  # type: ignore[attr-defined]

    # 清理当前线程的过期连接
    stale = [k for k, (_, ts) in conn_map.items() if now - ts > _PG_CONN_TTL]
    for k in stale:
        conn_entry = conn_map.pop(k, None)
        if conn_entry:
            try:
                conn_entry[0].close()
            except Exception:
                logger.debug("PG 连接关闭失败（缓存清理路径）: %s", k)

    # 返回缓存连接或新建
    if db_url in conn_map:
        conn, _ = conn_map[db_url]
        try:
            # 健康检查
            with conn.cursor() as _cur:
                _cur.execute("SELECT 1")
            conn_map[db_url] = (conn, now)
            return conn
        except Exception:
            # 连接失效，清理后重新创建
            conn_entry = conn_map.pop(db_url, None)
            if conn_entry:
                try:
                    conn_entry[0].close()
                except Exception:
                    pass
    try:
        conn = pg_module.connect(db_url, connect_timeout=5)
        conn_map[db_url] = (conn, now)
        return conn
    except Exception:
        return None


def _batch_embed(texts: list[str]) -> list[list[float]] | None:
    """调用 SiliconFlow embedding API（bge-m3），供 cross_domain_dedup 使用。"""
    if _requests is None:
        logger.debug("_batch_embed: requests 模块不可用")
        return None
    api_key = get_env("SILICONFLOW_API_KEY", "")
    if not api_key:
        logger.debug("_batch_embed: SILICONFLOW_API_KEY 未设置")
        return None
    try:
        resp = _requests.post(
            "https://api.siliconflow.cn/v1/embeddings",
            json={"model": "BAAI/bge-m3", "input": texts},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("_batch_embed: HTTP %s — %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        return [d["embedding"] for d in data.get("data", [])]
    except Exception as e:
        logger.debug("_batch_embed 异常: %s", e)
        return None


def _causal_boost(
    raw_results: list[dict],
    rerank_map: dict[str, float],
    alpha: float = 0.15,
    cap: float = 1.3,
) -> None:
    """查询 memory_links 表，将被因果链引用的记忆 rerank_score 提升。

    只 boost 已在候选池中的记忆（不新增候选），
    确保因果链只微调排序，不引入无关结果。

    Args:
        raw_results: Hindsight recall 的原始结果
        rerank_map: {memory_id: rerank_score}，原地修改
        alpha: 提权系数，越大因果影响越强
        cap: 最大提权上限，防翻转排序
    """
    if _psycopg2 is None:
        return
    db_url = get_env("KT_DB_URL", "")
    if not db_url:
        return
    recalled_ids = [str(m.get("id", "")) for m in raw_results if m.get("id")]
    if not recalled_ids:
        return
    try:
        # 从连接缓存获取复用连接（thread-local）
        conn = _get_cached_conn(db_url, _psycopg2)
        if conn is None:
            return
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT from_unit_id::text, to_unit_id::text, link_type, weight "
                "FROM memory_links "
                "WHERE from_unit_id::text = ANY(%s) "
                "  AND to_unit_id::text = ANY(%s) "
                "  AND link_type IN ('causes', 'caused_by')",
                (recalled_ids, list(rerank_map.keys())),
            )
            for from_id, to_id, link_type, weight in cursor.fetchall():
                from_score = rerank_map.get(from_id, 0.0)
                if from_score <= 0 or to_id not in rerank_map:
                    continue
                boost = 1.0 + alpha * from_score * (weight or 0.5)
                current = rerank_map[to_id]
                rerank_map[to_id] = current * min(boost, cap)
        # 更新 thread-local 缓存时间戳
        if hasattr(_pg_conn_local, "conn_map"):
            _pg_conn_local.conn_map[db_url] = (conn, time.time())  # type: ignore[attr-defined]
    except Exception as e:
        logger.warning("causal_boost 失败: %s", e)