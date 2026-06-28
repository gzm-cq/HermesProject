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
    # LLM 参数
    llm_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    llm_api_key: str = ""
    llm_model: str = "s-deepseek-v4-flash"
    # Embedding 参数
    embed_base_url: str | None = None
    embed_model: str | None = None
    embed_api_key: str | None = None
    embed_batch_size: int = 20

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
    }
    for key, env_var in env_mapping.items():
        if env_var in os.environ:
            config[key] = os.environ[env_var]

    return config
