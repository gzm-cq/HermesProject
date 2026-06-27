"""phase/split.py 单元测试"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from knowledge_tree_builder.config import AppConfig
from knowledge_tree_builder.phase.split import (
    _check_format_valid,
    _check_type_match,
    check_self_explanatory,
    process_candidates,
)


class TestCheckSelfExplanatory:
    """check_self_explanatory 自解释检查测试"""

    def test_clean_text_passes(self) -> None:
        ok, reason = check_self_explanatory("HDBSCAN 通过层次聚类覆盖不同密度的簇")
        assert ok is True
        assert reason == ""

    def test_pronoun_this_method_rejected(self) -> None:
        ok, reason = check_self_explanatory("这种方法通过层次聚类实现")
        assert ok is False
        assert "pronoun" in reason

    def test_pronoun_the_model_rejected(self) -> None:
        ok, reason = check_self_explanatory("该模型在准确率上优于基线")
        assert ok is False
        assert "pronoun" in reason

    def test_pronoun_above_algorithm_rejected(self) -> None:
        ok, reason = check_self_explanatory("上述算法通过注意力机制实现")
        assert ok is False
        assert "pronoun" in reason

    def test_meta_reference_as_above_rejected(self) -> None:
        ok, reason = check_self_explanatory("如上所述，HDBSCAN 优于 DBSCAN")
        assert ok is False
        assert "meta_ref" in reason

    def test_article_intro_rejected(self) -> None:
        ok, reason = check_self_explanatory("本文介绍了三种聚类算法的对比")
        assert ok is False
        assert "meta_ref" in reason

    def test_hdbscan_abbreviation_accepted(self) -> None:
        ok, _ = check_self_explanatory("HDBSCAN 通过层次聚类覆盖不同密度的簇")
        assert ok is True

    def test_rag_llm_accepted(self) -> None:
        ok, _ = check_self_explanatory("RAG 通过外部知识库增强 LLM 的生成质量")
        assert ok is True

    def test_unknown_abbreviation_accepted(self) -> None:
        """缩写检查已移除，XYZPR 不再被拒绝"""
        ok, reason = check_self_explanatory("XYZPR 是一种新的训练方法")
        assert ok is True
        assert reason == ""


class TestCheckFormatValid:
    """_check_format_valid 格式合法性测试"""

    def test_principle_with_causal_verb(self) -> None:
        ok, _ = _check_format_valid("HDBSCAN 通过层次聚类实现任意形状簇发现", "principle")
        assert ok is True

    def test_principle_without_causal_verb(self) -> None:
        ok, reason = _check_format_valid("注意力机制由 Q/K/V 三个向量组成", "principle")
        assert ok is False
        assert "因果" in reason

    def test_formula_with_equals_sign(self) -> None:
        ok, _ = _check_format_valid("attention = softmax(Q × K^T / √d)", "formula")
        assert ok is True

    def test_formula_with_function_name(self) -> None:
        ok, _ = _check_format_valid("余弦相似度通过 cosine 函数计算两个向量的方向相似度", "formula")
        assert ok is True

    def test_formula_constant_only(self) -> None:
        ok, reason = _check_format_valid("一加一等于二是数学基础", "formula")
        assert ok is False

    def test_conclusion_with_comparison(self) -> None:
        ok, _ = _check_format_valid("HDBSCAN 在非均匀密度数据上优于 DBSCAN", "conclusion")
        assert ok is True

    def test_conclusion_without_condition(self) -> None:
        ok, reason = _check_format_valid("这个数据结构非常高效", "conclusion")
        assert ok is False

    def test_method_with_steps(self) -> None:
        ok, _ = _check_format_valid("部署流程分三步：首先备份，其次同步，最后验证", "method")
        assert ok is True

    def test_method_with_normative(self) -> None:
        ok, _ = _check_format_valid("部署前必须运行 plan 命令验证清单", "method")
        assert ok is True

    def test_method_without_signals(self) -> None:
        ok, reason = _check_format_valid("知识树包含多个领域", "method")
        assert ok is False

    def test_key_point_basic(self) -> None:
        ok, _ = _check_format_valid("自进化Agent分为三大范式：模型中心、环境中心、共进化", "key_point")
        assert ok is True

    def test_key_point_too_short(self) -> None:
        ok, reason = _check_format_valid("三大范式分类", "key_point")
        assert ok is False


class TestCheckTypeMatch:
    """_check_type_match 类型匹配测试"""

    def test_principle_correct(self) -> None:
        assert _check_type_match("HDBSCAN 通过层次聚类实现", "principle") == "principle"

    def test_principle_actually_keypoint(self) -> None:
        result = _check_type_match("注意力机制由三个向量组成", "principle")
        assert result == "key_point"

    def test_conclusion_actually_keypoint(self) -> None:
        result = _check_type_match("知识树包含领域和科目两层", "conclusion")
        assert result == "key_point"


class TestProcessCandidates:
    """process_candidates 主流程测试"""

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_atomic_passes_directly(self, mock_llm: Any, default_config: AppConfig) -> None:
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "HDBSCAN 通过层次聚类覆盖不同密度的簇", "type": "principle", "claims_count": 1},
            ],
        }
        result = process_candidates(report, config=default_config)
        assert len(result["atomic_knowledge"]) == 1
        assert result["atomic_knowledge"][0]["text"] == "HDBSCAN 通过层次聚类覆盖不同密度的簇"

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_non_atomic_triggers_split(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {
            "split_items": [
                {"text": "Revision 算子通过分析失败轨迹修改代码", "type": "principle", "claims_count": 1},
                {"text": "Refinement 算子通过微调优化实现性能提升", "type": "principle", "claims_count": 1},
            ],
        }
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "Revision 通过分析失败轨迹修改代码，同时 Refinement 在成功基础上微调优化", "type": "principle", "claims_count": 2},
            ],
        }
        result = process_candidates(report, config=default_config)
        assert len(result["atomic_knowledge"]) == 2

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_quality_eval_rejects_bad_format(self, mock_llm: Any, default_config: AppConfig) -> None:
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "注意力机制由三个向量组成这是一条较长的文本", "type": "principle", "claims_count": 1},
            ],
        }
        result = process_candidates(report, config=default_config)
        assert len(result["atomic_knowledge"]) == 0

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_self_explanatory_rejects_pronoun(self, mock_llm: Any, default_config: AppConfig) -> None:
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "该方法通过层次聚类实现任意形状簇发现", "type": "principle", "claims_count": 1},
            ],
        }
        result = process_candidates(report, config=default_config)
        assert len(result["atomic_knowledge"]) == 0

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_self_explanatory_disabled(self, mock_llm: Any, default_config: AppConfig) -> None:
        default_config.self_explanatory_rules = False
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "该方法通过层次聚类实现任意形状簇发现", "type": "principle", "claims_count": 1},
            ],
        }
        result = process_candidates(report, config=default_config)
        assert len(result["atomic_knowledge"]) == 1

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_split_llm_error_to_review(self, mock_llm: Any, default_config: AppConfig) -> None:
        mock_llm.return_value = {"error": "timeout"}
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "Revision 通过分析失败轨迹修改代码，同时 Refinement 在成功基础上微调优化", "type": "principle", "claims_count": 2},
            ],
        }
        result = process_candidates(report, config=default_config)
        assert len(result["review_queue_items"]) > 0
        assert result["review_queue_items"][0]["type"] == "incomplete_split"

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_empty_candidates(self, mock_llm: Any, default_config: AppConfig) -> None:
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [],
        }
        result = process_candidates(report, config=default_config)
        assert result["atomic_knowledge"] == []
        assert result["stats"]["total"] == 0

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_sum_check_failure_triggers_resplit(self, mock_llm: Any, default_config: AppConfig) -> None:
        """sum 校验失败 → 重新分析成功，不产生 consistency_warning。"""
        # 父候选 claims_count=3
        # 第1次拆解: LLM 返回 3 条，但其中一条被标为 claims_count=2（LLM 错误）
        # sum=1+1+2=4 != 3 → 触发重试
        # 非原子条目(claims=2)重试拆解 → 返回 claims_count=1 → 成功
        mock_llm.side_effect = [
            # 第1次拆解
            {
                "split_items": [
                    {"text": "HDBSCAN 通过层次聚类覆盖不同密度的簇", "type": "principle", "claims_count": 1},
                    {"text": "DBSCAN 基于密度可达性发现任意形状簇", "type": "principle", "claims_count": 1},
                    {"text": "KNN 通过近邻投票实现分类，SVM 通过最大间隔实现分类", "type": "principle", "claims_count": 2},
                ],
            },
            # 第2次重新分析（仅对 claims_count=2 的条目）→ 返回 2 条 claims=1
            {
                "split_items": [
                    {"text": "KNN 通过近邻投票实现分类", "type": "principle", "claims_count": 1},
                    {"text": "SVM 通过最大间隔实现分类", "type": "principle", "claims_count": 1},
                ],
            },
        ]
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "HDBSCAN 通过层次聚类覆盖不同密度的簇，DBSCAN 基于密度可达性发现任意形状簇，KNN 通过近邻投票实现分类同时 SVM 通过最大间隔实现分类", "type": "principle", "claims_count": 3},
            ],
        }
        result = process_candidates(report, config=default_config)
        # 重试后应有4条原子知识点（2条直接通过 + 2条 resplit 产出）
        assert len(result["atomic_knowledge"]) == 4
        # 不应有 consistency_warning
        consistency_warnings = [
            r for r in result["review_queue_items"]
            if r["type"] == "consistency_warning"
        ]
        assert len(consistency_warnings) == 0

    @patch("knowledge_tree_builder.phase.split.call_llm_json")
    def test_sum_check_failure_produces_consistency_warning(self, mock_llm: Any, default_config: AppConfig) -> None:
        """sum 校验失败 → 重试返回 claims 不足 → 规则覆盖无法修复 → consistency_warning。"""
        # 父候选 claims_count=3
        # Round 0: LLM 返回 2 条 (claims=1, claims=2), sum=3 == 3 → 不触发 resplit
        #   claims=1 直接通过, claims=2 非原子 → 进入 round 1
        # Round 1: LLM 拆解 claims=2 条目，返回 1 条 claims=1
        #   sum=1 != expected=2 → 触发 resplit
        # Resplit: 仍返回 1 条 claims=1 → retry_sum=1 != expected=2
        #   规则覆盖: adjust("HDBSCAN...", 1) → 无连词 → 1
        #   rule_sum=1 != 2 → consistency_warning
        mock_llm.side_effect = [
            # Round 0: 拆解为 2 条
            {
                "split_items": [
                    {"text": "HDBSCAN 通过层次聚类覆盖不同密度的簇", "type": "principle", "claims_count": 1},
                    {"text": "DBSCAN 基于密度可达性发现任意形状簇，KNN 通过近邻投票实现分类", "type": "principle", "claims_count": 2},
                ],
            },
            # Round 1: 拆解 claims=2 条目，只返回 1 条 claims=1
            {
                "split_items": [
                    {"text": "DBSCAN 基于密度可达性发现任意形状簇", "type": "principle", "claims_count": 1},
                ],
            },
            # Round 1 resplit: 仍返回 1 条 claims=1
            {
                "split_items": [
                    {"text": "DBSCAN 基于密度可达性发现任意形状簇", "type": "principle", "claims_count": 1},
                ],
            },
        ]
        report = {
            "article_title": "test",
            "analysis": {"content_summary": "test", "empty_article": False},
            "candidates": [
                {"text": "HDBSCAN 通过层次聚类覆盖不同密度的簇，DBSCAN 基于密度可达性发现任意形状簇同时 KNN 通过近邻投票实现分类", "type": "principle", "claims_count": 3},
            ],
        }
        result = process_candidates(report, config=default_config)
        # 应有 consistency_warning
        consistency_warnings = [
            r for r in result["review_queue_items"]
            if r["type"] == "consistency_warning"
        ]
        assert len(consistency_warnings) > 0
