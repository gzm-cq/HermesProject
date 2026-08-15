"""config.py — 全局常量：路径、阈值、映射表。

从 flywheel-health-report.py L37-182 和 auto-tuner.py L40-63 搬入。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# === Default Paths ===
DEFAULT_HERMES_HOME = "/root/.hermes"
CRON_STATE_SUBPATH = Path("lib") / "cron-state"
CRON_LOG_SUBPATH = Path("logs") / "cron"
TRACE_LOG_SUBPATH = Path("plugins") / "knowledge-navigation" / "trace.log"
KN_BASELINE_SUBPATH = Path("plugins") / "knowledge-navigation" / "baselines"
DATA_FLYWHEEL_SUBPATH = Path("data") / "flywheel"
OUTPUT_SUBPATH = Path("logs") / "reports"
SKILL_USAGE_SUBPATH = Path("skills") / ".usage.json"
ERROR_LOG_SUBPATH = Path("logs") / "errors.log"
MEMORY_DIR_SUBPATH = Path("memories")
JOBS_JSON_SUBPATH = Path("cron") / "jobs.json"
SELF_EVOLVING_OUTPUT_SUBPATH = Path("self-evolving") / "output"
LEDGER_SUBPATH = Path("data") / "flywheel" / "ledger.jsonl"

# === Thresholds ===
TH = {
    # 产出质量
    "router_full_off_pct": 30,     # >30% -> P0
    "recall_empty_pct": 20,        # >20% -> P1
    "skill_f1_low": 0.4,           # <0.4 -> P0
    "kn_avg_score_low": 0.5,       # <0.5 per dimension -> P1
    "kt_orphan_pct": 90,           # >90% -> P1
    "unknown_dim_pct": 50,         # >50% -> P1
    # 任务可靠性
    "elapsed_deviation_sigma": 2.0, # >2 sigma from mean -> P1
    "elapsed_significant_pct": 50,  # 变化幅度 >50% 才报告
    # Router 分析
    "eval_window_sec": 5.0,         # 5 秒内的后继 mask 视为 eval 触发
    # 报告类型检测
    "boot_catchup_window_hours": 12,  # boot 后 12h 内视为 catch-up
    # 数据可信度 (标注不报警)
    "baseline_stale_hours": 48,
    "min_sample_size": 50,
    # Skill 真实使用
    "skill_unused_warn_days": 30,
    "skill_unused_warn_count": 20,
    # 全局错误
    "error_rate_high_pct": 5,
    # SAG 贡献
    "sag_merge_zero_pct": 50,
    # 记忆清理
    "memory_char_usage_high_pct": 90,
    "memory_cleanup_stale_hours": 48,
    # Self-Evolving（能力飞轮）：每日 17:30 调度，超过该时长无产出视为停滞
    "se_stale_hours": 36,
}

# === 推荐生成阈值（generate_recommendations 专用，与告警 TH 区分） ===
REC_TH = {
    # Router
    "router_full_off_high_pct": 15,
    "router_empty_high_pct": 10,
    "router_latency_high_ms": 8000,
    "router_score_low": 0.4,
    # SAG
    "sag_on_low_pct": 10,
    "sag_on_high_pct": 30,
    "sag_latency_high_ms": 3000,
    "sag_merge_zero_high_pct": 50,
    # Skill
    "skill_f1_moderate": 0.6,
    "skill_pr_imbalance_ratio": 0.7,
    # KN
    "kn_unknown_dim_high_pct": 20,
    "kn_dim_min_sample": 3,
    # 知识树
    "kt_orphan_high_pct": 50,
    "kt_fragment_high_count": 10,
    "kt_confidence_low": 0.8,
    # 记忆
    "memory_usage_high_pct": 80,
    "memory_no_output_usage_pct": 50,
    # 系统错误
    "error_high_count": 50,
    "error_concentration_ratio": 0.5,
}

# === Test query filter ===
_TEST_QUERY_RE = re.compile(
    r"^(gen_|eval-|test_|test-|exact_kw_|semantic_|entity_|causal_|"
    r"temporal_|conflict_|tool_|debug_|api_|compare_|workflow_|complex_|numeric_)",
    re.IGNORECASE,
)

# === Active cron jobs — all user cronjobs monitored by flywheel health report ===
# 包括：核心飞轮 + 新增 3 个（dream-daily、每周深度研究、system-health-check）
# 注：knowledge-navigation-baseline / run-skill-eval 已合并进 runner（阶段 0/1 内部执行），
#     不再作为独立 cronjob 监控；clustering-analysis 已取消（测试无实际效果）。
ACTIVE_CRON_JOBS = frozenset({
    # 核心飞轮
    "memory-cleanup",
    "skillopt-nightly-run",
    "kn-router-health-check",
    "daily-learn",
    "knowledge-tree-consolidate",
    "knowledge-tree-kvector",
    # 新增：之前未跟踪的 job
    "dream-daily",
    "每周深度研究-知识树学习",
    "system-health-check",
    # 能力飞轮：Self-Evolving 自动写回闭环（F-5 + B）
    "self-evolving-nightly",
})

# 已知的非飞轮 state 文件白名单（cron 基础设施 job，不纳入巡检报告）
EXCLUDED_STATE_FILES = frozenset({
    "cron-boot-detect",
    "cron-periodic-detect",
    "cron-periodic-dedup",
    "flywheel-health-report",
    "deploy-cleanup-health-check",
})

# === Flywheel mapping ===
# 注：knowledge-navigation-baseline / run-skill-eval 已合并进 runner 内部执行，
#     不再出现在 cron state / jobs.json 中；clustering-analysis 已取消。
_CRON_TO_FLYWHEEL = {
    "kn-router-health-check": "Router",
    "skillopt-nightly-run": "Skill",
    "memory-cleanup": "记忆",
    "knowledge-tree-consolidate": "知识树",
    "knowledge-tree-kvector": "知识树",
    "daily-learn": "知识路",
    "dream-daily": "知识路",
    "每周深度研究-知识树学习": "知识树",
    "system-health-check": "系统",
    "self-evolving-nightly": "能力飞轮",
}

_FLYWHEEL_ORDER = ["Router", "Skill", "知识树", "记忆", "知识路", "系统", "能力飞轮"]

# === Required output files for integrity check ===
# 注意：Router 的 baseline_latest.json 已从列表移除 —— knowledge-navigation-baseline
# cron job 已禁用（_disabled_reason：由 flywheel-health-report 阶段 1 内建 KN LLM Judge
# run_judge_within_window() 替代，避免重复 judge 消耗 2 倍 token），该文件不再有任何生产者，
# 保留会导致持续误报 P1「产出文件缺失」。Router 质量数据现由 trace.log 实时扫描 + kn_judge 提供。
REQUIRED_OUTPUTS = {
    "Skill": Path("data") / "flywheel" / "skill_eval_prev.json",
    "知识树": Path("data") / "flywheel" / "kt-baseline-latest.json",
}

# === Flywheel dependency chain (downstream -> upstream) ===
# 注：run-skill-eval 已合并进 runner 阶段 0 内部执行，不再有独立 cron state，
#     skillopt-nightly-run 无上游 cron 依赖（由 runner-summary 登记时序勾稽）。
FLYWHEEL_DEPENDENCIES: dict[str, list[str]] = {}

# ============================================================
# auto-tuner 路径常量（从 auto-tuner.py 搬入，统一管理）
# ============================================================
HERMES_HOME = os.environ.get("HERMES_HOME", DEFAULT_HERMES_HOME)
ENV_FILE = os.path.join(HERMES_HOME, ".env")
HISTORY_FILE = os.path.join(HERMES_HOME, "data", "flywheel", "daily-summary-history.jsonl")
LOG_FILE = os.path.join(HERMES_HOME, "data", "flywheel", "auto-tuner-log.jsonl")
PAUSE_FILE = os.path.join(HERMES_HOME, "data", "flywheel", "auto-tuner.pause")
BACKUP_DIR = os.path.join(HERMES_HOME, "backups", "auto-tuner")
STATE_FILE = os.path.join(HERMES_HOME, "data", "flywheel", "auto-tuner-state.json")

CRON_LIB = os.environ.get("CRON_LIB", "/root/.hermes/lib/cron_common.sh")
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")

# 参数池
# 格式：(param_name, default, min, max, step, feedback_csv)
#   param_name 必须与 KN config.py from_env() 读取的 ENV 变量名完全一致
#   feedback_csv 列：自优化的改善反馈键，逗号分隔；每个键的方向由 _parse_feedback 解析
#
# 4 路召回 + 全局打分 全覆盖：
#   Hindsight: KN_MIN_SCORE, KN_MAX_RESULTS, KN_MAX_TEXT_LENGTH, KN_TEMPORAL_HALFLIFE, KN_TEMPORAL_FLOOR_WEIGHT
#   SAG:       KN_SAG_MAX_INJECT, KN_SAG_SEARCH_TOP_K, KN_SAG_MIN_SCORE, KN_SAG_POINTER_THRESHOLD
#   跨域去重:  KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR
#   全局打分:  KN_LAMBDA_MRR, KN_SCORE_SPAN_TOP3_THRESHOLD, KN_SCORE_SPAN_HALF_THRESHOLD
#   因果链(按需启用): KN_CAUSAL_BOOST_ALPHA, KN_CAUSAL_BOOST_CAP
#
# 已移除：KN_TOKEN_BUDGET_TOTAL / _KT_RATIO / _SKILL_RATIO / _HINDSIGHT_RATIO。
#   产品决策为「不做 token 预算控制，只记录实际消耗」，消费侧不再有截断逻辑，
#   其反馈键 token_exhaust_pct 也已随之下线；继续自动调这 4 个参数只会把噪声
#   计入 degradation_count / direction_history，污染整个 Router 飞轮的方向判据。
PARAM_DEFS = [
    # === Hindsight 路（因果绑定到 relevant_rate_h / avg_relevance_h）===
    ("KN_MIN_SCORE",               0.50, 0.40, 0.65, 0.05, "kn_judge_relevant_rate_h,kn_judge_relevant_rate_kt,kn_judge_avg_relevance_h,router_empty_pct"),
    ("KN_MAX_RESULTS",             3,    2,    8,    1,    "kn_judge_relevant_rate_h,kn_judge_relevant_rate_kt,kn_judge_avg_relevance_h,memory_hindsight_count"),
    ("KN_MAX_TEXT_LENGTH",         200,  120,  400,  50,   "kn_judge_relevant_rate_h,kn_judge_relevant_rate_kt"),
    ("KN_TEMPORAL_HALFLIFE",       30,   14,   90,   7,    "kn_judge_relevant_rate_h,kn_judge_relevant_rate_kt,kn_judge_avg_relevance_h"),
    ("KN_TEMPORAL_FLOOR_WEIGHT",   0.5,  0.3,  0.8,  0.1,  "kn_judge_relevant_rate_h,kn_judge_relevant_rate_kt"),
    # === SAG 路（因果绑定到 relevant_rate_sag）===
    ("KN_SAG_MAX_INJECT",          3.0,  2.0,  6.0,  1.00, "kn_judge_relevant_rate_sag,sag_total_kept"),
    ("KN_SAG_SEARCH_TOP_K",        3,    3,    10,   1,    "kn_judge_relevant_rate_sag,sag_total_kept"),
    ("KN_SAG_MIN_SCORE",           0.5,  0.3,  0.8,  0.05, "kn_judge_relevant_rate_sag,sag_on_pct,sag_total_kept"),
    ("KN_SAG_POINTER_THRESHOLD",   300,  150,  800,  100,  "kn_judge_relevant_rate_sag,sag_total_kept"),
    # === 跨域去重（影响 hindsight 命中 + sag 回流）===
    ("KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR", 0.5, 0.3, 0.8, 0.1, "kn_judge_relevant_rate_h,kn_judge_relevant_rate_sag,sag_total_kept"),
    # === 全局打分 / 重排（因果绑定到 h/kt 质量）===
    ("KN_LAMBDA_MRR",              0.55, 0.35, 0.70, 0.05, "kn_judge_relevant_rate_h,kn_judge_relevant_rate_kt,kn_judge_avg_relevance_h"),
    ("KN_SCORE_SPAN_TOP3_THRESHOLD",0.85, 0.80, 0.95, 0.05, "kn_judge_avg_relevance_h,kn_judge_avg_relevance_kt"),
    ("KN_SCORE_SPAN_HALF_THRESHOLD",0.65, 0.60, 0.85, 0.05, "kn_judge_avg_relevance_h,kn_judge_avg_relevance_kt"),
    # === 因果链提权（只有 KN_ENABLE_CAUSAL_CHAIN=true 时生效，否则反馈为平→auto-tuner 自动锁定）===
    ("KN_CAUSAL_BOOST_ALPHA",      0.05, 0.02, 0.20, 0.01, "kn_judge_relevant_rate_h"),
    ("KN_CAUSAL_BOOST_CAP",        1.10, 1.05, 1.30, 0.05, "kn_judge_relevant_rate_h"),
    # === Skill 路控制环（F-2）：让数据飞轮能"测"更能"控" ===
    #     auto-tuner 基于 skill_used_count 反馈驱动执行参数，经 .env 下发到
    #     skillopt_runner._load_skillopt_env_overrides() 消费（SKILLOPT_ENABLED/MAX_PER_NIGHT/COOLDOWN_DAYS）。
    #     范围刻意收紧，即便 auto-tuner 把它们推到边界也是安全值（不会无限 churn）。
    #     注：SKILLOPT_ENABLED 是手动总开关（默认 1，置 0 整轮跳过），bool 不适合连续搜索，
    #         不进 auto-tuner，由运维在 .env 直接维护。
    ("SKILLOPT_MAX_PER_NIGHT",     1, 1, 3, 1, "skill_used_count"),
    ("SKILLOPT_COOLDOWN_DAYS",     3, 1, 5, 1, "skill_used_count"),
    # === SAG 生产端闭环（F-3）：dream-daily 读取 DREAM_PROMOTE_THRESHOLD（.env），
    #     auto-tuner 基于 kn_judge_relevant_rate_sag 反馈动态调节晋升阈值（消费侧质量差→收紧晋升）。
    ("DREAM_PROMOTE_THRESHOLD",    0.6, 0.3, 0.9, 0.05, "kn_judge_relevant_rate_sag"),
]

FEEDBACK_KEYS = [
    "kn_avg_score", "router_empty_pct", "sag_total_kept",
    "sag_merge_zero_pct", "memory_hindsight_count",
    "sag_on_pct",
    # Skill 路真实使用反馈
    "skill_used_count",
    # KN LLM Judge 质量评估（collect_baseline.py --judge 产出），
    # 作为 KN_* 调优的主反馈。mask 级（_h/_kt/_sag）实现「参数 → 其影响的那一路质量」因果绑定：
    #   kn_judge_relevant_rate[_h|_kt|_sag]  (0~1, 越大越好) = 该路 judged 中评分 >= 0.5 占比
    #   kn_judge_avg_relevance[_h|_kt|_sag]  (0~1, 越大越好) = 该路 judged 所有 LLM 评分均值
    #   kn_judge_sample_count[_h|_kt|_sag]   (int)            = 该路样本量，用于可信度判断
    #   kn_judge_relevant_rate / avg_relevance / sample_count  为全局键（兼容旧绑定）
    "kn_judge_relevant_rate", "kn_judge_avg_relevance", "kn_judge_sample_count",
    "kn_judge_relevant_rate_h", "kn_judge_avg_relevance_h", "kn_judge_sample_count_h",
    "kn_judge_relevant_rate_kt", "kn_judge_avg_relevance_kt", "kn_judge_sample_count_kt",
    "kn_judge_relevant_rate_sag", "kn_judge_avg_relevance_sag", "kn_judge_sample_count_sag",
]

# KN Judge 子配置：控制健康巡检报告何时触发、最小样本量、最大耗时保护等
KN_JUDGE_CFG = {
    "enabled": True,                  # 集成开关（关了就走 kn_avg_score）
    "sample_size": 200,               # 每次 judge 采样条数（最近 N 条）
    "min_sample": 20,                 # 全局键最小样本量（滚动窗口下约 40 条，此为可行阈值）
    "mask_window_days": 30,           # mask 级 judge 滚动聚合窗口（天）：保证各路有足够样本，
                                      #   解决每日窗口样本不足导致 kn_judge 长期为 None 的根因
    "mask_min_sample": 12,            # 单路（h/kt/sag）最小样本量，低于则对应 mask 键不可信
    "parallel": 5,                    # 并发度（与 JUDGE_PARALLEL 一致）
    "max_walltime_sec": 3600,         # 硬超时（1 小时），防止阻塞整个报告
    "min_age_breakpoint_hours": 6,    # 本轮 date 窗口至少需要 6 小时数据才输出到 daily summary
    "fallback_on_fail": True,        # judge 失败时用 kn_avg_score/recall kept 粗估写回，防止反馈断裂
}

# 收敛/锁定/暂停阈值
NO_CHANGE_LOCK_THRESHOLD = 3
CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD = 3
COOLDOWN_DAYS_AFTER_APPLY = 0
