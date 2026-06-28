"""测试 report_graph.py 纯函数（无 LLM / 无外部 I/O）。

覆盖 5 个纯函数：
  - _parse_prompt_review_response   (保护逻辑核心)
  - _extract_source_sections_simple (文本解析)
  - _parse_synthesize_response      (JSON fallback)
  - _parse_goal_response            (JSON fallback)
  - _match_facts_to_chapter         (关键词匹配)
"""

from __future__ import annotations

import json as _json
from typing import Any

import pytest

from ai_report.graph.report_graph import (
    _extract_source_sections_simple,
    _match_facts_to_chapter,
    _parse_goal_response,
    _parse_prompt_review_response,
    _parse_synthesize_response,
)


# ═══════════════════════════════════════════════════════════════
# _parse_prompt_review_response  — 保护逻辑（最高优先级）
# ═══════════════════════════════════════════════════════════════

def _make_cp(title: str, **overrides: Any) -> dict[str, Any]:
    cp: dict[str, Any] = {
        "title": title,
        "writing_intent": f"{title} 的写作意图",
        "key_points": [f"{title} 要点 A", f"{title} 要点 B"],
        "materials_text": f"{title} 的素材内容",
        "section_type": "body",
    }
    cp.update(overrides)
    return cp


ORIGINAL = [
    _make_cp("第一章", writing_intent="原始意图：技术可行性"),
    _make_cp("第二章", key_points=["预算分析", "投资回报"]),
    _make_cp("第三章"),
]


class TestParsePromptReviewResponse:
    """_parse_prompt_review_response 保护逻辑测试"""

    def test_llm_keeps_everything_unchanged(self):
        """LLM 原样返回 → 各字段不变"""
        result = _parse_prompt_review_response(
            _json.dumps(ORIGINAL, ensure_ascii=False), ORIGINAL
        )
        assert len(result) == 3
        assert result[0]["key_points"] == ORIGINAL[0]["key_points"]
        assert result[0]["writing_intent"] == ORIGINAL[0]["writing_intent"]

    def test_key_points_merge_llm_additions(self):
        """LLM 新增 key_point → 原要点保留 + 新要点追加（去重）"""
        llm_output = [
            {**ORIGINAL[0], "key_points": ["第一章 要点 A", "第一章 新要点 C"]},
            *ORIGINAL[1:],
        ]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert result[0]["key_points"] == [
            "第一章 要点 A", "第一章 要点 B", "第一章 新要点 C"
        ]

    def test_key_points_protected_when_llm_drops_all(self):
        """LLM 整键丢弃 key_points → 还原为原始列表"""
        llm_output = [
            {k: v for k, v in ORIGINAL[0].items() if k != "key_points"},
            *ORIGINAL[1:],
        ]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert result[0].get("key_points") == ORIGINAL[0]["key_points"]

    def test_key_points_protected_when_llm_returns_string(self):
        """LLM 返回非 list 的 key_points → 还原为原始列表"""
        llm_output = [
            {**ORIGINAL[0], "key_points": "错误的字符串格式"},
            *ORIGINAL[1:],
        ]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert result[0].get("key_points") == ORIGINAL[0]["key_points"]

    def test_writing_intent_restored_when_llm_changes(self):
        """LLM 改写 writing_intent → 还原为原始值"""
        llm_output = [
            {**ORIGINAL[0], "writing_intent": "LLM 改写的意图"},
            *ORIGINAL[1:],
        ]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert result[0]["writing_intent"] == ORIGINAL[0]["writing_intent"]

    def test_writing_intent_restored_when_llm_drops_key(self):
        """LLM 整键丢弃 writing_intent → 还原为原始值"""
        llm_output = [
            {k: v for k, v in ORIGINAL[0].items() if k != "writing_intent"},
            *ORIGINAL[1:],
        ]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert result[0].get("writing_intent") == ORIGINAL[0]["writing_intent"]

    def test_materials_text_filled_when_llm_omits(self):
        """LLM 不返回 materials_text → 从原始补齐"""
        llm_output = [
            {k: v for k, v in ORIGINAL[0].items() if k != "materials_text"},
            *ORIGINAL[1:],
        ]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert result[0].get("materials_text") == ORIGINAL[0]["materials_text"]

    def test_section_type_filled_when_llm_omits(self):
        """LLM 不返回 section_type → 从原始补齐"""
        llm_output = [
            {k: v for k, v in ORIGINAL[0].items() if k != "section_type"},
            *ORIGINAL[1:],
        ]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert result[0].get("section_type") == ORIGINAL[0]["section_type"]

    def test_llm_return_fewer_chapters(self):
        """LLM 返回章节数少于原始 → 只保护存在的索引"""
        llm_output = ORIGINAL[:2]
        result = _parse_prompt_review_response(
            _json.dumps(llm_output, ensure_ascii=False), ORIGINAL
        )
        assert len(result) == 2

    def test_llm_response_unparseable_fallback(self):
        """LLM 输出完全无法解析 → 返回原始"""
        result = _parse_prompt_review_response("这不是 JSON", ORIGINAL)
        assert result is ORIGINAL  # 返回同一引用

    def test_llm_response_double_wrapped_in_dict(self):
        """LLM 输出包裹在 {'optimized': [...]} 中 → 正确解析"""
        result = _parse_prompt_review_response(
            _json.dumps({"optimized": ORIGINAL}, ensure_ascii=False), ORIGINAL
        )
        assert len(result) == 3


# ═══════════════════════════════════════════════════════════════
# _extract_source_sections_simple  — 文本段落拆分
# ═══════════════════════════════════════════════════════════════

class TestExtractSourceSectionsSimple:
    """_extract_source_sections_simple 文本解析测试"""

    def test_empty_source(self):
        assert _extract_source_sections_simple("") == []

    def test_single_file_marker(self):
        text = "📄 文件A.md\n内容A"
        result = _extract_source_sections_simple(text)
        assert len(result) == 1
        assert "文件A.md" in result[0]

    def test_multiple_file_markers(self):
        text = "📄 文件A.md\n内容A\n📄 文件B.md\n内容B"
        result = _extract_source_sections_simple(text)
        assert len(result) == 2

    def test_chinese_numbered_sections_fallback(self):
        text = "一、背景介绍\n这是背景内容\n二、技术方案\n这是方案内容"
        result = _extract_source_sections_simple(text)
        assert len(result) == 2

    def test_no_markers_single_block(self):
        text = "只有一段连续文本\n没有标记\n全部作为一段"
        result = _extract_source_sections_simple(text)
        assert len(result) >= 1

    def test_blank_lines_between_markers(self):
        text = "📄 文件A.md\n\n\n内容A\n\n📄 文件B.md\n内容B"
        result = _extract_source_sections_simple(text)
        # 空块不应被加入
        assert all(r.strip() for r in result)


# ═══════════════════════════════════════════════════════════════
# _parse_synthesize_response  — JSON fallback 解析
# ═══════════════════════════════════════════════════════════════

class TestParseSynthesizeResponse:
    """_parse_synthesize_response JSON 解析测试"""

    def test_parse_direct_list(self):
        response = _json.dumps([{"title": "章1"}, {"title": "章2"}])
        result = _parse_synthesize_response(response)
        assert len(result) == 2
        assert result[0]["title"] == "章1"

    def test_parse_nested_in_chapters_key(self):
        response = _json.dumps({"chapters": [{"title": "章1"}]})
        result = _parse_synthesize_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "章1"

    def test_parse_json_in_code_block(self):
        response = "```json\n[{\"title\": \"章1\"}]\n```"
        result = _parse_synthesize_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "章1"

    def test_parse_json_in_markdown_with_text(self):
        response = "以下是结果：\n```\n[{\"title\": \"章1\"}]\n```\n完毕"
        result = _parse_synthesize_response(response)
        assert len(result) == 1

    def test_unparseable_returns_fallback(self):
        result = _parse_synthesize_response("完全不是 JSON")
        assert len(result) == 5  # 降级为 5 章默认大纲
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════
# _parse_goal_response  — goal JSON 解析
# ═══════════════════════════════════════════════════════════════

class TestParseGoalResponse:
    """_parse_goal_response JSON 解析测试"""

    def test_standard_goal_json(self):
        response = _json.dumps({
            "title": "报告标题",
            "purpose": "目的",
            "target_audience": "读者",
            "overall_strategy": "策略",
            "writing_role": {"role": "专家", "tone": "正式"},
        })
        result = _parse_goal_response(response, "默认标题")
        assert result["title"] == "报告标题"
        assert result["writing_role"]["role"] == "专家"

    def test_partial_goal_json(self):
        response = _json.dumps({"title": "新标题"})
        result = _parse_goal_response(response, "默认标题")
        assert result["title"] == "新标题"

    def test_fallback_to_default_title(self):
        result = _parse_goal_response("not json", "默认标题")
        assert result["title"] == "默认标题"

    def test_extract_from_code_block(self):
        response = "```json\n{\"title\": \"标题\", \"purpose\": \"说明\"}\n```"
        result = _parse_goal_response(response, "默认")
        assert result["title"] == "标题"
        assert result["purpose"] == "说明"

    def test_invalid_json_in_code_block(self):
        result = _parse_goal_response("```json\n{invalid}\n```", "回退标题")
        assert result["title"] == "回退标题"

    def test_writing_role_type_guard(self):
        """writing_role 非 dict 时不应覆盖"""
        response = _json.dumps({"title": "T", "writing_role": "不应该覆盖"})
        result = _parse_goal_response(response, "默认")
        assert isinstance(result["writing_role"], dict)
        assert result["title"] == "T"


# ═══════════════════════════════════════════════════════════════
# _match_facts_to_chapter  — 关键词匹配逻辑
# ═══════════════════════════════════════════════════════════════

class TestMatchFactsToChapter:
    """_match_facts_to_chapter 关键词匹配测试"""

    @pytest.fixture
    def facts(self) -> list[dict]:
        return [
            {"fact": "总投资金额为5.2亿元", "category": "投资金额"},
            {"fact": "项目于2026年启动", "category": "时间节点"},
            {"fact": "采用三网隔离架构", "category": "架构方案"},
            {"fact": "互联网层配置Qoder", "category": "技术路线"},
            {"fact": "工控网层部署文心大模型", "category": "技术路线"},
        ]

    def test_match_by_key_points_keyword(self, facts):
        """key_points 中的关键词匹配到事实"""
        result = _match_facts_to_chapter(facts, "技术可行性", ["三网隔离"])
        matched_texts = [f["fact"] for f in result]
        assert "采用三网隔离架构" in matched_texts

    def test_match_by_writing_intent(self, facts):
        """writing_intent 中的关键词匹配到事实"""
        result = _match_facts_to_chapter(facts, "投资规模与预算", [])
        matched_texts = [f["fact"] for f in result]
        assert "总投资金额为5.2亿元" in matched_texts

    def test_core_categories_always_included(self, facts):
        """核心类别（投资金额/时间节点/架构方案）无条件保留"""
        result = _match_facts_to_chapter(facts, "无关内容", ["无关关键词"])
        matched_texts = [f["fact"] for f in result]
        # 核心类别的事实应被包含
        assert "总投资金额为5.2亿元" in matched_texts
        assert "采用三网隔离架构" in matched_texts

    def test_empty_facts(self):
        result = _match_facts_to_chapter([], "测试", ["A"])
        assert result == []

    def test_no_matches_returns_core_categories_only(self, facts):
        """无关键词匹配时，仅返回核心类别"""
        result = _match_facts_to_chapter(facts, "无关内容", ["绝对不存在的关键词xyz"])
        matched_texts = [f["fact"] for f in result]
        assert "互联网层配置Qoder" not in matched_texts
        assert "工控网层部署文心大模型" not in matched_texts


# ═══════════════════════════════════════════════════════════════
# _parse_optimize_response  — optimize_structure 解析
# ═══════════════════════════════════════════════════════════════

class TestParseOptimizeResponse:
    """_parse_optimize_response JSON 解析测试（新增节点）"""

    def test_parse_direct_list(self):
        from ai_report.graph.report_graph import _parse_optimize_response
        response = '[{"title": "章1", "level": 1, "writing_intent": "测试"}]'
        result = _parse_optimize_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "章1"

    def test_parse_nested_in_chapters(self):
        from ai_report.graph.report_graph import _parse_optimize_response
        response = '{"chapters": [{"title": "章1", "level": 1}]}'
        result = _parse_optimize_response(response)
        assert len(result) == 1

    def test_parse_json_in_markdown_block(self):
        from ai_report.graph.report_graph import _parse_optimize_response
        response = '```json\n[{"title": "章1", "level": 1}]\n```'
        result = _parse_optimize_response(response)
        assert len(result) == 1

    def test_unparseable_returns_fallback_3_chapters(self):
        from ai_report.graph.report_graph import _parse_optimize_response
        result = _parse_optimize_response("不是 JSON")
        assert len(result) == 3  # 降级为3章默认大纲

    def test_parse_empty_array(self):
        from ai_report.graph.report_graph import _parse_optimize_response
        result = _parse_optimize_response("[]")
        assert len(result) == 3  # 空数组也被视为未解析，降级为3章默认大纲
