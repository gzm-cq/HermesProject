"""test_skill_matcher_reasoning_fallback.py — Reasoning 兜底兼容 A 方案对象格式（2026-09-04）。

2026-09-04 A 方案上线后 LLM 输出格式从纯数组 [...] 改为对象
{"intent": "...", "skills": [...]}。原兜底正则只匹配数组，
在 content 为空时（finish_reason=length）会提取失败。

本测试验证：
1. reasoning_content 含对象 JSON 时，兜底能正确提取（对象格式）
2. reasoning_content 含旧数组格式时，兜底仍能工作（向后兼容）
3. 两个格式都能被后续 json.loads 正确解析为 dict/list
4. 端到端：content 空 + reasoning 含对象 → 解析成功并返回 skills
"""
import json
import re
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/mnt/d/HermesProject/plugins/knowledge-navigation/src")
from knowledge_navigation.core import skill_matcher as sm  # noqa: E402


# 测试用 skill 数据（需与 reasoning 回复中引用的技能名匹配，且 _skill_index 非空避免早退）
_SKILLS = [
    {"name": "docx-infographic-pipeline", "description": "Markdown to DOCX pipeline", "path": "/x"},
    {"name": "sn-md-to-html-report", "description": "MD to HTML report", "path": "/x"},
    {"name": "some-skill", "description": "some skill", "path": "/x"},
    {"name": "other-skill", "description": "other skill", "path": "/x"},
]
_INDEX = {s["name"]: {"name": s["name"], "description": s["description"]} for s in _SKILLS}
# 兜底正则返回的 raw 会经 json.loads 解析，skills 名必须在 index 中
_INDEX.update({"sn-md-to-html-report": _INDEX["sn-md-to-html-report"]})


class TestReasoningFallback(unittest.TestCase):
    """验证 LLM reasoning 兜底路径兼容 A 方案对象格式。"""

    def _make_mock_response(self, content: str, reasoning: str = "",
                            finish_reason: str = "") -> MagicMock:
        """构造模拟的 OpenAI chat completion 响应。"""
        resp = MagicMock()
        resp.headers.get.return_value = "s-deepseek-v4-flash"
        resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": content,
                    "reasoning_content": reasoning or None,
                },
                "finish_reason": finish_reason or ("length" if not content else "stop"),
            }]
        }
        return resp

    def test_object_format_extracted(self):
        """A 方案对象格式（dict with intent and skills）可被提取并解析。"""
        reasoning = (
            "Let me analyze the intent. The user wants to analyze Excel data."
            "\n\nFinal answer:\n```json\n"
            '{"intent": "excel-analysis", "skills": ["sn-md-to-html-report", "docx-infographic-pipeline"]}'
            "\n```\n"
        )
        # 复用源码的实际兜底逻辑（直接调 _llm_match 会走整条链路，先用正则块验证提取）
        m = re.search(
            r'\{\s*"intent"\s*:\s*"[^"]*"\s*,\s*"skills"\s*:\s*\[[^\]]*\]\s*\}\s*$',
            reasoning,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(m, "Should extract object format from reasoning")
        parsed = json.loads(m.group(0) if m else "")
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["intent"], "excel-analysis")
        self.assertEqual(len(parsed["skills"]), 2)

    def test_array_format_still_works(self):
        """旧数组格式兼容性：[...]，A 方案之前就是这种。"""
        reasoning = (
            "Thinking step by step...\n\nResult:\n["
            '"some-skill", "other-skill"]'
        )
        m = re.search(r'\[\s*"[^"]*"(?:\s*,\s*"[^"]*")*\s*\]\s*$', reasoning, re.MULTILINE)
        self.assertIsNotNone(m, "Should extract array format from reasoning")
        parsed = json.loads(m.group(0) if m else "")
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 2)

    def test_object_with_multiline_skills(self):
        """对象格式多行 skills 数组（实际 LLM 输出变体）。"""
        reasoning = (
            "The user wants to do deep analysis. Here is the answer:\n\n"
            '{\n  "intent": "deep-research",\n  "skills": [\n'
            '    "some-skill",\n    "other-skill"\n'
            '  ]\n}\n'
        )
        m = re.search(
            r'\{\s*"intent"\s*:\s*"[^"]*"\s*,\s*"skills"\s*:\s*\[[^\]]*\]\s*\}\s*$',
            reasoning,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(m, "Should handle multi-line object")
        parsed = json.loads(m.group(0) if m else "")
        self.assertEqual(parsed["intent"], "deep-research")
        self.assertIn("some-skill", parsed["skills"])

    def test_no_match_returns_none(self):
        """推理中没有 JSON 时返回 None，不会误匹配。"""
        reasoning = "I'm thinking but I don't have a concrete answer yet."
        m = re.search(
            r'\{\s*"intent"\s*:\s*"[^"]*"\s*,\s*"skills"\s*:\s*\[[^\]]*\]\s*\}\s*$',
            reasoning,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNone(m, "Should return None when no JSON in reasoning")

    def test_integration_with_full_mock(self):
        """端到端：content 空 + reasoning 含对象 → 兜底提取并返回 skills（with_intent=True）。"""
        fake_reasoning = (
            "I need to identify the intent of this request. It's a report generation task.\n"
            'Final: {"intent": "report-generation", '
            '"skills": ["docx-infographic-pipeline", "sn-md-to-html-report"]}'
        )

        with patch("httpx.post") as mock_post:
            mock_post.return_value = self._make_mock_response(
                content="", reasoning=fake_reasoning, finish_reason="length",
            )
            with patch.object(sm, "_get_llm_timeout", return_value=5), \
                 patch.object(sm, "_get_skill_list", return_value=_SKILLS), \
                 patch.object(sm, "_get_top_k", return_value=3), \
                 patch.object(sm, "_skill_index", _INDEX):
                result = sm._llm_match(query="帮我生成一份月度报告", with_intent=True)

        self.assertIsInstance(result, tuple, "with_intent=True must return tuple")
        intent, skills = result
        self.assertEqual(intent, "report-generation")
        self.assertEqual(len(skills), 2)

    def test_integration_without_intent_legacy(self):
        """端到端：content 空 + reasoning 含旧数组 → 兜底提取并返回 list（with_intent=False）。"""
        fake_reasoning = (
            "Some reasoning here.\n"
            'Final: ["some-skill", "other-skill"]'
        )

        with patch("httpx.post") as mock_post:
            mock_post.return_value = self._make_mock_response(
                content="", reasoning=fake_reasoning, finish_reason="length",
            )
            with patch.object(sm, "_get_llm_timeout", return_value=5), \
                 patch.object(sm, "_get_skill_list", return_value=_SKILLS), \
                 patch.object(sm, "_get_top_k", return_value=3), \
                 patch.object(sm, "_skill_index", _INDEX):
                result = sm._llm_match(query="analyze this", with_intent=False)

        self.assertIsInstance(result, list, "Default path returns list")
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()