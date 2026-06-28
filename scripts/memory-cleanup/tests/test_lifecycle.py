"""lifecycle.py 单元测试 — 冷记忆检测 + 高频记忆检测。"""

from datetime import datetime, timedelta

import pytest

from memory_cleanup.core.lifecycle import detect_cold_memories, detect_hot_memories


class TestDetectColdMemories:
    """detect_cold_memories() 测试。"""

    def test_empty_entries_returns_empty(self) -> None:
        result = detect_cold_memories([], 30)
        assert result == []

    def test_recent_entries_not_cold(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"content": "最近访问的记忆", "last_accessed": datetime(2026, 6, 20)},
            {"content": "今天访问的记忆", "last_accessed": datetime(2026, 6, 28)},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 0

    def test_old_entries_are_cold(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"content": "很久以前的记忆", "last_accessed": datetime(2026, 1, 1)},
            {"content": "两个月前的记忆", "last_accessed": datetime(2026, 4, 28)},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 2

    def test_boundary_exactly_cold_days(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"content": "刚好30天前", "last_accessed": datetime(2026, 5, 29)},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 1

    def test_string_last_accessed(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"content": "字符串日期格式", "last_accessed": "2026-01-15"},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 1

    def test_created_at_estimation(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"content": "只有创建时间", "created_at": datetime(2026, 1, 1)},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 1
        assert "estimated_last_access" in result[0]
        assert "days_since_access" in result[0]

    def test_no_date_uses_default_estimation(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"content": "没有任何日期信息的普通记忆条目"},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 0

    def test_mixed_entries(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"content": "冷记忆1", "last_accessed": datetime(2026, 1, 1)},
            {"content": "热记忆1", "last_accessed": datetime(2026, 6, 25)},
            {"content": "冷记忆2", "last_accessed": datetime(2026, 3, 1)},
            {"content": "热记忆2", "last_accessed": datetime(2026, 6, 20)},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 2

    def test_result_has_contains_index(self) -> None:
        now = datetime(2026, 6, 28)
        entries = [
            {"index": 0, "content": "测试索引保留", "last_accessed": datetime(2026, 1, 1)},
        ]
        result = detect_cold_memories(entries, 30, now=now)
        assert len(result) == 1
        assert result[0]["index"] == 0
        assert result[0]["content"] == "测试索引保留"


class TestDetectHotMemories:
    """detect_hot_memories() 测试。"""

    def test_empty_entries_returns_empty(self) -> None:
        result = detect_hot_memories([], 10)
        assert result == []

    def test_high_access_count_is_hot(self) -> None:
        entries = [
            {"content": "高频记忆", "access_count": 15},
        ]
        result = detect_hot_memories(entries, 10)
        assert len(result) == 1
        assert result[0]["estimated_access_count"] == 15

    def test_low_access_count_not_hot(self) -> None:
        entries = [
            {"content": "低频记忆", "access_count": 3},
        ]
        result = detect_hot_memories(entries, 10)
        assert len(result) == 0

    def test_boundary_exactly_threshold(self) -> None:
        entries = [
            {"content": "刚好10次", "access_count": 10},
        ]
        result = detect_hot_memories(entries, 10)
        assert len(result) == 1

    def test_frequency_keywords_estimation(self) -> None:
        entries = [
            {"content": "这是一个经常使用的常用工具"},
        ]
        result = detect_hot_memories(entries, 10)
        assert len(result) == 1
        assert result[0]["estimated_access_count"] >= 10

    def test_no_access_count_default(self) -> None:
        entries = [
            {"content": "普通记忆，没有特别的频率描述"},
        ]
        result = detect_hot_memories(entries, 10)
        assert len(result) == 0

    def test_mixed_entries(self) -> None:
        entries = [
            {"content": "高频1", "access_count": 20},
            {"content": "低频1", "access_count": 2},
            {"content": "经常使用的常用工具，每天都要用，日常必备"},
            {"content": "普通条目"},
        ]
        result = detect_hot_memories(entries, 10)
        assert len(result) >= 2

    def test_result_preserves_fields(self) -> None:
        entries = [
            {"content": "测试字段保留", "access_count": 12, "tags": ["tag1", "tag2"]},
        ]
        result = detect_hot_memories(entries, 10)
        assert len(result) == 1
        assert result[0]["content"] == "测试字段保留"
        assert result[0]["tags"] == ["tag1", "tag2"]


class TestFeatureFlagInteraction:
    """Feature Flag 关闭时无副作用测试。"""

    def test_cold_memory_default_config_off(self) -> None:
        from memory_cleanup.config import AppConfig
        cfg = AppConfig()
        assert cfg.cold_memory_eviction is False
        assert cfg.hot_memory_promotion is False

    def test_cold_memory_days_default(self) -> None:
        from memory_cleanup.config import AppConfig
        cfg = AppConfig()
        assert cfg.cold_memory_days == 30
        assert cfg.hot_memory_access_count == 10
        assert cfg.l2_max_entries == 200

    def test_config_validation(self) -> None:
        from memory_cleanup.config import AppConfig
        with pytest.raises(ValueError, match="cold_memory_days"):
            AppConfig(cold_memory_days=0)
        with pytest.raises(ValueError, match="hot_memory_access_count"):
            AppConfig(hot_memory_access_count=0)
        with pytest.raises(ValueError, match="l2_max_entries"):
            AppConfig(l2_max_entries=5)

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from memory_cleanup.config import AppConfig
        monkeypatch.setenv("MEMORY_CLEANUP_COLD_MEMORY_EVICTION", "true")
        monkeypatch.setenv("MEMORY_CLEANUP_COLD_MEMORY_DAYS", "60")
        monkeypatch.setenv("MEMORY_CLEANUP_HOT_MEMORY_PROMOTION", "true")
        monkeypatch.setenv("MEMORY_CLEANUP_HOT_MEMORY_ACCESS_COUNT", "20")
        monkeypatch.setenv("MEMORY_CLEANUP_L2_MAX_ENTRIES", "300")
        cfg = AppConfig.from_env()
        assert cfg.cold_memory_eviction is True
        assert cfg.cold_memory_days == 60
        assert cfg.hot_memory_promotion is True
        assert cfg.hot_memory_access_count == 20
        assert cfg.l2_max_entries == 300
