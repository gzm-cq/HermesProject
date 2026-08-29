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

2026-08-29 A+B 改造：
  A-1 队列消费：处理完的任务从 failed_tasks 移除（此前从不清理，导致连续四天
      每晚重跑完全相同的 10 个任务）。
  A-2 全局去重：同一 task_id 挂在多个 skill 下时只处理一次。
  A-3 并发：串行改为线程池（默认 3），单项目标超时（默认 900s）。
  B-1 相似度去重：产出与上次高度相似（默认 0.9）则跳过写回，避免 LLM 措辞抖动
      导致每晚重写文档。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    from skill_patch import (get_char_limits, get_block_count_limit,
                             count_blocks, find_skill_md,
                             patch_skill_md, patch_skill_md_detailed)
except Exception:  # noqa: BLE001
    class _FallbackResult:  # noqa: D401
        ok = False
        status = 'rejected'
        reason = 'skill_patch 不可用'
        size = 0

    def patch_skill_md(*_a, **_k):  # type: ignore
        return False

    def patch_skill_md_detailed(*_a, **_k):  # type: ignore
        return _FallbackResult()

    def find_skill_md(*_a, **_k):  # type: ignore
        return None

    def get_char_limits():  # type: ignore
        return 12000, 30000

    def get_block_count_limit():  # type: ignore
        return 8

    def count_blocks(text):  # type: ignore
        return 0


def _task_id_of(task: dict) -> str:
    return str(task.get("id") or task.get("task_id") or "")


def _extract_failed_tasks(state_file: str) -> list[dict]:
    """从 skillopt state.json 读取 failed_tasks（dict skill->list），展平为带 skill 的列表。

    A-2 全局去重：同一 task_id 可能同时挂在多个 skill 下（如 devops/kanban-worker
    与 hindsight-memory 共用 4 个 task），此前会被 revise→refine 两遍、分别写进
    两份 SKILL.md。这里按 task_id 全局只保留首次出现。
    """
    items: list[dict] = []
    seen: set[str] = set()
    dup = 0
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
            if not isinstance(t, dict):
                continue
            t = dict(t)
            t.setdefault("skill", skill)
            tid = _task_id_of(t)
            if tid:
                if tid in seen:
                    dup += 1
                    continue
                seen.add(tid)
            items.append(t)
    if dup:
        print(f"Self-Evolving: 全局去重，跳过 {dup} 个跨 skill 重复的 task_id")
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


# ── A/B 改造：并发、超时、相似度、队列消费 ──────────────────────────────────

DEFAULT_MAX_WORKERS = 3       # LLM 网关并发度，保守取值避免打满网关
DEFAULT_ITEM_TIMEOUT = 900    # 单项（revise+refine）目标超时，秒
DEFAULT_SIMILARITY = 0.9      # 与上次产出相似度超过此值则判定「无实质改进」
APPLIED_STATE_CAP = 300       # applied_state.json 最多保留的 task 指纹条数
STATE_BACKUP_CAP = 10         # state.json 消费前备份最多保留份数


def _env_num(name: str, default: float, caster) -> Any:
    raw = os.environ.get(name, "")
    if raw:
        try:
            v = caster(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return default


def _json_load(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _json_save_atomic(path: Path, data: Any) -> None:
    """原子写 JSON：先写 .tmp 再 rename，避免中断留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _fingerprint(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def _prune_dir(directory: Path, prefix: str, keep: int) -> None:
    """按文件名倒序保留最近 keep 个，删除更旧的（防止备份无限累积）。"""
    try:
        matches = sorted(
            (p for p in directory.iterdir()
             if p.is_file() and p.name.startswith(prefix)),
            key=lambda p: p.name, reverse=True,
        )
    except OSError:
        return
    for old in matches[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def _similarity(a: str, b: str) -> float:
    """两段文本的相似度（0~1），用于判定「LLM 是否只是换了个说法」。"""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()


def _applied_state_path(home: str) -> Path:
    return Path(home) / "self-evolving" / "applied_state.json"


def _consume_failed_tasks(state_file: str, consumed: set[str]) -> bool:
    """A-1 队列消费：把已处理的 task_id 从 state.json 的 failed_tasks 中移除。

    只在「任务确实被处理过」时移除。LLM 调用异常（临时故障）不应移除，
    留给下一晚重试。

    Returns:
        True 表示已回写 state 文件。
    """
    if not consumed:
        return False
    path = Path(state_file)
    if not path.is_file():
        return False
    state = _json_load(path, None)
    if not isinstance(state, dict):
        return False
    ft = state.get("failed_tasks")
    if not isinstance(ft, dict):
        return False

    removed = 0
    for skill in list(ft.keys()):
        tasks = ft.get(skill)
        if not isinstance(tasks, list):
            continue
        kept = [t for t in tasks
                if not (isinstance(t, dict) and _task_id_of(t) in consumed)]
        removed += len(tasks) - len(kept)
        ft[skill] = kept

    if removed == 0:
        return False

    # 备份 + 原子写（state.json 是 skillopt 与 self-evolving 共享，谨慎处理）
    bak_dir = path.parent / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H-%M-%S")
    try:
        shutil.copy2(path, bak_dir / f"state.json.pre-consume-{stamp}")
    except OSError:
        pass
    _prune_dir(bak_dir, "state.json.pre-consume-", STATE_BACKUP_CAP)
    _json_save_atomic(path, state)
    # 注：removed 可能大于 len(consumed) —— 同一 task_id 曾重复挂在多个 skill 下，
    # 处理一次即可，两处残留记录一并清理。
    print(f"Self-Evolving: 已从队列移除 {removed} 条记录"
          f"（涉及 {len(consumed)} 个 task），剩余 "
          f"{sum(len(v) if isinstance(v, list) else 0 for v in ft.values())} 项")
    return True


def _filter_oversized(items: list[dict], home: str) -> tuple[list[dict], list[dict]]:
    """前置过滤：目标 SKILL.md 已超硬上限的 task 直接跳过，不调 LLM。

    这些 task 注定会被写回护栏拒绝，先跑一遍 revise→refine 只是白烧 token。
    被跳过的 task 不从队列消费 —— 人工整合文档后会自动恢复处理。

    Returns:
        (可处理的 items, 被跳过的 (task, 文件大小) 列表)
    """
    _soft, hard = get_char_limits()
    block_limit = get_block_count_limit()
    size_cache: dict = {}
    kept: list[dict] = []
    skipped: list[dict] = []

    for t in items:
        skill = t.get("skill", "unknown")
        if skill not in size_cache:
            p = find_skill_md(skill, home)
            info = None
            if p is not None:
                try:
                    text = p.read_text(encoding="utf-8")
                    info = {"size": len(text), "blocks": count_blocks(text)}
                except OSError:
                    info = None
            size_cache[skill] = info
        info = size_cache[skill]
        if info is not None and (info["size"] > hard
                                 or info["blocks"] >= block_limit):
            skipped.append({"skill": skill, "task_id": _task_id_of(t),
                            "size": info["size"], "blocks": info["blocks"],
                            "reason": ("超硬上限" if info["size"] > hard
                                       else "待复核块过多")})
            continue
        kept.append(t)

    if skipped:
        by_skill: dict = {}
        for s in skipped:
            by_skill.setdefault(s["skill"], []).append(s)
        for skill, rows in by_skill.items():
            r = rows[0]
            detail = (f"{r['size']} 字符 > 硬上限 {hard}" if r["reason"] == "超硬上限"
                      else f"待复核 SE 块已达 {r['blocks']} 个 ≥ 上限 {block_limit}")
            print(f"  [SE] 跳过 {len(rows)} 项（{skill} {detail}，"
                  f"需人工整合后恢复）")
    return kept, skipped


def _write_record(out_dir: Path, skill: str, task_id: str, rec: dict) -> None:
    """落盘单条处理记录（I/O 失败不影响主流程）。"""
    out_path = out_dir / f"{skill}_{task_id}.json"
    try:
        out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    except OSError:
        pass


def _run_llm_stage(
    idx: int, task: dict, sink: list, config_path: str | None,
) -> None:
    """单个 task 的 revise→refine 阶段（在 worker 线程中执行）。"""
    failed_content = _get_failed_content(task)
    context = _get_context(task)
    try:
        rev_out = revise(failed_content, context, config_path=config_path)
        revised = getattr(rev_out, "revised_content", None) or ""
        ref_out = refine(revised, config_path=config_path)
        refined = getattr(ref_out, "refined_content", None) or revised
        sink[idx] = {
            "ok": True, "revised": revised, "refined": refined,
            "rev_out": rev_out, "ref_out": ref_out,
        }
    except Exception as e:  # noqa: BLE001
        sink[idx] = {"ok": False, "err": str(e)}


def _run_llm_parallel(
    items: list[dict], config_path: str | None,
    max_workers: int, item_timeout: float,
) -> list:
    """A-3 并发跑 revise→refine。

    用 daemon 线程分批执行：主线程 join 带超时，超时未返回的任务记为 timeout，
    进程退出时不会被挂起的线程拖住。

    Returns:
        与 items 等长的结果列表；每项为 dict 或 None（超时未完成）。
    """
    sink: list = [None] * len(items)
    for start in range(0, len(items), max_workers):
        batch = list(range(start, min(start + max_workers, len(items))))
        threads = []
        for i in batch:
            t = threading.Thread(
                target=_run_llm_stage,
                args=(i, items[i], sink, config_path),
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=item_timeout)
        pending = [i for i in batch if sink[i] is None]
        for i in pending:
            tid = _task_id_of(items[i]) or items[i].get("skill", "?")
            print(f"  [SE] 超时未完成（{item_timeout:g}s）: {tid} — 保留在队列，明晚重试")
    return sink


def run(state_file: str, trace_file: str | None, output_dir: str,
        config_path: str | None, dry_run: bool,
        auto_apply: bool = False, home: str | None = None,
        max_items: int = 10, max_workers: int | None = None,
        item_timeout: float | None = None,
        similarity_threshold: float | None = None) -> int:
    home = home or os.environ.get("HERMES_HOME") or "/root/.hermes"
    items = _load_trace_file(trace_file) if trace_file else _extract_failed_tasks(state_file)
    if not items:
        print("Self-Evolving: 无失败轨迹可处理，跳过")
        append_ledger_event("self_evolving", {"status": "no_input", "count": 0})
        return 0

    # 前置过滤：SKILL.md 已超硬上限的 task 不调 LLM（注定被写回护栏拒绝）。
    # 这些 task 不从队列消费，人工整合文档后自动恢复。
    oversized: list[dict] = []
    if auto_apply or dry_run:
        # dry-run 也走过滤，否则看到的候选列表与真实运行不一致，运维会误判
        items, oversized = _filter_oversized(items, home)
        if not items:
            print("Self-Evolving: 所有候选任务的 SKILL.md 均已超硬上限，跳过"
                  "（需人工整合后再启用自动优化）")
            append_ledger_event("self_evolving", {
                "status": "all_oversized",
                "oversized": len(oversized),
                "auto_apply": auto_apply,
                "dry_run": dry_run,
            })
            return 0

    # 限制单次处理数量，防 OOM（exit=137）
    if len(items) > max_items:
        print(f"Self-Evolving: 截断 {len(items)} → {max_items} 项（--max-items={max_items}）")
        items = items[:max_items]

    workers = int(max_workers or _env_num("SE_MAX_WORKERS", DEFAULT_MAX_WORKERS, int))
    timeout = float(item_timeout or _env_num("SE_ITEM_TIMEOUT", DEFAULT_ITEM_TIMEOUT, float))
    sim_thr = float(similarity_threshold
                    or _env_num("SE_SIMILARITY_THRESHOLD", DEFAULT_SIMILARITY, float))

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for task in items:
        print(f"  [SE] skill={task.get('skill', 'unknown')} "
              f"task={_task_id_of(task) or 'n/a'} "
              f"content_len={len(_get_failed_content(task))}")

    if dry_run:
        for task in items:
            print("  [SE][DRY-RUN] 将执行 revise→refine"
                  + ("→ 写回 SKILL.md" if auto_apply else ""))
        append_ledger_event("self_evolving", {
            "status": "dry_run", "count": len(items),
            "auto_apply": auto_apply, "dry_run": True,
        })
        print(f"Self-Evolving 完成(dry-run): 共 {len(items)} 项待处理")
        return 0

    results = _run_llm_parallel(items, config_path, workers, timeout)

    # 已应用产出的指纹（B-1 相似度去重）：只在真正写回成功后更新
    applied_path = _applied_state_path(home)
    applied_state = _json_load(applied_path, {})
    if not isinstance(applied_state, dict):
        applied_state = {}

    processed = 0
    errors = 0
    applied = 0
    blocked = 0
    unchanged = 0
    timeouts = 0
    consumed: set[str] = set()

    for idx, task in enumerate(items):
        skill = task.get("skill", "unknown")
        task_id = _task_id_of(task) or "n/a"
        res = results[idx]

        if res is None:
            timeouts += 1
            continue

        if not res.get("ok"):
            print(f"  [SE] 处理失败 skill={skill} task={task_id}: {res.get('err')}")
            errors += 1
            continue

        revised = res["revised"]
        refined = res["refined"]

        # ── B-1 相似度去重：与上次产出几乎一致则跳过写回 ──
        status = "skipped"
        if auto_apply and refined:
            prev = applied_state.get(task_id)
            prev_content = prev.get("content", "") if isinstance(prev, dict) else ""
            if prev_content:
                sim = _similarity(prev_content, refined)
                if sim >= sim_thr:
                    print(f"  [SE] 无实质改进（相似度 {sim:.2f} ≥ {sim_thr}），"
                          f"跳过写回 {skill} (task {task_id})")
                    unchanged += 1
                    status = "unchanged"
                    consumed.add(task_id)
                    rec = {
                        "skill": skill, "task_id": task_id,
                        "revised_content": revised, "refined_content": refined,
                        "auto_applied": False, "skip_reason": "similar_to_previous",
                        "similarity": round(sim, 4),
                        "revision": getattr(res["rev_out"], "to_dict", lambda: {})(),
                        "refinement": getattr(res["ref_out"], "to_dict", lambda: {})(),
                    }
                    _write_record(out_dir, skill, task_id, rec)
                    processed += 1
                    continue

            pr = patch_skill_md_detailed(skill, refined, task_id=task_id, home=home)
            status = pr.status
            if pr.status == "applied":
                applied += 1
                applied_state[task_id] = {
                    "skill": skill,
                    "sha1": _fingerprint(refined),
                    "content": refined,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            elif pr.status == "unchanged":
                unchanged += 1
            else:
                blocked += 1
                print(f"  [SE] 写回被拒 skill={skill} task={task_id}: {pr.reason}")
        elif auto_apply:
            blocked += 1

        # 可选：重组算子（S3-S6 闭环反馈来源）。默认关闭（SE_ENABLE_RECOMBINE!=1），
        # 开启后记录 synergy_score 到 output JSON 的 recombination 字段，
        # 供健康报告聚合 se_recombine_synergy_avg。失败则静默跳过，不影响主流程。
        recombination_info: dict = {}
        if os.environ.get("SE_ENABLE_RECOMBINE") == "1" and revised and refined:
            try:
                from self_evolving.operators.recombination import recombine
                _rcb = recombine([revised, refined], _get_context(task) or "",
                                 config_path=config_path)
                recombination_info = {"synergy_score": float(
                    getattr(_rcb, "synergy_score", 0.0) or 0.0)}
            except Exception as _re:  # noqa: BLE001
                print(f"  [SE][recombine] 跳过（非致命）: {_re}")

        rec = {
            "skill": skill,
            "task_id": task_id,
            "revised_content": revised,
            "refined_content": refined,
            "auto_applied": auto_apply and status == "applied",
            "patch_status": status,
            "revision": getattr(res["rev_out"], "to_dict", lambda: {})(),
            "refinement": getattr(res["ref_out"], "to_dict", lambda: {})(),
            "recombination": recombination_info,
        }
        _write_record(out_dir, skill, task_id, rec)
        processed += 1
        # A-1: 处理过（无论写回/跳过/被拒）就从队列移除，避免明晚原样重跑。
        # 只有 LLM 异常与超时才保留重试。
        consumed.add(task_id)

    # ── A-1 队列消费 ──
    consumed_n = 0
    if consumed and not trace_file:
        if _consume_failed_tasks(state_file, consumed):
            consumed_n = len(consumed)

    if applied_state:
        if len(applied_state) > APPLIED_STATE_CAP:
            ordered = sorted(
                applied_state.items(),
                key=lambda kv: kv[1].get("ts", "") if isinstance(kv[1], dict) else "",
            )
            applied_state = dict(ordered[-APPLIED_STATE_CAP:])
        _json_save_atomic(applied_path, applied_state)

    append_ledger_event("self_evolving", {
        "status": "ok" if errors == 0 and timeouts == 0 else "partial",
        "processed": processed,
        "errors": errors,
        "applied": applied,
        "blocked": blocked,
        "unchanged": unchanged,
        "timeouts": timeouts,
        "oversized": len(oversized),
        "consumed": consumed_n,
        "auto_apply": auto_apply,
        "dry_run": dry_run,
        "workers": workers,
        "source": "trace_file" if trace_file else "skillopt_state",
    })
    print(f"Self-Evolving 完成: processed={processed}, errors={errors}, "
          f"applied={applied}, blocked={blocked}, unchanged={unchanged}, "
          f"timeouts={timeouts}, consumed={consumed_n}")
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
    ap.add_argument("--max-items", type=int, default=10,
                    help="单次运行最多处理的任务数（默认 10，防 OOM）")
    ap.add_argument("--max-workers", type=int, default=None,
                    help="LLM 阶段并发度（默认 3，可用 SE_MAX_WORKERS 覆盖）")
    ap.add_argument("--item-timeout", type=float, default=None,
                    help="单项 revise→refine 目标超时秒数（默认 900，"
                         "可用 SE_ITEM_TIMEOUT 覆盖）")
    ap.add_argument("--similarity-threshold", type=float, default=None,
                    help="与上次产出的相似度阈值，超过则跳过写回"
                         "（默认 0.9，可用 SE_SIMILARITY_THRESHOLD 覆盖）")
    args = ap.parse_args(argv)
    return run(args.state_file, args.trace_file, args.output_dir, args.config,
               args.dry_run, args.auto_apply, args.home, args.max_items,
               args.max_workers, args.item_timeout, args.similarity_threshold)


if __name__ == "__main__":
    sys.exit(main())
