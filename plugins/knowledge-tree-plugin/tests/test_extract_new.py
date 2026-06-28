"""extract_new 模块测试 — 对话知识点提取。"""

from __future__ import annotations

from unittest.mock import patch


class TestExtractFromDialog:
    """extract_from_dialog 测试。"""

    @patch("knowledge_tree_plugin.extract_new._extract_one_chunk")
    def test_extract_success(self, mock_extract) -> None:
        """正常提取流程。"""
        from knowledge_tree_plugin.extract_new import extract_from_dialog

        mock_extract.return_value = ["知识点1", "知识点2", "知识点1"]

        result = extract_from_dialog(
            user_message="什么是欧姆定律？",
            llm_response="欧姆定律是 V=IR...",
            api_url="http://test:4142",
            api_key="",
            model="test-model",
        )

        assert result == ["知识点1", "知识点2"]
        mock_extract.assert_called_once()

    @patch("knowledge_tree_plugin.extract_new._extract_one_chunk")
    def test_extract_empty_result(self, mock_extract) -> None:
        """LLM 提取返回空列表。"""
        from knowledge_tree_plugin.extract_new import extract_from_dialog

        mock_extract.return_value = []

        result = extract_from_dialog(
            user_message="hi",
            llm_response="hello",
            api_url="http://test:4142",
            api_key="",
            model="test-model",
        )
        assert result == []

    @patch("knowledge_tree_plugin.extract_new._extract_one_chunk")
    def test_extract_exception(self, mock_extract) -> None:
        """LLM 异常时返回空列表。"""
        from knowledge_tree_plugin.extract_new import extract_from_dialog

        mock_extract.side_effect = ConnectionError("API timeout")

        result = extract_from_dialog(
            user_message="test",
            llm_response="test",
            api_url="http://test:4142",
            api_key="",
            model="test-model",
        )
        assert result == []

    def test_extract_truncates_long_dialog(self) -> None:
        """长对话自动按动态预算构造输入。"""
        from knowledge_tree_plugin.extract_new import extract_from_dialog

        long_msg = "A" * 5000
        long_resp = "B" * 5000

        with patch(
            "knowledge_tree_plugin.extract_new._extract_one_chunk"
        ) as mock_extract:
            mock_extract.return_value = []
            extract_from_dialog(
                user_message=long_msg,
                llm_response=long_resp,
                max_input_length=100,
                api_url="http://test:4142",
                api_key="",
                model="test-model",
            )
            text = mock_extract.call_args.args[0]
            assert len(text) <= 700

    def test_large_dialog_uses_parallel_chunks_and_dedups(self) -> None:
        """大输入走分块并行，结果做去重。"""
        from knowledge_tree_plugin.extract_new import extract_from_dialog

        user_msg = "请总结知识树插件性能优化。"
        long_resp = "\n\n".join(
            f"## 段落{i}\n知识树插件性能优化包括复用 embedding 和批量更新 K 向量。"
            for i in range(120)
        )

        def fake_extract(chunk: str, title: str, **kwargs: object) -> list[str]:
            return [
                "知识树插件应复用 embedding 以避免重复 API 调用。",
                "知识树插件应复用embedding以避免重复API调用。",
                f"{title} 包含动态分块提取。",
            ]

        with patch(
            "knowledge_tree_plugin.extract_new._extract_one_chunk",
            side_effect=fake_extract,
        ) as mock_extract:
            result = extract_from_dialog(
                user_message=user_msg,
                llm_response=long_resp,
                max_input_length=4000,
                api_url="http://test:4142",
                api_key="",
                model="test-model",
            )

        assert mock_extract.call_count > 1
        assert "知识树插件应复用 embedding 以避免重复 API 调用。" in result
        assert "知识树插件应复用embedding以避免重复API调用。" not in result
        assert len(result) <= 10
