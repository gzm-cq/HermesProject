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

# === Thresholds ===
TH = {
    # 产出质量
    "router_full_off_pct": 30,     # >30% -> P0
    "recall_empty_pct": 20,        # >20% -> P1
    "skill_f1_low": 0.4,           # <0.4 -> P0
    "kn_avg_score_low": 0.5,       # <0.5 per dimension -> P1
    "kt_orphan_pct": 90,           # >90% -> P1
    "cluster_noise_rate_high": 50, # >50% -> P1
    "unknown_dim_pct": 50,         # >50% -> P1
    # 任务可靠性
    "elapsed_deviation_sigma": 2.0, # >2 sigma from mean -> P1
    "elapsed_significant_pct": 50,  # 变化幅度 >50% 才报告
    # Router 分析
    "eval_window_sec": 5.0,         # 5 秒内的后继 mask 视为 eval 触发
    # 聚类趋势
    "trend_window_size": 3,         # 趋势基线滚动窗口大小
    "noise_outlier_pp": 2.0,        # 噪声率离群阈值（pp）
    # 报告类型检测
    "boot_catchup_window_hours": 12,  # boot 后 12h 内视为 catch-up
    # 数据可信度 (标注不报警)
    "baseline_stale_hours": 48,
    "min_sample_size": 50,
    # Skill 真实使用
    "skill_unused_warn_days": 30,
    "skill_unused_warn_count": 20,
    # Token 预算
    "token_budget_exhaust_pct": 10,
    # 全局错误
    "error_rate_high_pct": 5,
    # SAG 贡献
    "sag_merge_zero_pct": 50,
    # 记忆清理
    "memory_char_usage_high_pct": 90,
    "memory_cleanup_stale_hours": 48,
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
    # Token
    "token_avg_usage_high_ratio": 0.8,
    "token_exhaust_ratio": 0.95,
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
    # 聚类
    "cluster_noise_high_pct": 30,
    "cluster_min_count": 3,
    "cluster_links_min_count": 50,
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

# === Active cron jobs — only core flywheel tasks ===
ACTIVE_CRON_JOBS = frozenset({
    "memory-cleanup",
    "knowledge-navigation-baseline",
    "run-skill-eval",
    "skillopt-nightly-run",
    "kn-router-health-check",
    "daily-learn",
    "clustering-analysis",
    "knowledge-tree-consolidate",
    "knowledge-tree-kvector",
})

# 已知的非飞轮 state 文件白名单
EXCLUDED_STATE_FILES = frozenset({
    "system-health-check",
    "cron-boot-detect",
    "cron-periodic-detect",
    "cron-periodic-dedup",
    "flywheel-health-report",
})

# === Flywheel mapping ===
_CRON_TO_FLYWHEEL = {
    "knowledge-navigation-baseline": "Router",
    "kn-router-health-check": "Router",
    "run-skill-eval": "Skill",
    "skillopt-nightly-run": "Skill",
    "clustering-analysis": "聚类",
    "memory-cleanup": "记忆",
    "knowledge-tree-consolidate": "知识树",
    "knowledge-tree-kvector": "知识树",
    "daily-learn": "知识路",
}

_FLYWHEEL_ORDER = ["Router", "Skill", "知识树", "聚类", "记忆", "知识路"]

# === Required output files for integrity check ===
REQUIRED_OUTPUTS = {
    "Skill": Path("data") / "flywheel" / "skill_eval_prev.json",
    "知识树": Path("data") / "flywheel" / "kt-baseline-latest.json",
    "聚类": Path("data") / "flywheel" / "clustering_baseline_prev.json",
    "Router": Path("plugins") / "knowledge-navigation" / "baselines" / "baseline_latest.json",
}

# === Flywheel dependency chain (downstream -> upstream) ===
FLYWHEEL_DEPENDENCIES = {
    "skillopt-nightly-run": ["run-skill-eval"],
}

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
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "oc_f04a9f65d4b780511cc3f402c7d54ac3")

# 参数池
# 格式：(param_name, default, min, max, step, feedback_csv)
#   param_name 必须与 KN config.py from_env() 读取的 ENV 变量名完全一致
#   feedback_csv 列：自优化的改善反馈键，逗号分隔；每个键的方向由 _parse_feedback 解析
#
# 4 路召回覆盖：
#   Hindsight: KN_MIN_SCORE, KN_MAX_RESULTS, KN_MAX_TEXT_LENGTH, KN_TEMPORAL_HALFLIFE, KN_TEMPORAL_FLOOR_WEIGHT
#   SAG:       KN_SAG_MAX_INJECT, KN_SAG_SEARCH_TOP_K, KN_SAG_MIN_SCORE, KN_SAG_POINTER_THRESHOLD
#   KT:        KN_TOKEN_BUDGET_KT_RATIO
#   Token:     KN_TOKEN_BUDGET_TOTAL, KN_TOKEN_BUDGET_HINDSIGHT_RATIO
#   跨域去重:  KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR
PARAM_DEFS = [
    # === Hindsight 路 ===
    ("KN_MIN_SCORE",               0.50, 0.40, 0.65, 0.05, "kn_judge_relevant_rate,kn_judge_avg_relevance,router_empty_pct"),
    ("KN_MAX_RESULTS",             3,    2,    8,    1,    "kn_judge_relevant_rate,kn_judge_avg_relevance,memory_hindsight_count"),
    ("KN_MAX_TEXT_LENGTH",         200,  120,  400,  50,   "token_exhaust_pct,kn_judge_relevant_rate"),
    ("KN_TEMPORAL_HALFLIFE",       30,   14,   90,   7,    "kn_judge_relevant_rate,kn_judge_avg_relevance"),
    ("KN_TEMPORAL_FLOOR_WEIGHT",   0.5,  0.3,  0.8,  0.1,  "kn_judge_relevant_rate"),
    # === SAG 路 ===
    ("KN_SAG_MAX_INJECT",          3.0,  2.0,  6.0,  1.00, "sag_total_kept"),
    ("KN_SAG_SEARCH_TOP_K",        3,    3,    10,   1,    "sag_merge_zero_pct,sag_total_kept"),
    ("KN_SAG_MIN_SCORE",           0.5,  0.3,  0.8,  0.05, "sag_on_pct,sag_total_kept"),
    ("KN_SAG_POINTER_THRESHOLD",   300,  150,  800,  100,  "sag_total_kept,token_exhaust_pct"),
    # === 知识树路（token 配额，与 hindsight_ratio 联动，和≈1.0）===
    ("KN_TOKEN_BUDGET_KT_RATIO",   0.4,  0.2,  0.5,  0.05, "memory_hindsight_count,sag_total_kept"),
    # === Token 预算 ===
    ("KN_TOKEN_BUDGET_HINDSIGHT_RATIO", 0.4, 0.3, 0.6, 0.05, "memory_hindsight_count,sag_total_kept"),
    ("KN_TOKEN_BUDGET_TOTAL",      4000, 2000, 8000, 500,  "token_exhaust_pct"),
    # === 跨域去重 ===
    ("KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR", 0.5, 0.3, 0.8, 0.1, "kn_judge_relevant_rate,sag_total_kept"),
]

FEEDBACK_KEYS = [
    "kn_avg_score", "router_empty_pct", "sag_total_kept",
    "sag_merge_zero_pct", "memory_hindsight_count",
    "sag_on_pct", "token_exhaust_pct",
    # KN LLM Judge 质量评估（collect_baseline.py --judge 产出），
    # 作为 KN_MIN_SCORE 调优的主反馈：
    #   kn_judge_relevant_rate  (0~1, 越大越好) = judged 中评分 >= 0.5 占比
    #   kn_judge_avg_relevance  (0~1, 越大越好) = judged 所有 LLM 评分均值
    #   kn_judge_sample_count   (int)            = 本轮 judge 样本量，用于可信度判断
    "kn_judge_relevant_rate", "kn_judge_avg_relevance", "kn_judge_sample_count",
]

# KN Judge 子配置：控制健康巡检报告何时触发、最小样本量、最大耗时保护等
KN_JUDGE_CFG = {
    "enabled": True,                  # 集成开关（关了就走 kn_avg_score）
    "sample_size": 200,               # 每次 judge 采样条数（最近 N 条）
    "min_sample": 50,                 # 样本不足时跳过本轮 judge（避免小样本噪声干扰调优）
    "parallel": 5,                    # 并发度（与 JUDGE_PARALLEL 一致）
    "max_walltime_sec": 3600,         # 硬超时（1 小时），防止阻塞整个报告
    "min_age_breakpoint_hours": 6,    # 本轮 date 窗口至少需要 6 小时数据才输出到 daily summary
    "fallback_on_fail": True,        # judge 失败时用 kn_avg_score/recall kept 粗估写回，防止反馈断裂
}

# 收敛/锁定/暂停阈值
NO_CHANGE_LOCK_THRESHOLD = 3
CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD = 3
COOLDOWN_DAYS_AFTER_APPLY = 0
