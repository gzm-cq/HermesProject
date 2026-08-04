"""MemoryFileStore 单元测试。"""

import json
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from memory_cleanup.adapters.memory_store import MemoryFileStore
from memory_cleanup.config import AppConfig


class TestLoadFile:
    """load_file() 测试。"""

    def test_missing_path_returns_empty(self, app_config: AppConfig) -> None:
        store = MemoryFileStore(app_config)
        assert store.load_file("/nonexistent/path.md") == []

    def test_reads_and_splits_by_delimiter(self, app_config: AppConfig, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("条目一\n§\n条目二\n§\n条目三", encoding="utf-8")
        store = MemoryFileStore(app_config)
        result = store.load_file(str(p))
        assert result == ["条目一", "条目二", "条目三"]

    def test_dedup_removes_duplicates(self, app_config: AppConfig, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("条目一\n§\n条目一\n§\n条目二", encoding="utf-8")
        store = MemoryFileStore(app_config)
        result = store.load_file(str(p))
        assert result == ["条目一", "条目二"]

    def test_dedup_ignores_whitespace_differences(self, app_config: AppConfig, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("hello world\n§\nhello   world\n§\n条目二", encoding="utf-8")
        store = MemoryFileStore(app_config)
        result = store.load_file(str(p))
        # "hello world" and "hello   world" are same after whitespace normalization
        assert len(result) == 2
        assert result[-1] == "条目二"

    def test_empty_file_returns_empty(self, app_config: AppConfig, tmp_path: Path) -> None:
        p = tmp_path / "empty.md"
        p.write_text("", encoding="utf-8")
        store = MemoryFileStore(app_config)
        result = store.load_file(str(p))
        assert result == []

    def test_delimiter_only_file_returns_empty(self, app_config: AppConfig, tmp_path: Path) -> None:
        """仅含分隔符的文件应返回空列表，而非含空字符串的列表。"""
        p = tmp_path / "delimiter_only.md"
        p.write_text("\n§\n", encoding="utf-8")
        store = MemoryFileStore(app_config)
        result = store.load_file(str(p))
        assert result == []


class TestRetain:
    """_retain() 测试。"""

    def test_success_returns_true(self, app_config: AppConfig) -> None:
        store = MemoryFileStore(app_config)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.object(urllib.request, "urlopen", return_value=mock_resp):
            result = store._retain("test content")
            assert result is True

    def test_retry_then_fail(self, app_config: AppConfig) -> None:
        store = MemoryFileStore(app_config)
        mock_resp = MagicMock()
        mock_resp.__enter__.side_effect = Exception("connection error")
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(urllib.request, "urlopen", side_effect=Exception("timeout")) as mock_urlopen:
            result = store._retain("test content")
            assert result is False
            assert mock_urlopen.call_count == 2

    def test_retry_succeeds_on_second_attempt(self, app_config: AppConfig) -> None:
        store = MemoryFileStore(app_config)
        call_count = 0

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("first fail")
            resp = MagicMock()
            resp.status = 200
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(urllib.request, "urlopen", side_effect=_side_effect):
            result = store._retain("test content")
            assert result is True

    def test_retain_with_tags(self, app_config: AppConfig) -> None:
        """带 tags 的 retain 请求体应包含 tags 字段。"""
        store = MemoryFileStore(app_config)
        captured_request = {}

        def _capture_request(req: urllib.request.Request, **kwargs: object) -> MagicMock:
            captured_request["data"] = json.loads(req.data.decode())
            resp = MagicMock()
            resp.status = 200
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(urllib.request, "urlopen", side_effect=_capture_request):
            result = store._retain("test content", tags=["tag1", "tag2"])
            assert result is True
            assert "items" in captured_request["data"]
            assert len(captured_request["data"]["items"]) == 1
            assert captured_request["data"]["items"][0]["content"] == "test content"
            assert captured_request["data"]["items"][0]["tags"] == ["tag1", "tag2"]

    def test_retain_without_tags_no_tags_field(self, app_config: AppConfig) -> None:
        """不带 tags 的 retain 请求体不应包含 tags 字段。"""
        store = MemoryFileStore(app_config)
        captured_request = {}

        def _capture_request(req: urllib.request.Request, **kwargs: object) -> MagicMock:
            captured_request["data"] = json.loads(req.data.decode())
            resp = MagicMock()
            resp.status = 200
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(urllib.request, "urlopen", side_effect=_capture_request):
            result = store._retain("test content")
            assert result is True
            assert "tags" not in captured_request["data"]["items"][0]

    def test_retain_with_empty_tags_no_tags_field(self, app_config: AppConfig) -> None:
        """空 tags 列表不应包含 tags 字段。"""
        store = MemoryFileStore(app_config)
        captured_request = {}

        def _capture_request(req: urllib.request.Request, **kwargs: object) -> MagicMock:
            captured_request["data"] = json.loads(req.data.decode())
            resp = MagicMock()
            resp.status = 200
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(urllib.request, "urlopen", side_effect=_capture_request):
            result = store._retain("test content", tags=[])
            assert result is True
            assert "tags" not in captured_request["data"]["items"][0]


class TestExecuteCleanup:
    """execute_cleanup() 测试。"""

    @pytest.fixture
    def mock_memory_store(self) -> MagicMock:
        """Mock tools.memory_tool.MemoryStore 类。"""
        ms = MagicMock()
        ms.remove.return_value = {"success": True}
        ms.add.return_value = {"success": True}
        return ms

    @pytest.fixture
    def store(self, app_config: AppConfig) -> MemoryFileStore:
        return MemoryFileStore(app_config)

    def _execute(
        self,
        store: MemoryFileStore,
        entries: list[str],
        merge_list: list | None = None,
        compress_list: list | None = None,
        remove_list: list | None = None,
        v2_correct: list | None = None,
        v2_corrected: list | None = None,
        v2_keep: list | None = None,
        mock_ms: MagicMock | None = None,
    ) -> dict:
        """辅助方法：在 mock 环境中执行 execute_cleanup。"""
        if mock_ms is None:
            mock_ms = MagicMock()
            mock_ms.remove.return_value = {"success": True}
            mock_ms.add.return_value = {"success": True}

        # Mock the delayed import inside execute_cleanup
        mock_module = MagicMock()
        mock_module.MemoryStore = lambda *a, **kw: mock_ms  # type: ignore[attr-defined]
        mock_spec = MagicMock()
        mock_spec.loader.exec_module.return_value = None
        with patch("importlib.util.spec_from_file_location", return_value=mock_spec), \
             patch("importlib.util.module_from_spec", return_value=mock_module), \
             patch.dict(sys.modules, {
                 "tools": MagicMock(),
                 "tools.memory_tool": MagicMock(MemoryStore=lambda *a, **kw: mock_ms),
             }):
            # Also need to patch shutil.copy2 to avoid actual file copy
            with patch("shutil.copy2"):
                return store.execute_cleanup(
                    entries=entries,
                    source="MEMORY.md",
                    target="memory",
                    merge_list=merge_list or [],
                    compress_list=compress_list or [],
                    remove_list=remove_list or [],
                    v2_correct=v2_correct or [],
                    v2_corrected=v2_corrected or [],
                    v2_keep=v2_keep or [],
                )

    def test_merge_adds_new_and_removes_originals(
        self, store: MemoryFileStore, mock_memory_store: MagicMock
    ) -> None:
        entries = ["条目A", "条目B", "条目C"]
        merge_list = [{"indices": [0, 1], "合并为": "合并AB"}]
        result = self._execute(store, entries, merge_list=merge_list, mock_ms=mock_memory_store)
        mock_memory_store.add.assert_any_call("memory", "合并AB")
        mock_memory_store.remove.assert_any_call("memory", "条目A"[:80])
        mock_memory_store.remove.assert_any_call("memory", "条目B"[:80])
        assert len(result["ok"]) >= 1  # add ok + remove ok

    def test_compress_adds_before_remove(
        self, store: MemoryFileStore, mock_memory_store: MagicMock
    ) -> None:
        """验证修复：先 add 压缩版，成功后再 remove 原条目。"""
        entries = ["这是一条很长的条目内容需要压缩处理"]
        compress_list = [{"index": 0, "精简为": "压缩版"}]
        self._execute(store, entries, compress_list=compress_list, mock_ms=mock_memory_store)
        # add 和 remove 都应被调用（先 add 再 remove）
        mock_memory_store.add.assert_any_call("memory", "压缩版")
        mock_memory_store.remove.assert_any_call("memory", entries[0][:80])
        # 确认 add 是在 remove 之前调用的
        all_calls = mock_memory_store.mock_calls
        add_idx = next(i for i, c in enumerate(all_calls) if c[0] == "add")
        remove_idx = next(i for i, c in enumerate(all_calls) if c[0] == "remove")
        assert add_idx < remove_idx, "add 应在 remove 之前调用"

    def test_compress_add_fails_does_not_remove(
        self, store: MemoryFileStore, mock_memory_store: MagicMock
    ) -> None:
        """验证修复：如果 add 失败，不应 remove 原条目。"""
        mock_memory_store.add.return_value = {"success": False, "error": "db locked"}
        entries = ["一条重要条目"]
        compress_list = [{"index": 0, "精简为": "压缩版"}]
        result = self._execute(store, entries, compress_list=compress_list, mock_ms=mock_memory_store)
        mock_memory_store.remove.assert_not_called()
        assert len(result["fail"]) >= 1

    def test_remove_list_skips_keep_items(
        self, store: MemoryFileStore, mock_memory_store: MagicMock
    ) -> None:
        entries = ["条目A", "条目B", "条目C"]
        remove_list = [{"index": 0, "原因": "过期"}, {"index": 1, "原因": "重复"}]
        v2_keep = [{"index": 0}]
        result = self._execute(store, entries, remove_list=remove_list, v2_keep=v2_keep, mock_ms=mock_memory_store)
        # index 0 应被跳过（keep），index 1 应被删除
        remove_calls = [c[0][1] for c in mock_memory_store.remove.call_args_list]
        assert "条目A"[:80] not in remove_calls
        assert "条目B"[:80] in remove_calls


    def test_remove_uses_unique_prefix_for_near_duplicate_entries(
        self, store: MemoryFileStore, mock_memory_store: MagicMock
    ) -> None:
        """Near-duplicate USER entries can share the first 80 chars; remove key must be unique."""
        common = "User prefers real-production data analysis over synthetic tests for deployment verification. "
        entries = [
            common + "After code changes, they ask for real run verification.",
            common + "Trusts code review plus production logs.",
        ]
        remove_list = [{"index": 1, "原因": "重复"}]
        self._execute(store, entries, remove_list=remove_list, mock_ms=mock_memory_store)
        remove_key = mock_memory_store.remove.call_args_list[0][0][1]
        assert remove_key != entries[1][:80]
        assert entries[1].startswith(remove_key)
        assert remove_key not in entries[0]

    def test_empty_operations_return_ok(
        self, store: MemoryFileStore, mock_memory_store: MagicMock
    ) -> None:
        entries: list[str] = []
        result = self._execute(store, entries, mock_ms=mock_memory_store)
        assert result == {"ok": [], "fail": []}

    def test_remove_list_with_negative_index_skipped(
        self, store: MemoryFileStore, mock_memory_store: MagicMock
    ) -> None:
        entries = ["条目A"]
        remove_list = [{"index": -1, "原因": "无效"}]
        self._execute(store, entries, remove_list=remove_list, mock_ms=mock_memory_store)
        mock_memory_store.remove.assert_not_called()

    def _execute_with_hindsight(
        self,
        store: MemoryFileStore,
        entries: list[str],
        hindsight_list: list | None = None,
        mock_ms: MagicMock | None = None,
    ) -> dict:
        """辅助方法：带 hindsight_list 的 execute_cleanup。"""
        if mock_ms is None:
            mock_ms = MagicMock()
            mock_ms.remove.return_value = {"success": True}
            mock_ms.add.return_value = {"success": True}

        mock_module = MagicMock()
        mock_module.MemoryStore = lambda *a, **kw: mock_ms  # type: ignore[attr-defined]
        mock_spec = MagicMock()
        mock_spec.loader.exec_module.return_value = None
        with patch("importlib.util.spec_from_file_location", return_value=mock_spec), \
             patch("importlib.util.module_from_spec", return_value=mock_module), \
             patch.dict(sys.modules, {
                 "tools": MagicMock(),
                 "tools.memory_tool": MagicMock(MemoryStore=lambda *a, **kw: mock_ms),
             }):
            with patch("shutil.copy2"):
                with patch.object(MemoryFileStore, "_retain", return_value=True) as mock_retain:
                    result = store.execute_cleanup(
                        entries=entries,
                        source="USER.md",
                        target="user",
                        merge_list=[],
                        compress_list=[],
                        remove_list=[],
                        v2_correct=[],
                        v2_corrected=[],
                        v2_keep=[],
                        hindsight_list=hindsight_list or [],
                    )
                    return result, mock_retain

    def test_hindsight_retain_passes_tags_when_backfill_enabled(
        self, app_config: AppConfig, mock_memory_store: MagicMock
    ) -> None:
        """keyword_backfill=true 时，hindsight retain 应传入 tags。"""
        store = MemoryFileStore(app_config)
        entries = ["审计方法论：采用10+轮递进检查模式，逐步深入"]
        hindsight_list = [
            {"index": 0, "关键词": ["审计", "方法论", "风险"]},
        ]
        result, mock_retain = self._execute_with_hindsight(
            store, entries, hindsight_list=hindsight_list, mock_ms=mock_memory_store
        )
        assert mock_retain.call_count == 1
        call_args = mock_retain.call_args
        assert call_args[0][0] == entries[0]
        assert call_args.kwargs.get("tags") == ["审计", "方法论", "风险"]

    def test_hindsight_retain_no_tags_when_backfill_disabled(
        self, mock_memory_store: MagicMock
    ) -> None:
        """keyword_backfill=false 时，hindsight retain 不应传入 tags。"""
        cfg = AppConfig(keyword_backfill=False)
        store = MemoryFileStore(cfg)
        entries = ["审计方法论：采用10+轮递进检查模式，逐步深入"]
        hindsight_list = [
            {"index": 0, "关键词": ["审计", "方法论", "风险"]},
        ]
        result, mock_retain = self._execute_with_hindsight(
            store, entries, hindsight_list=hindsight_list, mock_ms=mock_memory_store
        )
        assert mock_retain.call_count == 1
        call_args = mock_retain.call_args
        assert call_args.kwargs.get("tags") is None

    def test_hindsight_retain_no_tags_when_no_keywords_field(
        self, app_config: AppConfig, mock_memory_store: MagicMock
    ) -> None:
        """hindsight 条目无关键词字段时，tags 应为 None。"""
        store = MemoryFileStore(app_config)
        entries = ["审计方法论：采用10+轮递进检查模式，逐步深入"]
        hindsight_list = [
            {"index": 0},  # 没有关键词字段
        ]
        result, mock_retain = self._execute_with_hindsight(
            store, entries, hindsight_list=hindsight_list, mock_ms=mock_memory_store
        )
        assert mock_retain.call_count == 1
        call_args = mock_retain.call_args
        assert call_args.kwargs.get("tags") is None

    def test_hindsight_retain_no_tags_when_keywords_empty(
        self, app_config: AppConfig, mock_memory_store: MagicMock
    ) -> None:
        """hindsight 条目关键词列表为空时，tags 应为 None。"""
        store = MemoryFileStore(app_config)
        entries = ["审计方法论：采用10+轮递进检查模式，逐步深入"]
        hindsight_list = [
            {"index": 0, "关键词": []},
        ]
        result, mock_retain = self._execute_with_hindsight(
            store, entries, hindsight_list=hindsight_list, mock_ms=mock_memory_store
        )
        assert mock_retain.call_count == 1
        call_args = mock_retain.call_args
        assert call_args.kwargs.get("tags") is None

    def test_hindsight_retain_filters_non_string_tags(
        self, app_config: AppConfig, mock_memory_store: MagicMock
    ) -> None:
        """hindsight 关键词中的非字符串项应被过滤。"""
        store = MemoryFileStore(app_config)
        entries = ["审计方法论：采用10+轮递进检查模式，逐步深入"]
        hindsight_list = [
            {"index": 0, "关键词": ["审计", None, "", 123, "方法论"]},
        ]
        result, mock_retain = self._execute_with_hindsight(
            store, entries, hindsight_list=hindsight_list, mock_ms=mock_memory_store
        )
        assert mock_retain.call_count == 1
        call_args = mock_retain.call_args
        tags = call_args.kwargs.get("tags")
        assert tags == ["审计", "方法论"]

    def test_hindsight_skips_already_removed(
        self, app_config: AppConfig, mock_memory_store: MagicMock
    ) -> None:
        """已被 compress 删除的条目不应再执行 hindsight retain。"""
        store = MemoryFileStore(app_config)
        entries = ["条目A内容需要压缩处理", "条目B"]
        hindsight_list = [
            {"index": 0, "关键词": ["标签1"]},
        ]
        compress_list = [
            {"index": 0, "精简为": "压缩后的条目A"},
        ]
        mock_module = MagicMock()
        mock_module.MemoryStore = lambda *a, **kw: mock_memory_store  # type: ignore[attr-defined]
        mock_spec = MagicMock()
        mock_spec.loader.exec_module.return_value = None
        with patch("importlib.util.spec_from_file_location", return_value=mock_spec), \
             patch("importlib.util.module_from_spec", return_value=mock_module), \
             patch.dict(sys.modules, {
                 "tools": MagicMock(),
                 "tools.memory_tool": MagicMock(MemoryStore=lambda *a, **kw: mock_memory_store),
             }):
            with patch("shutil.copy2"):
                with patch.object(MemoryFileStore, "_retain", return_value=True) as mock_retain:
                    store.execute_cleanup(
                        entries=entries,
                        source="USER.md",
                        target="user",
                        merge_list=[],
                        compress_list=compress_list,
                        remove_list=[],
                        v2_correct=[],
                        v2_corrected=[],
                        v2_keep=[],
                        hindsight_list=hindsight_list,
                    )
                    # index 0 已被 compress 删掉了，hindsight 应跳过
                    retain_contents = [
                        c.args[0] if c.args else ""
                        for c in mock_retain.call_args_list
                    ]
                    assert entries[0] not in retain_contents
