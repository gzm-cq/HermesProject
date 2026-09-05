"""test_skill_max_tokens.py — Skill matcher max_tokens=8192 验收（2026-09-04）。

Rationale: 2026-08-30 全系统统一 max_tokens=8192，但 knowledge-navigation
的 skill_matcher._llm_match 漏改仍为 16384，对 thinking 模型（s-deepseek-v4-flash）
意味着可消耗 16K token 思考，是 10.7s 精排延迟的主因之一。本测试验证调用时
max_tokens=8192。
"""
import inspect
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/mnt/d/HermesProject/plugins/knowledge-navigation/src")
from knowledge_navigation.core import skill_matcher as sm  # noqa: E402


class TestSkillMaxTokens(unittest.TestCase):
    """验证 skill 精排 LLM 调用使用正确的 max_tokens。"""

    @patch("httpx.post")
    def test_max_tokens_is_8192(self, mock_post):
        """_llm_match 内部调用的 httpx.post 应使用 max_tokens=8192。"""
        mock_resp = MagicMock()
        mock_resp.headers.get.return_value = "s-deepseek-v4-flash"
        mock_resp.json.return_value = {
            "choices": [{
                "message": {"content": '["test-skill"]', "reasoning_content": None},
                "finish_reason": "stop",
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with patch.object(sm, "_get_llm_timeout", return_value=5), \
             patch.object(sm, "_get_skill_list", return_value=[
                 {"name": "test-skill", "description": "a skill", "path": "/test"}
             ]), \
             patch.object(sm, "_get_top_k", return_value=3), \
             patch.object(sm, "_skill_index", {"test-skill": {}}):

            sm._llm_match(query="test", candidates=[
                {"name": "test-skill", "_score": 0.9}
            ])

        mock_post.assert_called_once()
        call_json = mock_post.call_args[1]["json"]
        self.assertEqual(call_json["max_tokens"], 8192,
            msg=f"Expected max_tokens=8192, got {call_json['max_tokens']}")

    def test_max_tokens_source_code(self):
        """断言源码里 max_tokens 字面量是 8192。"""
        source = inspect.getsource(sm._llm_match)
        self.assertIn('"max_tokens": 8192', source,
            msg="Source should contain max_tokens: 8192 (not 16384)")


if __name__ == "__main__":
    unittest.main()
