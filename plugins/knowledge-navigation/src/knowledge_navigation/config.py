"""Knowledge navigation plugin configuration and logging setup."""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


class JSONFormatter(logging.Formatter):
    """统一 JSON 日志格式器。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        # 包含通过 extra= 传入的自定义字段
        for key, value in record.__dict__.items():
            if key not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
                "asctime",
                "exc_info",
                "exc_text",
                "stack_info",
            }:
                log_entry[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

_TEST_QUERY_EXACT = {
    "test recall system",
    "help me search test query",
    "test eval query about memory",
    "unrelated topic here",
}


def is_test_trace_record(record_or_data: logging.LogRecord | dict) -> bool:
    """识别 pytest/mock 产生的 trace 记录，避免污染生产基线。"""
    if isinstance(record_or_data, dict):
        query = str(record_or_data.get("query_trunc", "") or "")
        error = str(record_or_data.get("error", "") or "")
    else:
        query = str(getattr(record_or_data, "query_trunc", "") or "")
        error = str(getattr(record_or_data, "error", "") or "")

    if query in _TEST_QUERY_EXACT:
        return True
    if "exact match query about memory" in query:
        return True
    if query.startswith("LiteLLM 配置出问题了"):
        return True
    return error in {"RuntimeError: API down", "RuntimeError: down"}


class TraceRecordFilter(logging.Filter):
    """过滤测试/模拟 query，防止 pytest 熔断记录写入生产 trace.log。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not is_test_trace_record(record)


@dataclass
class KnowledgeNavigationConfig:
    """Knowledge navigation plugin configuration，支持 ENV 变量覆盖。"""

    # Hindsight API configuration
    hindsight_api_url: str = field(
        default="http://localhost:9177/v1/default/banks/hermes/memories/recall"
    )

    # Recall behavior
    max_results: int = field(default=3)
    max_text_length: int = field(default=200)
    min_score: float = field(default=0.35)

    # Performance
    timeout_seconds: int = field(default=30)
    max_retries: int = field(default=2)

    # Logging
    trace_log_path: str = field(default="")
    max_trace_bytes: int = field(default=50 * 1024 * 1024)
    backup_count: int = field(default=3)

    # Debug mode
    debug_mode: bool = field(default=False)

    # Temporal fusion
    enable_temporal: bool = field(default=True)
    temporal_weight: float = field(default=0.5)  # 保留兼容，公式改为乘法时不再使用
    temporal_halflife_days: int = field(default=30)  # 时间衰减半衰期（天），越大衰老越慢
    temporal_floor_weight: float = field(default=0.5)  # 乘法融合保底系数，0.5=旧记忆至少保留50%基础分

    # Circuit breaker
    circuit_breaker_threshold: int = field(default=3)
    circuit_breaker_cooldown: int = field(default=120)

    # Evaluation
    eval_queries_path: str = field(default="/root/.hermes/data/eval_queries.json")
    eval_log_path: str = field(default="")
    eval_min_score: float = field(default=0.1)

    # Causal chain boost
    enable_causal_chain: bool = field(default=True)
    causal_boost_alpha: float = field(default=0.15)  # 提权系数
    causal_boost_cap: float = field(default=1.3)     # 最大提权上限

    # MMR diversity
    lambda_mrr: float = field(default=0.5)  # MMR λ，越大越重相关性

    # CE score span compression
    enable_score_span_compress: bool = field(default=True)  # 启用 CE 分数跨度压缩
    score_span_top3_threshold: float = field(default=0.9)   # top-3 阈值
    score_span_half_threshold: float = field(default=0.7)    # 半切阈值

    # Cross-domain dedup mode (2026-06-13)
    cross_domain_dedup_mode: str = field(default="text_only")  # text_only | text_embedding
    # text_only: 仅字符 n-gram Jaccard，无 API 调用
    # text_embedding: embed_fn 可用时 fallback 到 embedding

    # Evaluation toggle (2026-06-13)
    eval_match_enabled: bool = field(default=True)  # 生产环境可关闭 eval_match

    # Turn-to-turn dedup mode (2026-06-13)
    turn_to_turn_dedup_mode: str = field(default="demote")  # demote | remove
    # demote: 降低已注入记忆的分数而非完全移除
    # remove: 原行为，硬删除

    # Cross-domain dedup action (2026-06-28)
    cross_domain_dedup_action: str = field(default="demote")  # demote | remove
    # demote: 重复的知识树结果降权但保留，由后续分数过滤/top-k 自然淘汰
    # remove: 原行为，硬删除重复的知识树结果
    cross_domain_dedup_demote_factor: float = field(default=0.5)  # 降权系数

    # Notification (Feishu Open API)
    feishu_app_id: str = field(default="")
    feishu_app_secret: str = field(default="")
    feishu_home_channel: str = field(default="")

    # LLM Router
    router_model: str = field(default="s-deepseek-v4-flash")
    router_api_url: str = field(default="http://127.0.0.1:4142/v1")
    router_api_key: str = field(default="")
    router_timeout: int = field(default=15)  # 5s 太紧，15s 减少超时率

    # Skill Matcher: 三级筛选架构（关键词 + Embedding + LLM）
    # 注意：kn_skill_keyword_prescreen 已废弃，match_skills 现为纯 LLM 全量匹配
    # 保留配置项仅为向后兼容，实际不生效
    kn_skill_keyword_prescreen: bool = field(default=True)

    # Skill Matcher: Embedding 预筛选（Hybrid 模式，在关键词之后）
    kn_skill_embedding_prescreen: bool = field(default=False)
    kn_skill_embedding_model: str = field(default="BAAI/bge-m3")
    kn_skill_embedding_url: str = field(default="https://api.siliconflow.cn/v1")
    kn_skill_embedding_api_key: str = field(default="")
    kn_skill_embedding_top_k: int = field(default=20)  # embedding 筛选后的候选数

    # SAG API configuration
    sag_api_url: str = field(default="http://127.0.0.1:4173")
    sag_search_top_k: int = field(default=3)
    sag_search_timeout: int = field(default=10)
    sag_source_ids: str = field(default="00000000-0000-0000-0000-0000000000ff")
    sag_max_inject: int = field(default=3)  # merge 时最多注入条数（SAG topK 只控 vector 路，multi-hop 不受控）
    sag_pointer_threshold: int = field(default=300)  # content 超过此字符数时改注入指针，LLM 按需查全文
    enable_token_budget: bool = field(default=True)
    token_budget_total: int = field(default=4000)
    token_budget_hindsight_ratio: float = field(default=0.4)
    token_budget_kt_ratio: float = field(default=0.4)
    token_budget_skill_ratio: float = field(default=0.2)

    # Memory use log (P2-2 Phase A)
    enable_use_log: bool = field(default=True)
    use_log_batch_size: int = field(default=10)
    use_log_flush_interval_seconds: int = field(default=30)
    use_log_path: str = field(default="")

    # Skill index incremental update (P2-3)
    skill_index_incremental: bool = field(default=True)

    @classmethod
    def from_env(cls, defaults: dict | None = None) -> "KnowledgeNavigationConfig":
        """从环境变量加载配置，覆盖默认值。

        优先级：ENV 变量 > 传入的 defaults > 代码默认值。
        """
        values = dict(defaults) if defaults else {}
        if env_url := os.getenv("KN_HINDSIGHT_URL"):
            values["hindsight_api_url"] = env_url
        if env_results := os.getenv("KN_MAX_RESULTS"):
            values["max_results"] = int(env_results)
        if env_length := os.getenv("KN_MAX_TEXT_LENGTH"):
            values["max_text_length"] = int(env_length)
        if env_score := os.getenv("KN_MIN_SCORE"):
            values["min_score"] = float(env_score)
        if env_timeout := os.getenv("KN_TIMEOUT_SECONDS"):
            values["timeout_seconds"] = int(env_timeout)
        if env_retries := os.getenv("KN_MAX_RETRIES"):
            values["max_retries"] = int(env_retries)
        if env_trace := os.getenv("KN_TRACE_LOG_PATH"):
            values["trace_log_path"] = env_trace
        if env_bytes := os.getenv("KN_MAX_TRACE_BYTES"):
            values["max_trace_bytes"] = int(env_bytes)
        if env_backup := os.getenv("KN_BACKUP_COUNT"):
            values["backup_count"] = int(env_backup)
        if env_debug := os.getenv("KN_DEBUG_MODE"):
            values["debug_mode"] = env_debug.lower() in ("1", "true", "yes")
        if env_temporal := os.getenv("KN_ENABLE_TEMPORAL"):
            values["enable_temporal"] = env_temporal.lower() in ("1", "true", "yes")
        if env_weight := os.getenv("KN_TEMPORAL_WEIGHT"):
            values["temporal_weight"] = float(env_weight)
        if env_halflife := os.getenv("KN_TEMPORAL_HALFLIFE"):
            values["temporal_halflife_days"] = int(env_halflife)
        if env_floor := os.getenv("KN_TEMPORAL_FLOOR_WEIGHT"):
            values["temporal_floor_weight"] = float(env_floor)
        if env_eval_path := os.getenv("KN_EVAL_QUERIES_PATH"):
            values["eval_queries_path"] = env_eval_path
        if env_eval_log := os.getenv("KN_EVAL_LOG_PATH"):
            values["eval_log_path"] = env_eval_log
        if env_eval_score := os.getenv("KN_EVAL_MIN_SCORE"):
            values["eval_min_score"] = float(env_eval_score)
        if env_cb_threshold := os.getenv("KN_CB_THRESHOLD"):
            values["circuit_breaker_threshold"] = int(env_cb_threshold)
        if env_cb_cooldown := os.getenv("KN_CB_COOLDOWN"):
            values["circuit_breaker_cooldown"] = int(env_cb_cooldown)
        if env_causal := os.getenv("KN_ENABLE_CAUSAL_CHAIN"):
            values["enable_causal_chain"] = env_causal.lower() in ("1", "true", "yes")
        if env_app_id := os.getenv("FEISHU_APP_ID"):
            values["feishu_app_id"] = env_app_id
        if env_app_secret := os.getenv("FEISHU_APP_SECRET"):
            values["feishu_app_secret"] = env_app_secret
        if env_channel := os.getenv("FEISHU_HOME_CHANNEL"):
            values["feishu_home_channel"] = env_channel
        if env_dedup := os.getenv("KN_CROSS_DOMAIN_DEDUP_MODE"):
            values["cross_domain_dedup_mode"] = env_dedup
        if env_eval := os.getenv("KN_EVAL_MATCH_ENABLED"):
            values["eval_match_enabled"] = env_eval.lower() in ("1", "true", "yes")
        if env_ttd := os.getenv("KN_TURN_TO_TURN_MODE"):
            values["turn_to_turn_dedup_mode"] = env_ttd
        if env := os.getenv("KN_CROSS_DOMAIN_DEDUP_ACTION"):
            values["cross_domain_dedup_action"] = env
        if env := os.getenv("KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR"):
            values["cross_domain_dedup_demote_factor"] = float(env)
        if env := os.getenv("KN_ROUTER_MODEL"):
            values["router_model"] = env
        if env := os.getenv("KN_ROUTER_API_URL"):
            values["router_api_url"] = env
        if env := os.getenv("KN_ROUTER_API_KEY"):
            values["router_api_key"] = env
        if env := os.getenv("KN_ROUTER_TIMEOUT"):
            values["router_timeout"] = int(env)
        if env := os.getenv("KN_SKILL_KEYWORD_PRESCREEN"):
            values["kn_skill_keyword_prescreen"] = env.lower() in ("1", "true", "yes")
        if env := os.getenv("KN_SKILL_EMBEDDING_PRESCREEN"):
            values["kn_skill_embedding_prescreen"] = env.lower() in ("1", "true", "yes")
        if env := os.getenv("KN_SKILL_EMBEDDING_MODEL"):
            values["kn_skill_embedding_model"] = env
        if env := os.getenv("KN_SKILL_EMBEDDING_URL"):
            values["kn_skill_embedding_url"] = env
        if env := os.getenv("KN_SKILL_EMBEDDING_API_KEY"):
            values["kn_skill_embedding_api_key"] = env
        elif env := os.getenv("SILICONFLOW_API_KEY"):
            values["kn_skill_embedding_api_key"] = env
        if env := os.getenv("KN_SKILL_EMBEDDING_TOP_K"):
            values["kn_skill_embedding_top_k"] = int(env)
        if env := os.getenv("KN_ENABLE_TOKEN_BUDGET"):
            values["enable_token_budget"] = env.lower() in ("1", "true", "yes")
        if env := os.getenv("KN_TOKEN_BUDGET_TOTAL"):
            values["token_budget_total"] = int(env)
        if env := os.getenv("KN_TOKEN_BUDGET_HINDSIGHT_RATIO"):
            values["token_budget_hindsight_ratio"] = float(env)
        if env := os.getenv("KN_TOKEN_BUDGET_KT_RATIO"):
            values["token_budget_kt_ratio"] = float(env)
        if env := os.getenv("KN_TOKEN_BUDGET_SKILL_RATIO"):
            values["token_budget_skill_ratio"] = float(env)
        if env := os.getenv("KN_SAG_API_URL"):
            values["sag_api_url"] = env
        if env := os.getenv("KN_SAG_SEARCH_TOP_K"):
            values["sag_search_top_k"] = int(env)
        if env := os.getenv("KN_SAG_SEARCH_TIMEOUT"):
            values["sag_search_timeout"] = int(env)
        if env := os.getenv("KN_SAG_SOURCE_IDS"):
            values["sag_source_ids"] = env
        if env := os.getenv("KN_SAG_MAX_INJECT"):
            values["sag_max_inject"] = int(env)
        if env := os.getenv("KN_SAG_POINTER_THRESHOLD"):
            values["sag_pointer_threshold"] = int(env)
        if env := os.getenv("KN_ENABLE_USE_LOG"):
            values["enable_use_log"] = env.lower() in ("1", "true", "yes")
        if env := os.getenv("KN_USE_LOG_BATCH_SIZE"):
            values["use_log_batch_size"] = int(env)
        if env := os.getenv("KN_USE_LOG_FLUSH_INTERVAL"):
            values["use_log_flush_interval_seconds"] = int(env)
        if env := os.getenv("KN_USE_LOG_PATH"):
            values["use_log_path"] = env
        if env := os.getenv("KN_SKILL_INDEX_INCREMENTAL"):
            values["skill_index_incremental"] = env.lower() in ("1", "true", "yes")
        return cls(**values)


def setup_logging() -> None:
    """初始化日志系统。

    只写入 trace.log 文件，不输出到终端，避免干扰用户交互。
    使用 RotatingFileHandler 按 50MB 轮转，保留 3 个备份。
    配置可通过 KN_MAX_TRACE_BYTES / KN_BACKUP_COUNT 环境变量覆盖。
    """
    formatter = JSONFormatter()
    kn_logger = logging.getLogger("knowledge_navigation")

    # 如果配置了 trace_log_path，添加 RotatingFileHandler（跳过重复路径）
    trace_path = os.getenv("KN_TRACE_LOG_PATH")  # 优先用环境变量
    if not trace_path:
        trace_path = CONFIG.trace_log_path if hasattr(CONFIG, "trace_log_path") and CONFIG.trace_log_path else ""
    if trace_path:
        abs_path = os.path.abspath(trace_path)
        # 避免为同一路径重复添加 handler
        if not any(
            isinstance(h, RotatingFileHandler) and h.baseFilename == abs_path
            for h in kn_logger.handlers
        ):
            try:
                os.makedirs(os.path.dirname(trace_path) or ".", exist_ok=True)
                max_bytes = CONFIG.max_trace_bytes
                backup_count = CONFIG.backup_count
                fh = RotatingFileHandler(
                    trace_path,
                    mode="a",
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                fh.setFormatter(formatter)
                fh.addFilter(TraceRecordFilter())
                kn_logger.addHandler(fh)
                kn_logger.setLevel(logging.INFO)
                fh.setLevel(logging.INFO)
            except Exception:
                pass


# 默认全局配置实例（从环境变量加载）
CONFIG = KnowledgeNavigationConfig.from_env()

# 模块加载时自动初始化日志系统（任何进程 import 本模块即生效）
setup_logging()
