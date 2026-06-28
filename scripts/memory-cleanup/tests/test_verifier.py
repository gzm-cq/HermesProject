"""verifier.py 单元测试 — Phase 2 remove 候选验证。"""

from unittest.mock import MagicMock

import pytest

from memory_cleanup.core.verifier import phase2_verify


class TestPhase2Verify:
    """phase2_verify() 单元测试。"""

    def test_empty_remove_list(self, mock_llm_client: MagicMock, mock_session_db: MagicMock) -> None:
        result = phase2_verify([], [], "MEMORY.md", mock_llm_client, mock_session_db)
        assert result == {"correct": [], "corrected": [], "keep": []}

    def test_all_correct(self, mock_llm_client: MagicMock, mock_session_db: MagicMock) -> None:
        mock_llm_client.verify_one.return_value = {"verdict": "correct", "note": ""}
        entries = ["条目A", "条目B"]
        remove_list = [{"index": 0, "原因": "过期"}, {"index": 1, "原因": "重复"}]
        result = phase2_verify(entries, remove_list, "MEMORY.md", mock_llm_client, mock_session_db, max_workers=2)
        assert len(result["correct"]) == 2
        assert len(result["corrected"]) == 0
        assert len(result["keep"]) == 0

    def test_all_corrected(self, mock_llm_client: MagicMock, mock_session_db: MagicMock) -> None:
        mock_llm_client.verify_one.return_value = {
            "verdict": "corrected", "corrected_text": "原始条目-需要更新内容", "note": ""
        }
        entries = ["原始条目"]
        remove_list = [{"index": 0, "原因": "有偏差"}]
        result = phase2_verify(entries, remove_list, "USER.md", mock_llm_client, mock_session_db, max_workers=2)
        assert len(result["correct"]) == 0
        assert len(result["corrected"]) == 1
        assert result["corrected"][0]["corrected_text"] == "原始条目-需要更新内容"

    def test_all_keep(self, mock_llm_client: MagicMock, mock_session_db: MagicMock) -> None:
        mock_llm_client.verify_one.return_value = {"verdict": "keep", "note": "不应删除"}
        entries = ["重要条目"]
        remove_list = [{"index": 0, "原因": "误标"}]
        result = phase2_verify(entries, remove_list, "MEMORY.md", mock_llm_client, mock_session_db, max_workers=2)
        assert len(result["correct"]) == 0
        assert len(result["corrected"]) == 0
        assert len(result["keep"]) == 1
        assert result["keep"][0]["note"] == "不应删除"

    def test_mixed_results(self, mock_llm_client: MagicMock, mock_session_db: MagicMock) -> None:
        """混合 verdict 返回正确分类。"""
        responses = [
            {"verdict": "correct", "note": ""},
            {"verdict": "keep", "note": "需保留"},
            {"verdict": "corrected", "corrected_text": "条目C-有更新内容需要", "note": ""},
        ]
        mock_llm_client.verify_one.side_effect = responses
        entries = ["条目A", "条目B", "条目C"]
        remove_list = [{"index": 0, "原因": "a"}, {"index": 1, "原因": "b"}, {"index": 2, "原因": "c"}]
        result = phase2_verify(entries, remove_list, "MEMORY.md", mock_llm_client, mock_session_db, max_workers=2)
        assert len(result["correct"]) == 1
        assert len(result["corrected"]) == 1
        assert len(result["keep"]) == 1

    def test_session_db_called_with_entry_text(
        self, mock_llm_client: MagicMock, mock_session_db: MagicMock
    ) -> None:
        mock_llm_client.verify_one.return_value = {"verdict": "correct", "note": ""}
        entries = ["测试条目内容"]
        remove_list = [{"index": 0, "原因": "过期"}]
        phase2_verify(entries, remove_list, "MEMORY.md", mock_llm_client, mock_session_db, max_workers=2)
        mock_session_db.search.assert_called_once_with("测试条目内容")

    def test_negative_index_skipped(
        self, mock_llm_client: MagicMock, mock_session_db: MagicMock
    ) -> None:
        mock_llm_client.verify_one.return_value = {"verdict": "correct", "note": ""}
        entries = ["条目A"]
        remove_list = [{"index": -1, "原因": "无效"}]
        result = phase2_verify(entries, remove_list, "MEMORY.md", mock_llm_client, mock_session_db, max_workers=2)
        assert len(result["correct"]) == 0

    def test_out_of_range_index_skipped(
        self, mock_llm_client: MagicMock, mock_session_db: MagicMock
    ) -> None:
        mock_llm_client.verify_one.return_value = {"verdict": "correct", "note": ""}
        entries = ["条目A"]
        remove_list = [{"index": 99, "原因": "越界"}]
        result = phase2_verify(entries, remove_list, "MEMORY.md", mock_llm_client, mock_session_db, max_workers=2)
        assert len(result["correct"]) == 0
