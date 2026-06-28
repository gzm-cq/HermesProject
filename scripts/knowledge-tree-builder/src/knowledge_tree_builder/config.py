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
    4. 尝试从 KT_CONFIG_DIR 环境变量查找
    """
    path = Path(config_path)
    if path.is_absolute():
        return path.resolve()

    if path.exists():
        return path.resolve()

    try:
        import knowledge_tree_builder

        pkg_root = Path(knowledge_tree_builder.__file__).parent.parent.parent
        candidate = (pkg_root / path).resolve()
        if candidate.exists():
            return candidate
    except (ImportError, AttributeError):
        pass

    env_dir = os.getenv("KT_CONFIG_DIR")
    if env_dir:
        candidate = Path(env_dir) / path
        if candidate.exists():
            return candidate.resolve()

    return path.resolve()


@dataclass
class AppConfig:
    """应用配置"""

    # 数据库
    db_url: str = ""  # 从 KT_DB_URL 环境变量注入
    # 输入
    input_dir: str = "references"
    # LLM
    llm_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    llm_api_key: str = ""
    llm_model: str = "s-deepseek-v4-flash"
    extract_temperature: float = 0.0
    max_tokens: int = 2048
    llm_retries: int = 3
    llm_request_timeout_seconds: int = 120
    # Embedding
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    embed_model: str = "BAAI/bge-m3"
    embed_api_key: str = ""
    embed_batch_size: int = 20
    # 新管线参数
    max_candidates_per_article: int = 15        # K: 阶段1候选数上限
    article_max_chars: int = 12000              # 文章截断长度
    split_max_rounds: int = 2                   # R_max: 阶段2拆解轮数上限
    self_explanatory_rules: bool = True         # 是否启用自解释检查
    dedup_threshold_direct: float = 0.95        # 直接判重阈值
    dedup_threshold_llm: float = 0.90           # LLM 确认区间下界
    dedup_batch_size: int = 5                   # 去重批大小
    conflict_threshold: float = 0.80            # 矛盾检测阈值
    cold_start_text_dedup_count: int = 50       # 冷启动纯文本去重阈值
    subject_match_threshold: float = 0.70       # 科目匹配阈值
    cold_start_article_count: int = 3           # 冷启动科目创建阈值

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """从字典创建配置实例"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def load_config(config_path: str) -> dict[str, Any]:
    """从 YAML 文件加载配置，支持 ENV 覆盖和绝对路径解析。"""
    config: dict[str, Any] = {}

    resolved_path = _resolve_config_path(config_path)
    if resolved_path.exists():
        with open(resolved_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    env_mapping = {
        "db_url": "KT_DB_URL",
        "llm_api_key": "LITELLM_MASTER_KEY",
        "embed_api_key": "HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY",
        # Phase A 新增
        "max_candidates_per_article": "KT_MAX_CANDIDATES_PER_ARTICLE",
        "split_max_rounds": "KT_SPLIT_MAX_ROUNDS",
        "self_explanatory_rules": "KT_SELF_EXPLANATORY_RULES",
    }
    for key, env_var in env_mapping.items():
        if env_var in os.environ:
            config[key] = os.environ[env_var]

    # Phase A: ENV 值类型转换（ENV 值都是字符串）
    _int_env_fields = {"max_candidates_per_article", "split_max_rounds"}
    _bool_env_fields = {"self_explanatory_rules"}
    for key in _int_env_fields:
        if key in config and isinstance(config[key], str):
            try:
                config[key] = int(config[key])
            except ValueError:
                pass
    for key in _bool_env_fields:
        if key in config and isinstance(config[key], str):
            config[key] = config[key].lower() in ("true", "1", "yes")

    return config
