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
PARAM_DEFS = [
    ("KN_MIN_SCORE",               0.6, 0.4, 0.8, 0.05, "kn_avg_score,router_empty_pct"),
    ("sag_max_inject",             3.0, 2.0, 6.0, 1.00, "sag_total_kept"),
    ("sag_search_top_k",           3.0, 3.0, 10.0,1.00, "sag_merge_zero_pct"),
    ("token_budget_hindsight_ratio",0.4,0.3, 0.6, 0.05, "memory_hindsight_count,sag_total_kept"),
    ("sag_search_threshold",       0.5, 0.3, 0.8, 0.05, "sag_on_pct,sag_total_kept"),
    ("token_budget",               4000,2000,8000,500,  "token_exhaust_pct"),
]

FEEDBACK_KEYS = [
    "kn_avg_score", "router_empty_pct", "sag_total_kept",
    "sag_merge_zero_pct", "memory_hindsight_count",
    "sag_on_pct", "token_exhaust_pct",
]

# 收敛/锁定/暂停阈值
NO_CHANGE_LOCK_THRESHOLD = 3
CONSECUTIVE_DEGRADATION_SUSPEND_THRESHOLD = 3
COOLDOWN_DAYS_AFTER_APPLY = 0
