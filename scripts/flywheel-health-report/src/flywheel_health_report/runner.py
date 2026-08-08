"""runner.py — 飞轮报告内建的"前置 runner"（当前实际无外部任务需要跑）。

硬血缘分析（已确认）：
  - kn_judge_relevant_rate/avg_relevance/sample_count → report.py 内建 run_judge_within_window() 直接产出
  - skill_used_count → ~/.hermes/skills/.usage.json（report 扫 .usage.json）
  - 其余 8 个反馈键 → trace.log 扫描（report 内 analyze_* 模块直接算）
  - skillopt-nightly-run → Skill 优化自闭环，不写 KN_TOKEN_BUDGET_SKILL_RATIO 反馈字段
  - knowledge-navigation-baseline cron job = 重复跑 judge（和 report 内建重复），已在 jobs.json 中禁用

所以阶段 0 只做：写 runner-summary.json（记录本次报告的 KN judge "阶段 1 内部执行"），
供任务可靠性表合并展示（标记「本次已执行」而不是读旧 cron-state）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DEFAULT_HERMES_HOME


RUNNER_SUBPATH = Path("data") / "flywheel" / "runner-summary.json"


def _now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def build_runner_summary(home: Path, *, dry_run: bool) -> dict[str, Any]:
    """生成阶段 0 runner 摘要。

    返回结构：
      {
        "generated_at": ISO,
        "dry_run": bool,
        "stages": {
          "1_internal_kn_judge": {  # 阶段 1：report 内部将执行的 KN judge（声明式预登记）
            "tasks": [{
              "cron_name": "knowledge-navigation-baseline",
              "flywheel": "Router",
              "exec_location": "report_internal_run_judge_within_window",
              "status": "will_run_in_report_phase",
              "note": "由 report 内建 kn_judge.run_judge_within_window() 执行，避免外部脚本重复调用",
            }],
            "status": "will_run",
          }
        }
      }
    """
    return {
        "generated_at": _now_iso(),
        "dry_run": dry_run,
        "stages": {
            "1_internal_kn_judge": {
                "tasks": [
                    {
                        "cron_name": "knowledge-navigation-baseline",
                        "flywheel": "Router",
                        "exec_location": "report:run_judge_within_window",
                        "status": "will_run_in_report_phase",
                        "note": "report 内建 judge 替代原 collect_baseline.py --judge，避免 2 倍 LLM 消耗",
                    }
                ],
                "status": "will_run",
            }
        },
    }


def save_runner_summary(home: Path, summary: dict[str, Any]) -> Path:
    out = home / RUNNER_SUBPATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_runner_summary(home: Path) -> dict[str, Any]:
    path = home / RUNNER_SUBPATH
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_all(home: Path, *, dry_run: bool = False) -> int:
    """CLI 入口：阶段 0 只做 runner 摘要登记（0 耗时）。"""
    t0 = time.monotonic()
    summary = build_runner_summary(home, dry_run=dry_run)
    elapsed = round(time.monotonic() - t0, 3)
    summary["elapsed_seconds"] = elapsed
    if not dry_run:
        save_runner_summary(home, summary)
    print(f"[runner] 阶段 0 完成（无外部任务执行，登记阶段 1 内部将执行 KN judge）。elapsed={elapsed}s")
    print(f"[runner] runner-summary = {home / RUNNER_SUBPATH}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flywheel Runner: 阶段 0 登记 report 内部将执行的 KN judge，替代外部 cron job",
    )
    parser.add_argument(
        "--home",
        default=DEFAULT_HERMES_HOME,
        help=f"Hermes home path (default: {DEFAULT_HERMES_HOME})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run 模式：只打印，不写 runner-summary.json",
    )
    args = parser.parse_args()
    return run_all(Path(args.home), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
