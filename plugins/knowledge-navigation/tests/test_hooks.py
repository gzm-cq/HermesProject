"""Hook 逻辑测试。"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from knowledge_navigation import register
from knowledge_navigation.config import CONFIG, KnowledgeNavigationConfig
from knowledge_navigation.core import hooks as nav_hooks
from knowledge_navigation.core import circuit_breaker as cb
from knowledge_navigation.core.hooks import pre_llm_call

# 被测试的模块级符号
from knowledge_navigation.core.hooks import _CJK_STOP_CHARS, _extract_keywords


@pytest.fixture(autouse=True)
def _reset_circuit_breaker() -> None:
    """每个测试前重置熔断器状态、飞书 token 缓存和 task_tracker，防止跨测试泄漏。"""
    cb._circuit_failures = 0
    cb._circuit_open_until = 0.0
    cb._circuit_failure_types.clear()
    cb._LAST_NOTIFICATION_TIME = 0.0
    cb._FEISHU_TOKEN = ""
    cb._FEISHU_TOKEN_EXPIRES_AT = 0.0
    # 重置 _task_tracker 防止跨测试轮次累积
    nav_hooks._task_tracker._rounds.clear()
    nav_hooks._hit_counter._counts.clear()
    nav_hooks._compaction._rounds.clear()
    # 重置 turn-to-turn 去重缓存
    nav_hooks._injected_ids.clear()


@pytest.fixture(autouse=True)
def _mock_kt_disabled() -> None:
    """默认禁用知识树 recall（各测试不需要它）。"""
    with patch.object(nav_hooks, "HAS_KNOWLEDGE_TREE", False):
        yield


_TEST_TRACE = {
    "reranked": [
        {"node_id": "node1", "rerank_score": 0.9},
        {"node_id": "node2", "rerank_score": 0.8},
        {"node_id": "node3", "rerank_score": 0.7},
        {"node_id": "node4", "rerank_score": 0.6},
        {"node_id": "node5", "rerank_score": 0.5},
    ]
}


class TestRegister:
    """测试插件注册函数。"""

    def test_register_calls_ctx_register_hook(self, mock_ctx: MagicMock) -> None:
        """测试 register 正确注册 pre_llm_call 钩子。"""
        register(mock_ctx)
        mock_ctx.register_hook.assert_called_once_with("pre_llm_call", pre_llm_call)


class TestPreLlmCall:
    """测试 pre_llm_call Hook 逻辑。"""

    def _mock_recall(self, results: list | None = None, trace: dict | None = None) -> dict | None:
        """构建 recall 返回值。"""
        if results is None:
            return None
        return {"results": results, "trace": trace or _TEST_TRACE}


    def test_hindsight_timeout_does_not_wait_for_executor_shutdown(self) -> None:
        """Future 超时后应取消，不等待 executor shutdown（S-4 共享线程池）。"""
        from concurrent.futures import TimeoutError as FuturesTimeout

        class FakeFuture:
            cancelled = False

            def result(self, timeout=None):
                raise FuturesTimeout()

            def cancel(self):
                self.cancelled = True

            def done(self):
                return False

        fake_future = FakeFuture()

        def fake_submit(fn, *args, **kwargs):
            return fake_future

        with patch.object(nav_hooks._recall_executor, "submit", fake_submit), \
             patch.object(CONFIG, "timeout_seconds", 0.01), \
             patch.object(nav_hooks, "_router_route", return_value={"h": True, "kt": True, "s": True}):
            result = pre_llm_call("session-123", "12345678901", platform="cli")
            assert result is None

        assert fake_future.cancelled is True

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_internal_maintenance_prompt_skips_recall(self, mock_recall: MagicMock) -> None:
        """内部维护类 prompt 不应触发 Hindsight recall。"""
        result = pre_llm_call(
            "session-123",
            "Review the conversation above and update the skill if needed.",
            platform="cli",
        )

        assert result is None
        mock_recall.assert_not_called()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_long_operational_prompt_skips_recall(self, mock_recall: MagicMock) -> None:
        """操作型短指令后附带长上下文时也应跳过 recall。"""
        result = pre_llm_call(
            "session-123",
            "修复\n<memory-context>" + "历史上下文" * 80 + "</memory-context>",
            platform="cli",
        )

        assert result is None
        mock_recall.assert_not_called()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_recall_success_returns_context(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """测试成功 recall 返回格式化的上下文。"""
        mock_recall.return_value = self._mock_recall(
            results=[
                {"id": "node1", "text": "Memory one"},
                {"id": "node2", "text": "Memory two"},
            ],
        )

        result = pre_llm_call("session-123", "12345678901", platform="cli")

        assert result is not None
        assert '<user_query>' in result
        assert '<recalled_memory source="hindsight"' in result
        assert '<memory source="hindsight" node_id="node1">Memory one</memory>' in result
        assert '<memory source="hindsight" node_id="node2">Memory two</memory>' in result
        assert '<system_state>' in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_recall_error_returns_none(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """测试 recall 异常时返回 None。"""
        mock_recall.side_effect = RuntimeError("API down")

        result = pre_llm_call("session-123", "12345678901", platform="cli")

        assert result is None

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_recall_empty_result_returns_none(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """测试空结果返回 None。"""
        mock_recall.return_value = None

        result = pre_llm_call("session-123", "12345678901", platform="cli")

        assert result is None

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_recall_empty_results_list_returns_none(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """测试 results 为空列表时返回 None。"""
        mock_recall.return_value = self._mock_recall(results=[])

        with patch.object(nav_hooks, "_do_skill_match", return_value=""):
            result = pre_llm_call("session-123", "12345678901", platform="cli")

        assert result is None

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_low_score_results_filtered_out(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """测试低分结果被过滤后返回 None。"""
        mock_recall.return_value = self._mock_recall(
            results=[{"id": "node1", "text": "Low score memory"}],
            trace={"reranked": [{"node_id": "node1", "rerank_score": 0.1}]},
        )

        result = pre_llm_call("session-123", "12345678901", platform="cli")

        assert result is None

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_results_limited_by_max_results(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """测试结果数量被 max_results 限制。"""
        mock_recall.return_value = self._mock_recall(
            results=[
                {"id": f"node{i}", "text": f"Memory {i}"}
                for i in range(10)
            ],
            trace={
                "reranked": [
                    {"node_id": f"node{i}", "rerank_score": 0.9 - i * 0.05}
                    for i in range(10)
                ]
            },
        )

        result = pre_llm_call("session-123", "12345678901", platform="cli")

        assert result is not None
        # 新格式：user_query (3) + recalled_memory (7) + system_state (3) ≈ 13 行
        assert '<recalled_memory source="hindsight" count="3"' in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_exclude_marked_in_pipeline(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """测试排除标记在 pipeline 中生效。"""
        mock_recall.return_value = self._mock_recall(
            results=[
                {"id": "node1", "text": "Good memory"},
                {"id": "node2", "text": "Bad [标记: 错误] memory"},
                {"id": "node3", "text": "[标记: 作废] Invalid"},
            ],
            trace={
                "reranked": [
                    {"node_id": "node1", "rerank_score": 0.9},
                    {"node_id": "node2", "rerank_score": 0.85},
                    {"node_id": "node3", "rerank_score": 0.8},
                ]
            },
        )

        result = pre_llm_call("session-123", "12345678901", platform="cli")

        assert result is not None
        lines = result.split("\n")
        # 3 lines for user_query, 5 for recalled_memory (1+1+1+1+1), 3 for system_state = 11
        # The test message "12345678901" is 11 chars, which is > 10, so intent=task
        assert '<user_query>\n12345678901\n</user_query>' in result
        assert '<recalled_memory source="hindsight" count="1"' in result
        assert '<memory source="hindsight" node_id="node1">Good memory</memory>' in result
        assert '[标记:' not in result  # 排除的标记不应出现

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_eval_query_id_logged(
        self,
        mock_recall: MagicMock,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试 eval_query_id 在日志中记录。"""
        # 清除模块级缓存以重新加载
        nav_hooks._eval_queries = None

        # 创建临时评测查询文件
        eval_file = tmp_path / "eval_queries.json"
        eval_file.write_text(
            json.dumps([{"query_id": "eval-001", "query": "test eval query about memory", "dimension": "semantic"}])
        )

        with patch.object(CONFIG, "eval_queries_path", str(eval_file)):
            mock_recall.return_value = self._mock_recall(
                results=[{"id": "node1", "text": "Memory"}],
            )

            caplog.set_level(logging.INFO)
            pre_llm_call("session-123", "test eval query about memory", platform="cli")

        assert any(
            getattr(rec, "eval_query_id", None) == "eval-001"
            for rec in caplog.records
        ), "eval_query_id 应出现在日志中"

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_eval_query_not_matched_not_logged(
        self,
        mock_recall: MagicMock,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试不匹配的查询不记录 eval_query_id。"""
        # 清除模块级缓存以重新加载
        nav_hooks._eval_queries = None

        eval_file = tmp_path / "eval_queries.json"
        eval_file.write_text(
            json.dumps([{"query_id": "eval-001", "query": "specific error about system"}])
        )

        with patch.object(CONFIG, "eval_queries_path", str(eval_file)):
            mock_recall.return_value = self._mock_recall(
                results=[{"id": "node1", "text": "Memory"}],
            )

            caplog.set_level(logging.INFO)
            pre_llm_call("session-123", "unrelated topic here", platform="cli")

        assert not any(
            hasattr(rec, "eval_query_id")
            for rec in caplog.records
        ), "不匹配时不应出现 eval_query_id"


class TestCircuitBreaker:
    """测试熔断器（Circuit Breaker）机制。"""

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_opens_after_three_consecutive_failures(
        self,
        mock_recall: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """连续 3 次失败后熔断器打开。"""
        mock_recall.side_effect = RuntimeError("API down")

        threshold = CONFIG.circuit_breaker_threshold

        with patch.object(nav_hooks, "_do_skill_match", return_value=""):
            # 前 threshold-1 次失败计数增加但熔断器不打开
            for i in range(threshold - 1):
                result = pre_llm_call("session-123", "xyz xyz xyz", platform="cli")
                assert result is None
                assert cb._circuit_failures == i + 1
                assert cb._circuit_open_until == 0.0

            # 第 threshold 次失败触发熔断
            caplog.set_level(logging.WARNING)
            result = pre_llm_call("session-123", "xyz xyz xyz", platform="cli")
            assert result is None
            assert cb._circuit_failures == threshold
            assert cb._circuit_open_until > 0.0
            assert any("熔断器开启" in rec.getMessage() for rec in caplog.records)

            # 下一次调用因熔断直接返回 None，不调用 recall
            result = pre_llm_call("session-123", "xyz xyz xyz", platform="cli")
            assert result is None
            assert mock_recall.call_count == threshold  # 未增加

    @patch("knowledge_navigation.core.hooks.time.time")
    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_auto_recovers_after_cooldown(
        self,
        mock_recall: MagicMock,
        mock_time: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """冷却期后自动恢复。"""
        base_time = 1893456000.0  # 2030-01-01，避免 urllib3 SystemTimeWarning
        mock_time.return_value = base_time

        mock_recall.side_effect = RuntimeError("API down")

        threshold = CONFIG.circuit_breaker_threshold
        cooldown = CONFIG.circuit_breaker_cooldown

        with patch.object(nav_hooks, "_do_skill_match", return_value=""):
            # threshold 次异常触发熔断
            for _ in range(threshold):
                pre_llm_call("session-123", "xyz xyz xyz", platform="cli")
            assert cb._circuit_open_until == base_time + cooldown
            assert mock_recall.call_count == threshold

            # 冷却期内 — 熔断开启，recall 不被调用
            result = pre_llm_call("session-123", "xyz xyz xyz", platform="cli")
            assert result is None
            assert mock_recall.call_count == threshold  # 未增加

            # 时间跳到冷却期后
            mock_time.return_value = base_time + cooldown + 1.0
            mock_recall.side_effect = None
            mock_recall.return_value = {
                "results": [{"id": "node1", "text": "Memory"}],
                "trace": {"reranked": [{"node_id": "node1", "rerank_score": 0.9}]},
            }

            result = pre_llm_call("session-123", "xyz xyz xyz", platform="cli")
            assert result is not None
            assert cb._circuit_failures == 0  # 已重置
            assert cb._circuit_open_until == 0.0
            assert mock_recall.call_count == threshold + 1  # 恢复调用

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_success_resets_failure_counter(
        self,
        mock_recall: MagicMock,
    ) -> None:
        """成功调用重置失败计数。"""
        with patch.object(nav_hooks, "_do_skill_match", return_value=""):
            # 2 次失败
            mock_recall.side_effect = [RuntimeError("down"), RuntimeError("down")]
            for _ in range(2):
                assert pre_llm_call("session-123", "xyz xyz xyz", platform="cli") is None
            assert cb._circuit_failures == 2

            # 成功调用
            mock_recall.side_effect = None
            mock_recall.return_value = {
                "results": [{"id": "node1", "text": "Memory"}],
                "trace": {"reranked": [{"node_id": "node1", "rerank_score": 0.9}]},
            }
            assert pre_llm_call("session-123", "xyz xyz xyz", platform="cli") is not None
            assert cb._circuit_failures == 0  # 已重置

            # 再失败 1 次，计数应从 0 重新开始
            mock_recall.side_effect = RuntimeError("down")
            assert pre_llm_call("session-123", "xyz xyz xyz", platform="cli") is None
            assert cb._circuit_failures == 1

    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks.time.time")
    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_returns_none_during_circuit_open(
        self,
        mock_recall: MagicMock,
        mock_time: MagicMock,
        mock_kt: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """熔断期间 Hindsight 被跳过，KT 仍尝试；两侧均空时返回 None。"""
        base_time = 1893456000.0  # 2030-01-01，避免 urllib3 SystemTimeWarning
        mock_time.return_value = base_time

        # 直接设置熔断器为打开状态
        cb._circuit_failures = CONFIG.circuit_breaker_threshold
        cb._circuit_open_until = base_time + CONFIG.circuit_breaker_cooldown

        with patch.object(nav_hooks, "_do_skill_match", return_value=""):
            caplog.set_level(logging.INFO)
            result = pre_llm_call("session-123", "xyz xyz xyz", platform="cli")

            assert result is None
            mock_recall.assert_not_called()  # Hindsight recall 未被调用
            assert any("熔断器跳过 Hindsight recall" in rec.getMessage() for rec in caplog.records)
            # 熔断期间不应追加失败计数（防止熔断死循环）
            assert cb._circuit_failures == CONFIG.circuit_breaker_threshold

    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[{"id": 1, "text": "kt result"}])
    @patch("knowledge_navigation.core.hooks.time.time")
    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_circuit_open_with_kt_results_no_failure_recorded(
        self,
        mock_recall: MagicMock,
        mock_time: MagicMock,
        mock_kt: MagicMock,
    ) -> None:
        """熔断期间 KT 有结果时，不追加 service_error 失败计数。"""
        base_time = 1893456000.0
        mock_time.return_value = base_time

        cb._circuit_failures = CONFIG.circuit_breaker_threshold
        cb._circuit_open_until = base_time + CONFIG.circuit_breaker_cooldown

        result = pre_llm_call("session-123", "test query", platform="cli")

        mock_recall.assert_not_called()  # HS 未被调用
        # 熔断期间不应追加失败计数
        assert cb._circuit_failures == CONFIG.circuit_breaker_threshold


class TestKeywordExtraction:
    """测试 _extract_keywords 关键词提取函数。"""

    def test_extract_english_words(self) -> None:
        """测试英文词提取。"""
        kw = _extract_keywords("LiteLLM config API test")
        assert "litellm" in kw
        assert "config" in kw
        assert "api" in kw
        assert "test" in kw

    def test_skip_single_letter_english(self) -> None:
        """测试跳过单字母英文词。"""
        kw = _extract_keywords("a b c PG config")
        assert "pg" in kw
        assert "config" in kw
        assert "a" not in kw
        assert "b" not in kw
        assert "c" not in kw

    def test_extract_cjk_bigrams(self) -> None:
        """测试 CJK 二字组提取。"""
        kw = _extract_keywords("配置错误排查")
        assert "配置" in kw
        assert "置错" in kw
        assert "错误" in kw
        assert "误排" in kw
        assert "排查" in kw

    def test_filter_cjk_stop_chars(self) -> None:
        """测试过滤停用字开头的二字组。"""
        kw = _extract_keywords("我的问题")
        assert "问题" in kw
        assert "我的" in kw
        assert "的问" not in kw  # "的" 是停用字

    def test_extract_mixed_text(self) -> None:
        """测试中英文混合提取。"""
        kw = _extract_keywords("PG 连接错误怎么排查")
        assert "pg" in kw
        assert "连接" in kw
        assert "接错" in kw
        assert "错误" in kw
        assert "排查" in kw

    def test_empty_text(self) -> None:
        """测试空文本。"""
        assert _extract_keywords("") == set()

    def test_only_stop_chars(self) -> None:
        """测试只有停用字的文本。"""
        kw = _extract_keywords("的了")
        assert len(kw) == 0


class TestFlexibleEvalMatch:
    """测试灵活匹配逻辑。"""

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_keyword_match_records_candidate_not_counted(
        self,
        mock_recall: MagicMock,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """关键词重叠只记录候选，不进入 recall@k 计数。"""
        nav_hooks._eval_queries = None

        eval_file = tmp_path / "eval_queries.json"
        eval_file.write_text(
            json.dumps([{"query_id": "semantic_01", "query": "LiteLLM 配置处理", "dimension": "semantic"}])
        )

        with patch.object(CONFIG, "eval_queries_path", str(eval_file)):
            mock_recall.return_value = {
                "results": [{"id": "node1", "text": "Memory"}],
                "trace": {"reranked": [{"node_id": "node1", "rerank_score": 0.9}]},
            }

            caplog.set_level(logging.INFO)
            pre_llm_call("session-123", "LiteLLM 配置出问题了", platform="cli")

        success_records = [rec for rec in caplog.records if getattr(rec, "event", None) == "recall_success"]
        assert any(getattr(rec, "eval_candidate_id", None) == "semantic_01" for rec in success_records)
        assert all(not hasattr(rec, "eval_query_id") for rec in success_records)
        assert any(getattr(rec, "eval_counted", None) is False for rec in success_records)

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_keyword_match_low_overlap_rejected(
        self,
        mock_recall: MagicMock,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试低关键词重叠不匹配。"""
        nav_hooks._eval_queries = None

        eval_file = tmp_path / "eval_queries.json"
        eval_file.write_text(
            json.dumps([{"query_id": "semantic_01", "query": "systemd 服务的管理命令"}])
        )

        with patch.object(CONFIG, "eval_queries_path", str(eval_file)):
            mock_recall.return_value = {
                "results": [{"id": "node1", "text": "Memory"}],
                "trace": {"reranked": [{"node_id": "node1", "rerank_score": 0.9}]},
            }

            caplog.set_level(logging.INFO)
            pre_llm_call("session-123", "LiteLLM 配置出问题了", platform="cli")

        # 不匹配：关键词零重叠
        assert not any(
            hasattr(rec, "eval_query_id")
            for rec in caplog.records
        )

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_exact_match_still_works(
        self,
        mock_recall: MagicMock,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试精确匹配仍然优先。"""
        nav_hooks._eval_queries = None

        eval_file = tmp_path / "eval_queries.json"
        eval_file.write_text(
            json.dumps([{"query_id": "exact-001", "query": "exact match query about memory", "dimension": "semantic"}])
        )

        with patch.object(CONFIG, "eval_queries_path", str(eval_file)):
            mock_recall.return_value = {
                "results": [{"id": "node1", "text": "Memory"}],
                "trace": {"reranked": [{"node_id": "node1", "rerank_score": 0.9}]},
            }

            caplog.set_level(logging.INFO)
            pre_llm_call("session-123", "exact match query about memory", platform="cli")

        # 精确匹配应返回 eval_query_id，并进入计数
        assert any(
            getattr(rec, "eval_query_id", None) == "exact-001" and getattr(rec, "eval_counted", None) is True
            for rec in caplog.records
        )

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_explicit_eval_id_counts(
        self,
        mock_recall: MagicMock,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """[EVAL:id] 显式触发应进入 recall@k 计数。"""
        nav_hooks._eval_queries = None

        eval_file = tmp_path / "eval_queries.json"
        eval_file.write_text(
            json.dumps([{"query_id": "gen_001", "query": "任意评测问题", "dimension": "semantic", "expected_ids": ["11111111-1111-4111-8111-111111111111"]}])
        )

        with patch.object(CONFIG, "eval_queries_path", str(eval_file)):
            mock_recall.return_value = {
                "results": [{"id": "11111111-1111-4111-8111-111111111111", "text": "Memory"}],
                "trace": {"reranked": [{"node_id": "11111111-1111-4111-8111-111111111111", "rerank_score": 0.9}]},
            }

            caplog.set_level(logging.INFO)
            pre_llm_call("session-123", "[EVAL:gen_001] 今天随便问", platform="cli")

        assert any(
            getattr(rec, "eval_query_id", None) == "gen_001"
            and getattr(rec, "eval_counted", None) is True
            and getattr(rec, "eval_recall_hit", None) == 1
            for rec in caplog.records
        )

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_exact_match_beats_keyword(
        self,
        mock_recall: MagicMock,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试精确匹配优先于关键词匹配。"""
        nav_hooks._eval_queries = None

        # 两条 query，关键词重叠都能匹配
        # 但有一条是精确匹配
        eval_file = tmp_path / "eval_queries.json"
        eval_file.write_text(json.dumps([
            {"query_id": "exact-001", "query": "LiteLLM 配置出问题了", "dimension": "semantic"},
            {"query_id": "keyword-001", "query": "LiteLLM 配置相关的问题怎么处理", "dimension": "semantic"},
        ]))

        with patch.object(CONFIG, "eval_queries_path", str(eval_file)):
            mock_recall.return_value = {
                "results": [{"id": "node1", "text": "Memory"}],
                "trace": {"reranked": [{"node_id": "node1", "rerank_score": 0.9}]},
            }

            caplog.set_level(logging.INFO)
            pre_llm_call("session-123", "LiteLLM 配置出问题了", platform="cli")

        # 应匹配精确的 exact-001，不是关键词的 keyword-001
        got = None
        for rec in caplog.records:
            if getattr(rec, "eval_query_id", None):
                got = getattr(rec, "eval_query_id")
                break
        assert got == "exact-001", f"期望 exact-001，实际 {got}"


class TestRouterMask:
    """测试 LLM Router mask 行为。"""

    LONG_QUERY = "测试消息测试消息测试消息测试消息测试消息"

    def _patch_router(self, mask: dict[str, bool]) -> MagicMock:
        """patch hooks 模块中的 _router_route 返回指定 mask。"""
        return patch.object(nav_hooks, "_router_route", return_value=mask)

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_router_all_off_returns_none(self, mock_hs: MagicMock) -> None:
        """Router 全关闭 → return None，recall 不被调用。"""
        with self._patch_router({"h": False, "kt": False, "s": False}):
            result = pre_llm_call("sess1", self.LONG_QUERY, platform="cli")
        assert result is None
        mock_hs.assert_not_called()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_router_only_h_runs_hindsight(self, mock_hs: MagicMock) -> None:
        """Router {h:1, kt:0, s:0} → 只跑 Hindsight。"""
        mock_hs.return_value = {
            "results": [{"id": "n1", "text": "test memory", "score": 0.9}],
            "trace": {"reranked": [{"node_id": "n1", "rerank_score": 0.9}]},
        }
        with self._patch_router({"h": True, "kt": False, "s": False}):
            with patch.object(nav_hooks, "_do_skill_match", return_value=""):
                result = pre_llm_call("sess1", self.LONG_QUERY, platform="cli")
        assert result is not None
        assert "recalled_memory" in result
        assert "<knowledge" not in result
        mock_hs.assert_called_once()

    def test_router_only_kt_runs_knowledge_tree(self) -> None:
        """Router {h:0, kt:1, s:0} → 只跑知识树。"""
        with patch.object(nav_hooks, "HAS_KNOWLEDGE_TREE", True):
            fake_kt = [{"id": 1, "text": "kt node", "score": 0.8}]
            with patch.object(nav_hooks, "_do_kt_recall", return_value=fake_kt):
                with patch.object(nav_hooks, "_multi_hop_recall", return_value=[], create=True):
                    with patch.object(nav_hooks, "_do_skill_match", return_value=""):
                        with self._patch_router({"h": False, "kt": True, "s": False}):
                            result = pre_llm_call("sess2", self.LONG_QUERY, platform="cli")
        assert result is not None
        assert "<knowledge" in result
        assert 'source="knowledge_tree"' in result

    def test_router_only_skill_runs_skill(self) -> None:
        """Router {h:0, kt:0, s:1} → 只跑 skill（与旧 generic 行为等价）。"""
        with patch.object(nav_hooks, "_do_skill_match", return_value="<auto_loaded_skills>test</auto_loaded_skills>") as mock_skill:
            with self._patch_router({"h": False, "kt": False, "s": True}):
                result = pre_llm_call("sess3", self.LONG_QUERY, platform="cli")
        assert result is not None
        assert "auto_loaded_skills" in result
        mock_skill.assert_called_once()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_router_full_on_runs_all(self, mock_hs: MagicMock) -> None:
        """Router 全开 → 三路都跑（与原行为一致）。"""
        mock_hs.return_value = {
            "results": [{"id": "n1", "text": "test memory", "score": 0.9}],
            "trace": {"reranked": [{"node_id": "n1", "rerank_score": 0.9}]},
        }
        with patch.object(nav_hooks, "HAS_KNOWLEDGE_TREE", True):
            with patch.object(nav_hooks, "_do_kt_recall", return_value=[]):
                with patch.object(nav_hooks, "_multi_hop_recall", return_value=[], create=True):
                    with patch.object(nav_hooks, "_do_skill_match", return_value=""):
                        with self._patch_router({"h": True, "kt": True, "s": True}):
                            result = pre_llm_call("sess4", self.LONG_QUERY, platform="cli")
        assert result is not None
        assert "recalled_memory" in result
        mock_hs.assert_called_once()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    def test_router_failure_fallback_all_on(self, mock_hs: MagicMock) -> None:
        """Router 调用异常 → fallback 全开，三路都跑。"""
        mock_hs.return_value = {
            "results": [{"id": "n1", "text": "test memory", "score": 0.9}],
            "trace": {"reranked": [{"node_id": "n1", "rerank_score": 0.9}]},
        }
        with patch.object(nav_hooks, "_router_route", side_effect=RuntimeError("API down")):
            with patch.object(nav_hooks, "_do_skill_match", return_value=""):
                result = pre_llm_call("sess5", self.LONG_QUERY, platform="cli")
        assert result is not None
        mock_hs.assert_called_once()

    def test_turn_gate_skip_does_not_call_router(self) -> None:
        """turn_gate 跳过后 → router 不应被调用。"""
        with patch.object(nav_hooks, "_router_route") as mock_route:
            pre_llm_call("sess6", "好的", platform="cli")
        mock_route.assert_not_called()