"""fetch_hindsight_entries 真实 API 实现测试（2026-09-04 接上 mock → 真实 API）。"""

import json
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from memory_cleanup.adapters.memory_store import MemoryFileStore
from memory_cleanup.config import AppConfig


class _FakeResponse:
    """模拟 urllib urlopen 返回值。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestFetchHindsightEntries:
    """fetch_hindsight_entries() 真实 API 分页逻辑。"""

    def test_single_page_maps_fields(self, app_config: AppConfig) -> None:
        """单页响应映射 content/created_at/tags 字段。"""
        store = MemoryFileStore(app_config)
        payload = {
            "items": [
                {
                    "id": "aaa",
                    "text": "记忆内容A",
                    "date": "2026-09-01T10:00:00+00:00",
                    "tags": ["标签1"],
                },
                {
                    "id": "bbb",
                    "text": "记忆内容B",
                    "date": "2026-09-02T11:00:00+00:00",
                    "tags": [],
                },
            ],
            "total": 2,
            "limit": 200,
            "offset": 0,
        }
        with patch.object(urllib.request, "urlopen", return_value=_FakeResponse(payload)) as m:
            result = store.fetch_hindsight_entries(limit=10)
        assert len(result) == 2
        assert result[0]["content"] == "记忆内容A"
        assert result[0]["created_at"] == "2026-09-01T10:00:00+00:00"
        assert result[0]["tags"] == ["标签1"]
        assert result[1]["content"] == "记忆内容B"
        # 请求 URL 应指向 /list 端点并带 limit/offset
        called_url = m.call_args.args[0].full_url
        assert called_url.startswith(app_config.hindsight_url.rstrip("/") + "/list")

    def test_pagination_until_total(self, app_config: AppConfig) -> None:
        """分页直到拉满 total。"""
        store = MemoryFileStore(app_config)
        page_size = 2
        pages = {
            0: {"items": [{"text": f"内容{i}"} for i in range(2)], "total": 5, "offset": 0},
            2: {"items": [{"text": f"内容{i}"} for i in range(2, 4)], "total": 5, "offset": 2},
            4: {"items": [{"text": "内容4"}], "total": 5, "offset": 4},
        }

        def _fake_urlopen(req: urllib.request.Request, timeout: int = 30) -> _FakeResponse:
            import urllib.parse

            offset = int(urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)["offset"][0])
            return _FakeResponse(pages[offset])

        with patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen) as m:
            result = store.fetch_hindsight_entries(limit=100)
        assert len(result) == 5
        assert [r["content"] for r in result] == [f"内容{i}" for i in range(5)]
        assert m.call_count == 3

    def test_limit_caps_items(self, app_config: AppConfig) -> None:
        """limit 参数限制最大拉取条数。"""
        store = MemoryFileStore(app_config)
        payload = {"items": [{"text": f"内容{i}"} for i in range(50)], "total": 500, "offset": 0}
        with patch.object(urllib.request, "urlopen", return_value=_FakeResponse(payload)) as m:
            result = store.fetch_hindsight_entries(limit=10)
        assert len(result) == 50  # 单页返回 50 条，limit 只控制分页上限
        assert m.call_count == 1

    def test_api_failure_returns_partial(self, app_config: AppConfig) -> None:
        """API 失败时返回已拉取的部分，不抛异常。"""
        store = MemoryFileStore(app_config)
        payload = {"items": [{"text": "第一条"}], "total": 100, "offset": 0}

        def _fake_urlopen(req: urllib.request.Request, timeout: int = 30) -> _FakeResponse:
            import urllib.parse

            offset = int(urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)["offset"][0])
            if offset == 0:
                return _FakeResponse(payload)
            raise ConnectionError("connection reset")

        with patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
            result = store.fetch_hindsight_entries(limit=100)
        assert len(result) == 1
        assert result[0]["content"] == "第一条"
