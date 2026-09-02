"""config.py — 全局常量：路径、阈值、映射表。

从 flywheel-health-report.py L37-182 和 auto-tuner.py L40-63 搬入。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
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
    "skill_f1_low": 0.3,           # <0.3 -> P0（2026-09-02 从 0.4 下调：优化后正常水平 0.36-0.37，0.4 会误报；0.3 以下才视为明显退化）
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
# 包括：核心飞轮 + 新增（dream-daily、每周深度研究、system-health-self-heal）
# 注：knowledge-navigation-baseline / run-skill-eval 已合并进 runner（阶段 0/1 内部执行），
#     不再作为独立 cronjob 监控；clustering-analysis 已取消（测试无实际效果）。
# 2026-09-02 修正：system-health-check 已更名 system-health-self-heal（旧 state 文件为 08-23 残留孤儿）。
ACTIVE_CRON_JOBS = frozenset({
    # 核心飞轮
    "memory-cleanup",
    "skillopt-nightly-run",
    "daily-learn",
    "knowledge-tree-consolidate",
    "knowledge-tree-kvector",
    "kn-router-health-check",
    # 新增：之前未跟踪的 job
    "dream-daily",
    "每周深度研究-知识树学习",
    "system-health-self-heal",
    # 能力飞轮：Self-Evolving 自动写回闭环（F-5 + B）
    "self-evolving-nightly",
})

# 已知的非飞轮 state 文件白名单（cron 基础设施 job，不纳入巡检报告）
EXCLUDED_STATE_FILES = frozenset({
    "cron-boot-detect",
    "cron-periodic-dedup",
    "flywheel-health-report",
    "deploy-cleanup-health-check",
    # 2026-09-02 修正：旧名 system-health-check 是更名前的残留 state，已非活跃任务；
    # 新名 system-health-self-heal 已加入 ACTIVE_CRON_JOBS 正常监控
    "system-health-check",
})

# === Flywheel mapping ===
# 注：knowledge-navigation-baseline / run-skill-eval 已合并进 runner 内部执行，
#     不再出现在 cron state / jobs.json 中；clustering-analysis 已取消。
_CRON_TO_FLYWHEEL = {
    "skillopt-nightly-run": "Skill",
    "memory-cleanup": "记忆",
    "knowledge-tree-consolidate": "知识树",
    "knowledge-tree-kvector": "知识树",
    "daily-learn": "知识路",
    "dream-daily": "知识路",
    "每周深度研究-知识树学习": "知识树",
    "system-health-self-heal": "系统",
    "kn-router-health-check": "Router",
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
    # === 知识树建边（闭环扩展）：auto-tuner 基于 kt_orphan_pct 反馈调节建边相似度阈值，
    #     孤儿率偏高→降低阈值→更多同科高相似边→孤儿率下降（闭环自愈）。
    #     下界 0.55：相似度中位数约 0.467，低于 0.55 会连入语义弱相关 KP（噪声边），故设 floor。
    ("KT_EDGE_SIM_THRESHOLD",      0.65, 0.55, 0.85, 0.05, "kt_orphan_pct"),

    # === 知识树候选参数闭环（Phase A/B/C）===
    # 接入三要件：① 消费侧有 env 读取（config.py env_mapping / complex.py / cli.py）
    #            ② 反馈键已注册于 FEEDBACK_KEYS
    #            ③ 反馈每轮由 analyzer 真实计算并写入 daily summary
    # 注意：所有阈值范围刻意收紧为安全区间，即便 auto-tuner 推到边界也不会破坏知识树。
    # --- Phase A ---
    # K1: 跨 subject 向量桥接建边阈值（complex.py:166）。与 same_subject 同效应：阈值↓→边↑→孤儿率↓。
    ("KT_VECTOR_EDGE_SIM_THRESHOLD", 0.65, 0.55, 0.85, 0.05, "kt_orphan_pct"),
    # K3: 域合并余弦阈值（cli.py / consolidation.py）。域划分粒度：阈值↑→更少合并→碎片域↑。
    ("KT_DOMAIN_MERGE_THRESHOLD",   0.60, 0.40, 0.80, 0.05, "kt_fragment_domains"),
    # K2 subject_match_threshold（科目匹配阈值）延后接入：经全仓核查该字段无任何消费方
    #   （仅定义于 config.py 默认值与 default.yaml），auto-tuning 无因果反馈 → 违反「无无效参数」原则，
    #   故仅保留 env 映射（KT_SUBJECT_MATCH_THRESHOLD）待未来接入 subject 匹配逻辑后再进 PARAM_DEFS。
    # --- Phase B ---
    # K4: 阶段1候选数上限（merged.py:220）。上限↑→覆盖度↑但噪声率↑。
    ("KT_MAX_CANDIDATES_PER_ARTICLE", 15, 5, 40, 5, "kt_candidate_noise_rate"),
    # K5: 拆解轮数上限（split.py:307）。上限↑→分解更深→过度拆解率↑。
    ("KT_SPLIT_MAX_ROUNDS", 2, 1, 5, 1, "kt_over_split_rate"),
    # --- Phase C（K6-K9，已确认被消费方真实读取）---
    # K6: 文章截断长度（merged.py:142）。↑→信息更全→低置信 KP 占比↓。
    ("KT_ARTICLE_MAX_CHARS", 12000, 4000, 30000, 2000, "kt_low_conf_kp_rate"),
    # K7: 直接判重阈值（run.py:440）。↑→更严格判重→保留更多→低置信占比↑（噪声）。
    ("KT_DEDUP_THRESHOLD_DIRECT", 0.95, 0.85, 0.99, 0.01, "kt_low_conf_kp_rate"),
    # K8: LLM 确认区间下界（run.py:441）。与 K7 同向。
    ("KT_DEDUP_THRESHOLD_LLM", 0.90, 0.80, 0.98, 0.01, "kt_low_conf_kp_rate"),
    # K9: 矛盾检测阈值（run.py:435 已 plumbbed 到 admit）。↑→更少冲突标记→待审积压↓。
    ("KT_CONFLICT_THRESHOLD", 0.80, 0.60, 0.95, 0.05, "kt_pending_conflict_rate"),

    # === 能力飞轮 / Self-Evolving 候选参数闭环（Phase B/C）===
    # S1: 反思置信度阈值（kanban_reflection from_env）。↑→仅高置信反思被采纳→采纳率↓但质量↑。
    ("KN_REFLECTION_CONFIDENCE", 0.6, 0.3, 0.9, 0.05, "se_reflection_accept_rate"),
    # S2: 反思读取最近 N 轮 trace（kanban_reflection from_env）。↑→上下文更全→平均置信↑。
    ("KN_REFLECTION_MAX_TRACE_LINES", 5, 1, 15, 1, "se_reflection_mean_confidence"),
    # S3-S6: 重组算子阈值（recombination.py RecombinationConfig.from_env）。
    #   当前 SE 夜行 driver 仅运行 revise→refine，未接入重组；反馈键 se_recombine_synergy_avg
    #   在重组未启用时为 0（非 None）→ auto-tuner 自动锁定，不会伪优化。
    #   启用方式：在 self-evolving-nightly 调度设 SE_ENABLE_RECOMBINE=1，driver 记录 synergy_score。
    # S3: 重组组件上限。
    ("SE_RECOMBINE_MAX_COMPONENTS", 5, 2, 10, 1, "se_recombine_synergy_avg"),
    # S4: 语义相似合并阈值。
    ("SE_RECOMBINE_SEMANTIC_SIM", 0.7, 0.5, 0.9, 0.05, "se_recombine_synergy_avg"),
    # S5: 冲突严重度阈值。
    ("SE_RECOMBINE_CONFLICT_SEVERITY", 0.5, 0.3, 0.8, 0.05, "se_recombine_synergy_avg"),
    # S6: Jaccard 快判上下界（两个独立 env 变量，共用同一质量反馈）。
    ("SE_RECOMBINE_JACCARD_LOW", 0.3, 0.1, 0.5, 0.05, "se_recombine_synergy_avg"),
    ("SE_RECOMBINE_JACCARD_HIGH", 0.7, 0.5, 0.9, 0.05, "se_recombine_synergy_avg"),
]

# ============================================================
# 召回护栏（多目标约束 · B 项落地）
# ============================================================
# 背景：auto-tuner 用 joint_majority 软投票定组方向，当精度指标（kn_judge_*）
# 占多数票时会把所有成员（含收紧型过滤参数）一起上调，把召回（router_empty_pct /
# sag_on_pct）压塌——即"单目标过拟合"。本护栏作为「硬约束层」叠加在策略之上：
# 当某召回指标越界（cmp 成立）时，被守护参数的「收紧方向」被禁止；若组策略仍要求
# 沿该方向移动，则强制反向（loosen），给召回恢复空间。软投票保留，仅加硬顶。
#
# 字段：
#   metric   daily summary 中的反馈键
#   cmp      "ge" 表示 metric >= bound 触发（上限，down_better，如空结果率）
#            "le" 表示 metric <= bound 触发（下限，up_better，如 SAG 开启率）
#   bound    触发阈值（选值介于「健康线 <10」与「告警线 20」之间，取 15 作操作红线）
#   label    人类可读标签（写进调优 reason 便于审计）
#   guards   [(param, tighten_dir), ...]
#            tighten_dir = 该参数「收紧过滤」的方向（"up"/"down"），越界时禁止此方向、
#            强制反向（loosen）。只列真正收紧召回的参数，'loose 型'参数（如
#            KN_MAX_RESULTS / KN_SAG_SEARCH_TOP_K ↑=更松）不列入，避免反向误伤。
RECALL_GUARDS = [
    {
        "metric": "router_empty_pct", "cmp": "ge", "bound": 15.0,
        "label": "空结果率上限",
        "guards": [
            ("KN_MIN_SCORE", "up"),
            ("KN_SCORE_SPAN_TOP3_THRESHOLD", "up"),
            ("KN_SCORE_SPAN_HALF_THRESHOLD", "up"),
        ],
    },
    {
        "metric": "sag_on_pct", "cmp": "le", "bound": 10.0,
        "label": "SAG 开启率下限",
        "guards": [
            ("KN_SAG_MIN_SCORE", "up"),
            ("KN_SAG_POINTER_THRESHOLD", "up"),
        ],
    },
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
    "kt_orphan_pct",
    # 知识树闭环扩展反馈键（来自 kt-baseline-latest.json 的 metrics，analyzer 每轮计算）
    "kt_fragment_domains",
    "kt_candidate_noise_rate",     # 候选噪声率（低置信 KP 占比代理），越低越好
    "kt_over_split_rate",          # 过度拆解率（碎片域/总域），越低越好
    "kt_low_conf_kp_rate",         # 低置信 KP 占比，越低越好（K6/K7/K8 共用）
    "kt_pending_conflict_rate",    # 待审矛盾积压率，越低越好（K9）
    # 能力飞轮 / Self-Evolving 闭环扩展反馈键（来自 self-evolving output JSON，analyzer 每轮计算）
    "se_reflection_accept_rate",       # 反思置信度达标率，越高越好（S1）
    "se_reflection_mean_confidence",  # 反思平均置信度，越高越好（S2）
    "se_recombine_synergy_avg",       # 重组协同得分均值，越高越好（S3-S6，需启用重组）
]

# ============================================================
# 参数功能组（候选参数闭环 · 分组并行调优）
# ============================================================
# 设计动机：候选参数按「功能」天然成组，组内参数相互耦合（共享反馈键或竞争同一指标），
# 若像旧逻辑那样「每次只动一个参数、隔很多天各调一次」，会打破耦合、收敛慢且可能互相抵消。
# 新模型：auto-tuner 按 *组* 并行调优——每轮把全部「反馈可信且未收敛」的组都调了，
# 组内多个耦合参数按该组的策略 *一起* 移动。
#
# GroupSpec 字段：
#   gid          组 id（state/日志主键）
#   label        人类可读名
#   members      组内参数名元组（必须都在 PARAM_DEFS 中）
#   strategy     调优策略：
#                 "joint_majority"  多参耦合组：组内反馈键多数投票定「组方向」，
#                                    改善→各成员沿其有利方向同调，恶化→整体反向。
#                 "single"          单参组：沿用原 determine_direction 单参逻辑（退化为组合适）。
#                 "synergy_search"  重组组：以 se_recombine_synergy_avg 单一标量驱动，
#                                    改善→沿上次方向微调，恶化→整体回滚到上次联合移动前的值。
#   enabled_when 可选：仅当该 env 变量为真时组才参与（如因果链组依赖 KN_ENABLE_CAUSAL_CHAIN）。
#   feedback     留空→自动取 members 在 PARAM_DEFS 中 feedback_csv 的并集（推荐）。
@dataclass(frozen=True)
class GroupSpec:
    gid: str
    label: str
    members: tuple
    strategy: str
    enabled_when: str | None = None
    feedback: tuple = ()
    note: str = ""

    def feedback_keys(self) -> List[str]:
        if self.feedback:
            return list(self.feedback)
        keys: List[str] = []
        for m in self.members:
            pdef = next((p for p in PARAM_DEFS if p[0] == m), None)
            if not pdef:
                continue
            for k in (s.strip() for s in pdef[5].split(",") if s.strip()):
                if k not in keys:
                    keys.append(k)
        return keys


# 功能组清单（顺序即日志/调试确定性顺序，不决定调优先后——每轮所有可信组并行）
PARAM_GROUPS: List[GroupSpec] = [
    # —— 数据飞轮：知识导航 4 路 ——
    GroupSpec("hindsight", "Hindsight 召回路",
              ("KN_MIN_SCORE", "KN_MAX_RESULTS", "KN_MAX_TEXT_LENGTH",
               "KN_TEMPORAL_HALFLIFE", "KN_TEMPORAL_FLOOR_WEIGHT"),
              "joint_majority"),
    GroupSpec("sag", "SAG 召回路",
              ("KN_SAG_MAX_INJECT", "KN_SAG_SEARCH_TOP_K", "KN_SAG_MIN_SCORE",
               "KN_SAG_POINTER_THRESHOLD"),
              "joint_majority"),
    GroupSpec("xdedup", "跨域去重", ("KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR",), "single"),
    GroupSpec("rerank", "全局打分/重排",
              ("KN_LAMBDA_MRR", "KN_SCORE_SPAN_TOP3_THRESHOLD", "KN_SCORE_SPAN_HALF_THRESHOLD"),
              "joint_majority"),
    GroupSpec("causal", "因果链提权",
              ("KN_CAUSAL_BOOST_ALPHA", "KN_CAUSAL_BOOST_CAP"),
              "joint_majority", enabled_when="KN_ENABLE_CAUSAL_CHAIN"),
    # —— Skill 控制环 F-2 / SAG 生产端 F-3 ——
    GroupSpec("skillctl", "Skill 控制环 F-2",
              ("SKILLOPT_MAX_PER_NIGHT", "SKILLOPT_COOLDOWN_DAYS"), "joint_majority"),
    GroupSpec("sagprod", "SAG 生产端 F-3", ("DREAM_PROMOTE_THRESHOLD",), "single"),
    # —— 知识树 ——
    GroupSpec("kt_edge", "知识树建边",
              ("KT_EDGE_SIM_THRESHOLD", "KT_VECTOR_EDGE_SIM_THRESHOLD"), "joint_majority"),
    GroupSpec("kt_domain", "知识树域划分", ("KT_DOMAIN_MERGE_THRESHOLD",), "single"),
    GroupSpec("kt_split", "知识树候选/拆解",
              ("KT_MAX_CANDIDATES_PER_ARTICLE", "KT_SPLIT_MAX_ROUNDS"), "joint_majority"),
    GroupSpec("kt_quality", "知识树抽取质量",
              ("KT_ARTICLE_MAX_CHARS", "KT_DEDUP_THRESHOLD_DIRECT",
               "KT_DEDUP_THRESHOLD_LLM", "KT_CONFLICT_THRESHOLD"),
              "joint_majority"),
    # —— Self-Evolving 能力飞轮 ——
    GroupSpec("se_reflect", "Self-Evolving 反思",
              ("KN_REFLECTION_CONFIDENCE", "KN_REFLECTION_MAX_TRACE_LINES"), "joint_majority"),
    GroupSpec("se_recombine", "Self-Evolving 重组",
              ("SE_RECOMBINE_MAX_COMPONENTS", "SE_RECOMBINE_SEMANTIC_SIM",
               "SE_RECOMBINE_CONFLICT_SEVERITY", "SE_RECOMBINE_JACCARD_LOW",
               "SE_RECOMBINE_JACCARD_HIGH"),
              "synergy_search"),
]

# gid → GroupSpec 快速查表
GROUP_BY_ID = {g.gid: g for g in PARAM_GROUPS}
# 参数名 → 所属组（一个参数只属于一个组）
PARAM_TO_GROUP: Dict[str, str] = {}
for _g in PARAM_GROUPS:
    for _m in _g.members:
        PARAM_TO_GROUP[_m] = _g.gid

# 分组并行调优总开关 + 并行度上限
#   GROUP_TUNING_ENABLED=False → 退回旧的单参 select_param_to_tune 行为（兼容回滚）
#   MAX_GROUPS_PER_RUN=0       → 0 表示不限，每轮调全部「可信且未收敛」的组（默认）
GROUP_TUNING_ENABLED = True
MAX_GROUPS_PER_RUN = 0

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
