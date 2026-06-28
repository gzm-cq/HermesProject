"""全局测试 fixtures"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from recall_eval.config import AppConfig
from recall_eval.core.dataset import EvalDataset, EvalQuery


@pytest.fixture
def app_config() -> AppConfig:
    """返回默认测试配置。"""
    return AppConfig()


@pytest.fixture
def sample_query() -> str:
    """示例查询。"""
    return "LiteLLM 配置相关的问题怎么处理"


@pytest.fixture
def sample_context() -> str:
    """示例上下文。"""
    return """LiteLLM 网关地址: http://127.0.0.1:4142，通过 LiteLLM 统一代理所有 Provider。
API Key 通过 LITELLM_MASTER_KEY 环境变量注入。
支持多种模型：DeepSeek、GPT 等。"""


@pytest.fixture
def sample_answer() -> str:
    """示例回答。"""
    return "LiteLLM 配置问题可以检查网关地址（默认 http://127.0.0.1:4142）和 API Key（通过 LITELLM_MASTER_KEY 环境变量设置）。"


@pytest.fixture
def sample_eval_queries() -> list[EvalQuery]:
    """示例评估查询列表。"""
    return [
        EvalQuery(
            query_id="test_01",
            query="LiteLLM 配置问题",
            category="semantic",
            expected_context="LiteLLM 网关地址: http://127.0.0.1:4142",
            expected_answer="检查 LiteLLM 网关配置",
        ),
        EvalQuery(
            query_id="test_02",
            query="PG 连接错误",
            category="debug",
            expected_context="PG 端口: 5434，数据库名: hindsight",
            expected_answer="检查 PG 连接参数",
        ),
        EvalQuery(
            query_id="test_03",
            query="Hindsight API",
            category="api",
            expected_context="Hindsight REST API 端点包括 search 和 upsert",
            expected_answer="Hindsight 提供搜索和更新 API",
        ),
    ]


@pytest.fixture
def sample_dataset(sample_eval_queries: list[EvalQuery]) -> EvalDataset:
    """示例数据集。"""
    return EvalDataset(name="test_dataset", queries=sample_eval_queries)


@pytest.fixture
def temp_dataset_file(tmp_path: Path, sample_eval_queries: list[EvalQuery]) -> Path:
    """临时数据集文件。"""
    data = [q.to_dict() for q in sample_eval_queries]
    file_path = tmp_path / "test_queries.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return file_path


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """返回 mock 的 LLMClient 实例。"""
    client = MagicMock()
    client.evaluate_faithfulness.return_value = {
        "score": 0.85,
        "reason": "大部分内容基于上下文",
        "supported_claims": ["LiteLLM 网关地址", "API Key"],
        "unsupported_claims": [],
    }
    client.evaluate_relevance.return_value = {
        "score": 0.9,
        "reason": "上下文与查询高度相关",
        "relevant_topics": ["LiteLLM", "配置"],
        "irrelevant_topics": [],
    }
    client.evaluate_coverage.return_value = {
        "score": 0.75,
        "reason": "覆盖了主要要点",
        "query_points": ["LiteLLM", "配置"],
        "covered_points": ["LiteLLM", "配置"],
        "missing_points": [],
    }
    client.total_prompt_tokens = 1000
    client.total_completion_tokens = 500
    return client
