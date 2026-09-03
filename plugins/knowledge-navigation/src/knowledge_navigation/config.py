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
    recall_query_max_chars: int = field(default=800)  # recall query 最大字符数：超长 query 会被 Hindsight(400)/SAG(422) 拒绝，需截断（前 400 + 后 400）

    # Performance
    timeout_seconds: int = field(default=30)
    max_retries: int = field(default=2)
    hindsight_recall_max_retries: int = field(default=0)  # recall 链路专用：限制 Hindsight 重试，避免僵尸线程占满线程池（0=不重试，单次超时即放弃）
    kt_timeout_seconds: int = field(default=10)  # 知识树 recall（PG+pgvector）超时，远短于 Hindsight
    skill_timeout_seconds: int = field(default=60)  # skill 匹配含 LLM 精排(45s)+embedding，需更长窗口

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
    circuit_breaker_cooldown: int = field(default=90)  # 默认 2 分钟太长，缩短到 90 秒加快熔断自动恢复

    # Evaluation
    eval_queries_path: str = field(default="/root/.hermes/data/eval_queries.json")
    eval_log_path: str = field(default="")
    eval_min_score: float = field(default=0.1)

    # Causal chain boost
    enable_causal_chain: bool = field(default=True)
    causal_boost_alpha: float = field(default=0.05)  # 提权系数：默认 0.15 过于激进，调小到 0.05（可被 ENV 覆盖）
    causal_boost_cap: float = field(default=1.10)     # 提权上限倍率：1.3→1.1，最多 +10%，避免因果链把低相关条目顶前排

    # MMR diversity
    lambda_mrr: float = field(default=0.55)  # MMR λ：默认 0.5→0.55，略加强相关性

    # CE score span compression
    enable_score_span_compress: bool = field(default=True)  # 启用 CE 分数跨度压缩
    score_span_top3_threshold: float = field(default=0.85)  # top-3 阈值：0.9→0.85，更早触发压缩，提升多样性
    score_span_half_threshold: float = field(default=0.65)  # 半切阈值：0.7→0.65，压缩曲线更平滑

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
    router_model: str = field(default="agnes-2.5-flash")
    router_api_url: str = field(default="http://127.0.0.1:4142/v1")
    router_api_key: str = field(default="")
    router_timeout: int = field(default=15)  # 5s 太紧，15s 减少超时率

    # Skill Matcher LLM（精排阶段）
    skill_matcher_model: str = field(default="s-deepseek-v4-flash")
    skill_matcher_api_url: str = field(default="http://127.0.0.1:4142/v1")

    # Skill Matcher: 三级筛选架构（关键词 + Embedding + LLM）
    # keyword 预筛默认开启；关闭 kn_skill_keyword_prescreen 时退化为仅 LLM 全量匹配
    kn_skill_keyword_prescreen: bool = field(default=True)

    # Skill Matcher: Embedding 预筛选（Hybrid 模式，在关键词之后）
    kn_skill_embedding_prescreen: bool = field(default=True)  # 默认开启：先用 embedding 从全量 skill 库取 top-K 候选，再给 LLM，显著减少 skill 匹配的 LLM 调用量
    kn_skill_embedding_model: str = field(default="BAAI/bge-m3")
    kn_skill_embedding_url: str = field(default="https://api.siliconflow.cn/v1")
    kn_skill_embedding_api_key: str = field(default="")
    kn_skill_embedding_top_k: int = field(default=30)  # embedding 筛选后的候选数：默认 20→30，减少预筛阶段把潜在匹配技能过滤掉的概率

    # SkillRouter 语义召回后端（P0-1，自实现）
    # embedding 后端切换：api=远端 embedding API（默认，零风险）；skillrouter=本地 SkillRouter 0.6B 专用模型
    # 默认 api：生产行为完全不变；仅当显式置为 skillrouter 且环境就绪（模型+transformers）时才启用本地推理。
    kn_skill_embedding_backend: str = field(default="api")
    kn_skillrouter_embedding_dir: str = field(default="/root/.hermes/models/skillrouter/embedding")
    kn_skillrouter_reranker_dir: str = field(default="/root/.hermes/models/skillrouter/reranker")

    # Skill Matcher 2 步流程（embedding 主召回 + LLM 精排，≤3 早退）
    skill_embedding_main_top_k: int = field(default=30)  # embedding 主召回候选数（预筛覆盖与候选池大小的平衡点）
    skill_rerank_max_candidates: int = field(default=20)  # 精排输入上限（union 截断）

    # SAG API configuration
    sag_api_url: str = field(default="http://127.0.0.1:4173")
    sag_auth_token: str = field(default="")  # Bearer token for SAG v1.5.3+
    sag_api_search_path: str = field(default="/api/v1/search")  # SAG v1.5.3 API path
    sag_search_top_k: int = field(default=3)
    # 宽裕上限（非"必须 30s"）。旧注"单 worker 响应慢 ~21s"已过时：sag.service 现为
    # --workers 2，瓶颈一直是 LLM 答案合成而非 worker 数。KN 侧已传 include_summary=False
    # 跳过该合成，/search 实测由 ~3.9s 降至 ~0.15s，故 30s 留有极大余量。
    sag_search_timeout: int = field(default=30)
    sag_source_ids: str = field(default="33acad8140a04f4d835ac9a5a2eeef13")
    sag_max_inject: int = field(default=3)  # merge 时最多注入条数（SAG topK 只控 vector 路，multi-hop 不受控）
    sag_pointer_threshold: int = field(default=300)  # content 超过此字符数时改注入指针，LLM 按需查全文
    sag_min_score: float = field(default=0.35)  # SAG 独立 min_score，与 Hindsight 分开（SAG 原始 pgvector 得分集中在 0.4-0.7，与 cross-encoder 分不同分布）
    # 注：token 预算控制已于 2026-08-10 移除（只记录实际消耗，不做裁剪）。
    # 原 enable_token_budget / token_budget_total / token_budget_*_ratio 已删除。
    skill_max_chars_per_skill: int = field(default=4000)  # 单条 skill 注入字符上限（替代 router.py 内硬编码 4000）

    # Codegraph 符号级召回（P0-4）
    # 代码相关 query 经 subprocess 调 codegraph CLI 返回符号级结果（文件路径/行号/签名），
    # 不取 MCP 工具的 Tool Registry（hook 阶段无法作为 MCP 调用方）。
    codegraph_enabled: bool = field(default=True)  # 代码关键词命中时触发 codegraph 召回
    codegraph_bin: str = field(default="/root/.local/bin/codegraph")  # CLI 绝对路径（WSL 侧，非登录 shell 不在 PATH）
    codegraph_project_path: str = field(default="/mnt/d/HermesProject")  # 与 codegraph MCP 服务共用同一索引
    codegraph_timeout: int = field(default=5)  # subprocess 超时上限，绝不阻塞主召回链路
    codegraph_limit: int = field(default=5)  # 单次返回符号数量上限

    # Memory use log (P2-2 Phase A)
    enable_use_log: bool = field(default=True)
    use_log_batch_size: int = field(default=10)
    use_log_flush_interval_seconds: int = field(default=30)
    use_log_path: str = field(default="")

    # Skill index incremental update (P2-3)
    skill_index_incremental: bool = field(default=True)

    @classmethod
    def from_kit_config(cls) -> "KnowledgeNavigationConfig | None":
        """从 ~/.hermes-kit/config.yaml 加载配置。

        返回 None 表示 kit-config 不存在或无法读取，调用方应回退到 .env。
        """
        try:
            import yaml
            kit_home = os.getenv("HERMES_KIT_HOME", os.path.expanduser("~/.hermes-kit"))
            kit_config_path = os.path.join(kit_home, "config.yaml")
            if not os.path.isfile(kit_config_path):
                return None
            with open(kit_config_path) as f:
                cfg = yaml.safe_load(f) or {}
            pc = cfg.get("plugin_config", {})
            if not pc:
                return None
            # 映射 kit-config 键名 → KnowledgeNavigationConfig 字段名
            # kit-config 使用下划线命名，与 dataclass 字段名一致
            field_map = {
                "kn_min_score": "min_score",
                "kn_max_results": "max_results",
                "kn_max_text_length": "max_text_length",
                "kn_timeout_seconds": "timeout_seconds",
                "kn_max_retries": "max_retries",
                "kn_debug_mode": "debug_mode",
                "kn_enable_temporal": "enable_temporal",
                "kn_temporal_halflife": "temporal_halflife_days",
                "kn_temporal_floor_weight": "temporal_floor_weight",
                "kn_router_model": "router_model",
                "kn_router_timeout": "router_timeout",
                "kn_skill_embedding_prescreen": "kn_skill_embedding_prescreen",
                "kn_skill_embedding_top_k": "kn_skill_embedding_top_k",
                "kn_skill_index_incremental": "skill_index_incremental",
                "sag_search_top_k": "sag_search_top_k",
                "sag_search_timeout": "sag_search_timeout",
                "sag_auth_token": "sag_auth_token",
                "sag_api_search_path": "sag_api_search_path",
                "sag_max_inject": "sag_max_inject",
                "sag_pointer_threshold": "sag_pointer_threshold",
                "sag_min_score": "sag_min_score",
                "lambda_mrr": "lambda_mrr",
                "eval_min_score": "eval_min_score",
                "eval_match_enabled": "eval_match_enabled",
                "cross_domain_dedup_mode": "cross_domain_dedup_mode",
                "cross_domain_dedup_action": "cross_domain_dedup_action",
                "cross_domain_dedup_demote_factor": "cross_domain_dedup_demote_factor",
                "turn_to_turn_dedup_mode": "turn_to_turn_dedup_mode",
                "circuit_breaker_threshold": "circuit_breaker_threshold",
                "circuit_breaker_cooldown": "circuit_breaker_cooldown",
                "causal_boost_alpha": "causal_boost_alpha",
                "causal_boost_cap": "causal_boost_cap",
                "enable_score_span_compress": "enable_score_span_compress",
                "score_span_top3_threshold": "score_span_top3_threshold",
                "score_span_half_threshold": "score_span_half_threshold",
                "enable_causal_chain": "enable_causal_chain",
                "kn_lambda_mrr": "lambda_mrr",
                "enable_use_log": "enable_use_log",
                "use_log_batch_size": "use_log_batch_size",
                "use_log_flush_interval_seconds": "use_log_flush_interval_seconds",
            }
            values = {}
            for kit_key, field_name in field_map.items():
                if kit_key in pc:
                    raw = pc[kit_key]
                    # 获取 dataclass 字段类型做类型转换
                    field_type = cls.__dataclass_fields__[field_name].type
                    if field_type is bool:
                        values[field_name] = str(raw).lower() in ("1", "true", "yes")
                    elif field_type is int:
                        values[field_name] = int(raw)
                    elif field_type is float:
                        values[field_name] = float(raw)
                    else:
                        values[field_name] = raw
            return cls(**values) if values else None
        except Exception:
            _logger = logging.getLogger(__name__)
            _logger.warning("从 kit-config 加载配置失败", exc_info=True)
            return None

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
        if env_query_chars := os.getenv("KN_RECALL_QUERY_MAX_CHARS"):
            values["recall_query_max_chars"] = int(env_query_chars)
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
        if env := os.getenv("KN_CAUSAL_BOOST_ALPHA"):
            values["causal_boost_alpha"] = float(env)
        if env := os.getenv("KN_CAUSAL_BOOST_CAP"):
            values["causal_boost_cap"] = float(env)
        if env := os.getenv("KN_LAMBDA_MRR"):
            values["lambda_mrr"] = float(env)
        if env := os.getenv("KN_SCORE_SPAN_TOP3_THRESHOLD"):
            values["score_span_top3_threshold"] = float(env)
        if env := os.getenv("KN_SCORE_SPAN_HALF_THRESHOLD"):
            values["score_span_half_threshold"] = float(env)
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
        if env := os.getenv("KN_SKILL_MATCHER_MODEL"):
            values["skill_matcher_model"] = env
        if env := os.getenv("KN_SKILL_MATCHER_API_URL"):
            values["skill_matcher_api_url"] = env
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
        if env := os.getenv("KN_SKILL_EMBEDDING_BACKEND"):
            values["kn_skill_embedding_backend"] = env.strip().lower()
        if env := os.getenv("KN_SKILLROUTER_EMBEDDING_DIR"):
            values["kn_skillrouter_embedding_dir"] = env.strip()
        if env := os.getenv("KN_SKILLROUTER_RERANKER_DIR"):
            values["kn_skillrouter_reranker_dir"] = env.strip()
        if env := os.getenv("KN_SKILL_EMBEDDING_MAIN_TOP_K"):
            values["skill_embedding_main_top_k"] = int(env)
        if env := os.getenv("KN_SKILL_RERANK_MAX_CANDIDATES"):
            values["skill_rerank_max_candidates"] = int(env)
        # KN_ENABLE_TOKEN_BUDGET / KN_TOKEN_BUDGET_* 已废弃（2026-08-10 移除预算控制）。
        # 若 .env 中仍残留这些变量，此处静默忽略，不影响启动。
        if env := os.getenv("KN_SKILL_MAX_CHARS_PER_SKILL"):
            values["skill_max_chars_per_skill"] = int(env)
        if env := os.getenv("KN_KT_TIMEOUT_SECONDS"):
            values["kt_timeout_seconds"] = int(env)
        if env := os.getenv("KN_SKILL_TIMEOUT_SECONDS"):
            values["skill_timeout_seconds"] = int(env)
        if env := os.getenv("KN_HINDSIGHT_RECALL_MAX_RETRIES"):
            values["hindsight_recall_max_retries"] = int(env)
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
        if env := os.getenv("KN_SAG_MIN_SCORE"):
            values["sag_min_score"] = float(env)
        if env := os.getenv("KN_SAG_AUTH_TOKEN"):
            values["sag_auth_token"] = env
        elif not values.get("sag_auth_token"):
            _token_file = os.path.expanduser("~/.hermes/.sag_token")
            if os.path.isfile(_token_file):
                with open(_token_file) as _f:
                    values["sag_auth_token"] = _f.read().strip()
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
        # Codegraph (P0-4)
        if env := os.getenv("KN_CODEGRAPH_ENABLED"):
            values["codegraph_enabled"] = env.lower() in ("1", "true", "yes")
        if env := os.getenv("KN_CODEGRAPH_BIN"):
            values["codegraph_bin"] = env
        if env := os.getenv("KN_CODEGRAPH_PROJECT_PATH"):
            values["codegraph_project_path"] = env
        if env := os.getenv("KN_CODEGRAPH_TIMEOUT"):
            values["codegraph_timeout"] = int(env)
        if env := os.getenv("KN_CODEGRAPH_LIMIT"):
            values["codegraph_limit"] = int(env)
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
                kn_logger.exception("设置日志文件处理器失败")


# 默认全局配置实例（优先级：kit-config > .env > 代码默认值）
_kit_cfg = KnowledgeNavigationConfig.from_kit_config()
if _kit_cfg is not None:
    CONFIG = _kit_cfg
else:
    CONFIG = KnowledgeNavigationConfig.from_env()

# 模块加载时自动初始化日志系统（任何进程 import 本模块即生效）
setup_logging()
