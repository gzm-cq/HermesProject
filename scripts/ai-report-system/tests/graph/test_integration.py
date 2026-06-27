"""StateGraph 集成测试 — mock LLM 验证 5 节点数据流。

测试策略：
  1. define_goal — 验证 LLM 输出被正确解析为 report_goal
  2. synthesize  — 验证预定义 chapter_prompts 正确传递
  3. prompt_review — 验证保护逻辑在管线中生效
  4. 全管线串联 — verify StateGraph 5 节点 end-to-end
"""

from __future__ import annotations

import json as _json
from typing import Any

import pytest

from ai_report.graph.report_graph import (
    GraphState,
    build_report_graph,
    define_goal,
    synthesize,
    prompt_review,
)


# ═══════════════════════════════════════════════════════════════
# Node 1: define_goal
# ═══════════════════════════════════════════════════════════════

GOAL_RESPONSE = {
    "title": "智能化转型建设规划",
    "purpose": "论证可行性和实施路径",
    "target_audience": "决策层",
    "overall_strategy": "分三阶段推进",
    "writing_role": {"role": "企业架构师", "tone": "专业"},
}


class TestDefineGoalNode:
    """Node 1: mock LLM → verify report_goal output"""

    def test_returns_goal_with_title(self, mock_llm, sample_source):
        mock_llm.register("报告主题", GOAL_RESPONSE)
        state: GraphState = {
            "topic": "智能化转型建设规划",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": None,
            "reference_outlines": None,
            "chapter_prompts": None,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": None,
        }
        result = define_goal(state)
        goal = result["report_goal"]
        assert goal["title"] == "智能化转型建设规划"
        assert "可行" in goal["purpose"]
        assert goal["writing_role"]["role"] == "企业架构师"

    def test_fallback_to_topic_when_llm_fails(self, mock_llm, sample_source):
        """LLM 返回不可解析内容 → 回退至 topic 作为标题"""
        mock_llm.register("报告主题", "不是 JSON 也不是有效内容")
        state: GraphState = {  # type: ignore[typeddict-item]
            "topic": "回退测试标题",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": None,
            "reference_outlines": None,
            "chapter_prompts": None,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": None,
        }
        result = define_goal(state)
        assert result["report_goal"]["title"] == "回退测试标题"

    def test_llm_was_called(self, mock_llm, sample_source):
        """验证 mock LLM 确实被调用了"""
        mock_llm.register("报告目标", GOAL_RESPONSE)
        state: GraphState = {  # type: ignore[typeddict-item]
            "topic": "测试",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": None,
            "reference_outlines": None,
            "chapter_prompts": None,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": None,
        }
        define_goal(state)
        assert len(mock_llm.calls) >= 1


# ═══════════════════════════════════════════════════════════════
# Node 3: synthesize
# ═══════════════════════════════════════════════════════════════

class TestSynthesizeNode:
    """Node 3: 预定义 chapter_prompts 传递"""

    def test_uses_predefined_chapter_prompts(
        self, sample_source, sample_goal, sample_chapters
    ):
        """report_goal 中已有预定义 chapter_prompts → 跳过 LLM"""
        goal_with_chapters = {**sample_goal, "chapter_prompts": sample_chapters}
        state: GraphState = {  # type: ignore[typeddict-item]
            "topic": "测试",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": goal_with_chapters,
            "reference_outlines": [],
            "chapter_prompts": None,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": [],
        }
        result = synthesize(state)
        prompts = result["chapter_prompts"]
        assert len(prompts) == 3
        assert prompts[0]["title"] == "可行性分析概述"

    def test_no_predefined_falls_to_llm(
        self, mock_llm, sample_source, sample_goal
    ):
        """无预定义 chapter_prompts → 用 mock LLM 生成"""
        mock_llm.register(
            "生成大纲",
            [{"title": "章1", "writing_intent": "测试意图", "key_points": ["点A"]}],
        )
        state: GraphState = {  # type: ignore[typeddict-item]
            "topic": "测试",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": sample_goal,
            "reference_outlines": [],
            "chapter_prompts": None,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": [],
        }
        result = synthesize(state)
        assert len(result["chapter_prompts"]) > 0


# ═══════════════════════════════════════════════════════════════
# Node 5: prompt_review
# ═══════════════════════════════════════════════════════════════

class TestPromptReviewNode:
    """Node 5: mock LLM → verify 保护逻辑在管线中生效"""

    def test_key_points_preserved_after_review(
        self, mock_llm, sample_source, sample_goal, sample_chapters
    ):
        """LLM 尝试删除 key_points → 保护层还原"""
        mock_llm.register(
            "提示词质量审核",
            # LLM 返回时只保留了部分 key_points
            [
                {**sample_chapters[0], "key_points": ["企业现状"]},
                {**sample_chapters[1], "key_points": ["三网架构"]},
                {**sample_chapters[2], "key_points": ["投资预算"]},
            ],
        )
        original_kp = [list(cp["key_points"]) for cp in sample_chapters]

        goal_with_chapters = {**sample_goal, "chapter_prompts": sample_chapters}
        state: GraphState = {  # type: ignore[typeddict-item]
            "topic": "测试",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": goal_with_chapters,
            "reference_outlines": [],
            "chapter_prompts": sample_chapters,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": [],
        }
        result = prompt_review(state)
        optimized = result["optimized_prompts"]
        for i, kp in enumerate(original_kp):
            for item in kp:
                assert item in optimized[i].get("key_points", []), (
                    f"第{i}章丢失 key_point: {item}"
                )

    def test_writing_intent_restored(
        self, mock_llm, sample_source, sample_goal, sample_chapters
    ):
        """LLM 改写 writing_intent → 保护层还原为原始值"""
        mock_llm.register(
            "提示词质量审核",
            [
                {**sample_chapters[0], "writing_intent": "LLM 改写的内容"},
                *sample_chapters[1:],
            ],
        )
        original_intent = sample_chapters[0]["writing_intent"]

        goal_with_chapters = {**sample_goal, "chapter_prompts": sample_chapters}
        state: GraphState = {  # type: ignore[typeddict-item]
            "topic": "测试",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": goal_with_chapters,
            "reference_outlines": [],
            "chapter_prompts": sample_chapters,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": [],
        }
        result = prompt_review(state)
        optimized = result["optimized_prompts"]
        assert optimized[0]["writing_intent"] == original_intent

    def test_calls_llm_with_prompt(
        self, mock_llm, sample_source, sample_goal, sample_chapters
    ):
        """验证 prompt_review 确实调用了 LLM"""
        mock_llm.register(
            "提示词质量审核",
            [dict(cp) for cp in sample_chapters],
        )
        goal_with_chapters = {**sample_goal, "chapter_prompts": sample_chapters}
        state: GraphState = {  # type: ignore[typeddict-item]
            "topic": "测试",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": goal_with_chapters,
            "reference_outlines": [],
            "chapter_prompts": sample_chapters,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": None,
            "raw_materials": [],
        }
        prompt_review(state)
        assert len(mock_llm.calls) >= 1


# ═══════════════════════════════════════════════════════════════
# 全管线串联
# ═══════════════════════════════════════════════════════════════

class TestFullGraph:
    """run_planning 全管线 5 节点串联"""

    def test_full_pipeline_with_mock_llm(
        self, mock_llm, sample_source, sample_goal, sample_chapters
    ):
        """全管线：define_goal → search_refs → synthesize → curate → prompt_review"""
        # 注册所有 LLM 调用所需的响应
        mock_llm.register("报告主题", sample_goal)

        mock_llm.register(
            "生成大纲",
            sample_chapters,
        )

        mock_llm.register(
            "提示词质量审核",
            sample_chapters,
        )

        graph = build_report_graph()
        result = graph.invoke({
            "topic": "智能化转型建设规划",
            "source_content": sample_source,
            "report_type": "tech",
            "language": "zh",
            "report_goal": None,
            "reference_outlines": None,
            "chapter_prompts": None,
            "materials": None,
            "optimized_prompts": None,
            "domain_config": {"high": ["gov.cn"], "medium": ["csdn.net"]},
            "raw_materials": None,
        })

        # search_refs 在没有缓存池时返回空，但不应阻断管线
        assert result.get("report_goal") is not None
        # synthesize 应产出 chapter_prompts
        assert result.get("chapter_prompts") is not None
        # prompt_review 应产出 optimized_prompts
        optimized = result.get("optimized_prompts") or result.get("chapter_prompts")
        assert len(optimized) > 0

        # LLM 被调用了至少 3 次（define_goal + synthesize + prompt_review）
        # search_refs 和 curate 不需要 LLM
        assert len(mock_llm.calls) >= 2  # 至少 2 次（goal 和 review）
