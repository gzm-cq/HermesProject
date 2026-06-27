"""配置与 trace 过滤测试。"""

import logging

from knowledge_navigation.config import TraceRecordFilter, is_test_trace_record


def test_is_test_trace_record_matches_mock_queries() -> None:
    """pytest/mock query 不应进入生产 trace 或基线统计。"""
    assert is_test_trace_record({"query_trunc": "test recall system"}) is True
    assert is_test_trace_record({"query_trunc": "help me search test query"}) is True
    assert is_test_trace_record({"error": "RuntimeError: API down"}) is True
    assert is_test_trace_record({"query_trunc": "真实用户查询"}) is False


def test_test_trace_filter_blocks_mock_record() -> None:
    """RotatingFileHandler 上的过滤器应拦截测试/模拟记录。"""
    record = logging.LogRecord(
        name="knowledge_navigation.core.hooks",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="recall error",
        args=(),
        exc_info=None,
    )
    record.query_trunc = "test recall system"

    assert TraceRecordFilter().filter(record) is False
