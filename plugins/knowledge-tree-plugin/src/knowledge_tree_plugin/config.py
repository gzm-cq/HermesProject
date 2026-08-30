"""知识树插件本地配置 — YAML + 环境变量覆盖"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigurationError(ValueError):
    """配置错误 — API key 或必要参数缺失。"""
    pass


@dataclass
class PluginConfig:
    """插件配置 — 支持 YAML + 环境变量覆盖。"""

    # 数据库
    db_url: str = ""                     # PG 连接串；默认从 KT_DB_URL 环境变量读取

    # 知识树 recall 参数（pre_llm_call）
    max_recall_results: int = 5          # 最多返回知识点数
    recall_min_score: float = 0.3        # 注意力筛选最小分数
    cold_start_threshold: int = 20       # 子节点数 < 此值 → 回退余弦相似度定位

    # 知识提取参数（post_llm_call）
    extract_enabled: bool = True         # 是否启用 post_llm_call 自动提取
    extract_min_dialog_length: int = 50  # 对话长度至少多少字符才尝试提取
    extract_max_input_length: int = 4000 # 输入 LLM 提取的最大字符数
    min_knowledge_point_length: int = 10 # 准入最小字数
    extract_llm_timeout_seconds: int = 30 # 在线提取 LLM read timeout
    extract_llm_retries: int = 1          # 在线提取失败不重试，避免阻塞后台队列

    # LLM（post_llm_call 提取用）
    llm_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "KT_LLM_API_URL", "http://127.0.0.1:4142/v1/chat/completions"
        )
    )
    llm_api_key: str = ""
    llm_model: str = "s-deepseek-v4-flash"

    # Embedding（recall + 增量放置用）
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    embed_model: str = "BAAI/bge-m3"
    embed_api_key: str = ""
    embed_batch_size: int = 20

    # 去重
    dedup_cosine_threshold: float = 0.95
    conflict_cosine_threshold: float = 0.80

    # K 向量更新
    k_vector_alpha_max: float = 0.1

    # P3-9: 时态感知过滤（Feature Flag）
    # 默认关闭：时态过滤依赖知识点的 valid_from / valid_until 字段，而当前
    # get_child_nodes 返回的时态字段恒为 None（尚未在 schema/写入链路落地），
    # 全量开启会对所有召回结果统一降权而无实际收益。待时态字段回填完成后，
    # 通过 KT_ENABLE_TEMPORAL_FILTER=1 或 config.yaml 灰度开启。
    enable_temporal_filter: bool = False   # 是否启用时态过滤
    temporal_filter_demote_factor: float = 0.5  # 过期记忆的降权系数（0-1，越小降权越多）

    # 跨域多跳扩展（recall 主流程内建）
    # 默认开启：attention_filter 只做科目内注意力召回，跨科关联完全缺失。
    # 2026-08-30 修复 Route C 双向边遍历后，把跨域发现下沉进 _recall_core，
    # 使 recall_from_tree_raw 无论被谁调用都自带跨域结果，不依赖调用方二次展开。
    enable_multi_hop_expand: bool = True  # recall 主流程是否内建跨域多跳扩展
    multi_hop_top_k: int = 4              # 扩展结果条数上限（与 KN 侧对齐）

    def __post_init__(self) -> None:
        """环境变量覆盖（YAML 加载后执行）。"""
        env_map: dict[str, str] = {
            "db_url": "KT_DB_URL",
            "llm_api_url": "KT_LLM_API_URL",
            "llm_api_key": "LITELLM_MASTER_KEY",
            "llm_model": "KT_LLM_MODEL",
            "embed_base_url": "KT_EMBED_BASE_URL",
            "embed_model": "KT_EMBED_MODEL",
            "embed_api_key": "HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY",
        }
        for field_name, env_var in env_map.items():
            if env_var in os.environ:
                setattr(self, field_name, os.environ[env_var])

        int_env_map = {
            "extract_llm_timeout_seconds": "KT_EXTRACT_LLM_TIMEOUT_SECONDS",
            "extract_llm_retries": "KT_EXTRACT_LLM_RETRIES",
            "multi_hop_top_k": "KT_MULTI_HOP_TOP_K",
        }
        for field_name, env_var in int_env_map.items():
            if env_var in os.environ:
                try:
                    setattr(self, field_name, int(os.environ[env_var]))
                except ValueError:
                    pass

        bool_env_map = {
            "enable_temporal_filter": "KT_ENABLE_TEMPORAL_FILTER",
            "enable_multi_hop_expand": "KT_ENABLE_MULTI_HOP_EXPAND",
        }
        for field_name, env_var in bool_env_map.items():
            if env_var in os.environ:
                val = os.environ[env_var].lower() in ("1", "true", "yes")
                setattr(self, field_name, val)

        float_env_map = {
            "temporal_filter_demote_factor": "KT_TEMPORAL_FILTER_DEMOTE_FACTOR",
        }
        for field_name, env_var in float_env_map.items():
            if env_var in os.environ:
                try:
                    setattr(self, field_name, float(os.environ[env_var]))
                except ValueError:
                    pass

        # P0-7: 启动时验证 API key 非空
        if self.extract_enabled:
            if not self.llm_api_key:
                raise ConfigurationError(
                    "llm_api_key 不能为空 — 请设置 LITELLM_MASTER_KEY 环境变量或配置文件中指定"
                )
            if not self.embed_api_key:
                raise ConfigurationError(
                    "embed_api_key 不能为空 — 请设置 HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY 环境变量或配置文件中指定"
                )

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "PluginConfig":
        """从 YAML 文件加载配置（如有）。

        Args:
            config_path: YAML 配置文件路径；None 时使用所有字段默认值。

        Returns:
            PluginConfig 实例（环境变量会覆盖 YAML 中的值）。
        """
        config: dict[str, Any] = {}

        if config_path:
            path = Path(config_path)
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}

        # 只保留 PluginConfig 定义的字段
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in config.items() if k in valid_fields}

        return cls(**filtered)
