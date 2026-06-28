"""UseLogger 模块测试。"""

import json
import os
import tempfile
import time

import pytest

from knowledge_navigation.core.use_log import UseLogger


@pytest.fixture
def tmp_log_path() -> str:
    """临时日志文件路径。"""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestUseLoggerBasic:
    """UseLogger 基本功能测试。"""

    def test_disabled_logger_does_nothing(self, tmp_log_path: str) -> None:
        """禁用时不写入任何内容。"""
        logger = UseLogger(
            enabled=False,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        logger.log_recall("test query", [{"id": "n1", "score": 0.9}], "hindsight", "s1")
        logger.flush()
        logger.close()
        assert os.path.getsize(tmp_log_path) == 0

    def test_log_recall_writes_correct_format(self, tmp_log_path: str) -> None:
        """log_recall 写入的 JSON 格式正确。"""
        logger = UseLogger(
            enabled=True,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            results = [
                {"id": "node-1", "final_score": 0.95},
                {"id": "node-2", "score": 0.8},
            ]
            logger.log_recall("测试问题", results, "hindsight", "session-abc")

            with open(tmp_log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["type"] == "recall"
            assert entry["query"] == "测试问题"
            assert entry["source"] == "hindsight"
            assert entry["session_id"] == "session-abc"
            assert isinstance(entry["ts"], int)
            assert len(entry["results"]) == 2
            assert entry["results"][0]["node_id"] == "node-1"
            assert entry["results"][0]["score"] == 0.95
            assert entry["results"][1]["node_id"] == "node-2"
            assert entry["results"][1]["score"] == 0.8
        finally:
            logger.close()

    def test_log_recall_with_node_id_field(self, tmp_log_path: str) -> None:
        """结果用 node_id 字段时也能正确读取。"""
        logger = UseLogger(
            enabled=True,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            results = [{"node_id": "uuid-xxx", "score": 0.72}]
            logger.log_recall("q", results, "knowledge_tree", "s1")

            with open(tmp_log_path, "r", encoding="utf-8") as f:
                entry = json.loads(f.readline())

            assert entry["results"][0]["node_id"] == "uuid-xxx"
            assert entry["results"][0]["score"] == 0.72
        finally:
            logger.close()

    def test_log_recall_no_score_defaults_to_zero(self, tmp_log_path: str) -> None:
        """无 score 字段时默认 0.0。"""
        logger = UseLogger(
            enabled=True,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            results = [{"id": "n1"}]
            logger.log_recall("q", results, "hindsight", "s1")

            with open(tmp_log_path, "r", encoding="utf-8") as f:
                entry = json.loads(f.readline())

            assert entry["results"][0]["score"] == 0.0
        finally:
            logger.close()

    def test_log_usage_format(self, tmp_log_path: str) -> None:
        """log_usage 写入格式正确。"""
        logger = UseLogger(
            enabled=True,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            logger.log_usage("node-123", "hindsight", "用户问题", "sess-1")

            with open(tmp_log_path, "r", encoding="utf-8") as f:
                entry = json.loads(f.readline())

            assert entry["type"] == "usage"
            assert entry["node_id"] == "node-123"
            assert entry["source"] == "hindsight"
            assert entry["query"] == "用户问题"
            assert entry["session_id"] == "sess-1"
            assert isinstance(entry["ts"], int)
        finally:
            logger.close()

    def test_empty_results(self, tmp_log_path: str) -> None:
        """空结果列表也能正常记录。"""
        logger = UseLogger(
            enabled=True,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            logger.log_recall("q", [], "hindsight", "s1")

            with open(tmp_log_path, "r", encoding="utf-8") as f:
                entry = json.loads(f.readline())

            assert entry["results"] == []
        finally:
            logger.close()


class TestUseLoggerBatching:
    """批量 flush 测试。"""

    def test_batch_flush_when_threshold_reached(self, tmp_log_path: str) -> None:
        """达到 batch_size 时自动 flush。"""
        batch_size = 3
        logger = UseLogger(
            enabled=True,
            batch_size=batch_size,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            for i in range(batch_size - 1):
                logger.log_recall(f"q{i}", [{"id": f"n{i}", "score": 0.5}], "hindsight", "s1")

            assert os.path.getsize(tmp_log_path) == 0
            assert logger.buffer_size == batch_size - 1

            logger.log_recall("q_last", [{"id": "n_last", "score": 0.5}], "hindsight", "s1")

            assert logger.buffer_size == 0

            with open(tmp_log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == batch_size
        finally:
            logger.close()

    def test_manual_flush(self, tmp_log_path: str) -> None:
        """手动 flush 正确写入缓冲区。"""
        logger = UseLogger(
            enabled=True,
            batch_size=100,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            for i in range(5):
                logger.log_recall(f"q{i}", [{"id": f"n{i}", "score": 0.5}], "hindsight", "s1")

            assert logger.buffer_size == 5
            assert os.path.getsize(tmp_log_path) == 0

            logger.flush()

            assert logger.buffer_size == 0
            with open(tmp_log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 5
        finally:
            logger.close()

    def test_close_flushes_remaining(self, tmp_log_path: str) -> None:
        """close() 时刷盘剩余日志。"""
        logger = UseLogger(
            enabled=True,
            batch_size=100,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        for i in range(3):
            logger.log_recall(f"q{i}", [{"id": f"n{i}", "score": 0.5}], "hindsight", "s1")

        assert logger.buffer_size == 3
        logger.close()

        with open(tmp_log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 3

    def test_flush_empty_buffer_no_error(self, tmp_log_path: str) -> None:
        """空缓冲区 flush 不报错。"""
        logger = UseLogger(
            enabled=True,
            batch_size=10,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            logger.flush()
            assert logger.buffer_size == 0
        finally:
            logger.close()


class TestUseLoggerSilentFailure:
    """静默降级测试。"""

    def test_invalid_log_path_silent_failure(self, tmp_log_path: str) -> None:
        """无效路径时静默失败，不抛出异常。"""
        invalid_path = os.path.join(tmp_log_path, "nonexistent", "file.jsonl")
        logger = UseLogger(
            enabled=True,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=invalid_path,
        )
        try:
            logger.log_recall("q", [{"id": "n1", "score": 0.5}], "hindsight", "s1")
            logger.flush()
        finally:
            logger.close()

    def test_close_after_close_no_error(self, tmp_log_path: str) -> None:
        """重复 close() 不报错。"""
        logger = UseLogger(
            enabled=True,
            batch_size=10,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        logger.close()
        logger.close()

    def test_log_after_close_no_error(self, tmp_log_path: str) -> None:
        """关闭后 log_recall 不报错，也不写入。"""
        logger = UseLogger(
            enabled=True,
            batch_size=1,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        logger.close()
        logger.log_recall("q", [{"id": "n1", "score": 0.5}], "hindsight", "s1")
        logger.log_usage("n1", "hindsight")
        logger.flush()

        assert os.path.getsize(tmp_log_path) == 0


class TestUseLoggerThreading:
    """定时器相关测试。"""

    def test_timer_flush(self, tmp_log_path: str) -> None:
        """定时器触发刷盘。"""
        logger = UseLogger(
            enabled=True,
            batch_size=100,
            flush_interval_seconds=1,
            log_path=tmp_log_path,
        )
        try:
            logger.log_recall("q", [{"id": "n1", "score": 0.5}], "hindsight", "s1")
            assert logger.buffer_size == 1
            assert os.path.getsize(tmp_log_path) == 0

            time.sleep(1.5)

            assert logger.buffer_size == 0
            assert os.path.getsize(tmp_log_path) > 0
        finally:
            logger.close()

    def test_flush_interval_zero_no_timer(self, tmp_log_path: str) -> None:
        """flush_interval=0 时不启动定时器。"""
        logger = UseLogger(
            enabled=True,
            batch_size=100,
            flush_interval_seconds=0,
            log_path=tmp_log_path,
        )
        try:
            assert logger._timer is None
        finally:
            logger.close()
