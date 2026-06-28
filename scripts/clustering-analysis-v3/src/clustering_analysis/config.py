"""配置管理 — 支持 YAML + ENV 覆盖 + 绝对路径解析"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _resolve_config_path(config_path: str) -> Path:
    """解析配置文件路径为绝对路径。

    优先级：
    1. 如果是绝对路径，直接使用
    2. 如果相对路径存在，解析为绝对路径
    3. 尝试从包安装位置查找
    4. 尝试从 CLUSTERING_CONFIG_DIR 环境变量查找
    """
    path = Path(config_path)
    if path.is_absolute():
        return path.resolve()

    # 当前工作目录
    if path.exists():
        return path.resolve()

    # 包安装位置（向上追溯到项目根目录）
    try:
        import clustering_analysis

        pkg_root = Path(clustering_analysis.__file__).parent.parent.parent
        candidate = (pkg_root / path).resolve()
        if candidate.exists():
            return candidate
    except (ImportError, AttributeError):
        pass

    # 环境变量指定目录
    env_dir = os.getenv("CLUSTERING_CONFIG_DIR")
    if env_dir:
        candidate = Path(env_dir) / path
        if candidate.exists():
            return candidate.resolve()

    # 返回原始路径（让调用者处理不存在的情况）
    return path.resolve()


def _str_to_bool(value: str) -> bool:
    """将字符串转换为布尔值。"""
    return value.strip().lower() in ("true", "1", "yes", "on")


@dataclass
class AppConfig:
    """应用配置，支持 ENV 变量覆盖"""

    # 数据库
    db_url: str = ""  # 从 CLUSTERING_DB_URL 环境变量注入
    # 采样与聚类参数
    sample_size: int = 0  # <=0 表示不限量全量获取
    epsilon_range: list[float] = field(default_factory=lambda: [0.15, 0.20, 0.25, 0.30, 0.40, 0.50])
    min_samples: int = 3
    entity_boost_factor: float = 0.1
    bank_id: str = "hermes"
    max_group_size: int = 20
    max_workers: int = field(default_factory=lambda: min(32, (os.cpu_count() or 4) + 4))
    min_llm_size: int = 10
    # HDBSCAN 自适应参数
    hdbscan_adaptive: bool = True
    hdbscan_min_samples_min: int = 2
    hdbscan_min_samples_max: int = 10
    # LLM 参数
    llm_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    llm_api_key: str = ""
    llm_model: str = "s-deepseek-v4-flash"
    # Embedding 参数
    embed_base_url: str | None = None
    embed_model: str | None = None
    embed_api_key: str | None = None
    embed_batch_size: int = 20
    # 因果链增量检测
    causal_incremental: bool = True
    causal_new_only: bool = True
    # 去重配置
    dedup_use_minhash: bool = True
    dedup_minhash_threshold: float = 0.85
    dedup_minhash_num_perm: int = 128
    # 质量评分配置
    enable_quality_scoring: bool = False
    quality_score_batch_size: int = 20
    quality_score_model: str = "s-deepseek-v4-flash"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """从字典创建配置实例"""
        # 过滤掉 dataclass 中不存在的字段
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def load_config(config_path: str) -> dict[str, Any]:
    """从 YAML 文件加载配置，支持 ENV 覆盖和绝对路径解析。"""
    config: dict[str, Any] = {}

    # 解析绝对路径
    resolved_path = _resolve_config_path(config_path)

    if resolved_path.exists():
        with open(resolved_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # ENV 覆盖
    env_mapping = {
        "db_url": "CLUSTERING_DB_URL",
        "llm_api_key": "LITELLM_MASTER_KEY",
        "embed_api_key": "HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY",
        "hdbscan_adaptive": "CLUSTERING_HDBSCAN_ADAPTIVE",
        "hdbscan_min_samples_min": "CLUSTERING_HDBSCAN_MIN_SAMPLES_MIN",
        "hdbscan_min_samples_max": "CLUSTERING_HDBSCAN_MIN_SAMPLES_MAX",
        "sample_size": "CLUSTERING_SAMPLE_SIZE",
        "min_samples": "CLUSTERING_MIN_SAMPLES",
        "entity_boost_factor": "CLUSTERING_ENTITY_BOOST_FACTOR",
        "bank_id": "CLUSTERING_BANK_ID",
        "max_group_size": "CLUSTERING_MAX_GROUP_SIZE",
        "min_llm_size": "CLUSTERING_MIN_LLM_SIZE",
        "llm_api_url": "CLUSTERING_LLM_API_URL",
        "llm_model": "CLUSTERING_LLM_MODEL",
        "embed_base_url": "CLUSTERING_EMBED_BASE_URL",
        "embed_model": "CLUSTERING_EMBED_MODEL",
        "embed_batch_size": "CLUSTERING_EMBED_BATCH_SIZE",
        "causal_incremental": "CLUSTERING_CAUSAL_INCREMENTAL",
        "causal_new_only": "CLUSTERING_CAUSAL_NEW_ONLY",
        "dedup_use_minhash": "CLUSTERING_DEDUP_USE_MINHASH",
        "dedup_minhash_threshold": "CLUSTERING_DEDUP_MINHASH_THRESHOLD",
        "dedup_minhash_num_perm": "CLUSTERING_DEDUP_MINHASH_NUM_PERM",
        "enable_quality_scoring": "CLUSTERING_ENABLE_QUALITY_SCORING",
        "quality_score_batch_size": "CLUSTERING_QUALITY_SCORE_BATCH_SIZE",
        "quality_score_model": "CLUSTERING_QUALITY_SCORE_MODEL",
    }
    bool_keys = {"hdbscan_adaptive", "causal_incremental", "causal_new_only", "dedup_use_minhash", "enable_quality_scoring"}
    int_keys = {
        "sample_size", "min_samples", "hdbscan_min_samples_min",
        "hdbscan_min_samples_max", "max_group_size", "min_llm_size",
        "embed_batch_size", "dedup_minhash_num_perm", "quality_score_batch_size",
    }
    float_keys = {"entity_boost_factor", "dedup_minhash_threshold"}

    for key, env_var in env_mapping.items():
        if env_var in os.environ:
            value = os.environ[env_var]
            if key in bool_keys:
                config[key] = _str_to_bool(value)
            elif key in int_keys:
                try:
                    config[key] = int(value)
                except ValueError:
                    pass
            elif key in float_keys:
                try:
                    config[key] = float(value)
                except ValueError:
                    pass
            else:
                config[key] = value

    return config
