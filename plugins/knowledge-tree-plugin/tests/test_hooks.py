"""hooks 模块测试 — post_llm_call 入队，后台 worker 执行提取。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestPostLlmCall:
    """post_llm_call 测试。"""

    @patch("knowledge_tree_plugin.hooks._skip_non_user", return_value=False)
    @patch("knowledge_tree_plugin.hooks._skip_system_prompt", return_value=False)
    @patch("knowledge_tree_plugin.hooks._skip_post_llm_call_fn", return_value=None)
    @patch("knowledge_tree_plugin.hooks.should_skip_extraction", return_value="")
    @patch("knowledge_tree_plugin.hooks._ensure_worker_started")
    @patch("knowledge_tree_plugin.hooks._task_queue")
    def test_post_llm_call_enqueue(
        self,
        mock_queue: MagicMock,
        mock_ensure_worker: MagicMock,
        mock_cheap_gate: MagicMock,
        mock_skip_post: MagicMock,
        mock_skip_system: MagicMock,
        mock_skip_non_user: MagicMock,
    ) -> None:
        """正常路径只入队，不同步执行提取/放置。"""
        from knowledge_tree_plugin.hooks import post_llm_call

        post_llm_call("test_session", "什么是欧姆定律？", "欧姆定律是 V=IR，描述电压电流电阻关系。")

        mock_ensure_worker.assert_called_once()
        mock_queue.put_nowait.assert_called_once()
        task = mock_queue.put_nowait.call_args.args[0]
        assert task.session_id == "test_session"
        assert task.user_message == "什么是欧姆定律？"

    @patch("knowledge_tree_plugin.hooks._get_config")
    def test_extract_disabled(
        self,
        mock_get_config: MagicMock,
    ) -> None:
        """extract_enabled=False 时后台任务跳过。"""
        from knowledge_tree_plugin.hooks import ExtractTask, _process_extract_task

        cfg = MagicMock()
        cfg.extract_enabled = False
        mock_get_config.return_value = cfg

        with patch("knowledge_tree_plugin.hooks.extract_from_dialog") as mock_extract:
            _process_extract_task(ExtractTask("test", "q", "a"))
            mock_extract.assert_not_called()

    @patch("knowledge_tree_plugin.hooks._skip_non_user", return_value=False)
    @patch("knowledge_tree_plugin.hooks._skip_system_prompt", return_value=False)
    @patch("knowledge_tree_plugin.hooks._skip_post_llm_call_fn", return_value=None)
    @patch("knowledge_tree_plugin.hooks.should_skip_extraction", return_value="执行状态类响应")
    @patch("knowledge_tree_plugin.hooks._ensure_worker_started")
    def test_cheap_gate_skipped(
        self,
        mock_ensure_worker: MagicMock,
        mock_cheap_gate: MagicMock,
        mock_skip_post: MagicMock,
        mock_skip_system: MagicMock,
        mock_skip_non_user: MagicMock,
    ) -> None:
        """cheap gate 命中时不启动 worker、不入队。"""
        from knowledge_tree_plugin.hooks import post_llm_call

        with patch("knowledge_tree_plugin.hooks._task_queue") as mock_queue:
            post_llm_call("test", "部署", "部署完成，gateway active。")
            mock_ensure_worker.assert_not_called()
            mock_queue.put_nowait.assert_not_called()

    @patch("knowledge_tree_plugin.hooks._skip_non_user", return_value=False)
    @patch("knowledge_tree_plugin.hooks._skip_system_prompt", return_value=False)
    @patch("knowledge_tree_plugin.hooks._skip_post_llm_call_fn", return_value=None)
    @patch("knowledge_tree_plugin.hooks._get_config")
    def test_short_dialog_skipped(
        self,
        mock_get_config: MagicMock,
        mock_skip_post: MagicMock,
        mock_skip_system: MagicMock,
        mock_skip_non_user: MagicMock,
    ) -> None:
        """短对话在后台任务中跳过提取。"""
        from knowledge_tree_plugin.hooks import ExtractTask, _process_extract_task

        cfg = MagicMock()
        cfg.extract_enabled = True
        cfg.extract_min_dialog_length = 100
        mock_get_config.return_value = cfg

        with patch("knowledge_tree_plugin.hooks.extract_from_dialog") as mock_extract:
            _process_extract_task(ExtractTask("test", "q", "a"))
            mock_extract.assert_not_called()

    @patch("knowledge_tree_plugin.hooks._extract_executor")
    def test_extract_with_timeout_passes_retry_config(self, mock_executor: MagicMock) -> None:
        """extract_llm_retries / timeout 配置必须传入 extract_from_dialog。"""
        from knowledge_tree_plugin.hooks import ExtractTask, _extract_with_timeout

        future = MagicMock()
        future.result.return_value = ["知识点"]
        mock_executor.submit.return_value = future

        cfg = MagicMock()
        cfg.min_knowledge_point_length = 10
        cfg.extract_max_input_length = 4000
        cfg.llm_api_url = "http://llm.test/v1/chat/completions"
        cfg.llm_api_key = "x"
        cfg.llm_model = "test-model"
        cfg.extract_llm_retries = 2
        cfg.extract_llm_timeout_seconds = 40

        result = _extract_with_timeout(ExtractTask("s", "u", "a"), cfg)

        assert result == ["知识点"]
        call_kwargs = mock_executor.submit.call_args.kwargs
        assert call_kwargs["llm_retries"] == 2
        assert call_kwargs["llm_timeout_seconds"] == 40
        future.result.assert_called_once_with(timeout=88)
