"""session_db.py 单元测试 — SessionDB FTS 查询。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memory_cleanup.adapters.session_db import SessionDB
from memory_cleanup.config import AppConfig


class TestSessionDBSearch:
    """SessionDB.search() 测试。"""

    def test_search_db_not_exists_returns_not_found(self, app_config: AppConfig, tmp_path: Path) -> None:
        app_config.session_db_path = str(tmp_path / "missing-state.db")
        db = SessionDB(app_config)
        result = db.search("测试查询")
        assert result == {"found": False, "confidence": 0.0}

    def test_search_no_chinese_keywords(self, app_config: AppConfig, tmp_path: Path) -> None:
        """只有英文短词或无关键词时返回 not found。"""
        app_config.session_db_path = str(tmp_path / "state.db")
        db = SessionDB(app_config)
        result = db.search("the")
        assert result == {"found": False, "confidence": 0.0}

        result = db.search("ab")
        assert result == {"found": False, "confidence": 0.0}

        result = db.search("")
        assert result == {"found": False, "confidence": 0.0}

    def test_search_stop_words_only(self, app_config: AppConfig, tmp_path: Path) -> None:
        """只有停用词时返回 not found。"""
        app_config.session_db_path = str(tmp_path / "state.db")
        db = SessionDB(app_config)
        result = db.search("the this that were")
        assert result == {"found": False, "confidence": 0.0}

    def test_search_exception_returns_not_found(self, app_config: AppConfig, tmp_path: Path) -> None:
        """数据库操作异常时优雅降级。"""
        db_path = tmp_path / "state.db"
        db_path.write_text("invalid sqlite content", encoding="utf-8")
        app_config.session_db_path = str(db_path)
        db = SessionDB(app_config)
        # 无效的 SQLite 文件会导致执行异常，应返回 not found
        result = db.search("测试查询内容")
        assert result == {"found": False, "confidence": 0.0}
