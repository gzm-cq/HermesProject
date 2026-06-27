"""Hindsight API 客户端 mock 测试。"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from knowledge_navigation.adapters.hindsight import HindsightClient
from knowledge_navigation.config import CONFIG, KnowledgeNavigationConfig


def _reset_shared_session() -> None:
    """重置 HindsightClient 的共享 Session，防止跨测试污染。"""
    HindsightClient._shared_session = None


def _make_mock_session() -> MagicMock:
    """创建 mock Session 并注入到 HindsightClient。"""
    s = MagicMock()
    HindsightClient._shared_session = s
    return s


class TestHindsightClientRecall:
    """测试 HindsightClient.recall 方法。"""

    def test_recall_success(self) -> None:
        """测试成功的 recall 请求。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [{"id": "1", "text": "test"}]}
        mock_session.post.return_value = mock_response

        config = KnowledgeNavigationConfig()
        client = HindsightClient(
            base_url="http://test.local/api",
            timeout=config.timeout_seconds,
        )
        result = client.recall("query text")

        assert result is not None
        assert result["results"][0]["id"] == "1"
        mock_session.post.assert_called_once()

    def test_recall_json_decode_error(self) -> None:
        """测试 JSON 解析失败返回 None。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_session.post.return_value = mock_response

        client = HindsightClient(base_url="http://test.local/api")
        result = client.recall("query text")

        assert result is None

    @patch.object(CONFIG, "max_retries", 1)
    def test_recall_timeout_then_success(self) -> None:
        """测试超时后重试成功。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}

        mock_session.post.side_effect = [
            requests.exceptions.Timeout(),
            mock_response,
        ]

        config = KnowledgeNavigationConfig()
        client = HindsightClient(
            base_url="http://test.local/api",
            timeout=config.timeout_seconds,
        )
        result = client.recall("query text")

        assert result is not None
        assert mock_session.post.call_count == 2

    def test_recall_timeout_exhausted(self) -> None:
        """测试超时重试次数耗尽返回 None。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_session.post.side_effect = requests.exceptions.Timeout()

        config = KnowledgeNavigationConfig()
        client = HindsightClient(
            base_url="http://test.local/api",
            timeout=config.timeout_seconds,
        )
        result = client.recall("query text")

        assert result is None
        # max_retries + 1 attempts
        assert mock_session.post.call_count == config.max_retries + 1

    @patch.object(CONFIG, "max_retries", 1)
    def test_recall_connection_error_then_success(
        self,
    ) -> None:
        """测试连接错误后重试成功。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}

        mock_session.post.side_effect = [
            requests.exceptions.ConnectionError("Conn refused"),
            mock_response,
        ]

        client = HindsightClient(base_url="http://test.local/api")
        result = client.recall("query text")

        assert result is not None
        assert mock_session.post.call_count == 2

    def test_recall_connection_error_exhausted(self) -> None:
        """测试连接错误重试次数耗尽返回 None。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_session.post.side_effect = requests.exceptions.ConnectionError()

        config = KnowledgeNavigationConfig()
        client = HindsightClient(
            base_url="http://test.local/api",
            timeout=config.timeout_seconds,
        )
        result = client.recall("query text")

        assert result is None
        assert mock_session.post.call_count == config.max_retries + 1

    @patch.object(CONFIG, "max_retries", 1)
    def test_recall_rate_limit_retry(self) -> None:
        """测试 429 限流后重试。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        mock_response_ok = MagicMock()
        mock_response_ok.status_code = 200
        mock_response_ok.json.return_value = {"results": []}

        mock_session.post.side_effect = [mock_response_429, mock_response_ok]

        client = HindsightClient(base_url="http://test.local/api")
        result = client.recall("query text")

        assert result is not None
        assert mock_session.post.call_count == 2

    def test_recall_unexpected_error(self) -> None:
        """测试未预期异常返回 None。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_session.post.side_effect = RuntimeError("Unexpected")

        client = HindsightClient(base_url="http://test.local/api")
        result = client.recall("query text")

        assert result is None
        assert mock_session.post.call_count == 1

    def test_close_is_noop(self) -> None:
        """测试 close 方法是空操作（Session 由类级共享）。"""
        _reset_shared_session()
        client = HindsightClient(base_url="http://test.local/api")
        # close() 是空操作，不应抛出异常
        client.close()
        # 验证 session 未被关闭（仍可用）
        assert HindsightClient._shared_session is not None

    def test_max_results_in_payload(self) -> None:
        """测试 max_results 被正确传入 payload。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_session.post.return_value = mock_response

        client = HindsightClient(base_url="http://test.local/api")
        result = client.recall("query text", max_results=5)

        assert result is not None
        call_kwargs = mock_session.post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload.get("max_results") == 5