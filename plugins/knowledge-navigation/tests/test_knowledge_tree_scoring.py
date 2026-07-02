"""回归测试：知识树候选必须进入统一分数统计。"""

from unittest.mock import MagicMock, patch

import pytest

from knowledge_navigation.config import CONFIG
from knowledge_navigation.core import hooks as nav_hooks
from knowledge_navigation.core.hooks import pre_llm_call


@patch("knowledge_navigation.core.hooks._router_route")
@patch("knowledge_navigation.core.hooks._do_hindsight_recall")
def test_knowledge_tree_candidates_have_final_scores_in_score_stats(
    mock_recall: MagicMock,
    mock_route: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """KT-only fallback 时 score_stats 不能再出现 count=0。"""
    nav_hooks._circuit_failures = 0
    nav_hooks._circuit_open_until = 0.0
    nav_hooks._injected_ids.clear()

    mock_recall.return_value = {"results": [], "trace": {}}
    mock_route.return_value = {"h": False, "kt": True, "s": False}
    kt_results = [
        {"id": 101, "text": "知识树结果A", "score": 0.8},
        {"id": 102, "text": "知识树结果B", "score": 0.6},
    ]

    with patch.object(nav_hooks, "HAS_KNOWLEDGE_TREE", True), \
         patch.object(nav_hooks, "_do_kt_recall", return_value=kt_results), \
         patch.object(CONFIG, "eval_match_enabled", False):
        caplog.set_level("INFO")
        result = pre_llm_call("session-kt-score", "测试知识树统一打分质量", platform="cli")

    assert result is not None
    success_records = [rec for rec in caplog.records if getattr(rec, "event", None) == "recall_success"]
    assert success_records
    rec = success_records[-1]
    stats = getattr(rec, "score_stats")
    assert stats["count"] == 2
    assert stats["min"] > 0
    assert getattr(rec, "has_knowledge_tree") is True
