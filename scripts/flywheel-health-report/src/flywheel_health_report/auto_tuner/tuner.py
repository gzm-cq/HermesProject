#!/usr/bin/env python3
"""tuner.py — 飞轮参数自优化调优器（纯Python版，v2 闭环）

从 cron-wrappers/auto-tuner.py 拆分而来：
  - 路径常量 / 参数定义 / 阈值 → ..config
  - 飞书通知函数 → .notifier
  - 本模块保留：工具函数、指标提取、闭环核心、状态机、方向决策、暂停机制、main

与 bash 版 auto-tuner.sh **完全兼容**：
  - 路径常量一致
  - 日志 JSONL 字段一致
  - 状态文件 JSON 结构一致
  - .env 读写格式一致

核心闭环（修复 v1 bash 版的 6 个断裂点）：
  1. main() 开头调用 handle_pending_restart() — 验证上次调优是否生效（gateway 已重启）
     - 已重启：抓取当天指标作为 metrics_after，原子更新 JSONL，写入 applied 状态
     - 未重启：发飞书提醒，本次跳过调优
  2. determine_direction() 使用 last_tune.metrics_before / metrics_after 判断改善
     - 不使用 metric_diff（跨参污染）
  3. initial_value 存调优前的 old_value（不是调优后的 new_value）
  4. metrics_after 无数据时保持 pending_restart 状态（不瞎判恶化）
  5. 调优生效后加冷却期（last_tune_date==today 时跳过新调优，让指标稳定一天）
  6. update_state() 每次调优/确认生效时 **一定调用并写回磁盘**

用法:
    python3 -m flywheel_health_report.auto_tuner.tuner [--dry-run] [--help]
    python3 -m flywheel_health_report.auto_tuner.tuner --dry-run
"""

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from ..config import (
    HERMES_HOME, ENV_FILE, HISTORY_FILE, LOG_FILE, PAUSE_FILE,
    BACKUP_DIR, STATE_FILE, CRON_LIB, FEISHU_CHAT_ID,
    PARAM_DEFS, FEEDBACK_KEYS,
    NO_CHANGE_LOCK_THRESHOLD, CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD,
    COOLDOWN_DAYS_AFTER_APPLY,
)
from .notifier import _send_lark, notify_restart_reminder, notify_gateway_restart


# ============================================================
# 0. 颜色（stdout 是 TTY 时启用，与 bash 版一致）
# ============================================================

_IS_TTY = sys.stdout.isatty()


def _c(code: str) -> str:
    return code if _IS_TTY else ""


C_CYA = _c("\033[36m")
C_GRN = _c("\033[32m")
C_RED = _c("\033[31m")
C_YLW = _c("\033[33m")
C_BLU = _c("\033[34m")
C_RST = _c("\033[0m")


# ============================================================
# 1. 彩色日志（与 bash 版视觉一致）
# ============================================================

def log_info(msg: str) -> None:  print(f"{C_CYA}[tuner]{C_RST} {msg}")
def log_ok(msg: str) -> None:    print(f"{C_GRN}[  ok ]{C_RST} {msg}")
def log_warn(msg: str) -> None:  print(f"{C_YLW}[warn ]{C_RST} {msg}", file=sys.stderr)
def log_err(msg: str) -> None:   print(f"{C_RED}[error]{C_RST} {msg}", file=sys.stderr)
def log_step(msg: str) -> None:  print(f"{C_BLU}[step ]{C_RST} {msg}")


# 因果链提权参数：只有 KN_ENABLE_CAUSAL_CHAIN=true 时才生效；
# 关闭时直接跳过，避免占用"一次只动一个变量"的调优轮次。
_CAUSAL_PARAM_NAMES = frozenset({"KN_CAUSAL_BOOST_ALPHA", "KN_CAUSAL_BOOST_CAP"})


def _causal_chain_enabled() -> bool:
    """读 ENV_FILE 中 KN_ENABLE_CAUSAL_CHAIN；与 KN config.py 默认 True 一致。"""
    raw = read_env_param("KN_ENABLE_CAUSAL_CHAIN")
    if raw is None:
        return True  # 默认启用（与 KN plugin from_env() 默认 True 对齐）
    return str(raw).lower() in ("1", "true", "yes", "on")


def _is_param_permanently_skipped(name: str) -> bool:
    """功能级死参数（依赖的全局开关关闭，调了白调）直接永久 skip。"""
    if name in _CAUSAL_PARAM_NAMES and not _causal_chain_enabled():
        return True
    return False


# KN LLM Judge 评估样本阈值：与 kn_judge.py KN_JUDGE_CFG.min_sample=50 严格对齐
# 样本不足时 kn_judge_relevant_rate / avg_relevance 波动极大，不应驱动调优决策。
_KN_JUDGE_MIN_SAMPLE = 50
_KN_JUDGE_SUBJECTIVE_KEYS = frozenset({"kn_judge_relevant_rate", "kn_judge_avg_relevance"})


def _kn_judge_trusted(rec_primary: Dict[str, Any],
                      rec_secondary: Optional[Dict[str, Any]] = None) -> Tuple[bool, int]:
    """判断 KN LLM Judge 反馈键是否可信。

    规则：检查 records 中 kn_judge_sample_count，取最小的有效样本值；最小样本 >= _KN_JUDGE_MIN_SAMPLE 才可信。
    传入 before + after 两份记录时，任一方不足即视为不可信（避免跨日比较的噪声驱动调优）。
    返回: (is_trusted, actual_min_sample_count)。若 2 份都没 sample_count 字段则视为不可信（return False, 0）。
    """
    sc_list: List[int] = []
    for r in (rec_primary, rec_secondary):
        if not r: continue
        sc = r.get("kn_judge_sample_count")
        if sc is None: continue
        try: sc_list.append(int(sc))
        except (TypeError, ValueError): pass
    if not sc_list:
        return False, 0
    sc = min(sc_list)
    return (sc >= _KN_JUDGE_MIN_SAMPLE), sc


# ============================================================
# 2. 工具函数：日期 / 原子写入 / JSONL / .env
# ============================================================

def _today_cn() -> str:
    """Asia/Shanghai 日期，与飞轮报告一致。Linux 可用 TZ，Windows 回退本地。"""
    try:
        # 优先用 UTC+8 算法（不受运行环境 TZ 影响）
        now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)
        return now.strftime("%Y-%m-%d")
    except Exception:
        return _dt.date.today().strftime("%Y-%m-%d")


def _yesterday_cn() -> str:
    try:
        now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8, days=-1)
        return now.strftime("%Y-%m-%d")
    except Exception:
        return (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return _dt.datetime.now().isoformat()


def _atomic_write_json(path: str, obj: Any) -> None:
    """先写临时文件再 rename，避免掉电损坏。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, path)
    except (OSError, PermissionError):
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _atomic_write_lines(path: str, lines: List[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                prefix=".tmp_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln.rstrip("\n"))
                f.write("\n")
        shutil.move(tmp, path)
    except (OSError, PermissionError):
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _append_jsonl(path: str, rec: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False))
        f.write("\n")


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: Dict[str, Any]) -> None:
    _atomic_write_json(STATE_FILE, state)


def read_env_param(param_name: str) -> Optional[str]:
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith(param_name + "=") and not s.startswith("#"):
                    v = s[len(param_name) + 1:].strip()
                    return v if v else None
    except FileNotFoundError:
        pass
    return None


def write_env_param(param_name: str, new_value: str) -> bool:
    """存在则替换，不存在则追加。原子写入。"""
    try:
        lines: List[str] = []
        found = False
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith(param_name + "=") and not s.startswith("#"):
                        lines.append(f"{param_name}={new_value}\n")
                        found = True
                    else:
                        lines.append(line)
        except FileNotFoundError:
            pass
        if not found:
            lines.append(f"{param_name}={new_value}\n")
        os.makedirs(os.path.dirname(ENV_FILE) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(ENV_FILE) or ".",
                                    prefix=".tmp_", suffix=".env")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tf:
                tf.writelines(lines)
            shutil.move(tmp, ENV_FILE)
        except (OSError, PermissionError):
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise
        return True
    except (OSError, PermissionError):
        return False


def backup_env() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"env-{ts}.bak")
    shutil.copy2(ENV_FILE, backup_file)
    return backup_file


def _run_shell(cmd: str, timeout: int = 10) -> Tuple[str, str, int]:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1


# ============================================================
# 3. 指标提取（JSONL 单遍扫描，与 bash 版一致）
# ============================================================

def _extract_metrics_for_tuning(today_str: str, yesterday_str: str
                                ) -> Dict[str, Optional[Dict[str, Any]]]:
    """找最近两天 report_type=scheduled 的记录，返回 {today, yesterday}。"""
    today_rec: Optional[Dict[str, Any]] = None
    yesterday_rec: Optional[Dict[str, Any]] = None
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("report_type") != "scheduled":
                    continue
                d = rec.get("date", "")
                if d == today_str and today_rec is None:
                    today_rec = rec
                if d == yesterday_str and yesterday_rec is None:
                    yesterday_rec = rec
                if today_rec is not None and yesterday_rec is not None:
                    break
    except FileNotFoundError:
        pass

    # fallback: 如果今天没 scheduled，取昨天当今天，yesterday=None
    if today_rec is None:
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("report_type") != "scheduled":
                        continue
                    if rec.get("date", "") == yesterday_str:
                        today_rec = rec
                        break
        except FileNotFoundError:
            pass
    return {"today": today_rec, "yesterday": yesterday_rec}


def _extract_metrics_before(rec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从飞轮 report 里只取 feedback 相关字段（metrics_before/after 用）。"""
    if not rec:
        return {}
    out: Dict[str, Any] = {}
    for k in FEEDBACK_KEYS:
        if k in rec and rec[k] is not None:
            out[k] = rec[k]
    return out


# ============================================================
# 4. 闭环核心：handle_pending_restart（验证上次调优生效）
# ============================================================

def _get_last_tune_any() -> Optional[Dict[str, Any]]:
    """取日志文件里 **最后一条非 dry_run** 记录（不管参数是谁）。
    用于 handle_pending_restart —— 一次只动一个参数，所以最后一条就是上次动的。"""
    last: Optional[Dict[str, Any]] = None
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
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
                last = rec
    except FileNotFoundError:
        pass
    return last


def verify_restart(tune_timestamp: str) -> bool:
    """对比 gateway 启动时间 vs 调优 timestamp。"""
    try:
        out, _, rc = _run_shell(
            'systemctl show hermes-gateway --property=ActiveEnterTimestamp',
            timeout=5,
        )
        if rc != 0:
            return False
        val = out.strip().split("=", 1)[-1].strip()
        if not val:
            return False
        try:
            # e.g. "Sun 2026-07-26 03:12:45 CST"
            gw_epoch = _dt.datetime.strptime(val, "%a %Y-%m-%d %H:%M:%S %Z").timestamp()
        except Exception:
            return False
        try:
            tune_epoch = _dt.datetime.fromisoformat(tune_timestamp).timestamp()
        except Exception:
            return False
        return gw_epoch > tune_epoch
    except Exception:
        return False


def update_log_entry(param_name: str, tune_date: str, new_status: str,
                     metrics_after: Optional[Dict[str, Any]] = None) -> None:
    """原子更新 JSONL 里该参数在 tune_date 的最后一条记录的 status / metrics_after。"""
    if not os.path.exists(LOG_FILE):
        return
    lines: List[str] = []
    last_match_idx: Optional[int] = None
    raw_lines: List[str] = []
    with open(LOG_FILE, "r", encoding="utf-8") as fl:
        for lin in fl:
            raw_lines.append(lin.rstrip("\n"))
    for i, lin in enumerate(raw_lines):
        if not lin.strip():
            lines.append(lin)
            continue
        try:
            rec = json.loads(lin)
            if rec.get("parameter") == param_name and rec.get("date") == tune_date:
                last_match_idx = i
                lines.append(lin)
            else:
                lines.append(lin)
        except Exception:
            lines.append(lin)
    if last_match_idx is None:
        return
    try:
        rec = json.loads(raw_lines[last_match_idx])
    except Exception:
        return
    rec["status"] = new_status
    if metrics_after is not None:
        rec["metrics_after"] = metrics_after
    lines[last_match_idx] = json.dumps(rec, ensure_ascii=False)
    _atomic_write_lines(LOG_FILE, lines)


def _parse_feedback(feedback_csv: str) -> List[Tuple[str, str]]:
    """把 kn_avg_score,router_empty_pct 解析成 [(name, direction)]，
    direction 是 'up_better' / 'down_better' / 'stable_ok'。"""
    out: List[Tuple[str, str]] = []
    for name in (s.strip() for s in feedback_csv.split(",") if s.strip()):
        if name in ("kn_avg_score",
                    # KN LLM Judge 反馈：都是越高越好
                    "kn_judge_relevant_rate",
                    "kn_judge_avg_relevance",
                    "kn_judge_sample_count"):
            out.append((name, "up_better"))
        elif name in ("router_empty_pct", "sag_merge_zero_pct"):
            out.append((name, "down_better"))
        elif name in ("sag_total_kept", "memory_hindsight_count",
                      "sag_on_pct", "sag_recall_count",
                      "skill_f1", "skill_active_count", "skill_used_count",
                      "skill_total_uses", "hindsight_count",
                      "memory_compress_count", "memory_hindsight_count"):
            # 产出/贡献类：稳定或向上不恶化就算改善
            out.append((name, "stable_ok"))
        elif name in ("token_exhaust_pct", "router_error_rate",
                      "error_count", "warning_count"):
            out.append((name, "down_better"))
        else:
            out.append((name, "stable_ok"))
    return out


def _is_metric_improved(name: str, direction: str, old_v: float, new_v: float) -> bool:
    if direction == "up_better":
        return new_v >= old_v
    if direction == "down_better":
        return new_v <= old_v
    # stable_ok: 变化 <10% 视为改善（不恶化就是好）
    if old_v > 0:
        return abs(new_v - old_v) / old_v < 0.1
    return True  # old_v 是 0，按没变化处理


def handle_pending_restart() -> bool:
    """main() 第一步调用。
    返回 True = 有 pending / 刚处理完生效的，本次 **跳过新调优**。
    返回 False = 没有未完成的，允许本次做新调优。"""
    last = _get_last_tune_any()
    if not last:
        return False
    status = last.get("status", "")
    if status != "pending_restart":
        # 上次不是 pending_restart；但如果是今天 applied 的，也走冷却
        if status == "applied" and last.get("date") == _today_cn() and COOLDOWN_DAYS_AFTER_APPLY >= 0:
            log_info(f"上次调优({last.get('parameter')})今天刚确认生效，进入冷却期，跳过新调优")
            return True
        return False

    # ------------ pending_restart 处理流程 ------------
    param = last.get("parameter", "")
    tune_date = last.get("date", "")
    old_v = last.get("old_value")
    new_v = last.get("new_value")
    last_dir = last.get("direction", "up")
    ts = last.get("timestamp", "")

    if verify_restart(ts):
        log_ok(f"上次调优已生效（gateway 已重启）: {param}")
        today_str = _today_cn()
        yesterday_str = _yesterday_cn()
        data = _extract_metrics_for_tuning(today_str, yesterday_str)
        today_rec = data.get("today") or {}
        metrics_after = _extract_metrics_before(today_rec) if isinstance(today_rec, dict) else {}

        if not metrics_after:
            # 当天报告还没出，保持 pending_restart，等下次再判
            log_warn("当天指标数据尚未生成，保持 pending_restart 状态")
            return True

        # 填 metrics_after 并更新 applied
        update_log_entry(param, tune_date, "applied", metrics_after)
        log_ok("已记录 metrics_after 并更新日志状态")

        # 判断改善 → 调 state
        mb = last.get("metrics_before") or {}
        # 找对应 param 的 feedback 定义
        pdef = next((p for p in PARAM_DEFS if p[0] == param), None)
        improved = True
        no_change = (old_v == new_v)
        if pdef:
            feeds = _parse_feedback(pdef[5])
            # Round 2 P0-B: 先判断今日 judge 样本可信度（所有主观反馈键前置过滤）
            judge_trusted, judge_sc = _kn_judge_trusted(metrics_after, mb)
            has_subjective_any = any(n in _KN_JUDGE_SUBJECTIVE_KEYS for n, _ in feeds)
            skipped_untrusted = False
            ic = 0
            tc = 0
            has_any = False
            for name, d in feeds:
                tc += 1
                # 如果 judge 样本不足，跳过其主观反馈键，避免小样本噪声驱动方向
                if name in _KN_JUDGE_SUBJECTIVE_KEYS and not judge_trusted:
                    skipped_untrusted = True
                    log_info(f"  忽略反馈 {name}: kn_judge_sample_count={judge_sc} < {_KN_JUDGE_MIN_SAMPLE}（评估样本不足，不纳入改善判定）")
                    continue
                om = mb.get(name)
                nm = metrics_after.get(name)
                if om is None or nm is None:
                    continue
                has_any = True
                try:
                    om_f = float(om); nm_f = float(nm)
                except (TypeError, ValueError):
                    continue
                if _is_metric_improved(name, d, om_f, nm_f):
                    ic += 1
            if not has_any:
                # Round 2 P0-B: 若「所有反馈键被 judge 样本不足过滤掉」，不默认改善，走未知避免推到边界
                if skipped_untrusted and has_subjective_any:
                    improved = None
                    log_warn("反馈缺失：所有可用反馈键均因 KN LLM Judge 样本不足被跳过 → 本次改善判定=未知（保持 pending_restart 状态直到样本充足）")
                else:
                    improved = True  # 原语义：完全无数据时默认改善
            else:
                improved = ic >= max(tc / 2, 1)

        # improved=None 时，handle_pending_restart 仍保留 pending_restart（不进 update_state）
        if improved is None:
            log_info(f"暂不确认上次调优（改善未知），下次继续观察；不进冷却，允许本次处理其它非 pending 任务（若有）")
            # 回滚刚刚更新 log status=applied 的动作 → 改回 pending_restart，保持状态机一致
            update_log_entry(param, tune_date, "pending_restart", None)
            log_info("已回滚日志状态为 pending_restart，等待下一次有足够 judge 样本再确认")
            return False  # 不再占冷却位，允许后续继续观察或调其它参数

        state = load_state()
        last_dir_osc = state.get(param, {}).get("last_direction")
        new_state = update_state(
            state, param, last_dir, new_v,
            metrics_improved=improved, no_change=no_change,
            tune_date=tune_date,
            old_value=old_v,
            last_direction_record=last_dir_osc,
        )
        save_state(new_state)
        log_ok(f"状态已更新: improved={improved}, no_change={no_change}")
        # 冷却：今天刚确认生效，跳过新调优
        log_info("本次仅处理调优生效，跳过新调优（冷却期）")
        return True
    else:
        log_warn(f"上次调优尚未生效（gateway 未重启）: {param}")
        notify_restart_reminder(last)
        return True


# ============================================================
# 5. 调参状态机（收敛/锁定/暂停/震荡检测）
# ============================================================

def _ensure_pstate(state: Dict[str, Any], param: str) -> Dict[str, Any]:
    if param not in state or not isinstance(state[param], dict):
        state[param] = {
            "no_change_count": 0,
            "degradation_count": 0,
            "consecutive_degradation_count": 0,
            "locked": False,
            "suspended": False,
            "last_tune_date": "",
            "initial_value": None,
            "last_direction": None,
            "direction_history": [],
        }
    return state[param]


def is_param_converged(state: Dict[str, Any], param_name: str) -> bool:
    p = state.get(param_name) or {}
    if p.get("locked"):
        return True
    if p.get("suspended"):
        return True
    if int(p.get("no_change_count", 0)) >= NO_CHANGE_LOCK_THRESHOLD:
        return True
    return False


def are_all_params_converged(state: Dict[str, Any]) -> bool:
    def _eligible(pdef):
        name = pdef[0]
        if _is_param_permanently_skipped(name):
            return True  # 死参数视为收敛（不阻塞「全部收敛 → 跳过」的语义）
        return is_param_converged(state, name)
    return all(_eligible(p) for p in PARAM_DEFS)


def update_state(
    state: Dict[str, Any],
    param_name: str,
    direction: str,
    new_value: Any,
    *,
    metrics_improved: bool,
    no_change: bool,
    tune_date: str,
    old_value: Any = None,
    last_direction_record: Optional[str] = None,
) -> Dict[str, Any]:
    """返回一个新 state（不原地改，方便调试）。"""
    import copy
    ns = copy.deepcopy(state)
    p = _ensure_pstate(ns, param_name)

    # ① initial_value：第一次写入时保存 **调优前的旧值**
    if p.get("initial_value") is None:
        if old_value is not None:
            try:
                p["initial_value"] = float(old_value)
            except (TypeError, ValueError):
                pass
        # 极端 fallback：调优前值拿不到，用 new_value 也比 None 好，但 old_value 优先
        if p.get("initial_value") is None:
            try:
                p["initial_value"] = float(new_value)
            except (TypeError, ValueError):
                pass

    # ② last_tune_date 存日期（bash版这里是BUG存了参数值，我们修掉）
    p["last_tune_date"] = tune_date

    # ③ 方向历史 + 震荡检测（上→下→上 或 下→上→下 视为来回抖动）
    #    震荡后给一个"振荡惩罚"，加速收敛，且后续计数不应该被 else 清零。
    #    direction="none"（到边界跳过）不污染方向历史，不参与震荡检测。
    osc_punish = 0
    if direction != "none":
        history = p.setdefault("direction_history", [])
        if last_direction_record is not None:
            if not history or history[-1] != last_direction_record:
                history.append(last_direction_record)
        history.append(direction)
        if len(history) > 3:
            history[:] = history[-3:]
        if len(history) >= 3:
            a, b, c = history[-3], history[-2], history[-1]
            if a != b and b != c and a == c:
                osc_punish = 2
                history.clear()

    # ④ no_change / 振荡惩罚计数
    # 注意：no_change=False 只清零自然计数，**不清振荡惩罚**（否则振荡惩罚白加了）
    cur_nc = int(p.get("no_change_count", 0))
    if osc_punish > 0:
        # 有振荡惩罚 → 按惩罚累加
        p["no_change_count"] = cur_nc + osc_punish
    elif no_change:
        p["no_change_count"] = cur_nc + 1
    # else: 普通有变化调用 → 不清零（保留之前的振荡惩罚累积），仅正常参数变化不计数

    if int(p.get("no_change_count", 0)) >= NO_CHANGE_LOCK_THRESHOLD:
        p["locked"] = True
        p["no_change_count"] = 0
        p["degradation_count"] = 0
        p["consecutive_degradation_count"] = 0

    # ⑤ 改善 / 恶化计数
    if not metrics_improved:
        p["degradation_count"] = int(p.get("degradation_count", 0)) + 1
        p["consecutive_degradation_count"] = int(p.get("consecutive_degradation_count", 0)) + 1
        if int(p["consecutive_degradation_count"]) >= CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD:
            # 连续恶化 → 回滚 initial_value + 暂停
            p["suspended"] = True
            p["locked"] = True
            p["consecutive_degradation_count"] = 0
    else:
        p["degradation_count"] = 0
        p["consecutive_degradation_count"] = 0
        # 改善时也顺带清 no_change，别因为震荡给的 2 次误锁了真改善
        if int(p.get("no_change_count", 0)) < NO_CHANGE_LOCK_THRESHOLD:
            pass  # 清不清都行，保留震荡惩罚是合理的

    # ⑥ 记录本次方向（下次震荡判断用）；"none"（到边界跳过）不覆盖 last_direction
    if direction != "none":
        p["last_direction"] = direction
    return ns


# ============================================================
# 6. 方向决策 + 参数选择
# ============================================================

def _get_last_tune_for(param_name: str) -> Optional[Dict[str, Any]]:
    last: Optional[Dict[str, Any]] = None
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("parameter") == param_name:
                    last = rec
    except FileNotFoundError:
        pass
    return last


def determine_direction(
    param_name: str,
    current_val: float,
    pmin: float, pmax: float, step: float,
    feedback_csv: str,
    last_tune: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """返回 {"direction":"up"/"down", "new_value": float, "reason": str}；无法调返回 None。"""
    direction = "up"
    reason = "初始调优，离最小值较近，向上调整"
    dist_min = current_val - pmin
    dist_max = pmax - current_val

    if last_tune and isinstance(last_tune, dict):
        last_dir = last_tune.get("direction", "up")
        mb = last_tune.get("metrics_before") or {}
        ma = last_tune.get("metrics_after") or {}
        status = last_tune.get("status", "")

        ic = 0
        tc = 0
        has_any = False
        # Round 2 P0-B: 先判断今日 judge 样本可信度（同 handle_pending_restart 逻辑保持一致）
        judge_trusted, judge_sc = _kn_judge_trusted(ma if ma else {}, mb if mb else {})
        feeds = _parse_feedback(feedback_csv)
        has_subjective_any = any(n in _KN_JUDGE_SUBJECTIVE_KEYS for n, _ in feeds)
        skipped_untrusted = False
        for name, d in feeds:
            tc += 1
            if name in _KN_JUDGE_SUBJECTIVE_KEYS and not judge_trusted:
                skipped_untrusted = True
                log_info(f"  方向决策忽略反馈 {name}: kn_judge_sample_count={judge_sc} < {_KN_JUDGE_MIN_SAMPLE}")
                continue
            om = mb.get(name)
            nm = ma.get(name)
            if om is None or nm is None:
                continue
            try:
                om_f = float(om); nm_f = float(nm)
            except (TypeError, ValueError):
                continue
            has_any = True
            if _is_metric_improved(name, d, om_f, nm_f):
                ic += 1
        # **关键修正**：pending_restart 的记录 metrics_after=None，has_any=False
        # 这种情况我们不瞎判断"改善"，保守当作第一次调优（走位置策略），避免连续同向把参数推到边界。
        if status == "pending_restart" and not has_any:
            improved = None  # 哨兵：未知，走首次位置策略
        elif not has_any:
            # Round 2 P0-B: 如果是因为「judge 样本不足导致所有反馈都过滤」，走 None 未知避免推边界
            if skipped_untrusted and has_subjective_any:
                improved = None
                log_warn(f"  {param_name}: KN LLM Judge 样本不足导致反馈全跳过 → 改善判定=未知，走首次位置策略（避免错误同向）")
            else:
                improved = True  # 无反馈数据，默认改善
        else:
            improved = ic >= max(tc / 2, 1)

        if improved is True:
            direction = last_dir
            reason = f"上次调优改善指标，同向({direction})"
        elif improved is False:
            direction = "down" if last_dir == "up" else "up"
            reason = f"上次调优未改善指标，反向({direction})"
        else:  # None → pending_restart 无 metrics_after，走首次策略
            if dist_max < dist_min:
                direction = "down"
                reason = "上次调优尚无反馈数据(待重启生效)，按位置策略：离最大值近，向下调整"
            else:
                direction = "up"
                reason = "上次调优尚无反馈数据(待重启生效)，按位置策略：离最小值近，向上调整"
    else:
        # 首次调优：位置策略
        if dist_max < dist_min:
            direction = "down"
            reason = "当前值离最大值较近，向下调整"
        else:
            direction = "up"
            reason = "当前值离最小值较近，向上调整"

    # 边界修正
    if direction == "up" and (current_val + step) > pmax + 1e-9:
        direction = "down"
        reason = f"已达上限({pmax})，只能向下调整"
    elif direction == "down" and (current_val - step) < pmin - 1e-9:
        direction = "up"
        reason = f"已达下限({pmin})，只能向上调整"

    new_val = min(current_val + step, pmax) if direction == "up" else max(current_val - step, pmin)
    if abs(new_val - current_val) < 1e-9:
        return None
    return {"direction": direction, "new_value": round(new_val, 4), "reason": reason}


def validate_step(old_val: float, new_val: float, step: float) -> bool:
    """步幅安全：整数步长(step>=1)不超过|step|个绝对值；浮点不超过20%变化。"""
    try:
        of, nf, sf = float(old_val), float(new_val), float(step)
    except (TypeError, ValueError):
        return False
    if of == 0.0:
        return True
    if sf >= 1.0:
        return abs(nf - of) <= sf + 1e-9
    change_pct = abs((nf - of) / of) * 100.0
    return change_pct <= 20.0 + 1e-9


def select_param_to_tune(state: Dict[str, Any]) -> Optional[Tuple[str, float, float, float, float, str]]:
    """Round 3 P1-D：选择调参顺序策略。

    优先级链：
    1) virgin（从未调过）先于 remaining（调过但未收敛）
    2) 在 virgin / remaining 内部，先挑「今日反馈可信」的参数：
       a) 参数 feedback 完全不依赖 kn_judge 主观键 → 任何时候都可信
       b) 参数依赖主观键，但今日 kn_judge_sample_count >= _KN_JUDGE_MIN_SAMPLE → 可信
       c) 其他（今日 judge 样本不足 + 参数依赖主观键）→ 作为未可信组，放可信组之后
    3) 组内保留 PARAM_DEFS 顺序（保持确定性，便于调试）
    """
    # 先读今日 summary（本地 JSONL 快速，daily 报告若还没出会 fallback 到昨天）
    today_str = _today_cn()
    yesterday_str = _yesterday_cn()
    rec_data = _extract_metrics_for_tuning(today_str, yesterday_str)
    today_rec = rec_data.get("today") if isinstance(rec_data.get("today"), dict) else {}
    yesterday_rec = rec_data.get("yesterday") if isinstance(rec_data.get("yesterday"), dict) else {}
    judge_trusted, judge_sc = _kn_judge_trusted(today_rec, yesterday_rec)

    def _feedback_trust_today(feedback_csv: str) -> bool:
        subjective_hits = False
        for name in (s.strip() for s in feedback_csv.split(",") if s.strip()):
            if name in _KN_JUDGE_SUBJECTIVE_KEYS:
                subjective_hits = True
                break
        if not subjective_hits:
            return True  # 不依赖主观评估键 → 任何时刻可信
        return judge_trusted

    virgin_confident: List[Tuple[str, float, float, float, float, str]] = []
    virgin_unconfident: List[Tuple[str, float, float, float, float, str]] = []
    remaining_confident: List[Tuple[str, float, float, float, float, str]] = []
    remaining_unconfident: List[Tuple[str, float, float, float, float, str]] = []

    for pdef in PARAM_DEFS:
        name = pdef[0]
        if _is_param_permanently_skipped(name):
            log_info(f"参数 {name} 永久跳过（KN_ENABLE_CAUSAL_CHAIN=false，因果链关闭，调了无效）")
            continue
        if is_param_converged(state, name):
            log_info(f"参数 {name} 已收敛，跳过")
            continue
        fb_csv = pdef[5]
        confident = _feedback_trust_today(fb_csv)
        pst = state.get(name) or {}
        if pst.get("initial_value") is None:
            (virgin_confident if confident else virgin_unconfident).append(pdef)
        else:
            (remaining_confident if confident else remaining_unconfident).append(pdef)

    def _pick(vc, vu, rc, ru):
        order = (("virgin-confident", vc), ("virgin-unconfident", vu),
                 ("remaining-confident", rc), ("remaining-unconfident", ru))
        for label, bucket in order:
            if bucket:
                first = bucket[0]
                log_info(f"  选参[{label}] → {first[0]}（今日 KN LLM Judge 样本={judge_sc}, trusted={judge_trusted}）")
                return first
        return None

    picked = _pick(virgin_confident, virgin_unconfident, remaining_confident, remaining_unconfident)
    if picked is None:
        log_info("所有候选均收敛/跳过，本次无参数可调")
    return picked


# ============================================================
# 7. 暂停机制（与 bash 版 check_pause 一致，支持 auto-tuner.pause JSON）
# ============================================================

def check_pause() -> bool:
    """返回 True = 应该暂停（跳过本次）。
    支持两种格式：
      - 纯文本（手动暂停）：直接暂停
      - JSON {"pause_until": "2026-08-15"}：过期后自动删除并恢复
    """
    if not os.path.exists(PAUSE_FILE):
        return False
    try:
        with open(PAUSE_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return True
    if not raw:
        return True
    pause_until: Optional[str] = None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            pause_until = obj.get("pause_until")
    except json.JSONDecodeError:
        # 不是 JSON → 纯文本手动暂停
        log_info(f"暂停文件存在({PAUSE_FILE})，原因: {raw[:80] or '手动暂停'}")
        log_info("跳过本次调优")
        return True
    if pause_until:
        try:
            today = _dt.date.fromisoformat(_today_cn())
            target = _dt.date.fromisoformat(pause_until)
        except Exception:
            return True
        if today >= target:
            try:
                os.remove(PAUSE_FILE)
            except OSError:
                pass
            return False  # 到期，恢复
        return True
    return True  # JSON 但没 pause_until 字段，视为手动暂停


# ============================================================
# 8. main() — 完整闭环
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="auto-tuner.py",
        description="飞轮参数自优化调优器（v2 闭环：pending_restart 验证 + 冷却期 + 正确改善判断）",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="只做决策，不改 .env、不发飞书；写入 dry_run 日志")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run: bool = args.dry_run

    today = _today_cn()
    print("")
    print("============================================")
    print(f"  Auto-Tuner 开始 — {today}{' (DRY-RUN)' if dry_run else ''}")
    print("============================================")
    print("")

    # 1. 暂停检测
    if check_pause():
        return 0

    # 2. 闭环 Step 0：处理上次 pending_restart / 冷却
    if handle_pending_restart():
        return 0

    # 3. 历史数据文件存在性
    if not os.path.exists(HISTORY_FILE):
        log_warn(f"历史数据文件不存在: {HISTORY_FILE}")
        log_info("跳过本次调优（首次运行需累积数据）")
        return 0

    # 4. 提取今天/昨天指标
    log_info(f"获取指标数据: {today} / {_yesterday_cn()}")
    mdata = _extract_metrics_for_tuning(today, _yesterday_cn())
    today_rec = mdata.get("today")
    if not isinstance(today_rec, dict) or not today_rec:
        log_warn("未找到今天的指标数据，跳过本次调优")
        return 0
    log_ok("已获取指标数据")

    # 5. 加载状态 + 收敛总检
    state = load_state()
    log_info("已加载调优状态")
    if are_all_params_converged(state):
        log_info("所有参数已收敛，跳过本次调优")
        log_info("30 天后重新评估（下次可手动清除 state 中 locked 标记提前恢复）")
        return 0

    # 6. 选参数 + 解析定义
    pdef = select_param_to_tune(state)
    if pdef is None:
        log_info("没有可调优的参数（都收敛/暂停了）")
        return 0
    name, default, pmin, pmax, step, fb_csv = pdef
    log_info(f"选中参数: {name}")
    log_info(f"  默认值: {default}, 范围: [{pmin}, {pmax}], 步长: {step}")
    log_info(f"  反馈指标: {fb_csv}")

    # 7. 当前值（优先 .env，默认值兜底）
    env_val = read_env_param(name)
    if env_val is not None:
        try:
            current = float(env_val)
        except (TypeError, ValueError):
            current = float(default)
    else:
        current = float(default)
    log_info(f"  当前值: {current}")

    # 8. 上次调优记录（同参数）
    last_tune = _get_last_tune_for(name)

    # 9. 方向决策
    decision = determine_direction(name, current, pmin, pmax, step, fb_csv, last_tune)
    if not decision:
        log_warn("无法确定调优方向（可能已到边界），跳过")
        # 同步 bash 版约束：到边界也调用 update_state(no_change=True)，
        # 让 no_change_count 累加，达到 NO_CHANGE_LOCK_THRESHOLD 后能锁定该参数，
        # 避免每次运行都选中已到边界的参数再跳过。
        ns = update_state(
            state, name, "none", current,
            metrics_improved=True,  # 未恶化，仅无法继续调
            no_change=True,
            tune_date=today,
            old_value=current,
            last_direction_record=(state.get(name) or {}).get("last_direction"),
        )
        save_state(ns)
        log_ok(f"已记录 no_change（边界跳过），状态已写回")
        return 0
    direction = decision["direction"]
    new_val = float(decision["new_value"])
    reason = decision["reason"]
    print("")
    log_step("调优决策:")
    print(f"  参数:     {name}")
    print(f"  当前值:   {current}")
    print(f"  新值:     {new_val}")
    print(f"  方向:     {direction}")
    print(f"  原因:     {reason}")
    print("")

    # 10. 步幅安全校验
    if not validate_step(current, new_val, step):
        log_warn("步幅超过安全阈值，跳过本次调优")
        return 0

    # 11. metrics_before（今天的报告，用于后续改善判断）
    metrics_before = _extract_metrics_before(today_rec)

    # 12. DRY-RUN 分支
    if dry_run:
        print("")
        log_info("================================================")
        log_info("  DRY-RUN 模式 — 以下操作不会实际执行")
        log_info("================================================")
        print(f"  参数:      {name}")
        print(f"  当前值:    {current} → 新值: {new_val}")
        print(f"  方向:      {direction}")
        print(f"  原因:      {reason}")
        print(f"  备份:      {BACKUP_DIR}/env-*.bak")
        print(f"  操作:      修改 {ENV_FILE} → {name}={new_val}")
        print(f"  操作:      手动重启 hermes-gateway（飞书通知）")
        print(f"  日志:      {LOG_FILE}")
        print("")
        entry = {
            "date": today,
            "parameter": name,
            "old_value": float(current),
            "new_value": float(new_val),
            "direction": direction,
            "reason": reason,
            "dry_run": True,
            "metrics_before": metrics_before,
            "metrics_after": None,
            "status": "dry_run",
            "timestamp": _now_iso(),
        }
        _append_jsonl(LOG_FILE, entry)
        log_ok("DRY-RUN 完成，决策已写入日志")
        return 0

    # ===== 实际执行 =====
    log_step(f"备份 {ENV_FILE}")
    backup_file = backup_env()
    log_ok(f"已备份到: {backup_file}")

    log_step(f"修改参数 {name}: {current} → {new_val}")
    ok = write_env_param(name, f"{new_val:g}" if isinstance(new_val, float) else f"{new_val}")
    if not ok:
        log_err(f"写入 .env 失败: {ENV_FILE}")
        return 2
    log_ok(".env 已更新")

    # 通知重启（不自动重启，避免杀死 cronjob）
    print("")
    notify_gateway_restart(name, current, new_val, reason, dry_run=False)

    # 记日志：pending_restart
    log_step("记录调优日志")
    entry = {
        "date": today,
        "parameter": name,
        "old_value": float(current),
        "new_value": float(new_val),
        "direction": direction,
        "reason": reason,
        "dry_run": False,
        "metrics_before": metrics_before,
        "metrics_after": None,
        "status": "pending_restart",
        "backup_file": backup_file,
        "timestamp": _now_iso(),
    }
    _append_jsonl(LOG_FILE, entry)

    # **一定调用 update_state 并写回**（bash 版这里漏了）
    ns = update_state(
        state, name, direction, new_val,
        metrics_improved=True,  # 刚调，还没生效，默认 true（不会触发恶化计数）
        no_change=False,
        tune_date=today,
        old_value=current,
    )
    save_state(ns)
    log_ok("调优状态已写回（pending_restart，等重启生效）")

    print("")
    print("============================================")
    print(f"  Auto-Tuner 完成 — {name}: {current} → {new_val}")
    print("============================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
