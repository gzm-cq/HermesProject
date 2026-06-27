"""
测试 DAG 层推导工具
===================

覆盖场景：
  测试 1: 3 intro + 3 body + 1 conclusion → 3 层
  测试 2: 0 intro + 4 body + 0 conclusion → 1 层（降级）
  测试 3: 1 intro + 0 body + 1 conclusion → 2 层
  测试 4: 仅 2 章 → 单层
  测试 5: 混合 section_type 未知 → 视为 body
  测试 6: 空列表 → []
  测试 7: chapter_prompts 覆盖 section_type
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ai_report.core.dag_utils import derive_dag_layers, _extract_section_types


# ── 模拟数据类 ────────────────────────────────────────


@dataclass
class MockSection:
    """模拟 SectionSpec，只关注测试需要的字段。"""
    title: str
    section_type: str = "body"
    level: int = 1
    estimated_words: int = 1000
    required_data: list[str] = field(default_factory=list)
    diagram_types: list[str] = field(default_factory=list)
    content_template: str | None = None


# ── 测试分层推导 ───────────────────────────────────────


class TestDeriveDagLayers:
    """DAG 分层核心逻辑测试。"""

    @pytest.mark.unit
    def test_standard_3_layers(self) -> None:
        """测试 1: 3 intro + 3 body + 1 conclusion → 3 层"""
        sections = [
            MockSection("背景与现状", "intro"),
            MockSection("研究目标", "intro"),
            MockSection("调研方法", "intro"),
            MockSection("技术方案", "body"),
            MockSection("架构设计", "body"),
            MockSection("实施路径", "body"),
            MockSection("总结与建议", "conclusion"),
        ]
        layers = derive_dag_layers(sections)
        assert len(layers) == 3, f"期望 3 层，得到 {len(layers)}"
        assert layers[0] == [0, 1, 2], f"Layer 0 应为 intro 索引: {layers[0]}"
        assert layers[1] == [3, 4, 5], f"Layer 1 应为 body 索引: {layers[1]}"
        assert layers[2] == [6], f"Layer 2 应为 conclusion 索引: {layers[2]}"

    @pytest.mark.unit
    def test_all_body_no_intro(self) -> None:
        """测试 2: 0 intro + 4 body + 0 conclusion → 1 层（降级）"""
        sections = [
            MockSection("章一", "body"),
            MockSection("章二", "body"),
            MockSection("章三", "body"),
            MockSection("章四", "body"),
        ]
        layers = derive_dag_layers(sections)
        assert len(layers) == 1, f"降级应为单层: {layers}"
        assert layers[0] == [0, 1, 2, 3]

    @pytest.mark.unit
    def test_intro_and_conclusion_only(self) -> None:
        """测试 3: 1 intro + 0 body + 1 conclusion → 1 层（≤3 降级）"""
        sections = [
            MockSection("引言", "intro"),
            MockSection("结论", "conclusion"),
        ]
        layers = derive_dag_layers(sections)
        assert len(layers) == 1, f"≤3 章降级为单层: {layers}"
        assert layers[0] == [0, 1]

    @pytest.mark.unit
    def test_only_two_chapters(self) -> None:
        """测试 4: 仅 2 章 → 单层"""
        sections = [
            MockSection("章一", "body"),
            MockSection("章二", "conclusion"),
        ]
        layers = derive_dag_layers(sections)
        assert len(layers) == 1, f"≤3 章应单层: {layers}"
        assert layers[0] == [0, 1]

    @pytest.mark.unit
    def test_unknown_section_type_as_body(self) -> None:
        """测试 5: 未知 section_type → 视为 body"""
        sections = [
            MockSection("引言", "intro"),
            MockSection("未知类型", "weird_type"),
            MockSection("主体一", "body"),
            MockSection("结论", "conclusion"),
        ]
        layers = derive_dag_layers(sections)
        assert len(layers) == 3, f"应 3 层: {layers}"
        assert layers[0] == [0], f"intro: {layers[0]}"
        assert layers[1] == [1, 2], f"未知→body: {layers[1]}"
        assert layers[2] == [3], f"conclusion: {layers[2]}"

    @pytest.mark.unit
    def test_empty_sections(self) -> None:
        """测试 6: 空列表 → []"""
        assert derive_dag_layers([]) == []

    @pytest.mark.unit
    def test_only_one_chapter(self) -> None:
        """单章 → 单层"""
        sections = [MockSection("唯一章节", "body")]
        layers = derive_dag_layers(sections)
        assert layers == [[0]]

    @pytest.mark.unit
    def test_exactly_three_chapters(self) -> None:
        """3 章 → 单层（降级）"""
        sections = [
            MockSection("A", "intro"),
            MockSection("B", "body"),
            MockSection("C", "conclusion"),
        ]
        layers = derive_dag_layers(sections)
        assert len(layers) == 1
        assert layers[0] == [0, 1, 2]

    @pytest.mark.unit
    def test_chapter_prompts_override(self) -> None:
        """测试 7: chapter_prompts section_type 覆盖 sections"""
        sections = [
            MockSection("章一", "body"),
            MockSection("章二", "body"),
            MockSection("章三", "body"),
        ]
        prompts = [
            {"title": "章一", "section_type": "intro"},
            {"title": "章二", "section_type": "body"},
            {"title": "章三", "section_type": "conclusion"},
        ]
        layers = derive_dag_layers(sections, chapter_prompts=prompts)
        # ≤3 章降级为单层
        assert len(layers) == 1
        assert layers[0] == [0, 1, 2]

    @pytest.mark.unit
    def test_analysis_as_body(self) -> None:
        """analysis 类型并入 body 层"""
        sections = [
            MockSection("概述", "intro"),
            MockSection("分析", "analysis"),
            MockSection("建议", "recommendation"),
        ]
        layers = derive_dag_layers(sections)
        # ≤3 章节降级为单层
        assert len(layers) == 1
        assert layers[0] == [0, 1, 2]


class TestExtractSectionTypes:
    """section_type 提取优先级测试。"""

    @pytest.mark.unit
    def test_prefer_chapter_prompts(self) -> None:
        """chapter_prompts 优先于 sections。"""
        sections = [MockSection("章", "body")]
        prompts = [{"title": "章", "section_type": "intro"}]
        result = _extract_section_types(sections, prompts, 1)
        assert result == ["intro"]

    @pytest.mark.unit
    def test_fallback_to_section(self) -> None:
        """无 chapter_prompts 时回退到 sections。"""
        sections = [MockSection("章", "intro")]
        result = _extract_section_types(sections, None, 1)
        assert result == ["intro"]

    @pytest.mark.unit
    def test_fallback_to_body(self) -> None:
        """无任何信息时回退到 'body'。"""
        # 传入的 sections 没有 section_type 属性
        class PlainSection:
            def __init__(self, title: str) -> None:
                self.title = title
        sections = [PlainSection("A"), PlainSection("B")]
        result = _extract_section_types(sections, None, 2)
        assert result == ["body", "body"]

    @pytest.mark.unit
    def test_shorter_prompts_list(self) -> None:
        """prompts 列表短于章节数时剩余用 body。"""
        sections = [
            MockSection("章一", "body"),
            MockSection("章二", ""),
        ]
        prompts = [{"section_type": "intro"}]  # 只有 1 个
        result = _extract_section_types(sections, prompts, 2)
        assert result == ["intro", "body"]
