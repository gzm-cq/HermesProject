"""全局测试 fixtures。

注意：所有 hooks/e2e 测试默认 mock Router 返回全开 + HAS_KNOWLEDGE_TREE=True + 禁用
eval queries 加载，避免真实 HTTP 请求和网络超时。需要自定义行为可自行 patch。
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# pytest 进程必须写临时 trace，避免污染生产 trace.log。
os.environ["KN_TRACE_LOG_PATH"] = os.path.join(
    tempfile.gettempdir(), "knowledge-navigation-pytest-trace.log"
)
# 测试环境默认禁用 use log，避免产生副作用（专门的 use log 测试会单独启用）
os.environ["KN_ENABLE_USE_LOG"] = "false"

import pytest

from knowledge_navigation.config import KnowledgeNavigationConfig


@pytest.fixture
def default_config() -> KnowledgeNavigationConfig:
    """返回默认测试配置。"""
    return KnowledgeNavigationConfig()


@pytest.fixture(autouse=True)
def cleanup_hooks_globals() -> None:
    """自动清理 hooks 模块级全局状态，避免测试间污染。"""
    import knowledge_navigation.core.hooks as nav_hooks
    import knowledge_navigation.core.circuit_breaker as _cb
    _cb._hindsight_cb._failures = 0
    _cb._hindsight_cb._open_until = 0.0
    _cb._hindsight_cb._failure_types.clear()
    _cb._sag_cb._failures = 0
    _cb._sag_cb._open_until = 0.0
    _cb._sag_cb._failure_types.clear()
    nav_hooks._injected_ids.clear()
    nav_hooks._hit_counter = nav_hooks._HitCounter()
    nav_hooks._task_tracker = nav_hooks._TaskTracker()
    # 清理 use_logger
    if nav_hooks._use_logger is not None:
        try:
            nav_hooks._use_logger.close()
        except Exception:
            pass
        nav_hooks._use_logger = None
    yield
    # 测试后再次清理
    if nav_hooks._use_logger is not None:
        try:
            nav_hooks._use_logger.close()
        except Exception:
            pass
        nav_hooks._use_logger = None


@pytest.fixture(autouse=True)
def mock_router_and_eval() -> None:
    """全局 mock Router 返回全开 + HAS_KNOWLEDGE_TREE=True + 禁用 eval queries。

    避免真实 HTTP 请求和网络超时，所有 hooks/e2e 测试默认 Router 全开。
    需要自定义行为的测试可用自己的 patch.object 覆盖（内层 wins）。
    """
    import knowledge_navigation.core.hooks as nav_hooks

    with patch.object(nav_hooks, "_router_route", return_value={"h": True, "kt": True, "s": True}), \
         patch.object(nav_hooks, "HAS_KNOWLEDGE_TREE", True), \
         patch.object(nav_hooks, "_eval_queries", []), \
         patch.object(nav_hooks, "_do_hindsight_recall", return_value=None), \
         patch.object(nav_hooks, "_do_kt_recall", return_value=[]), \
         patch.object(nav_hooks, "_do_skill_match", return_value=""):
        yield


@pytest.fixture
def mock_ctx() -> MagicMock:
    """返回 mock 的 Hermes 插件上下文。"""
    ctx = MagicMock()
    ctx.register_hook = MagicMock()
    return ctx


@pytest.fixture
def sample_raw_results() -> list[dict]:
    """返回示例原始结果列表。"""
    return [
        {"id": "node1", "text": "  First memory text  "},
        {"id": "node2", "text": "Second memory text"},
        {"id": "node3", "text": "Third memory text"},
        {"id": "node4", "text": ""},
        {"id": "node5", "text": "Fifth memory text"},
    ]


@pytest.fixture
def sample_rerank_map() -> dict[str, float]:
    """返回示例 rerank 分数映射。"""
    return {
        "node1": 0.95,
        "node2": 0.75,
        "node3": 0.55,
        "node5": 0.88,
    }


@pytest.fixture
def sample_raw_results_with_time() -> list[dict]:
    """返回包含时间戳的示例结果。"""
    now = datetime.now(timezone.utc)
    return [
        {"id": "node1", "text": "Recent memory", "mentioned_at": now.isoformat()},
        {"id": "node2", "text": "Old memory", "mentioned_at": (now - timedelta(days=60)).isoformat()},
        {"id": "node3", "text": "Medium memory", "mentioned_at": (now - timedelta(days=15)).isoformat()},
    ]