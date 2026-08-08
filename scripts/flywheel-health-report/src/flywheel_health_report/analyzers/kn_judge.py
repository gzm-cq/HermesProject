"""kn_judge.py — 知识导航 LLM Judge 质量评估（健康巡检内集成）。

封装 collect_baseline.run_judge，支持：
- since/until 窗口过滤（与飞轮报告数据窗口一致）
- 最小样本量检查（防止小样本噪声）
- 环境变量 / KN_JUDGE_CFG 驱动 LLM 配置
- 失败兜底：用 kn_avg_score + kept 粗估（避免反馈断裂）

与其他参数优化保持一致：直接产出写入 daily summary 的字段。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..config import KN_JUDGE_CFG


# --------------------------------------------------------------------
# collect_baseline 的 run_judge / collect_all_recalls / _judge_one 导入
# （优先 import 安装在 /root/.hermes 下的脚本，否则走 fallback）
# --------------------------------------------------------------------

_COLLECT_BASELINE_IMPORTED = False
_collect_all_recalls = None
_judge_one = None
_bootstrap_ci = None


def _ensure_collect_baseline_imported(home: Path) -> bool:
    """延迟 import，避免脚本路径在测试环境下不存在时 import 崩溃。"""
    global _COLLECT_BASELINE_IMPORTED, _collect_all_recalls, _judge_one, _bootstrap_ci
    if _COLLECT_BASELINE_IMPORTED:
        return _collect_all_recalls is not None

    _COLLECT_BASELINE_IMPORTED = True

    candidates: list[str] = [
        str(home / "plugins" / "knowledge-navigation" / "scripts"),
        str(home / "plugins" / "knowledge-navigation" / "src"),
        os.path.join(os.path.dirname(__file__),
                     "..", "..", "..", "..", "..", "plugins",
                     "knowledge-navigation", "scripts"),
    ]
    for p in candidates:
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from collect_baseline import (  # type: ignore[import-not-found]
            collect_all_recalls,
            _judge_one as judge_one,
            bootstrap_ci,
        )
        _collect_all_recalls = collect_all_recalls
        _judge_one = judge_one
        _bootstrap_ci = bootstrap_ci
    except Exception as e:
        sys.stderr.write(f"[kn_judge] collect_baseline import 失败: {type(e).__name__}: {e}\n")
        return False
    return True


def _load_env_file(home: Path) -> None:
    """读取 ~/.hermes/.env，让 LLM API 配置对 judge 子进程可用。"""
    env_file = home / ".env"
    if not env_file.is_file():
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def _load_llm_config(home: Path) -> dict[str, str] | None:
    """构造 judge 的 LLM 配置。优先级：显式 env > .env > 空。"""
    _load_env_file(home)
    url = (
        os.environ.get("LLM_API_URL")
        or os.environ.get("LITELLM_API_URL")
        or "http://127.0.0.1:4142/v1/chat/completions"
    )
    key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("LITELLM_MASTER_KEY")
        or ""
    )
    model = os.environ.get("LLM_MODEL", "s-deepseek-v4-flash")
    if not url or not key:
        sys.stderr.write(
            "[kn_judge] LLM_API_URL/KEY 未配置，无法启动 judge\n"
        )
        return None
    return {"url": url, "key": key, "model": model}


# --------------------------------------------------------------------
# Judge 执行主函数
# --------------------------------------------------------------------

def run_judge_within_window(
    home: Path,
    since_iso: str,
    until_iso: str = "",
    *,
    force_full_eval: bool = False,
) -> dict[str, Any]:
    """在 [since, until) 窗口内执行 judge（时间不足/样本过少时自动降级）。

    Args:
        home: Hermes home，用来定位 trace.log/.env/collect_baseline.py
        since_iso: 窗口下界（含），形如 2026-08-07 或 2026-08-07T00:00:00
        until_iso: 窗口上界（不含），空串则无上界
        force_full_eval: 强制跑满 200 条（忽略 min_sample 限制，人工调试用）

    Returns:
        可直接合并进 daily summary 的 dict：
        {kn_judge_relevant_rate, kn_judge_avg_relevance, kn_judge_sample_count,
         kn_judge_ci_lo, kn_judge_ci_hi, kn_judge_fallback, kn_judge_error}
        若样本不足 / 未启用，则返回空 dict（不污染 summary）。
    """
    if not KN_JUDGE_CFG.get("enabled"):
        return {"kn_judge_error": "disabled_by_config"}

    cfg = KN_JUDGE_CFG
    sample_size = int(cfg.get("sample_size", 200))
    min_sample = int(cfg.get("min_sample", 50))
    parallel = int(cfg.get("parallel", 5))
    max_wt = float(cfg.get("max_walltime_sec", 3600))
    fallback_ok = bool(cfg.get("fallback_on_fail", True))

    # 先尝试 import collect_baseline
    ok_import = _ensure_collect_baseline_imported(home)
    if not ok_import or _collect_all_recalls is None or _judge_one is None:
        return {"kn_judge_error": "collect_baseline_import_failed"}

    trace_path = home / "plugins" / "knowledge-navigation" / "trace.log"
    if not trace_path.is_file():
        return {"kn_judge_error": "trace_log_missing"}

    # 读取 recall_success，按窗口过滤
    all_rec = _collect_all_recalls(str(trace_path))
    windowed = [
        r for r in all_rec
        if r.get("timestamp", "") >= since_iso
        and (not until_iso or r.get("timestamp", "") < until_iso)
    ]

    total_windowed = len(windowed)
    if total_windowed < min_sample and not force_full_eval:
        return {
            "kn_judge_sample_count": total_windowed,
            "kn_judge_error": f"sample_below_min:{total_windowed}<{min_sample}",
        }

    llm_cfg = _load_llm_config(home)
    if llm_cfg is None:
        return {"kn_judge_sample_count": total_windowed, "kn_judge_error": "llm_config_missing"}

    # 取最近 sample_size 条（与 judge 脚本一致）
    sample = windowed[-sample_size:] if len(windowed) >= sample_size else windowed
    os.environ["JUDGE_INSECURE"] = "1"
    os.environ["JUDGE_PARALLEL"] = str(parallel)

    start = time.monotonic()
    judged = 0
    errors = 0
    scores: list[float] = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_judge_one, rec, llm_cfg): i for i, rec in enumerate(sample)}
        for future in as_completed(futures):
            elapsed = time.monotonic() - start
            if elapsed > max_wt:
                # 超过硬超时，已评部分就用已评的数据（允许不完整）
                break
            result = future.result()
            if result is None or result[0] is None:
                errors += 1
                continue
            llm_score, ok = result
            if not ok:
                errors += 1
                continue
            scores.append(llm_score)
            judged += 1

    if judged < min(min_sample // 2, 20):
        # 成功评分过少，走兜底
        if fallback_ok:
            fb = _kn_judge_fallback(all_rec, since_iso, until_iso)
            fb["kn_judge_error"] = f"too_few_judged:{judged}"
            return fb
        return {
            "kn_judge_sample_count": judged,
            "kn_judge_error": f"too_few_judged:{judged}",
        }

    avg_rel = sum(scores) / len(scores) if scores else 0.0
    rel_rate = sum(1 for s in scores if s >= 0.5) / len(scores) if scores else 0.0
    ci = _bootstrap_ci(scores) if _bootstrap_ci and len(scores) > 1 else (0.0, 0.0, 0.0)

    return {
        "kn_judge_sample_count": judged,
        "kn_judge_relevant_rate": round(rel_rate, 4),
        "kn_judge_avg_relevance": round(avg_rel, 4),
        "kn_judge_ci_lo": round(ci[1], 4),
        "kn_judge_ci_hi": round(ci[2], 4),
        "kn_judge_fallback": False,
    }


# --------------------------------------------------------------------
# 兜底：judge 跑不起来时，用 kn_avg_score + kept 粗估，防止反馈断裂
# 策略：avg_score 归一化 → 0.45~0.65 映射到 0.4~0.75，kept 低不重罚（和 v2 prompt 一致）
# --------------------------------------------------------------------

def _kn_judge_fallback(all_records: list[dict], since: str, until: str) -> dict[str, Any]:
    """用 kept + avg_score 粗估 judge 评分。"""
    if not all_records:
        return {
            "kn_judge_sample_count": 0,
            "kn_judge_relevant_rate": 0,
            "kn_judge_avg_relevance": 0,
            "kn_judge_fallback": True,
        }
    windowed = [
        r for r in all_records
        if r.get("timestamp", "") >= since
        and (not until or r.get("timestamp", "") < until)
    ]
    windowed = windowed[-200:] or all_records[-200:]
    if not windowed:
        return {
            "kn_judge_sample_count": 0,
            "kn_judge_relevant_rate": 0,
            "kn_judge_avg_relevance": 0,
            "kn_judge_fallback": True,
        }
    scores = [float(r.get("avg_score") or 0) for r in windowed]
    kepts = [int(r.get("kept_results") or 0) for r in windowed]
    avg_scr = sum(scores) / len(scores) if scores else 0
    # avg_score 0.40 ~ 0.65 → judge 0.40 ~ 0.75（单调线性映射）
    clipped = max(0.40, min(0.65, avg_scr))
    est_avg = 0.40 + (clipped - 0.40) / 0.25 * 0.35
    # kept >=1 视为非空，kept=0 给相关率打 0.2 折
    non_empty_ratio = sum(1 for k in kepts if k > 0) / len(kepts)
    # 相关率估算：avg 之上 + non_empty 修正
    est_rel_rate = max(0.1, min(0.95, est_avg * (0.85 + 0.3 * non_empty_ratio)))
    return {
        "kn_judge_sample_count": len(windowed),
        "kn_judge_relevant_rate": round(est_rel_rate, 4),
        "kn_judge_avg_relevance": round(est_avg, 4),
        "kn_judge_fallback": True,
    }


# --------------------------------------------------------------------
# 参数优化现状分析：读取 auto-tuner-log.jsonl 生成摘要，供健康巡检报告
# --------------------------------------------------------------------

def summarize_param_tuning(log_file: Path, state_file: Path) -> dict[str, Any]:
    """读取 auto-tuner 日志 + state，输出参数优化轨迹摘要（与其他参数优化一致）。

    返回结构：
    {
      "params": { "KN_MIN_SCORE": { "current", "initial", "history": [...],
                                     "locked"/"suspended"/"converged", "improvement" },
                   "sag_max_inject": {...} },
      "last_tune": { ... },
      "any_pending_restart": bool,
      "history_count": int,
    }
    """
    from ..config import PARAM_DEFS, STATE_FILE, LOG_FILE, HERMES_HOME  # local

    # 允许 override
    if not str(log_file):
        log_file = Path(LOG_FILE)
    if not str(state_file):
        state_file = Path(STATE_FILE)

    # --- state ---
    state: dict[str, Any] = {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    # --- history: 每条参数 按 date 顺序 最近 5 条 ---
    per_param_history: dict[str, list[dict]] = {}
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = rec.get("parameter", "")
                if not p or rec.get("dry_run"):
                    continue
                per_param_history.setdefault(p, []).append(rec)
    except FileNotFoundError:
        pass

    # --- 当前值：读 .env ---
    env_file = Path(HERMES_HOME) / ".env"
    env_map: dict[str, str] = {}
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                env_map[k.strip()] = v.strip()
    except FileNotFoundError:
        pass

    params: dict[str, Any] = {}
    any_pending = False
    for pdef in PARAM_DEFS:
        name, default, pmin, pmax, step, _ = pdef
        pst = state.get(name) or {}

        # 当前值（.env > 最新历史 applied/new_value > default）
        current = None
        if name in env_map:
            try:
                current = float(env_map[name])
            except (TypeError, ValueError):
                current = None
        if current is None and name in per_param_history:
            last = per_param_history[name][-1]
            if last.get("status", "") == "applied":
                current = float(last.get("new_value") or default)
        if current is None:
            current = float(default)

        history: list[dict] = []
        for rec in per_param_history.get(name, [])[-5:]:
            history.append({
                "date": rec.get("date", ""),
                "old": rec.get("old_value"),
                "new": rec.get("new_value"),
                "direction": rec.get("direction", ""),
                "status": rec.get("status", ""),
                "reason": rec.get("reason", "")[:60],
            })

        initial = pst.get("initial_value")
        try:
            improvement: float | None = (
                round(current - float(initial), 4)
                if initial is not None else None
            )
        except (TypeError, ValueError):
            improvement = None

        locked = bool(pst.get("locked"))
        suspended = bool(pst.get("suspended"))
        converged = locked or suspended or (
            int(pst.get("no_change_count", 0)) >= 3
        )

        # pending_restart 检查（最近一条日志的状态）
        last_status = ""
        if per_param_history.get(name):
            last_status = per_param_history[name][-1].get("status", "")
            if last_status == "pending_restart":
                any_pending = True

        params[name] = {
            "current": round(current, 4),
            "default": float(default),
            "range": [float(pmin), float(pmax)],
            "step": float(step),
            "initial_value": initial,
            "improvement_since_initial": improvement,
            "history": history,
            "locked": locked,
            "suspended": suspended,
            "converged": converged,
            "last_status": last_status,
            "state_counts": {
                "no_change_count": int(pst.get("no_change_count", 0)),
                "degradation_count": int(pst.get("degradation_count", 0)),
                "consecutive_degradation_count": int(pst.get("consecutive_degradation_count", 0)),
            },
        }

    total_hist = sum(len(v) for v in per_param_history.values())

    # 最近一次非 dry-run 调优
    last_tune: dict | None = None
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("dry_run"):
                    continue
                last_tune = rec
    except FileNotFoundError:
        pass

    return {
        "params": params,
        "any_pending_restart": any_pending,
        "history_count": total_hist,
        "last_tune": {
            "date": last_tune.get("date", "") if last_tune else "",
            "parameter": last_tune.get("parameter", "") if last_tune else "",
            "old": last_tune.get("old_value") if last_tune else None,
            "new": last_tune.get("new_value") if last_tune else None,
            "status": last_tune.get("status", "") if last_tune else "",
        } if last_tune else {},
    }
