"""guarded_chat_completion 重试/退避策略单元测试。

覆盖：5xx/网络可重试退避、429 尊重 Retry-After、超时与 4xx 业务错误不重试、
空内容瞬时故障重试、重试耗尽抛 ConnectionError、成功路径返回响应。
"""

from unittest.mock import patch

import pytest

from hermes_common.llm_guard import LLMTransportError, guarded_chat_completion


def _ok_resp(content: str = "ok") -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_retries_then_succeeds_with_exponential_backoff():
    calls = {"n": 0}

    def post_fn(body, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise LLMTransportError(502, True, message="server error")
        return _ok_resp()

    sleeps = []
    with patch("hermes_common.llm_guard.time.sleep", side_effect=lambda d: sleeps.append(d)):
        resp = guarded_chat_completion(
            post_fn, model="m", messages=[], min_interval=0, max_retries=3
        )

    assert resp == _ok_resp()
    assert calls["n"] == 3
    # attempt1→sleep(1.0), attempt2→sleep(2.0), attempt3 为最后一次不 sleep
    assert sleeps == [1.0, 2.0]


def test_exhaustion_raises_connectionerror():
    def post_fn(body, timeout):
        raise LLMTransportError(502, True, message="server error")

    sleeps = []
    with patch("hermes_common.llm_guard.time.sleep", side_effect=lambda d: sleeps.append(d)):
        with pytest.raises(ConnectionError):
            guarded_chat_completion(
                post_fn, model="m", messages=[], min_interval=0, max_retries=3
            )

    # 前两次退避，最后一次不 sleep
    assert sleeps == [1.0, 2.0]


def test_429_retries_with_retry_after():
    def post_fn(body, timeout):
        raise LLMTransportError(429, True, retry_after="0.5", message="rate limit")

    sleeps = []
    with patch("hermes_common.llm_guard.time.sleep", side_effect=lambda d: sleeps.append(d)):
        with pytest.raises(ConnectionError):
            guarded_chat_completion(
                post_fn, model="m", messages=[], min_interval=0, max_retries=2
            )

    assert sleeps == [0.5]


def test_timeout_does_not_retry():
    def post_fn(body, timeout):
        raise LLMTransportError("timeout", False, message="timed out")

    sleeps = []
    with patch("hermes_common.llm_guard.time.sleep", side_effect=lambda d: sleeps.append(d)):
        with pytest.raises(ConnectionError):
            guarded_chat_completion(
                post_fn, model="m", messages=[], min_interval=0, max_retries=3
            )

    assert sleeps == []


def test_4xx_business_error_does_not_retry():
    def post_fn(body, timeout):
        raise LLMTransportError(400, False, message="bad request")

    sleeps = []
    with patch("hermes_common.llm_guard.time.sleep", side_effect=lambda d: sleeps.append(d)):
        with pytest.raises(ConnectionError):
            guarded_chat_completion(
                post_fn, model="m", messages=[], min_interval=0, max_retries=3
            )

    assert sleeps == []


def test_empty_content_retries_then_succeeds():
    calls = {"n": 0}

    def post_fn(body, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {"content": ""}}]}
        return _ok_resp()

    sleeps = []
    with patch("hermes_common.llm_guard.time.sleep", side_effect=lambda d: sleeps.append(d)):
        resp = guarded_chat_completion(
            post_fn, model="m", messages=[], min_interval=0, max_retries=3
        )

    assert resp == _ok_resp()
    assert calls["n"] == 2
    # 第一次空内容退避一次
    assert sleeps == [1.0]
