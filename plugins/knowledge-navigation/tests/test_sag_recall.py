"""SAG 第四路召回的静默降级与命中率埋点测试。

覆盖：熔断器开启跳过、HTTP 4xx 不熔断、HTTP 5xx 熔断、服务端异常、
200 空 sections（未识别软件）静默降级、200 有结果命中埋点。
这些路径必须返回 ([], None) 或 ([], error) 且不向主注入流抛出异常，
否则会污染 HS/KT/Skill 三路的正常注入。

注意：_do_sag_recall 内部以 `import requests as _req` 局部导入，
故需 patch 顶层 `requests.post`（局部 import 取到的是同一被 patch 模块对象）。
"""

from unittest.mock import patch

import pytest

from knowledge_navigation.core import circuit_breaker as cb
from knowledge_navigation.core.hooks import router as kn_router


@pytest.fixture(autouse=True)
def _reset_sag_cb() -> None:
    cb._sag_cb._failures = 0
    cb._sag_cb._open_until = 0.0
    cb._sag_cb._failure_types.clear()
    yield


def _force_open_circuit() -> None:
    """将 SAG 熔断器置于开启态（未来时间戳，使 is_open 恒为真）。"""
    cb._sag_cb._open_until = 1e18


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _run_sag(query: str = "如何在 Qoder 中配置 hook") -> tuple[list, object]:
    return kn_router._do_sag_recall(query)


def test_sag_circuit_open_skips() -> None:
    """熔断器开启时直接返回空，不发起请求。"""
    _force_open_circuit()
    with patch("requests.post") as mock_post:
        results, err = _run_sag()
    assert results == []
    assert err is None
    mock_post.assert_not_called()


def test_sag_200_empty_sections_silent_degrade() -> None:
    """服务端返回 200 但无 sections（未识别当前软件）-> 静默降级为空。"""
    with patch("requests.post", return_value=_FakeResp(200, {"sections": []})):
        results, err = _run_sag()
    assert results == []
    assert err is None
    # 成功后不应累计熔断失败
    assert cb._sag_cb._failures == 0


def test_sag_200_with_sections_hit() -> None:
    """服务端返回 200 且有 sections -> 返回结果。"""
    sections = [{"id": "s1", "text": "doc"}]
    with patch("requests.post", return_value=_FakeResp(200, {"sections": sections})):
        results, err = _run_sag()
    assert results == sections
    assert err is None


def test_sag_4xx_does_not_trip_circuit() -> None:
    """4xx 客户端错误不计熔断、返回空列表且不抛异常。"""
    with patch("requests.post", return_value=_FakeResp(422, {})):
        results, err = _run_sag()
    assert results == []
    assert err is not None and "HTTP 422" in err
    assert cb._sag_cb._failures == 0


def test_sag_5xx_trips_circuit_and_returns_empty() -> None:
    """5xx 服务端错误计入熔断但返回空列表、不抛异常。"""
    with patch("requests.post", return_value=_FakeResp(503, {})):
        results, err = _run_sag()
    assert results == []
    assert err is not None and "HTTP 503" in err
    assert cb._sag_cb._failures >= 1


def test_sag_exception_returns_empty_no_raise() -> None:
    """请求抛异常时静默降级为空，不向上抛出。"""
    with patch("requests.post", side_effect=ConnectionError("refused")):
        results, err = _run_sag()
    assert results == []
    assert err is not None and "ConnectionError" in err
    assert cb._sag_cb._failures >= 1
