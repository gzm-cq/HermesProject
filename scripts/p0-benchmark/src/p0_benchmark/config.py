"""配置管理 — YAML + ENV 覆盖。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AppConfig:
    """Benchmark 配置。"""

    # 通用配置
    output_path: str = "reports"
    log_level: str = "INFO"
    seed: int = 42

    # P0-1: Skill Matcher Benchmark
    skill_benchmark_queries: int = 100
    skill_benchmark_prescreen_top_k: int = 20
    skill_benchmark_accuracy_threshold: float = 0.95

    # P0-2: pgvector 去重 Benchmark
    dedup_benchmark_sizes: list[int] = field(default_factory=lambda: [1000, 5000, 10000])
    dedup_benchmark_threshold: float = 0.95
    dedup_benchmark_repeat: int = 3

    # P0-3: LLM 合并调用 Benchmark
    llm_benchmark_article_count: int = 50
    llm_benchmark_model: str = "s-deepseek-v4-flash"
    llm_benchmark_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"

    @classmethod
    def from_yaml(cls, yaml_data: dict[str, Any]) -> AppConfig:
        """从 YAML 数据创建配置。"""
        return cls(
            output_path=yaml_data.get("output_path", "reports"),
            log_level=yaml_data.get("log_level", "INFO"),
            seed=yaml_data.get("seed", 42),
            skill_benchmark_queries=yaml_data.get("skill_benchmark_queries", 100),
            skill_benchmark_prescreen_top_k=yaml_data.get("skill_benchmark_prescreen_top_k", 20),
            skill_benchmark_accuracy_threshold=yaml_data.get("skill_benchmark_accuracy_threshold", 0.95),
            dedup_benchmark_sizes=yaml_data.get("dedup_benchmark_sizes", [1000, 5000, 10000]),
            dedup_benchmark_threshold=yaml_data.get("dedup_benchmark_threshold", 0.95),
            dedup_benchmark_repeat=yaml_data.get("dedup_benchmark_repeat", 3),
            llm_benchmark_article_count=yaml_data.get("llm_benchmark_article_count", 50),
            llm_benchmark_model=yaml_data.get("llm_benchmark_model", "s-deepseek-v4-flash"),
            llm_benchmark_api_url=yaml_data.get("llm_benchmark_api_url", "http://127.0.0.1:4142/v1/chat/completions"),
        )


def load_config(config_path: str = "config/default.yaml") -> dict[str, Any]:
    """加载 YAML 配置文件。"""
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def setup_logging(level: str = "INFO") -> None:
    """设置日志级别。"""
    import logging
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
