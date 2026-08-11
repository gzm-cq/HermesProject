"""Hindsight API 客户端 mock 测试。"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from knowledge_navigation.adapters.hindsight import HindsightClient, HindsightClientError
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
        """测试 JSON 解析失败抛 HindsightClientError（服务端故障，无 status_code）。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_session.post.return_value = mock_response

        client = HindsightClient(base_url="http://test.local/api")
        with pytest.raises(HindsightClientError):
            client.recall("query text")

    @patch.object(CONFIG, "hindsight_recall_max_retries", 1)
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
        """测试超时重试次数耗尽抛 HindsightClientError。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_session.post.side_effect = requests.exceptions.Timeout()

        config = KnowledgeNavigationConfig()
        client = HindsightClient(
            base_url="http://test.local/api",
            timeout=config.timeout_seconds,
        )
        with pytest.raises(HindsightClientError):
            client.recall("query text")
        # 召回路径使用 hindsight_recall_max_retries（默认 0，即不重试）
        assert mock_session.post.call_count == config.hindsight_recall_max_retries + 1

    @patch.object(CONFIG, "hindsight_recall_max_retries", 1)
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
        """测试连接错误重试次数耗尽抛 HindsightClientError。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_session.post.side_effect = requests.exceptions.ConnectionError()

        config = KnowledgeNavigationConfig()
        client = HindsightClient(
            base_url="http://test.local/api",
            timeout=config.timeout_seconds,
        )
        with pytest.raises(HindsightClientError):
            client.recall("query text")
        assert mock_session.post.call_count == config.hindsight_recall_max_retries + 1

    @patch.object(CONFIG, "hindsight_recall_max_retries", 1)
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

    @patch.object(CONFIG, "hindsight_recall_max_retries", 0)
    def test_recall_rate_limit_no_retry_by_default(self) -> None:
        """默认 0 重试时，429 应立即抛错，不得空转 sleep 后再失败。

        回归保护：召回路径的重试上限必须完全由 hindsight_recall_max_retries
        控制；若内部守卫误用 CONFIG.max_retries，会多睡一轮再抛错，
        进而拖垮 pre_llm_call 的并行截止预算。
        """
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        mock_session.post.return_value = mock_response_429

        client = HindsightClient(base_url="http://test.local/api")
        with patch("knowledge_navigation.adapters.hindsight.time.sleep") as mock_sleep:
            with pytest.raises(HindsightClientError) as exc_info:
                client.recall("query text")
            mock_sleep.assert_not_called()

        assert exc_info.value.status_code == 429
        assert mock_session.post.call_count == 1

    @patch.object(CONFIG, "hindsight_recall_max_retries", 0)
    def test_recall_timeout_no_sleep_by_default(self) -> None:
        """默认 0 重试时，超时应立即抛错且不 sleep。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_session.post.side_effect = requests.exceptions.Timeout()

        client = HindsightClient(base_url="http://test.local/api")
        with patch("knowledge_navigation.adapters.hindsight.time.sleep") as mock_sleep:
            with pytest.raises(HindsightClientError):
                client.recall("query text")
            mock_sleep.assert_not_called()

        assert mock_session.post.call_count == 1

    def test_recall_unexpected_error(self) -> None:
        """测试未预期异常包装为 HindsightClientError。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_session.post.side_effect = RuntimeError("Unexpected")

        client = HindsightClient(base_url="http://test.local/api")
        with pytest.raises(HindsightClientError):
            client.recall("query text")
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

    def test_recall_http_400_raises_with_status_code(self) -> None:
        """测试 HTTP 400 抛 HindsightClientError 并携带 status_code=400（客户端错误）。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_session.post.return_value = mock_response

        client = HindsightClient(base_url="http://test.local/api")
        with pytest.raises(HindsightClientError) as exc_info:
            client.recall("query text")
        assert exc_info.value.status_code == 400

    def test_recall_http_500_raises_with_status_code(self) -> None:
        """测试 HTTP 500 抛 HindsightClientError 并携带 status_code=500（服务端故障）。"""
        _reset_shared_session()
        mock_session = _make_mock_session()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_session.post.return_value = mock_response

        client = HindsightClient(base_url="http://test.local/api")
        with pytest.raises(HindsightClientError) as exc_info:
            client.recall("query text")
        assert exc_info.value.status_code == 500