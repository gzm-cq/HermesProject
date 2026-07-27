"""LLM 客户端模块测试"""
import json
from unittest.mock import patch, MagicMock
from io import BytesIO

import pytest

from kanban_reflection.adapters.llm_client import LLMClient


class TestLLMClient:
    """测试 LLM 客户端"""

    @pytest.fixture
    def client(self) -> LLMClient:
        return LLMClient(
            api_url="http://test:8080/v1/chat",
            model="test-model",
            timeout=10,
        )

    def test_init(self, client: LLMClient) -> None:
        """初始化应正确设置参数"""
        assert client._api_url == "http://test:8080/v1/chat"
        assert client._model == "test-model"
        assert client._timeout == 10

    @patch("urllib.request.urlopen")
    def test_chat_completion_success(self, mock_urlopen: MagicMock, client: LLMClient) -> None:
        """成功的 API 调用应返回解析后的 JSON"""
        response_data = json.dumps({
            "choices": [{"message": {"content": '{"key": "value"}'}}],
        })
        mock_urlopen.return_value.__enter__.return_value = BytesIO(
            response_data.encode("utf-8")
        )

        result = client.chat_completion(
            messages=[{"role": "user", "content": "hello"}],
        )

        assert "choices" in result
        assert result["choices"][0]["message"]["content"] == '{"key": "value"}'

    @patch("urllib.request.urlopen")
    def test_extract_content(self, mock_urlopen: MagicMock, client: LLMClient) -> None:
        """extract_content 应正确提取文本"""
        response_data = json.dumps({
            "choices": [{"message": {"content": "测试输出"}}],
        })
        mock_urlopen.return_value.__enter__.return_value = BytesIO(
            response_data.encode("utf-8")
        )

        response = client.chat_completion([{"role": "user", "content": "hi"}])
        content = client.extract_content(response)
        assert content == "测试输出"

    def test_extract_content_missing_choices(self, client: LLMClient) -> None:
        """choices 缺失时应抛出 ValueError"""
        with pytest.raises(ValueError, match="响应格式异常"):
            client.extract_content({"invalid": True})

    def test_parse_json_response_plain(self, client: LLMClient) -> None:
        """普通 JSON 应正常解析"""
        result = client.parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_response_markdown(self, client: LLMClient) -> None:
        """markdown 包裹的 JSON 应能解析"""
        text = '```json\n{"key": "value"}\n```'
        result = client.parse_json_response(text)
        assert result == {"key": "value"}

    def test_parse_json_response_markdown_alt(self, client: LLMClient) -> None:
        """不同格式的 markdown 包裹也应能解析"""
        text = '```\n{"key": "value"}\n```'
        result = client.parse_json_response(text)
        assert result == {"key": "value"}

    def test_parse_json_response_invalid(self, client: LLMClient) -> None:
        """无效 JSON 应抛出异常"""
        with pytest.raises(json.JSONDecodeError):
            client.parse_json_response("not json")

    @patch("urllib.request.urlopen")
    def test_chat_completion_with_response_format(
        self, mock_urlopen: MagicMock, client: LLMClient,
    ) -> None:
        """response_format 参数应传递给 API"""
        response_data = json.dumps({
            "choices": [{"message": {"content": "{}"}}],
        })
        mock_urlopen.return_value.__enter__.return_value = BytesIO(
            response_data.encode("utf-8")
        )

        client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            response_format={"type": "json_object"},
        )

        # 验证请求体中包含 response_format
        call_kwargs = mock_urlopen.call_args
        assert call_kwargs is not None
        req = call_kwargs[0][0]
        body = json.loads(req.data.decode())
        assert body["response_format"] == {"type": "json_object"}
