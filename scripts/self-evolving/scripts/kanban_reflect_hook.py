#!/usr/bin/env python3
"""Kanban 反思集成脚本 — S-P1-2：失败检测 + 反思分析 + 重试注入

功能：
1. 从 trace.log 中检测 Kanban 任务失败事件
2. 调用反思回路分析失败原因
3. 输出含反思信息的重试 prompt 注入文本

用法：
    python scripts/kanban_reflect_hook.py --task-id <id> --trace trace.log
    python scripts/kanban_reflect_hook.py --task-id <id> --trace trace.log --inject-retry
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# 确保可以从 src 目录导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kanban_reflection.config import KanbanReflectionConfig
from kanban_reflection.core.reflector import (
    reflect_on_failure,
    read_trace_lines,
)

logger = logging.getLogger(__name__)

# 重试 prompt 模版
RETRY_INJECT_TEMPLATE = """{inject_prefix}

上轮任务执行失败，反思分析如下：
- 失败原因：{failure_reason}
- 错误类型：{failure_type}
- 优化建议：{suggestion}

请在重试时参考上述分析，避免重复出现相同问题。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kanban 反思集成 — 分析失败任务并输出重试注入文本",
    )
    parser.add_argument(
        "--task-id", "-t", required=True,
        help="Kanban 任务 ID",
    )
    parser.add_argument(
        "--trace", "-f", required=True,
        help="trace.log 文件路径",
    )
    parser.add_argument(
        "--goal", "-g", default="",
        help="任务目标描述（可选）",
    )
    parser.add_argument(
        "--max-lines", "-n", type=int, default=5,
        help="读取最近 N 轮消息（默认 5）",
    )
    parser.add_argument(
        "--inject-retry", action="store_true",
        help="输出可直接注入到重试 prompt 的文本",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅预览 trace 内容，不调用 LLM",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="详细输出",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s | %(message)s")

    config = KanbanReflectionConfig.from_env()

    # 1. 读取失败轨迹
    trace_lines = read_trace_lines(
        args.trace, args.task_id, max_lines=args.max_lines,
    )

    if not trace_lines:
        logger.warning("未找到任务 %s 的 trace 记录", args.task_id)
        print(json.dumps({"error": "trace_not_found", "task_id": args.task_id}))
        sys.exit(1)

    logger.info("读取到 %d 条 trace 记录", len(trace_lines))

    if args.dry_run:
        print(json.dumps({
            "task_id": args.task_id,
            "trace_count": len(trace_lines),
            "trace_preview": trace_lines,
        }, ensure_ascii=False, indent=2))
        return

    # 2. 执行反思分析
    logger.info("正在分析任务 %s 的失败原因...", args.task_id)
    result = reflect_on_failure(
        task_id=args.task_id,
        task_goal=args.goal,
        trace_lines=trace_lines,
        config=config,
    )

    # 3. 输出
    if args.inject_retry:
        # 输出可直接注入到重试 prompt 的文本
        inject_text = RETRY_INJECT_TEMPLATE.format(
            inject_prefix=config.retry_inject_prefix,
            failure_reason=result.failure_reason,
            failure_type=result.failure_type,
            suggestion=result.suggestion,
        )
        print(inject_text)
    else:
        # 输出 JSON 格式反思结果
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
