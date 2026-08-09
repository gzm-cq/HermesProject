"""runner.py — 飞轮报告前置 runner：执行 skill eval 评估 + 登记 KN judge 声明。

硬血缘分析（已确认）：
  - kn_judge_relevant_rate/avg_relevance/sample_count → report.py 内建 run_judge_within_window() 直接产出
  - skill_used_count → ~/.hermes/skills/.usage.json（report 扫 .usage.json）
  - 其余 8 个反馈键 → trace.log 扫描（report 内 analyze_* 模块直接算）
  - skillopt-nightly-run → Skill 优化自闭环，不写 KN_TOKEN_BUDGET_SKILL_RATIO 反馈字段
  - knowledge-navigation-baseline cron job = 重复跑 judge（和 report 内建重复），已在 jobs.json 中禁用
  - run-skill-eval cron job → 合并到跑轮 runner 阶段 0 实际执行，避免独立 cron 12:00 跑

阶段 0 执行：
  1. 实际执行 run_skill_eval.py --json，结果写入 skill_eval_prev.json
  2. 写 runner-summary.json（登记已执行的 skill eval + 声明 KN judge 由 report 内部执行）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DEFAULT_HERMES_HOME


RUNNER_SUBPATH = Path("data") / "flywheel" / "runner-summary.json"

# skill eval 常量
SKILL_EVAL_OUTPUT = Path("data") / "flywheel" / "skill_eval_prev.json"
SKILL_EVAL_SCRIPT = Path("plugins") / "knowledge-navigation" / "scripts" / "run_skill_eval.py"


def _now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def _run_skill_eval(home: Path, *, dry_run: bool) -> dict[str, Any]:
    """执行 skill eval 评估，返回执行结果摘要。

    调用 run_skill_eval.py --json，将 stdout 保存到 skill_eval_prev.json。
    退化告警由飞轮报告内的 analyze_skill_eval 趋势对比负责。
    """
    eval_script = home / SKILL_EVAL_SCRIPT
    out_path = home / SKILL_EVAL_OUTPUT
    result: dict[str, Any] = {
        "cron_name": "run-skill-eval",
        "flywheel": "Skill",
        "exec_location": "runner:_run_skill_eval",
        "status": "done",
    }

    if not eval_script.is_file():
        result["status"] = "skipped"
        result["note"] = f"评估脚本不存在: {eval_script}"
        print(f"[runner] ⚠️  skill eval 脚本不存在: {eval_script}")
        return result

    if dry_run:
        result["note"] = "dry-run 模式，跳过执行"
        print(f"[runner] [dry-run] 将执行: python3 {eval_script} --json → {out_path}")
        return result

    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(eval_script), "--json"],
            capture_output=True, text=True, timeout=300,
            cwd=str(eval_script.parent.parent),  # knowledge-navigation 插件目录
        )
        if proc.returncode != 0:
            result["status"] = "failed"
            result["note"] = f"脚本退出码 {proc.returncode}: {proc.stderr[:200]}"
            print(f"[runner] ❌  skill eval 执行失败 (exit={proc.returncode})")
            print(f"[runner]    stderr: {proc.stderr[:200]}")
            return result

        data = json.loads(proc.stdout)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(proc.stdout, encoding="utf-8")

        meta = data.get("meta", {})
        result["avg_f1"] = meta.get("avg_f1", "N/A")
        result["note"] = f"F1={meta.get('avg_f1', 'N/A')}"
        print(f"[runner] ✅  skill eval 完成, F1={meta.get('avg_f1', 'N/A')}, 已保存到 {out_path}")
        return result

    except subprocess.TimeoutExpired:
        result["status"] = "failed"
        result["note"] = "执行超时 (300s)"
        print("[runner] ❌  skill eval 执行超时 (300s)")
        return result
    except json.JSONDecodeError as e:
        result["status"] = "failed"
        result["note"] = f"结果 JSON 解析失败: {e}"
        print(f"[runner] ❌  skill eval 结果 JSON 解析失败: {e}")
        return result
    except Exception as e:
        result["status"] = "failed"
        result["note"] = f"异常: {type(e).__name__}: {str(e)[:200]}"
        print(f"[runner] ❌  skill eval 异常: {type(e).__name__}: {str(e)[:200]}")
        return result


def build_runner_summary(home: Path, *, dry_run: bool,
                         skill_eval_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成阶段 0 runner 摘要。

    返回结构：
      {
        "generated_at": ISO,
        "dry_run": bool,
        "stages": {
          "0_skill_eval": {
            "tasks": [{
              "cron_name": "run-skill-eval",
              "flywheel": "Skill",
              "exec_location": "runner:_run_skill_eval",
              "status": "done|skipped|failed",
              "note": "...",
            }],
            "status": "done|skipped|failed",
          },
          "1_internal_kn_judge": {
            "tasks": [{
              "cron_name": "knowledge-navigation-baseline",
              "flywheel": "Router",
              "exec_location": "report:run_judge_within_window",
              "status": "will_run_in_report_phase",
              "note": "由 report 内建 kn_judge.run_judge_within_window() 执行",
            }],
            "status": "will_run",
          }
        }
      }
    """
    stages: dict[str, Any] = {}

    # 阶段 0：skill eval（已执行）
    if skill_eval_result:
        stages["0_skill_eval"] = {
            "tasks": [skill_eval_result],
            "status": skill_eval_result.get("status", "unknown"),
        }

    # 阶段 1：KN judge（由 report 内部执行，此处声明）
    stages["1_internal_kn_judge"] = {
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

    return {
        "generated_at": _now_iso(),
        "dry_run": dry_run,
        "stages": stages,
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
    """CLI 入口：阶段 0 执行 skill eval + 登记 KN judge 声明。"""
    t0 = time.monotonic()

    # 1. 执行 skill eval
    skill_eval_result = _run_skill_eval(home, dry_run=dry_run)

    # 2. 构建 runner 摘要
    summary = build_runner_summary(home, dry_run=dry_run,
                                   skill_eval_result=skill_eval_result)
    elapsed = round(time.monotonic() - t0, 3)
    summary["elapsed_seconds"] = elapsed
    if not dry_run:
        save_runner_summary(home, summary)
    se_status = skill_eval_result.get("status", "?")
    se_disp = "已执行" if se_status == "done" else se_status
    print(f"[runner] 阶段 0 完成（skill eval {se_disp}，登记阶段 1 KN judge）。elapsed={elapsed}s")
    print(f"[runner] runner-summary = {home / RUNNER_SUBPATH}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flywheel Runner: 阶段 0 执行 skill eval + 登记 KN judge 声明",
    )
    parser.add_argument(
        "--home",
        default=DEFAULT_HERMES_HOME,
        help=f"Hermes home path (default: {DEFAULT_HERMES_HOME})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run 模式：只打印，不写文件",
    )
    args = parser.parse_args()
    return run_all(Path(args.home), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
