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
    PARAM_DEFS, FEEDBACK_KEYS, KN_JUDGE_CFG,
    NO_CHANGE_LOCK_THRESHOLD, CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD,
    COOLDOWN_DAYS_AFTER_APPLY,
    PARAM_GROUPS, GROUP_BY_ID, PARAM_TO_GROUP,
    GROUP_TUNING_ENABLED, MAX_GROUPS_PER_RUN,
    RECALL_GUARDS,
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


# KN LLM Judge 评估样本阈值（与 kn_judge.py KN_JUDGE_CFG 对齐，由配置驱动）
_KN_JUDGE_MIN_SAMPLE = int(KN_JUDGE_CFG.get("min_sample", 20))
_KN_JUDGE_MASK_MIN_SAMPLE = int(KN_JUDGE_CFG.get("mask_min_sample", 12))

# #5 严重度快车道阈值：mask 路 relevant_rate < 此值视为强负信号，优先调优。
# relevant_rate = 该路 judged 中评分>=0.5 的占比，<0.5 即多数不相关。
SEVERITY_FLOOR = 0.5
# 首次调优用更大步幅快速探明梯度方向（粗→细搜索的第一步）
COARSE_STEP_FACTOR = 2.0

# 参数重命名迁移（state 历史补全）：旧参数名 → 当前 PARAM_DEFS 名称。
# 来源：commit e3816e7 修正 PARAM_DEFS env 名（sag_* → KN_SAG_*）。
# state.json 里以旧名累积的 degradation_count / direction_history / best_value
# 等学习历史，加载时统一迁移到新名，避免「查不到旧键 → 清空历史当 virgin 重头调」。
_STATE_ALIASES = {
    "sag_max_inject": "KN_SAG_MAX_INJECT",
    "sag_search_top_k": "KN_SAG_SEARCH_TOP_K",
    "sag_search_threshold": "KN_SAG_MIN_SCORE",
}

# 已从参数池移除的参数（token 预算类，产品决策下线），其 state 历史无意义，迁移时丢弃。
_DROPPED_STATE_KEYS = frozenset({
    "token_budget",
    "token_budget_hindsight_ratio",
    "KN_TOKEN_BUDGET_TOTAL",
    "KN_TOKEN_BUDGET_HINDSIGHT_RATIO",
    "KN_TOKEN_BUDGET_KT_RATIO",
})


# 迁移时要合并到新名的「学习历史」字段（动态锁定/暂停等不合并，避免用旧名残留状态误锁新名）
_MIGRATE_FIELDS = (
    "initial_value", "best_value",
    "degradation_count", "consecutive_degradation_count",
    "no_change_count", "direction_history", "last_direction",
)


def _migrate_state(state: Dict[str, Any]) -> bool:
    """把旧参数名的 state 历史迁移到当前名；丢弃已下线参数。返回是否发生变更。

    规则：
      - 旧名存在、新名不存在 → 直接改名（历史整体保留）。
      - 旧名存在、新名也存在 → 若新名仍是无学习历史（virgin），把旧名学习历史
        合并进来；否则新名以在用为准，丢弃旧名残留。无论哪种都删掉旧名键。
      - 已下线参数键（token 预算类）→ 直接丢弃。
    """
    changed = False

    def _is_virgin(p: Dict[str, Any]) -> bool:
        return not any(
            p.get(f) is not None and p.get(f) not in ("", [], 0)
            for f in _MIGRATE_FIELDS
        )

    for old, new in _STATE_ALIASES.items():
        if old not in state:
            continue
        old_state = state[old]
        if not isinstance(old_state, dict):
            state.pop(old)
            changed = True
            continue
        if new not in state:
            # 新名不存在 → 直接改名，历史整体迁移
            state[new] = old_state
        elif _is_virgin(state[new]):
            # 新名是 virgin（从没调过）→ 把旧名学习历史合并进新名
            ns = state[new]
            for f in _MIGRATE_FIELDS:
                if f in old_state and ns.get(f) in (None, "", [], 0):
                    ns[f] = old_state[f]
        # 新名已有学习历史 → 新名在用为准，旧名残留直接丢弃
        state.pop(old)
        changed = True
    for k in list(state.keys()):
        if k in _DROPPED_STATE_KEYS:
            state.pop(k)
            changed = True
    return changed

# 所有 KN Judge 主观反馈键（全局 + mask 级），这些键需要样本量可信门控
_KN_JUDGE_SUBJECTIVE_KEYS = frozenset({
    "kn_judge_relevant_rate", "kn_judge_avg_relevance",
    "kn_judge_relevant_rate_h", "kn_judge_avg_relevance_h",
    "kn_judge_relevant_rate_kt", "kn_judge_avg_relevance_kt",
    "kn_judge_relevant_rate_sag", "kn_judge_avg_relevance_sag",
})


def _feedback_key_trusted(name: str,
                         rec_primary: Optional[Dict[str, Any]],
                         rec_secondary: Optional[Dict[str, Any]] = None) -> bool:
    """单个反馈键是否可信：检查其对应样本量是否达标。

    - 全局键(kn_judge_relevant_rate / avg_relevance) → kn_judge_sample_count >= min_sample
    - mask 键(*_h / *_kt / *_sag) → kn_judge_sample_count_<short> >= mask_min_sample
    传入 before + after 两份记录时，任一方不足即视为不可信（避免跨日噪声驱动调优）。
    """
    short = None
    for s in ("_h", "_kt", "_sag"):
        if name.endswith(s):
            short = s[1:]
            break
    if short:
        sample_keys = [f"kn_judge_sample_count_{short}"]
        threshold = _KN_JUDGE_MASK_MIN_SAMPLE
    else:
        sample_keys = ["kn_judge_sample_count"]
        threshold = _KN_JUDGE_MIN_SAMPLE
    counts: List[int] = []
    for r in (rec_primary, rec_secondary):
        if not r:
            continue
        for k in sample_keys:
            c = r.get(k)
            if c is None:
                continue
            try:
                counts.append(int(c))
            except (TypeError, ValueError):
                pass
    if not counts:
        return False
    return min(counts) >= threshold


def _param_judge_trusted(feedback_csv: str,
                        rec_primary: Optional[Dict[str, Any]],
                        rec_secondary: Optional[Dict[str, Any]] = None) -> bool:
    """参数级别的 judge 可信度：只要其任一主观键有样本即视为可信（有某路信号就能调）。"""
    keys = [n for n in (s.strip() for s in feedback_csv.split(",") if s.strip())]
    subj = [k for k in keys if k in _KN_JUDGE_SUBJECTIVE_KEYS]
    if not subj:
        return True
    return any(_feedback_key_trusted(k, rec_primary, rec_secondary) for k in subj)


def _kn_judge_trusted(rec_primary: Dict[str, Any],
                      rec_secondary: Optional[Dict[str, Any]] = None,
                      feedback_keys: Optional[List[str]] = None) -> Tuple[bool, int]:
    """兼容旧调用 + 支持按反馈键集合判定。

    - 给定 feedback_keys 时：任一主观键可信即视为可信（只要有某路信号就能调）；
    - 未给定时：退化为全局样本量检查。
    返回 (is_trusted, actual_min_sample_count)。
    """
    if feedback_keys:
        subj = [k for k in feedback_keys if k in _KN_JUDGE_SUBJECTIVE_KEYS]
        if not subj:
            return True, 0
        trusted = _param_judge_trusted(",".join(subj), rec_primary, rec_secondary)
        return trusted, 0
    sc_list: List[int] = []
    for r in (rec_primary, rec_secondary):
        if not r:
            continue
        sc = r.get("kn_judge_sample_count")
        if sc is None:
            continue
        try:
            sc_list.append(int(sc))
        except (TypeError, ValueError):
            pass
    if not sc_list:
        return False, 0
    sc = min(sc_list)
    return (sc >= _KN_JUDGE_MIN_SAMPLE), sc


# ============================================================
# 2. 工具函数：日期 / 原子写入 / JSONL / .env
# ============================================================

def _report_date_today() -> str:
    """返回与 daily-summary 'date' 字段一致的日期。

    飞轮报告在 CN 08:00 (= UTC 00:00) 生成，data_window = UTC 昨天。
    daily-summary 的 date 字段 = data_window。
    所以调参器用相同的日期基准，才能匹配到 daily-summary 记录。
    """
    try:
        utc_now = _dt.datetime.now(_dt.timezone.utc)
        # data_window = UTC 昨天（匹配 report.py generate_report L92）
        return (utc_now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return _dt.date.today().strftime("%Y-%m-%d")


def _report_date_yesterday() -> str:
    """返回与 daily-summary data_window_prev 一致的日期。"""
    try:
        utc_now = _dt.datetime.now(_dt.timezone.utc)
        return (utc_now - _dt.timedelta(days=2)).strftime("%Y-%m-%d")
    except Exception:
        return (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


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
    state: Dict[str, Any] = {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    # 加载时做一次 state 命名迁移（旧参数名 → 新参数名）：把旧名累积的学习历史
    # 补到当前 PARAM_DEFS 名称下，并丢弃已下线参数。迁移有变更则写回磁盘，
    # 否则下次加载仍会重复迁移（幂等，无副作用）。
    if _migrate_state(state):
        save_state(state)
        log_info("已完成 state 命名迁移（旧参数名历史已补全到新参数名）")
    return state


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


def rollback_param_to_baseline(param_name: str, baseline_value: Any) -> bool:
    """把 .env 中的 param_name 还原为首次调优前的基线值 initial_value。

    在参数因「连续恶化」被 suspend 时调用：suspend 只是停止继续调这个参数，
    如果不把 .env 改回去，运行时会一直停留在最后一次（最差的）取值上。

    返回 True 表示 .env 现在处于 baseline（含「本来就等于 baseline」的情况）。
    绝不抛异常——回滚失败不能阻断 auto-tuner 的状态保存。
    """
    if baseline_value is None:
        log_warn(f"回滚跳过：{param_name} 无 initial_value 基线（状态文件里从未记录）")
        return False
    try:
        target_f = float(baseline_value)
    except (TypeError, ValueError):
        log_warn(f"回滚跳过：{param_name} 的 initial_value={baseline_value!r} 不是合法数值")
        return False
    target = f"{target_f:g}"

    current = read_env_param(param_name)
    if current is not None:
        try:
            if abs(float(current) - target_f) < 1e-9:
                log_info(f"回滚跳过：{param_name} 当前已是基线值 {target}")
                return True
        except (TypeError, ValueError):
            pass  # 当前值不可解析 → 照常覆写为基线

    try:
        bak = backup_env()
        log_info(f"回滚前已备份 .env → {bak}")
    except (OSError, PermissionError, shutil.Error) as e:
        log_warn(f"回滚前备份 .env 失败（{e}），仍继续尝试写入")

    if not write_env_param(param_name, target):
        log_err(f"回滚失败：无法写入 {param_name}={target} 到 {ENV_FILE}")
        return False

    log_ok(f"已回滚 {param_name}: {current} → {target}（连续恶化触发暂停）")
    return True


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
    # 注意：此时 today_rec 与 yesterday_rec 指向同一条记录，
    # 后续 P4 _metrics_unchanged 会检测到 metrics_after==metrics_before 并判为 unknown。
    if today_rec is None:
        log_info("今日 scheduled 报告未找到，fallback 取昨日数据作为 today（metrics_before==metrics_after 风险由 P4 _metrics_unchanged 兜底）")
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
    """对比 gateway 启动时间 vs 调优 timestamp。

    手动解析 systemctl 输出中的 CST 为 +0800（避免 strptime %Z 跨平台歧义）。
    """
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
            # e.g. "Sun 2026-07-26 03:12:45 CST" → split 得 4 段
            parts = val.split()
            if len(parts) < 3:
                return False
            date_part = parts[1]  # 2026-07-26
            time_part = parts[2]  # 03:12:45
            tz_part = parts[3] if len(parts) >= 4 else ""
            # CST = China Standard Time = UTC+8
            tz_offset = _dt.timedelta(hours=8) if tz_part == "CST" else _dt.timedelta(hours=0)
            if tz_part and tz_part != "CST":
                # 尝试用 strptime 兜底（非 CST 时区）
                try:
                    gw_epoch = _dt.datetime.strptime(val, "%a %Y-%m-%d %H:%M:%S %Z").timestamp()
                except Exception:
                    return False
            else:
                dt_naive = _dt.datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
                gw_dt = dt_naive.replace(tzinfo=_dt.timezone(tz_offset))
                gw_epoch = gw_dt.timestamp()
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
    else:
        # 显式传 None → 清除 metrics_after（improved=None 回滚时避免残留旧值误导后续判定）
        rec.pop("metrics_after", None)
    lines[last_match_idx] = json.dumps(rec, ensure_ascii=False)
    _atomic_write_lines(LOG_FILE, lines)


def _parse_feedback(feedback_csv: str) -> List[Tuple[str, str]]:
    """把 kn_avg_score,router_empty_pct 解析成 [(name, direction)]，
    direction 是 'up_better' / 'down_better' / 'stable_ok'。"""
    out: List[Tuple[str, str]] = []
    for name in (s.strip() for s in feedback_csv.split(",") if s.strip()):
        # KN LLM Judge 反馈（全局 + mask 级）：越高越好
        if (name.startswith("kn_judge_relevant_rate")
                or name.startswith("kn_judge_avg_relevance")
                or name.startswith("kn_judge_sample_count")):
            out.append((name, "up_better"))
            continue
        if name in ("kn_avg_score",):
            out.append((name, "up_better"))
        elif name in ("router_empty_pct", "sag_merge_zero_pct", "kt_orphan_pct",
                    "kt_fragment_domains", "kt_candidate_noise_rate",
                    "kt_over_split_rate", "kt_low_conf_kp_rate",
                    "kt_pending_conflict_rate"):
            out.append((name, "down_better"))
        elif name in ("se_reflection_accept_rate", "se_reflection_mean_confidence",
                      "se_recombine_synergy_avg"):
            out.append((name, "up_better"))
        elif name in ("sag_total_kept", "memory_hindsight_count",
                      "sag_on_pct", "sag_recall_count",
                      "skill_f1", "skill_active_count", "skill_used_count",
                      "skill_total_uses", "hindsight_count",
                      "memory_compress_count", "memory_hindsight_count"):
            # 产出/贡献类：稳定或向上不恶化就算改善
            out.append((name, "stable_ok"))
        elif name in ("router_error_rate",
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


def _metrics_unchanged(mb: Dict[str, Any], ma: Dict[str, Any]) -> bool:
    """判断 metrics_after 是否与 metrics_before 完全一致（重启后指标无任何变化）。

    当 due to 日期错配 / gateway 未实际重启 / 报告未更新时，ma 与 mb 指向同一条记录，
    导致所有 _is_metric_improved 都返回 True（同值=stable 改善），direction 永远同向，
    degradation_count 永远不会增长。此哨兵用于把这种情况判为「未知」而非「改善」。
    """
    if not mb or not ma:
        return False
    # 只比较两字典都存在的数值键
    keys = [k for k in mb if k in ma and isinstance(mb[k], (int, float)) and isinstance(ma[k], (int, float))]
    if not keys:
        return False
    return all(abs(float(mb[k]) - float(ma[k])) < 1e-9 for k in keys)


def _get_all_pending_tunes() -> List[Dict[str, Any]]:
    """返回日志里全部非 dry_run 且 status==pending_restart 的记录。

    分组并行下每轮会写多条 pending 记录（每组一条），必须全部遍历回填
    metrics_after，否则只有最后一条能被旧逻辑处理（见 _get_last_tune_any 只取最后一条）。"""
    out: List[Dict[str, Any]] = []
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
                if rec.get("status") == "pending_restart":
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


def _compute_improvement(rec: Dict[str, Any], metrics_after: Dict[str, Any]) -> Optional[bool]:
    """判定一条调优记录（单参或组）生效后的改善情况。

    返回 True=改善 / False=恶化 / None=未知（反馈缺失或样本不可信）。
    - group 记录：用 GROUP_BY_ID[gid].feedback_keys() 取反馈键并集；
    - 单参记录：用 PARAM_DEFS 里该参数 feedback_csv。
    旧逻辑只用 last.get("parameter") 查 PARAM_DEFS，对 group 记录（parameter=gid
    不在 PARAM_DEFS）会查不到 → 永远判为「改善」，这是分组并行退化的根因之一。"""
    mb = rec.get("metrics_before") or {}
    gid = rec.get("group")
    feeds: List[Tuple[str, str]] = []
    if gid and gid in GROUP_BY_ID:
        g = GROUP_BY_ID[gid]
        for k in g.feedback_keys():
            d = _feedback_dir(k)
            if d:
                feeds.append((k, d))
    else:
        pdef = next((p for p in PARAM_DEFS if p[0] == rec.get("parameter", "")), None)
        if pdef:
            feeds = _parse_feedback(pdef[5])
    # P4（重大修复）：metrics_after 与 metrics_before 完全一致 → 重启后指标无任何变化。
    # 这通常意味着「gateway 未实际重启」或「日期错配指向同一报告」，
    # 此时同值会让所有 _is_metric_improved 返回 True（同值=stable 改善），
    # 使 direction 永远同向、degradation_count 永远不增长。判为「未知」而非「改善」。
    if _metrics_unchanged(mb, metrics_after):
        return None
    has_subjective_any = any(n in _KN_JUDGE_SUBJECTIVE_KEYS for n, _ in feeds)
    skipped_untrusted = False
    ic = tc = 0
    has_any = False
    for name, d in feeds:
        tc += 1
        # 对应路 judge 样本不足 → 跳过该主观反馈键，避免小样本噪声驱动方向
        if name in _KN_JUDGE_SUBJECTIVE_KEYS and not _feedback_key_trusted(name, mb, metrics_after):
            skipped_untrusted = True
            continue
        om = mb.get(name)
        nm = metrics_after.get(name)
        if om is None or nm is None:
            continue
        has_any = True
        try:
            om_f, nm_f = float(om), float(nm)
        except (TypeError, ValueError):
            continue
        if _is_metric_improved(name, d, om_f, nm_f):
            ic += 1
    if not has_any:
        # 修复：无反馈数据时返回 None（未知）而非 True（改善），
        # 避免零反馈组被判为改善 → degradation_count 永不增长 → 永不回滚。
        return None
    return ic >= max(tc / 2, 1)


def _apply_tune_result_to_state(state: Dict[str, Any], rec: Dict[str, Any],
                               improved: bool) -> Dict[str, Any]:
    """把一条已确认生效（applied + 改善判定）的调优结果写回 state（含连续恶化回滚）。

    - group 记录：逐成员更新 state[member]（复用 update_state 的收敛/锁/恶化逻辑），
      并同步组级 gstate（no_change_count / last_values），保证退化能被检测与回滚。
    - 单参记录：等价于原逻辑，写回 state[parameter]。"""
    gid = rec.get("group")
    if gid and gid in GROUP_BY_ID:
        g = GROUP_BY_ID[gid]
        old_values = rec.get("old_values") or {}
        new_values = rec.get("new_values") or {}
        gstate = _ensure_gstate(state, gid)
        for m in g.members:
            if m not in new_values:
                continue
            old_v = old_values.get(m)
            new_v = new_values[m]
            last_dir = "up" if (new_v > old_v if old_v is not None else False) else "down"
            prev_suspended = bool((state.get(m) or {}).get("suspended", False))
            state = update_state(
                state, m, last_dir, new_v,
                metrics_improved=improved, no_change=(old_v == new_v),
                tune_date=rec.get("date", ""), old_value=old_v,
                last_direction_record=(state.get(m) or {}).get("last_direction"),
            )
            now_suspended = bool((state.get(m) or {}).get("suspended", False))
            if now_suspended and not prev_suspended:
                pst = state.setdefault(m, {})
                baseline = pst.get("best_value")
                if baseline is None:
                    baseline = pst.get("initial_value")
                rollback_ok = rollback_param_to_baseline(m, baseline)
                pst["rolled_back_at"] = _now_iso()
                pst["rolled_back_to"] = baseline
                pst["rollback_ok"] = rollback_ok
        # 组级记账
        if improved:
            gstate["no_change_count"] = 0
        else:
            gstate["no_change_count"] = int(gstate.get("no_change_count", 0)) + 1
        gstate["last_direction"] = "up" if improved else "down"
        gstate["last_tune_date"] = rec.get("date", "")
        for m in g.members:
            if m in old_values:
                gstate["last_values"][m] = old_values[m]
        return state
    # 单参
    param = rec.get("parameter", "")
    old_v = rec.get("old_value")
    new_v = rec.get("new_value")
    last_dir = rec.get("direction", "up")
    tune_date = rec.get("date", "")
    last_dir_osc = (state.get(param) or {}).get("last_direction")
    prev_suspended = bool((state.get(param) or {}).get("suspended", False))
    new_state = update_state(
        state, param, last_dir, new_v,
        metrics_improved=improved, no_change=(old_v == new_v),
        tune_date=tune_date, old_value=old_v,
        last_direction_record=last_dir_osc,
    )
    # 连续恶化触发 suspend 的「上升沿」→ 把 .env 还原到 initial_value 基线。
    # 用上升沿（False→True）而非 now_suspended 判定，保证只回滚一次。
    now_suspended = bool((new_state.get(param) or {}).get("suspended", False))
    if now_suspended and not prev_suspended:
        pst = new_state.setdefault(param, {})
        baseline = pst.get("best_value")
        if baseline is None:
            baseline = pst.get("initial_value")
        rollback_ok = rollback_param_to_baseline(param, baseline)
        pst["rolled_back_at"] = _now_iso()
        pst["rolled_back_to"] = baseline
        pst["rollback_ok"] = rollback_ok
    return new_state


def handle_pending_restart() -> bool:
    """main() 第一步调用。

    新模型（分组并行）：遍历日志里**全部** status==pending_restart 的记录，
    逐条验证 gateway 是否已重启 + 报告日期是否已推进到调优日之后，
    是则回填 metrics_after 并判定改善、写回 state。legacy 单参模型每轮只写一条
    pending，等价于只处理最后一条，行为不变。

    返回 True = 本轮跳过新调优（仍有未验证的 pending，或本轮已确认生效进入冷却）。
    返回 False = 没有未完成的 pending，允许本次做新调优。"""
    pending = _get_all_pending_tunes()
    if not pending:
        # 没有 pending：但若点位是今天刚 applied，仍走冷却
        last = _get_last_tune_any()
        if last and last.get("status") == "applied" and \
                last.get("date") == _report_date_today() and COOLDOWN_DAYS_AFTER_APPLY >= 0:
            log_info(f"上次调优({last.get('parameter')})今天刚确认生效，进入冷却期，跳过新调优")
            return True
        return False

    # 计算「调优后一日」报告（所有 pending 共享同一份最新报告）
    today_str = _report_date_today()
    yesterday_str = _report_date_yesterday()
    data = _extract_metrics_for_tuning(today_str, yesterday_str)
    today_rec = data.get("today") or {}
    today_date = str(today_rec.get("date", "")) if isinstance(today_rec, dict) else ""
    metrics_after = _extract_metrics_before(today_rec) if isinstance(today_rec, dict) else {}

    state = load_state()
    need_wait = False       # 有 pending 尚未可验证（gateway 未重启 / 报告未推进 / 指标未生成）
    resolved = False        # 本轮已确认生效（applied）的条数 > 0
    for rec in pending:
        param = rec.get("parameter", "")
        tune_date = rec.get("date", "")
        ts = rec.get("timestamp", "")
        if not verify_restart(ts):
            log_warn(f"上次调优尚未生效（gateway 未重启）: {param}")
            notify_restart_reminder(rec)
            need_wait = True
            continue
        # 严格取「调优后一日」报告：日期未推进到 tune_date 之后 → 保持 pending
        if not today_date or today_date <= tune_date:
            log_warn(f"metrics_after 需严格取调优后一日：当前报告日期({today_date or '无'})未推进到调优日({tune_date})之后，保持 pending_restart")
            need_wait = True
            continue
        if not metrics_after:
            log_warn("当天指标数据尚未生成，保持 pending_restart 状态")
            need_wait = True
            continue
        # 已验证（gateway 已重启 + 报告已推进）→ 判定改善
        improved = _compute_improvement(rec, metrics_after)
        if improved is None:
            # 已验证但改善未知（metrics_unchanged / 样本不足）→ 保持 pending 观察，
            # 但不阻塞新调优（避免无效应参数永久卡死整轮）
            update_log_entry(param, tune_date, "pending_restart", None)
            continue
        update_log_entry(param, tune_date, "applied", metrics_after)
        state = _apply_tune_result_to_state(state, rec, improved)
        resolved = True
        log_ok(f"已确认上次调优生效并写回 state: {param} (improved={improved})")

    save_state(state)
    if need_wait:
        log_info("仍有 pending_restart 记录未验证（gateway 未重启 / 报告未推进），本轮跳过新调优")
        return True
    if resolved:
        log_info("本轮已确认部分 pending_restart 生效，进入冷却期，跳过新调优")
        return True
    # 全部已验证但改善未知，且无待验证项 → 允许本轮新调优
    return False


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
        p["suspended"] = False  # 稳定（无变化）→ 解除之前的恶化暂停
        p["no_change_count"] = 0
        p["degradation_count"] = 0
        p["consecutive_degradation_count"] = 0

    # ⑤ 改善 / 恶化计数
    if not metrics_improved:
        p["degradation_count"] = int(p.get("degradation_count", 0)) + 1
        p["consecutive_degradation_count"] = int(p.get("consecutive_degradation_count", 0)) + 1
        if int(p["consecutive_degradation_count"]) >= CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD:
            # 连续恶化 → 暂停该参数。
            # 注意：本函数是纯状态计算，**不碰 .env**。实际把 .env 还原到
            # initial_value 的动作由调用点 handle_pending_restart() 在检测到
            # suspended 的 False→True 上升沿时调用 rollback_param_to_baseline() 完成。
            p["suspended"] = True
            p["locked"] = True
            p["consecutive_degradation_count"] = 0
    else:
        p["degradation_count"] = 0
        p["consecutive_degradation_count"] = 0
        p["suspended"] = False  # 指标改善 → 解除恶化暂停
        # 改善时记录历史最佳值（best-so-far 记忆），连续恶化时优先回滚到此而非初始基线
        try:
            p["best_value"] = float(new_value)
        except (TypeError, ValueError):
            pass
        # 指标改善即清零锁定计数器：与恶化时(p["no_change_count"] = 0)对称，
        # 避免此前震荡/无变化累加的惩罚误锁住一次真实改善后的后续调参。
        p["no_change_count"] = 0

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
                if rec.get("dry_run"):
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
    summary_rec: Optional[Dict[str, Any]] = None,
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
        # mask 级改造：逐反馈键检查对应路样本量是否可信（全局键查全局样本，mask 键查该路样本）
        feeds = _parse_feedback(feedback_csv)
        has_subjective_any = any(n in _KN_JUDGE_SUBJECTIVE_KEYS for n, _ in feeds)
        skipped_untrusted = False
        for name, d in feeds:
            tc += 1
            # 信任门控须从「当日完整 summary 记录」取样本计数键
            # （kn_judge_sample_count_<short> 只存在于 summary 全量记录，不在
            #  tune 日志的 metrics_before/after 里）。若只用 mb/ma 会导致永远查不到
            #  样本量 → 永远判不可信 → mask 反馈被跳过 → 退化成位置游走（伪优化器）。
            trust_src = summary_rec if isinstance(summary_rec, dict) else None
            if name in _KN_JUDGE_SUBJECTIVE_KEYS and not _feedback_key_trusted(name, trust_src, None):
                skipped_untrusted = True
                log_info(f"  方向决策忽略反馈 {name}: 对应路 KN Judge 样本不足，跳过")
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
        elif _metrics_unchanged(mb, ma):
            # P4：metrics_after == metrics_before → 重启后无变化，判未知
            improved = None
            log_info(f"  {param_name}: metrics_after == metrics_before（重启后指标无变化），改善判定=未知，走首次位置策略")
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

    # 粗→细搜索：首次调优（无历史）用更大步幅快速探明梯度方向，后续用正常步幅精调
    eff_step = step
    if last_tune is None:
        eff_step = step * COARSE_STEP_FACTOR
        reason += f"（首次调优用粗步幅 {eff_step:g} 探方向）"

    # 边界修正
    if direction == "up" and (current_val + eff_step) > pmax + 1e-9:
        direction = "down"
        reason = f"已达上限({pmax})，只能向下调整"
    elif direction == "down" and (current_val - eff_step) < pmin - 1e-9:
        direction = "up"
        reason = f"已达下限({pmin})，只能向上调整"

    new_val = min(current_val + eff_step, pmax) if direction == "up" else max(current_val - eff_step, pmin)
    if abs(new_val - current_val) < 1e-9:
        return None
    # 返回实际生效步幅 eff_step，供 validate_step 用同一步幅校验，
    # 避免粗步幅(step*2)被原始 step 校验误拦截（首次调优必被跳过的问题）。
    return {"direction": direction, "new_value": round(new_val, 4), "reason": reason,
            "eff_step": round(eff_step, 4)}


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


# ============================================================
# 6.5 分组并行调优（功能组 · 每组独立策略）
# ============================================================
# 取代旧「每次只动一个参数」模型：auto-tuner 按 *组* 并行调优，每轮把全部
# 「反馈可信且未收敛」的组都调了；组内耦合参数按该组的策略 *一起* 移动。
# 旧的单参函数（determine_direction / select_param_to_tune）保留，作为 single 策略与回退路径。

def _current_env(pdef: Tuple[str, float, float, float, float, str]) -> float:
    """读 .env 当前值，取不到回退默认。"""
    v = read_env_param(pdef[0])
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return float(pdef[1])


def _fmt_env(name: str, val: float) -> str:
    pdef = next((p for p in PARAM_DEFS if p[0] == name), None)
    if pdef and float(pdef[4]) >= 1 and float(pdef[4]).is_integer():
        return f"{int(round(float(val))):d}"
    return f"{float(val):g}"


def _reverse(d: Optional[str]) -> Optional[str]:
    if d == "up":
        return "down"
    if d == "down":
        return "up"
    return None


def _feedback_dir(name: str) -> Optional[str]:
    for n, d in _parse_feedback(name):
        if n == name:
            return d
    return None


def _member_beneficial_direction(pdef: Tuple[str, float, float, float, float, str]) -> Optional[str]:
    """成员参数「有利方向」：其反馈键多数投票（忽略 stable_ok）。平票/未知返回 None。"""
    ups = downs = 0
    for _n, d in _parse_feedback(pdef[5]):
        if d == "up_better":
            ups += 1
        elif d == "down_better":
            downs += 1
    if ups > downs:
        return "up"
    if downs > ups:
        return "down"
    return None


def _position_dir(cur: float, pdef: Tuple[str, float, float, float, float, str]) -> str:
    """以参数区间中点为基准的位置方向（用于未知/平票时的探索）。"""
    pmin, pmax = pdef[2], pdef[3]
    return "down" if cur > (pmin + pmax) / 2 else "up"


def _step_param(cur: float, direction: Optional[str],
               pdef: Tuple[str, float, float, float, float, str],
               coarse: bool) -> Optional[float]:
    """按方向步进，受 [pmin,pmax] 夹取；到边界返回 None。整数步长结果取整。"""
    if direction not in ("up", "down"):
        return None
    pmin, pmax, step = pdef[2], pdef[3], pdef[4]
    eff = step * COARSE_STEP_FACTOR if coarse else step
    nv = min(cur + eff, pmax) if direction == "up" else max(cur - eff, pmin)
    if abs(nv - cur) < 1e-9:
        return None
    if float(step) >= 1 and float(step).is_integer():
        nv = int(round(nv))
    return round(float(nv), 4)


def _ensure_gstate(state: Dict[str, Any], gid: str) -> Dict[str, Any]:
    groups = state.setdefault("groups", {})
    if gid not in groups or not isinstance(groups[gid], dict):
        groups[gid] = {
            "last_direction": None,
            "no_change_count": 0,
            "consecutive_degradation_count": 0,
            "locked": False,
            "suspended": False,
            "last_tune_date": "",
            "initial_values": {},
            "last_values": {},
            "best_values": {},
        }
    return groups[gid]


def _group_enabled(g) -> bool:
    if g.enabled_when:
        raw = read_env_param(g.enabled_when)
        if raw is None:
            if g.enabled_when == "KN_ENABLE_CAUSAL_CHAIN":
                return _causal_chain_enabled()
            return False
        return str(raw).lower() in ("1", "true", "yes", "on")
    return True


def _group_converged(state: Dict[str, Any], g) -> bool:
    gs = (state.get("groups") or {}).get(g.gid) or {}
    if gs.get("locked") or gs.get("suspended"):
        return True
    return all(is_param_converged(state, m) for m in g.members)


def _group_feedback_trusted(g, today_rec, yesterday_rec) -> bool:
    fb_csv = ",".join(g.feedback_keys())
    return _param_judge_trusted(fb_csv, today_rec, yesterday_rec)


def _group_severity(g, today_rec, yesterday_rec):
    keys = [k for k in g.feedback_keys() if k in _KN_JUDGE_SUBJECTIVE_KEYS]
    rates: List[float] = []
    for r in (today_rec, yesterday_rec):
        if not r:
            continue
        for k in keys:
            v = r.get(k)
            if isinstance(v, (int, float)):
                rates.append(float(v))
    if not rates:
        return (1, 1.0)
    worst = min(rates)
    return (0 if worst < SEVERITY_FLOOR else 1, worst)


def _group_improved(fb_keys: List[str], last_tune: Optional[Dict[str, Any]],
                    today_rec: Optional[Dict[str, Any]]) -> Optional[bool]:
    """组内反馈键多数投票：改善=True / 恶化=False / 未知=None。
    逻辑对齐 determine_direction，但跨组内全部反馈键聚合。"""
    if not last_tune:
        return None
    status = last_tune.get("status", "")
    mb = last_tune.get("metrics_before") or {}
    ma = last_tune.get("metrics_after") or {}
    ic = tc = 0
    has_any = False
    skipped_untrusted = False
    has_subjective = False
    for name in fb_keys:
        d = _feedback_dir(name)
        if d is None:
            continue
        tc += 1
        if name in _KN_JUDGE_SUBJECTIVE_KEYS:
            has_subjective = True
            if not _feedback_key_trusted(name, today_rec, None):
                skipped_untrusted = True
                continue
        om = mb.get(name)
        nm = ma.get(name)
        if om is None or nm is None:
            continue
        try:
            om_f, nm_f = float(om), float(nm)
        except (TypeError, ValueError):
            continue
        has_any = True
        if _is_metric_improved(name, d, om_f, nm_f):
            ic += 1
    if status == "pending_restart" and not has_any:
        return None
    if not has_any:
        if skipped_untrusted and has_subjective:
            return None
        return None  # 无反馈数据：视为未知而非默认改善，避免无信号时持续单向漂移
    if _metrics_unchanged(mb, ma):
        return None
    return ic >= max(tc / 2, 1)


def _get_last_tune_for_group(gid: str) -> Optional[Dict[str, Any]]:
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
                if rec.get("group") == gid:
                    last = rec
    except FileNotFoundError:
        pass
    return last


# ---------- 召回护栏（多目标硬约束）----------

def _active_recall_guards(today_rec) -> List[Dict[str, Any]]:
    """返回当前已触发（越界）的护栏列表。today_rec 为当日 daily summary。"""
    if not isinstance(today_rec, dict):
        return []
    out: List[Dict[str, Any]] = []
    for g in RECALL_GUARDS:
        v = today_rec.get(g["metric"])
        if v is None:
            continue
        try:
            vf = float(v)
        except (TypeError, ValueError):
            continue
        if g["cmp"] == "ge" and vf >= g["bound"]:
            out.append(g)
        elif g["cmp"] == "le" and vf <= g["bound"]:
            out.append(g)
    return out


def _guard_forbid_dir(param: str, triggered: List[Dict[str, Any]]) -> Optional[str]:
    """若 param 被某条已触发护栏守护，返回其被禁的「收紧方向」；否则 None。"""
    for g in triggered:
        for (p, td) in g["guards"]:
            if p == param:
                return td
    return None


def _apply_recall_guard(param: str, d: Optional[str], triggered: List[Dict[str, Any]]):
    """召回护栏：若 param 被触发护栏守护、且拟移动方向恰为其「收紧方向」，
    强制反向（loosen），给召回恢复空间。返回 (direction, guard_fired: bool)。"""
    forbid = _guard_forbid_dir(param, triggered)
    if forbid and d == forbid:
        return _reverse(d), True
    return d, False


# ---------- 三种策略 ----------

def _strategy_single(g, state, today_rec, yesterday_rec, gstate, last_tune):
    """单参组：退化为原 determine_direction 逻辑。"""
    m = g.members[0]
    pdef = next((p for p in PARAM_DEFS if p[0] == m), None)
    if pdef is None:
        return None
    cur = _current_env(pdef)
    if is_param_converged(state, m):
        return None
    lt = _get_last_tune_for(m)
    dec = determine_direction(m, cur, pdef[2], pdef[3], pdef[4], pdef[5],
                              lt, summary_rec=today_rec)
    if not dec:
        return None
    nv = float(dec["new_value"])
    # —— 召回护栏（single 安全网：当前 guarded 参数均不在 single 组，仅防御性）——
    triggered = _active_recall_guards(today_rec)
    forbid = _guard_forbid_dir(m, triggered)
    if forbid and abs(nv - cur) >= 1e-9:
        tighten = "up" if nv > cur else "down"
        if tighten == forbid:
            nv2 = _step_param(cur, _reverse(forbid), pdef, lt is None)
            if nv2 is not None and abs(nv2 - cur) >= 1e-9:
                nv = nv2
                dec["reason"] = (dec.get("reason", "") + " | GUARD[recall]").strip()
    if abs(nv - cur) < 1e-9:
        return None
    return {"changes": {m: nv}, "improved": None, "reason": dec.get("reason", ""),
            "eff_steps": {m: dec.get("eff_step", pdef[4])}}


def _strategy_joint_majority(g, state, today_rec, yesterday_rec, gstate, last_tune):
    """多参耦合组：组内反馈键多数投票定「组方向」；
    改善→各成员沿其有利方向同调，恶化→整体反向（沿各自有利方向的反方向）。
    叠加召回护栏：被触发护栏守护的「收紧型」参数，禁止继续收紧、强制反向（loosen）。"""
    fb = g.feedback_keys()
    improved = _group_improved(fb, last_tune, today_rec)
    triggered = _active_recall_guards(today_rec)
    changes: Dict[str, float] = {}
    eff_steps: Dict[str, float] = {}
    reasons = []
    guard_reasons = []
    for m in g.members:
        pdef = next((p for p in PARAM_DEFS if p[0] == m), None)
        if pdef is None:
            continue
        cur = _current_env(pdef)
        if is_param_converged(state, m):
            continue
        ben = _member_beneficial_direction(pdef) or _position_dir(cur, pdef)
        d = _reverse(ben) if improved is False else ben
        # —— 召回护栏：组策略要求沿收紧方向移动且护栏已触发时，强制反向（loosen）——
        d, fired = _apply_recall_guard(m, d, triggered)
        if fired:
            guard_reasons.append(f"{m}→{d}")
        coarse = last_tune is None
        nv = _step_param(cur, d, pdef, coarse)
        if nv is not None:
            changes[m] = nv
            eff_steps[m] = (pdef[4] * COARSE_STEP_FACTOR) if coarse else pdef[4]
            reasons.append(f"{m}:{d}")
    if not changes:
        return None
    reason = f"joint_majority(improved={improved}) " + ", ".join(reasons)
    if guard_reasons:
        reason += " | GUARD[" + "/".join(g["label"] for g in triggered) + "]:" + ",".join(guard_reasons)
    return {"changes": changes, "improved": improved,
            "reason": reason, "eff_steps": eff_steps}


def _strategy_synergy_search(g, state, today_rec, yesterday_rec, gstate, last_tune):
    """重组组：以 se_recombine_synergy_avg 单一标量驱动。
    无 synergy（重组未启用 / synergy=0）→ 无信号，安全跳过，避免空转。
    改善→沿各成员有利方向微调；恶化→整体回滚到上次联合移动前的值。"""
    fb = g.feedback_keys()
    syn = today_rec.get("se_recombine_synergy_avg") if isinstance(today_rec, dict) else None
    if syn is None:
        return None
    try:
        syn_f = float(syn)
    except (TypeError, ValueError):
        return None
    if syn_f <= 0:
        return None  # 重组未产生 synergy：不调，避免伪优化（与单参 None 陷阱同理）
    improved = _group_improved(fb, last_tune, today_rec)
    changes: Dict[str, float] = {}
    eff_steps: Dict[str, float] = {}
    for m in g.members:
        pdef = next((p for p in PARAM_DEFS if p[0] == m), None)
        if pdef is None:
            continue
        cur = _current_env(pdef)
        if is_param_converged(state, m):
            continue
        if improved is False:
            prev = (gstate.get("last_values") or {}).get(m)
            if prev is not None and abs(float(prev) - cur) > 1e-9:
                changes[m] = float(prev)
                eff_steps[m] = abs(float(prev) - cur)
            continue
        ben = _member_beneficial_direction(pdef) or _position_dir(cur, pdef)
        coarse = last_tune is None
        nv = _step_param(cur, ben, pdef, coarse)
        if nv is not None:
            changes[m] = nv
            eff_steps[m] = (pdef[4] * COARSE_STEP_FACTOR) if coarse else pdef[4]
    if not changes:
        return None
    return {"changes": changes, "improved": improved,
            "reason": f"synergy_search(synergy={syn_f:g}, improved={improved})",
            "eff_steps": eff_steps}


def _run_group_strategy(g, state, today_rec, yesterday_rec):
    gstate = _ensure_gstate(state, g.gid)
    last_tune = _get_last_tune_for_group(g.gid)
    if g.strategy == "single":
        return _strategy_single(g, state, today_rec, yesterday_rec, gstate, last_tune)
    if g.strategy == "synergy_search":
        return _strategy_synergy_search(g, state, today_rec, yesterday_rec, gstate, last_tune)
    return _strategy_joint_majority(g, state, today_rec, yesterday_rec, gstate, last_tune)


def select_groups_to_tune(state: Dict[str, Any], today_rec, yesterday_rec) -> List:
    """返回本轮应选中的功能组（全部可信且未收敛的组，并行调）。"""
    out = []
    for g in PARAM_GROUPS:
        if not _group_enabled(g):
            continue
        if _group_converged(state, g):
            continue
        if not _group_feedback_trusted(g, today_rec, yesterday_rec):
            continue
        out.append(g)
    out.sort(key=lambda g: _group_severity(g, today_rec, yesterday_rec))
    if MAX_GROUPS_PER_RUN and MAX_GROUPS_PER_RUN > 0:
        out = out[:MAX_GROUPS_PER_RUN]
    return out


def are_all_groups_converged(state: Dict[str, Any]) -> bool:
    for g in PARAM_GROUPS:
        if not _group_enabled(g):
            continue
        if not _group_converged(state, g):
            return False
    return True


def _update_state_for_group(state: Dict[str, Any], g, dec, tune_date: str) -> Dict[str, Any]:
    """双写：组级 gstate（initial/last/best 值 + 方向） + 逐成员 update_state
    （保持收敛/锁/恶化回滚逻辑不变，平滑兼容旧单参状态）。"""
    gstate = _ensure_gstate(state, g.gid)
    changes = dec["changes"]
    # 首次捕获初始值
    for m in changes:
        if m not in gstate["initial_values"]:
            gstate["initial_values"][m] = _current_env(
                next(p for p in PARAM_DEFS if p[0] == m))
    # 捕获移动前的值（供 synergy 回滚）
    pre_values = {}
    for m, nv in changes.items():
        pre_values[m] = _current_env(next(p for p in PARAM_DEFS if p[0] == m))
        gstate["last_values"][m] = pre_values[m]
    # 组方向（用于日志/回滚触发；取首个成员移动方向）
    if changes:
        first_m = next(iter(changes))
        gstate["last_direction"] = "up" if changes[first_m] > pre_values[first_m] else "down"
    gstate["last_tune_date"] = tune_date
    # metrics_improved：仅当明确恶化时才记恶化（避免未知/位置探索误触发 suspend）
    metrics_improved = (dec.get("improved") is not False)
    for m, nv in changes.items():
        pdef = next(p for p in PARAM_DEFS if p[0] == m)
        cur = pre_values[m]
        # best_values 记忆
        try:
            gstate["best_values"][m] = float(nv)
        except (TypeError, ValueError):
            pass
        ns = update_state(
            state, m, gstate["last_direction"] or "up", nv,
            metrics_improved=metrics_improved, no_change=False,
            tune_date=tune_date, old_value=cur,
            last_direction_record=(state.get(m) or {}).get("last_direction"),
        )
        state = ns
    return state


def _record_group_no_change(state: Dict[str, Any], g, tune_date: str) -> Dict[str, Any]:
    """组无实际变化：累加 no_change，使成员最终可锁定（与旧单参行为一致）。"""
    gstate = _ensure_gstate(state, g.gid)
    gstate["no_change_count"] = int(gstate.get("no_change_count", 0)) + 1
    gstate["last_tune_date"] = tune_date
    for m in g.members:
        if is_param_converged(state, m):
            continue
        ns = update_state(
            state, m, "none", _current_env(next(p for p in PARAM_DEFS if p[0] == m)),
            metrics_improved=True, no_change=True, tune_date=tune_date,
            old_value=None,
            last_direction_record=(state.get(m) or {}).get("last_direction"),
        )
        state = ns
    return state


def _append_group_log(g, dec, pre_values, today: str, today_rec, dry_run: bool) -> None:
    entry = {
        "date": today,
        "group": g.gid,
        "label": g.label,
        "strategy": g.strategy,
        "parameter": g.gid,
        "parameters": list(dec["changes"].keys()),
        "old_values": {m: pre_values.get(m) for m in dec["changes"]},
        "new_values": dec["changes"],
        "direction": "up",
        "reason": dec.get("reason", ""),
        "dry_run": dry_run,
        "metrics_before": _extract_metrics_before(today_rec),
        "metrics_after": None,
        "status": "dry_run" if dry_run else "pending_restart",
        "timestamp": _now_iso(),
    }
    # 方向：用首个成员移动方向
    if dec["changes"]:
        first_m = next(iter(dec["changes"]))
        entry["direction"] = "up" if dec["changes"][first_m] > (pre_values.get(first_m) or 0) else "down"
    _append_jsonl(LOG_FILE, entry)


# select_param_to_tune 保留（single 策略与 legacy 回退使用）
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
    today_str = _report_date_today()
    yesterday_str = _report_date_yesterday()
    rec_data = _extract_metrics_for_tuning(today_str, yesterday_str)
    today_rec = rec_data.get("today") if isinstance(rec_data.get("today"), dict) else {}
    yesterday_rec = rec_data.get("yesterday") if isinstance(rec_data.get("yesterday"), dict) else {}

    def _feedback_trust_today(feedback_csv: str) -> bool:
        """mask 级改造：只要参数任一主观键今日有样本（该路可信）即视为可信，可参与调优。"""
        return _param_judge_trusted(feedback_csv, today_rec, yesterday_rec)

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

    # #5 严重度快车道：当参数绑定的 mask 主观键 relevant_rate < SEVERITY_FLOOR 时，
    # 视为强负信号，在同一 tier 内优先于普通信号调优，避免被前面参数排队饿死
    # （原逻辑严格按 PARAM_DEFS 顺序取第一个 eligible，SAG/KT 等后位参数要等前面逐项收敛）。
    # 返回 (urgent_flag, worst_rate)：urgent(0) 排前；worst_rate 越小越紧急排前。
    def _sev(pdef: Tuple[str, float, float, float, float, str]):
        fb_csv = pdef[5]
        keys = [k for k in (s.strip() for s in fb_csv.split(",") if s.strip())
                if k in _KN_JUDGE_SUBJECTIVE_KEYS]
        rates: List[float] = []
        for r in (today_rec, yesterday_rec):
            if not r:
                continue
            for k in keys:
                v = r.get(k)
                if isinstance(v, (int, float)):
                    rates.append(float(v))
        if not rates:
            return (1, 1.0)  # 无主观信号：非紧急，排最后
        worst = min(rates)
        return (0 if worst < SEVERITY_FLOOR else 1, worst)

    def _sort_bucket(bucket):
        return sorted(bucket, key=_sev)

    virgin_confident = _sort_bucket(virgin_confident)
    virgin_unconfident = _sort_bucket(virgin_unconfident)
    remaining_confident = _sort_bucket(remaining_confident)
    remaining_unconfident = _sort_bucket(remaining_unconfident)

    def _pick(vc, vu, rc, ru):
        order = (("virgin-confident", vc), ("virgin-unconfident", vu),
                 ("remaining-confident", rc), ("remaining-unconfident", ru))
        for label, bucket in order:
            if bucket:
                first = bucket[0]
                urgent, worst = _sev(first)
                tag = f"严重度快车道(worst_rate={worst:.3f})" if urgent == 0 else "常规顺序"
                log_info(f"  选参[{label}] → {first[0]}（{tag}；今日 KN LLM Judge 样本可信度按各路分别判定）")
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
            today = _dt.date.fromisoformat(_report_date_today())
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

    today = _report_date_today()
    print("")
    print("============================================")
    print(f"  Auto-Tuner 开始 — {today}{' (DRY-RUN)' if dry_run else ''}")
    print("============================================")
    print("")

    # 1. 暂停检测
    if check_pause():
        return 0

    # 2. 闭环 Step 0：处理上次 pending_restart / 冷却
    #    dry-run 保持只读：跳过 handle_pending_restart，避免误改真实 .env/state
    if dry_run:
        log_info("[DRY-RUN] 跳过 handle_pending_restart（只读模式，不解析/写回 pending 记录）")
    elif handle_pending_restart():
        return 0

    # 3. 历史数据文件存在性
    if not os.path.exists(HISTORY_FILE):
        log_warn(f"历史数据文件不存在: {HISTORY_FILE}")
        log_info("跳过本次调优（首次运行需累积数据）")
        return 0

    # 4. 提取今天/昨天指标
    log_info(f"获取指标数据: {today} / {_report_date_yesterday()}")
    mdata = _extract_metrics_for_tuning(today, _report_date_yesterday())
    today_rec = mdata.get("today")
    yesterday_rec = mdata.get("yesterday")
    if not isinstance(today_rec, dict) or not today_rec:
        log_warn("未找到今天的指标数据，跳过本次调优")
        return 0
    log_ok("已获取指标数据")

    # 5. 加载状态 + 收敛总检
    state = load_state()
    log_info("已加载调优状态")

    if GROUP_TUNING_ENABLED and PARAM_GROUPS:
        # ======================================================
        # 新模型：分组并行调优（每组独立策略，每轮调全部可信组）
        # ======================================================
        if are_all_groups_converged(state):
            log_info("所有功能组已收敛，跳过本次调优")
            return 0

        groups = select_groups_to_tune(state, today_rec, yesterday_rec)
        if not groups:
            log_info("没有可调优的功能组（都收敛/暂停/反馈不可信）")
            return 0
        log_info(f"选中 {len(groups)} 个功能组并行调优: " + ", ".join(g.gid for g in groups))

        # 跑各组策略，收集有实际变化的决策
        decisions = []  # (g, dec, pre_values)
        for g in groups:
            dec = _run_group_strategy(g, state, today_rec, yesterday_rec)
            if dec and dec.get("changes"):
                pre = {m: _current_env(next(p for p in PARAM_DEFS if p[0] == m))
                       for m in dec["changes"]}
                decisions.append((g, dec, pre))

        if not decisions:
            log_info("选中组本次均无实际变化（成员已到边界/收敛），记录 no_change 后跳过")
            for g in groups:
                state = _record_group_no_change(state, g, today)
            save_state(state)
            return 0

        # 打印决策
        for g, dec, pre in decisions:
            print("")
            log_step(f"组决策 [{g.gid}] ({g.strategy}) — {g.label}:")
            for m, nv in dec["changes"].items():
                print(f"  {m}: {pre[m]} → {nv}")
            print(f"  原因: {dec.get('reason', '')}")

        # 步幅安全校验（逐参数用其 eff_step）；不合规的参数从本次决策中剔除
        for g, dec, pre in decisions:
            for m, nv in list(dec["changes"].items()):
                eff = dec.get("eff_steps", {}).get(
                    m, next(p for p in PARAM_DEFS if p[0] == m)[4])
                if not validate_step(pre[m], nv, eff):
                    log_warn(f"组 {g.gid} 参数 {m} 步幅超安全阈值，跳过该参数")
                    dec["changes"].pop(m, None)
        decisions = [(g, dec, pre) for g, dec, pre in decisions if dec.get("changes")]

        metrics_before = _extract_metrics_before(today_rec)

        # DRY-RUN 分支
        if dry_run:
            print("")
            log_info("================================================")
            log_info("  DRY-RUN 模式 — 以下操作不会实际执行")
            log_info("================================================")
            for g, dec, pre in decisions:
                print(f"  组 {g.gid} ({g.label}):")
                for m, nv in dec["changes"].items():
                    print(f"    {m}: {pre[m]} → {nv}")
            print(f"  日志: {LOG_FILE}")
            for g, dec, pre in decisions:
                _append_group_log(g, dec, pre, today, today_rec, True)
            log_ok("DRY-RUN 完成，决策已写入日志")
            return 0

        # ===== 实际执行：一次性备份 + 批量写参 + 单次重启通知 =====
        log_step(f"备份 {ENV_FILE}")
        backup_file = backup_env()
        log_ok(f"已备份到: {backup_file}")

        changed_summary = []
        for g, dec, pre in decisions:
            for m, nv in dec["changes"].items():
                ok = write_env_param(m, _fmt_env(m, nv))
                if not ok:
                    log_err(f"写入 .env 失败: {m}")
                    return 2
                changed_summary.append(f"{m}={nv:g}")
        log_ok(".env 已更新: " + ", ".join(changed_summary))

        print("")
        notify_gateway_restart(
            "GROUP_TUNE", "", "",
            "分组并行调优: " + "; ".join(
                f"{g.gid}[" + ", ".join(f"{m}={dec['changes'][m]:g}" for m in dec["changes"]) + "]"
                for g, dec, pre in decisions),
            dry_run=False)

        log_step("记录调优日志 + 状态")
        for g, dec, pre in decisions:
            _append_group_log(g, dec, pre, today, today_rec, False)
            state = _update_state_for_group(state, g, dec, today)
        save_state(state)
        log_ok("调优状态已写回（pending_restart，等重启生效）")

        print("")
        print("============================================")
        print(f"  Auto-Tuner 完成 — {len(decisions)} 个功能组已调优")
        print("============================================")
        return 0

    else:
        # ======================================================
        # 旧模型：单参（兼容回退，GROUP_TUNING_ENABLED=False）
        # ======================================================
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

        # 9. 方向决策（传入当日完整 summary 记录，供 mask 信任门控读取样本计数）
        decision = determine_direction(name, current, pmin, pmax, step, fb_csv, last_tune, summary_rec=today_rec)
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
        # 实际生效步幅（首次调优为粗步幅 step*2），validate_step 用同一数值校验，
        # 避免粗步幅被原始 step 误拦截
        eff_step = float(decision.get("eff_step", step))
        print("")
        log_step("调优决策:")
        print(f"  参数:     {name}")
        print(f"  当前值:   {current}")
        print(f"  新值:     {new_val}")
        print(f"  方向:     {direction}")
        print(f"  原因:     {reason}")
        print("")

        # 10. 步幅安全校验（用实际生效步幅 eff_step，粗步幅按粗步幅校验）
        if not validate_step(current, new_val, eff_step):
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
