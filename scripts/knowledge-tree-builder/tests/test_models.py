"""models.py 单元测试"""

from __future__ import annotations

import pytest

from knowledge_tree_builder.models import (
    KNOWLEDGE_TYPE_LABELS,
    KNOWLEDGE_TYPE_NAMES,
    KnowledgeType,
    _has_multiple_clauses,
    adjust_claims_count,
)


class TestKnowledgeType:
    """KnowledgeType 枚举测试"""

    def test_all_five_types_exist(self) -> None:
        assert len(KnowledgeType) == 5

    def test_type_values(self) -> None:
        assert KnowledgeType.PRINCIPLE.value == "principle"
        assert KnowledgeType.FORMULA.value == "formula"
        assert KnowledgeType.KEY_POINT.value == "key_point"
        assert KnowledgeType.CONCLUSION.value == "conclusion"
        assert KnowledgeType.METHOD.value == "method"

    def test_str_equality(self) -> None:
        assert "principle" == KnowledgeType.PRINCIPLE
        assert "formula" == KnowledgeType.FORMULA
        assert "key_point" == KnowledgeType.KEY_POINT

    def test_knowledge_type_names_complete(self) -> None:
        assert len(KNOWLEDGE_TYPE_NAMES) == 5
        assert "principle" in KNOWLEDGE_TYPE_NAMES
        assert "method" in KNOWLEDGE_TYPE_NAMES

    def test_labels_complete(self) -> None:
        assert len(KNOWLEDGE_TYPE_LABELS) == 5
        assert KNOWLEDGE_TYPE_LABELS[KnowledgeType.PRINCIPLE] == "原理"
        assert KNOWLEDGE_TYPE_LABELS[KnowledgeType.METHOD] == "方法/流程"


class TestAdjustClaimsCount:
    """adjust_claims_count 规则修正测试"""

    def test_no_signal_returns_llm_value(self) -> None:
        assert adjust_claims_count("HDBSCAN 通过层次聚类覆盖不同密度的簇", 1) == 1

    def test_conjunction_and(self) -> None:
        assert adjust_claims_count("模型A通过离线推理自我改进，并且环境中心通过增强交互实现进化", 1) == 2

    def test_conjunction_but(self) -> None:
        assert adjust_claims_count("方法A效果好但是成本高", 1) == 2

    def test_conjunction_simultaneously(self) -> None:
        assert adjust_claims_count("Revision 在失败后触发，同时 Refinement 在成功后触发", 1) == 2

    def test_single_semicolon(self) -> None:
        assert adjust_claims_count("第一条知识；第二条知识", 1) == 2

    def test_multiple_semicolons(self) -> None:
        assert adjust_claims_count("知识A；知识B；知识C", 1) == 3

    def test_chinese_and_english_semicolons(self) -> None:
        assert adjust_claims_count("知识A；知识B;知识C", 1) == 3

    def test_llm_higher_than_rule_preserved(self) -> None:
        # LLM=3, 规则=2 → 保留 3
        assert adjust_claims_count("模型A通过离线推理自我改进，并且环境中心通过增强交互实现进化", 3) == 3

    def test_llm_zero_rule_overrides(self) -> None:
        # LLM=0, 规则=2 → 返回 2
        assert adjust_claims_count("模型A通过离线推理自我改进，并且环境中心通过增强交互实现进化", 0) == 2

    def test_empty_text(self) -> None:
        assert adjust_claims_count("", 1) == 1

    def test_whitespace_text(self) -> None:
        assert adjust_claims_count("   ", 2) == 2

    def test_negative_llm_claims(self) -> None:
        assert adjust_claims_count("简单知识", -1) == 0


class TestHasMultipleClauses:
    """_has_multiple_clauses 多主谓检测测试"""

    def test_two_independent_predicates(self) -> None:
        text = "Revision 算子通过分析失败轨迹来修改代码实现，Recombination 算子重新组合已有功能模块解决新问题"
        assert _has_multiple_clauses(text) is True

    def test_single_clause(self) -> None:
        text = "HDBSCAN 通过层次聚类覆盖不同密度的簇"
        assert _has_multiple_clauses(text) is False

    def test_short_parts_ignored(self) -> None:
        text = "是的，这个方法很好"
        assert _has_multiple_clauses(text) is False
