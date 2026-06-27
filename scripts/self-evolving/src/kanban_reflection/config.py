"""Kanban 反思回路配置 — Dataclass + ENV 覆盖

优先级：ENV 变量 > 配置文件 > 默认值
"""

import os
from dataclasses import dataclass, field


@dataclass
class KanbanReflectionConfig:
    """Kanban 反思回路配置"""

    # LLM 配置
    llm_api_url: str = field(default="http://127.0.0.1:4142/v1/chat/completions")
    llm_model: str = field(default="s-deepseek-v4-flash")
    llm_api_key: str = field(default="")
    llm_timeout: int = field(default=60)

    # 反思参数
    max_trace_lines: int = field(default=5)       # 读取最近 N 轮消息
    max_input_length: int = field(default=4000)    # 反思输入截断长度
    confidence_threshold: float = field(default=0.6)  # 反思置信度阈值

    # 重试注入
    max_retries: int = field(default=3)            # 最大重试次数
    retry_inject_prefix: str = field(
        default="\n[反思分析] 上轮失败分析如下，请参考改进："
    )

    # 日志
    log_level: str = field(default="INFO")
    trace_log_path: str = field(default="trace.log")

    # 失败类型标签（参考 SEAL 6 类）
    failure_types: tuple = field(default=(
        "tool_execution_error",    # 工具执行错误
        "output_mismatch",         # 输出不符合要求
        "status_inconsistency",    # 状态不一致
        "user_correction",         # 用户纠正
        "kanban_timeout",          # Kanban 超时
        "llm_anomaly",            # LLM 异常
        "other",                   # 其他/未知
    ))

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "KanbanReflectionConfig":
        """从环境变量加载配置，覆盖默认值"""
        values: dict = {}

        if env_url := os.getenv("KN_REFLECTION_API_URL"):
            values["llm_api_url"] = env_url
        if env_model := os.getenv("KN_REFLECTION_MODEL"):
            values["llm_model"] = env_model
        if env_key := os.getenv("KN_REFLECTION_API_KEY"):
            values["llm_api_key"] = env_key
        if env_timeout := os.getenv("KN_REFLECTION_TIMEOUT"):
            values["llm_timeout"] = int(env_timeout)
        if env_lines := os.getenv("KN_REFLECTION_MAX_TRACE_LINES"):
            values["max_trace_lines"] = int(env_lines)
        if env_confidence := os.getenv("KN_REFLECTION_CONFIDENCE"):
            values["confidence_threshold"] = float(env_confidence)
        if env_retries := os.getenv("KN_REFLECTION_MAX_RETRIES"):
            values["max_retries"] = int(env_retries)
        if env_log := os.getenv("KN_REFLECTION_LOG_LEVEL"):
            values["log_level"] = env_log
        if env_trace := os.getenv("KN_REFLECTION_TRACE_PATH"):
            values["trace_log_path"] = env_trace

        if overrides:
            values.update(overrides)

        return cls(**values)
