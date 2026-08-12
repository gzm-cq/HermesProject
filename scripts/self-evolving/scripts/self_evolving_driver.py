#!/usr/bin/env python3
"""self_evolving_driver.py — Self-Evolving 编排接入（F-5）。

把 SkillOpt 的失败轨迹（HERMES_HOME/skillopt-runner/state.json 的 failed_tasks）
或显式传入的 trace 文件，依次喂给 Revision → Refinement 算子，结果写回输出目录，
并追加统一反馈账本事件（F-1）。

这是能力飞轮闭环的最后一环：算子已就绪（Revision/Recombination/Refinement），
但此前无调用方编排。本驱动在 skillopt 之后运行，消费其失败轨迹做自我进化。

用法:
  python3 self_evolving_driver.py [--state-file PATH] [--trace-file PATH] \\
                                 [--output-dir DIR] [--config PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 让 self_evolving 包可导入（scripts/self-evolving/src）
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ── 统一反馈账本（F-1）bootstrap ──────────────────────
try:
    from hermes_common import bootstrap  # noqa: F401
except ImportError:
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path
    _parent = _os.environ.get("HERMES_COMMON_SRC") or ""
    if not _parent:
        _d = _Path(__file__).resolve().parent
        for _ in range(12):
            _cand = _d / "libs" / "hermes_common"
            if (_cand / "hermes_common" / "__init__.py").is_file():
                _parent = str(_cand)
                break
            if _d.parent == _d:
                break
            _d = _d.parent
    if not _parent:
        _prod = "/root/.hermes/lib"
        if _os.path.isfile(_os.path.join(_prod, "hermes_common", "__init__.py")):
            _parent = _prod
    if _parent and _parent not in _sys.path:
        _sys.path.insert(0, _parent)
    from hermes_common import bootstrap  # noqa: F401
bootstrap()
from hermes_common.ledger import append_ledger_event

from self_evolving.operators.revision import revise
from self_evolving.operators.refinement import refine

# 本地安全写回模块（scripts/self-evolving/scripts/skill_patch.py，自包含、不依赖未部署的 common）
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
try:
    from skill_patch import patch_skill_md
except Exception:  # noqa: BLE001
    def patch_skill_md(*_a, **_k):  # type: ignore
        return False
    patch_skill_md = None  # type: ignore


def _extract_failed_tasks(state_file: str) -> list[dict]:
    """从 skillopt state.json 读取 failed_tasks（dict skill->list），展平为带 skill 的列表。"""
    items: list[dict] = []
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return items
    ft = state.get("failed_tasks") or {}
    if not isinstance(ft, dict):
        return items
    for skill, tasks in ft.items():
        if not isinstance(tasks, list):
            continue
        for t in tasks:
            if isinstance(t, dict):
                t = dict(t)
                t.setdefault("skill", skill)
                items.append(t)
    return items


def _load_trace_file(trace_file: str) -> list[dict]:
    try:
        with open(trace_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("failed_tasks", [data])
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    return []


def _get_failed_content(task: dict) -> str:
    for k in ("failed_content", "content", "code", "text", "output"):
        v = task.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(task, ensure_ascii=False)


def _get_context(task: dict) -> str:
    for k in ("context", "error_message", "error", "trace", "goal"):
        v = task.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return task.get("skill", "unknown-skill")


def run(state_file: str, trace_file: str | None, output_dir: str,
        config_path: str | None, dry_run: bool,
        auto_apply: bool = False, home: str | None = None) -> int:
    home = home or os.environ.get("HERMES_HOME") or "/root/.hermes"
    items = _load_trace_file(trace_file) if trace_file else _extract_failed_tasks(state_file)
    if not items:
        print("Self-Evolving: 无失败轨迹可处理，跳过")
        append_ledger_event("self_evolving", {"status": "no_input", "count": 0})
        return 0

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    errors = 0
    applied = 0
    blocked = 0
    for task in items:
        skill = task.get("skill", "unknown")
        task_id = task.get("id", task.get("task_id", "n/a"))
        failed_content = _get_failed_content(task)
        context = _get_context(task)
        print(f"  [SE] skill={skill} task={task_id} content_len={len(failed_content)}")

        if dry_run:
            print(f"  [SE][DRY-RUN] 将执行 revise→refine" + ("→ 写回 SKILL.md" if auto_apply else ""))
            processed += 1
            continue

        try:
            rev_out = revise(failed_content, context, config_path=config_path)
            revised = getattr(rev_out, "revised_content", None) or ""
            ref_out = refine(revised, config_path=config_path)
            refined = getattr(ref_out, "refined_content", None) or revised
        except Exception as e:  # noqa: BLE001
            print(f"  [SE] 处理失败 skill={skill} task={task_id}: {e}")
            errors += 1
            continue

        # 自动写回（B）：将 refine 产出安全写回对应 SKILL.md（受 HARD_BLOCK 护栏约束）
        if auto_apply and refined:
            ok = patch_skill_md(skill, refined, task_id=str(task_id), home=home)
            if ok:
                applied += 1
            else:
                blocked += 1

        rec = {
            "skill": skill,
            "task_id": task_id,
            "revised_content": revised,
            "refined_content": refined,
            "auto_applied": bool(auto_apply and refined),
            "revision": getattr(rev_out, "to_dict", lambda: {})(),
            "refinement": getattr(ref_out, "to_dict", lambda: {})(),
        }
        out_path = out_dir / f"{skill}_{task_id}.json"
        try:
            out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        processed += 1

    append_ledger_event("self_evolving", {
        "status": "ok" if errors == 0 else "partial",
        "processed": processed,
        "errors": errors,
        "applied": applied,
        "blocked": blocked,
        "auto_apply": auto_apply,
        "dry_run": dry_run,
        "source": "trace_file" if trace_file else "skillopt_state",
    })
    print(f"Self-Evolving 完成: processed={processed}, errors={errors}, applied={applied}, blocked={blocked}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Self-Evolving 编排驱动（Revision→Refinement）")
    hermes_home = os.environ.get("HERMES_HOME") or "/root/.hermes"
    ap.add_argument("--state-file",
                    default=os.path.join(hermes_home, "skillopt-runner", "state.json"))
    ap.add_argument("--trace-file", default=None,
                    help="显式失败轨迹 JSON（覆盖 state.json）")
    ap.add_argument("--output-dir",
                    default=os.path.join(hermes_home, "self-evolving", "output"))
    ap.add_argument("--config", default=None,
                    help="self-evolving config yaml（默认 config/default.yaml）")
    ap.add_argument("--auto-apply", action="store_true",
                    help="将 refine 产出安全写回对应 skill 的 SKILL.md（受 HARD_BLOCK 护栏约束）")
    ap.add_argument("--home", default=None,
                    help="显式 HERMES_HOME（默认取环境变量或 /root/.hermes）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return run(args.state_file, args.trace_file, args.output_dir, args.config,
               args.dry_run, args.auto_apply, args.home)


if __name__ == "__main__":
    sys.exit(main())
