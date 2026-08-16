"""批量回填 kt_entity_links — 遍历所有 kp_id 不在 kt_entity_links 的叶子节点，
调用 LLM 从知识点原文中提取命名实体后写入 kt_entity_links。

用法:
    python -m scripts.backfill_entities [--dry-run] [--batch-size 50] [--resume]

安全:
    - 默认 --dry-run 模式，仅统计不写入
    - 幂等：WHERE NOT EXISTS 保证已回填节点不重复处理
    - 每批独立 INSERT ON CONFLICT DO NOTHING，部分失败可重跑
    - --resume 跳过已回填节点（从上次中断处继续）

依赖:
    - PG 连接（KT_DB_URL 环境变量）
    - LiteLLM 网关（http://127.0.0.1:4142/v1, LITELLM_MASTER_KEY 环境变量）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

import psycopg2

logger = logging.getLogger(__name__)

# ========== 默认值 ==========

DEFAULT_BATCH_SIZE = 50
LLM_API_URL = "http://127.0.0.1:4142/v1/chat/completions"
LLM_MODEL = "s-deepseek-v4-flash"
LLM_MAX_TOKENS = 100
LLM_TEMPERATURE = 0.1
LLM_RETRIES = 3
LLM_TIMEOUT = 60

# ========== LLM 调用 ==========

_EXTRACT_SYSTEM_PROMPT = (
    "You are an entity extraction assistant. "
    "Extract only named entities (noun phrases, key concepts, technical terms) from the given text. "
    "Return ONLY a JSON array of strings. No other text."
)

_EXTRACT_USER_TEMPLATE = (
    'Extract 2-8 named entities (noun phrases, key concepts, technical terms) '
    'from this knowledge point text. Return ONLY a JSON array of strings, like: '
    '["entity1", "entity2"]. No other text.\n\n'
    'Text: {text}'
)


def _call_llm_extract(text: str, api_key: str) -> list[str]:
    """调用 LiteLLM 从知识点文本中提取实体。

    Returns:
        实体列表；失败返回空列表。
    """
    import requests

    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": _EXTRACT_USER_TEMPLATE.format(text=text)},
    ]

    for attempt in range(LLM_RETRIES):
        try:
            # s-deepseek*/agnes 必须启用 thinking 且 max_tokens>8192（业务硬约束）
            _bf_think = LLM_MODEL.startswith(("s-deepseek", "agnes"))
            _bf_mt = 16384 if _bf_think else LLM_MAX_TOKENS
            _bf_thinking = {"type": "enabled"} if _bf_think else {"type": "disabled"}
            resp = requests.post(
                LLM_API_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Connection": "close",
                },
                json={
                    "model": LLM_MODEL,
                    "messages": messages,
                    "temperature": LLM_TEMPERATURE,
                    "max_tokens": _bf_mt,
                    "extra_body": {"thinking": _bf_thinking},
                },
                timeout=(10, LLM_TIMEOUT),
            )
            resp.raise_for_status()
            content = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                logger.warning("LLM 返回空内容，text=%.60s...", text)
                return []

            # 去掉 markdown fence
            if content.startswith("```"):
                lines = content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines).strip()

            # 尝试解析 JSON
            parsed = json.loads(content)
            if isinstance(parsed, list):
                # 过滤空字符串
                return [str(e) for e in parsed if isinstance(e, str) and e.strip()]
            # 可能包裹在 {"entities": [...]} 里
            if isinstance(parsed, dict):
                for key in ("entities", "entity", "result", "data"):
                    val = parsed.get(key)
                    if isinstance(val, list):
                        return [str(e) for e in val if isinstance(e, str) and e.strip()]
            logger.warning("LLM 返回非期望格式: %.100s", content)
            return []

        except Exception as exc:
            logger.warning("LLM 调用第 %d/%d 次失败: %s", attempt + 1, LLM_RETRIES, exc)
            if attempt < LLM_RETRIES - 1:
                time.sleep(2 ** attempt)

    logger.error("LLM 调用 %d 次均失败，text=%.60s...", LLM_RETRIES, text)
    return []


# ========== 数据库操作 ==========


def _get_db_url() -> str:
    """获取 PG 连接字符串（优先级：KT_DB_URL 环境变量 > PGURL 环境变量）。"""
    db_url = os.environ.get("KT_DB_URL") or os.environ.get("PGURL") or ""
    if not db_url:
        logger.error(
            "未设置 KT_DB_URL 或 PGURL 环境变量。示例:\n"
            "  export KT_DB_URL='postgresql://user:pass@127.0.0.1:5434/hindsight'"
        )
        sys.exit(1)
    return db_url


def _get_kps_without_entities(cursor, batch_size: int) -> list[tuple[int, str]]:
    """查询还没有实体记录的 KPs，返回 [(kp_id, text), ...]。

    Args:
        cursor: PG cursor
        batch_size: 每批最大数量

    Returns:
        [(kp_id, text), ...]
    """
    cursor.execute(
        "SELECT kt.id, kpt.text "
        "FROM knowledge_tree kt "
        "JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id "
        "WHERE kt.node_type = 'knowledge_point' "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM kt_entity_links kel WHERE kel.kp_id = kt.id"
        "  ) "
        "ORDER BY kt.id "
        "LIMIT %s",
        (batch_size,),
    )
    return [(r[0], r[1]) for r in cursor.fetchall()]


def _count_kps_without_entities(cursor) -> int:
    """统计还没有实体记录的 KPs 总数。"""
    cursor.execute(
        "SELECT COUNT(*) FROM knowledge_tree kt "
        "WHERE kt.node_type = 'knowledge_point' "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM kt_entity_links kel WHERE kel.kp_id = kt.id"
        "  )"
    )
    return int(cursor.fetchone()[0])


def _insert_entity(cursor, kp_id: int, entity: str) -> None:
    """插入一条实体记录（ON CONFLICT 防重）。"""
    cursor.execute(
        "INSERT INTO kt_entity_links (kp_id, entity) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (kp_id, entity),
    )


# ========== 主逻辑 ==========


def backfill_entities(
    *,
    dry_run: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    resume: bool = False,
) -> dict[str, int]:
    """批量回填实体到 kt_entity_links。

    Args:
        dry_run: True 时只统计不写入
        batch_size: 每批处理的知识点数量
        resume: True 时从上次中断处继续

    Returns:
        {"total": 待处理数, "filled": 成功回填数, "errors": 失败数}
    """
    stats: dict[str, int] = {"total": 0, "filled": 0, "errors": 0}

    api_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not api_key:
        logger.error("未设置 LITELLM_MASTER_KEY 环境变量")
        return stats

    db_url = _get_db_url()
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cursor = conn.cursor()

    try:
        # 1. 统计总数
        total = _count_kps_without_entities(cursor)
        stats["total"] = total

        if total == 0:
            logger.info("没有需要回填实体的知识点")
            return stats

        logger.info("需要回填实体的知识点数: %d", total)

        if dry_run:
            logger.info("[dry-run] 预览: %d 个知识点待回填实体", total)
            return stats

        # 2. 分页处理（每批 batch_size 条）
        processed = 0
        batch_num = 0

        # 显式事务：每批 commit 一次，代替 autocommit
        conn.autocommit = False

        while True:
            batch = _get_kps_without_entities(cursor, batch_size)
            if not batch:
                break

            batch_num += 1
            total_batches = max(1, (total + batch_size - 1) // batch_size)

            for kp_id, text in batch:
                try:
                    entities = _call_llm_extract(text, api_key)
                    if entities:
                        for entity in entities:
                            _insert_entity(cursor, kp_id, entity)
                        stats["filled"] += 1
                        logger.debug(
                            "KP %d: 提取到 %d 个实体: %s",
                            kp_id, len(entities), entities,
                        )
                    else:
                        stats["errors"] += 1
                        logger.warning("KP %d: 未提取到实体", kp_id)
                except Exception as exc:
                    stats["errors"] += 1
                    logger.warning("KP %d 处理失败: %s", kp_id, exc)

                processed += 1

            # 每批 commit 一次（显式事务替代 autocommit）
            try:
                conn.commit()
            except Exception as exc:
                logger.warning("批次 %d commit 失败: %s", batch_num, exc)
                conn.rollback()

            # 进度日志
            if batch_num % 5 == 0 or batch_num == total_batches or processed == total:
                logger.info(
                    "进度: %d/%d (filled=%d, errors=%d, batch=%d/%d)",
                    min(processed, total),
                    total,
                    stats["filled"],
                    stats["errors"],
                    batch_num,
                    total_batches,
                )

        logger.info(
            "回填完成: total=%d, filled=%d, errors=%d",
            stats["total"],
            stats["filled"],
            stats["errors"],
        )

    except Exception as exc:
        logger.error("回填过程异常: %s", exc)
        raise
    finally:
        cursor.close()
        conn.close()

    return stats


# ========== CLI 入口 ==========


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量回填 kt_entity_links — 从知识点原文提取命名实体",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="仅预览，不实际写入（默认关闭）。",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批次处理的知识点数（默认: {DEFAULT_BATCH_SIZE}）。",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="跳过已回填节点，从上次中断处继续。",
    )
    return parser.parse_args(argv)


def main() -> None:
    """CLI 入口。"""
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info(
        "启动回填: dry_run=%s, batch_size=%d, resume=%s",
        args.dry_run, args.batch_size, args.resume,
    )

    stats = backfill_entities(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        resume=args.resume,
    )

    sys.exit(0 if stats.get("errors", 0) == 0 else 1)


if __name__ == "__main__":
    main()
