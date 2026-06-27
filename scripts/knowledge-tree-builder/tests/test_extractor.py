"""测试 extractor 模块 — 含 mock LLM 调用"""

from unittest.mock import patch

from knowledge_tree_builder.core.extractor import (
    _parse_extracted_points,
    extract_knowledge_points,
)


class TestExtractKnowledgePoints:
    """测试知识点提取"""

    def test_parse_markdown_list(self) -> None:
        response = """- 知识点一：这是第一个知识点
- 知识点二：这是第二个知识点
- 知识点三：这是第三个知识点"""
        result = _parse_extracted_points(response)
        assert len(result) == 3
        assert result[0] == "知识点一：这是第一个知识点"

    def test_parse_numbered_list(self) -> None:
        response = """1. 第一个知识点
2. 第二个知识点"""
        result = _parse_extracted_points(response)
        assert len(result) == 2
        assert result[0] == "第一个知识点"

    def test_parse_mixed_prefix(self) -> None:
        response = """- 用「-」开头的知识点
• 用「•」开头的知识点
— 用「—」开头的知识点"""
        result = _parse_extracted_points(response)
        assert len(result) == 3

    def test_empty_response(self) -> None:
        result = _parse_extracted_points("")
        assert result == []

    @patch("knowledge_tree_builder.core.extractor.call_llm")
    def test_extract_knowledge_points_calls_llm(self, mock_call_llm) -> None:
        """验证 extract_knowledge_points 调用 call_llm 并解析结果"""
        mock_call_llm.return_value = """- SE-Agent 三大进化算子
- Revision 算子分析失败轨迹
- Refinement 算子微调优化"""
        result = extract_knowledge_points(
            "SE-Agent 文章内容...",
            article_title="SE-Agent 精读笔记",
            api_url="http://test/api",
            model="test-model",
        )
        assert len(result) == 3
        assert "SE-Agent 三大进化算子" in result[0]
        mock_call_llm.assert_called_once()

    @patch("knowledge_tree_builder.core.extractor.call_llm")
    def test_extract_empty_response(self, mock_call_llm) -> None:
        """LLM 返回空时返回空列表"""
        mock_call_llm.return_value = ""
        result = extract_knowledge_points("some text")
        assert result == []

    @patch("knowledge_tree_builder.core.extractor.call_llm")
    def test_extract_single_point(self, mock_call_llm) -> None:
        """LLM 只返回一个知识点"""
        mock_call_llm.return_value = "- 唯一知识点"
        result = extract_knowledge_points("text")
        assert len(result) == 1
        assert result[0] == "唯一知识点"
