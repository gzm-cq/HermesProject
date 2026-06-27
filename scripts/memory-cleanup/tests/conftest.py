"""全局测试 fixtures"""

from unittest.mock import MagicMock

import pytest

from memory_cleanup.config import AppConfig


@pytest.fixture
def app_config() -> AppConfig:
    """返回默认测试配置。"""
    return AppConfig()


@pytest.fixture
def sample_entries() -> list[str]:
    """返回测试用条目列表。"""
    return [
        "LiteLLM 网关地址: http://127.0.0.1:4142",
        "UTC 时间陷阱：数据库时间戳是 UTC，展示时需转换为本地时间",
        "2026-05-01 完成了MES数据迁移，共迁移3000条记录",
        "用户偏好：不要并发执行，先排事情再排时间",
        "论文投稿目标：IEEE TNNLS，引用编号 [3][7][12]",
        "PG 端口: 5434，数据库名: hindsight",
        "§",
        "清理原则：V5方案三阶段流程说明",
        "Gateway 管理方式：所有 Provider 通过 LiteLLM 统一代理",
        "MES 时间线：2026-Q1 完成，2026-Q2 上线",
    ]


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """返回 mock 的 LLMClient 实例。"""
    client = MagicMock()
    client.classify_batch.return_value = {
        "merge": [],
        "remove": [{"index": 2, "原因": "业务数据"}, {"index": 4, "原因": "论文信息"}],
        "compress": [{"index": 1, "精简为": "UTC时间陷阱：DB时间戳需转本地时间"}],
    }
    client.verify_one.return_value = {"verdict": "correct", "note": ""}
    return client


@pytest.fixture
def mock_session_db() -> MagicMock:
    """返回 mock 的 SessionDB 实例。"""
    db = MagicMock()
    db.search.return_value = {"found": False, "confidence": 0.0}
    return db
