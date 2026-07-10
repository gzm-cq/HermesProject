"""read_sessions + 时间戳管理单元测试。"""
import sqlite3
import time
from pathlib import Path


def _create_test_db(db_path: Path, sessions: list[dict], messages: list[dict] | None = None):
    """创建测试用 SQLite 数据库。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            message_count INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            started_at REAL,
            ended_at REAL,
            archived INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL
        )
    """)
    for s in sessions:
        cur.execute(
            "INSERT INTO sessions (id, title, message_count, input_tokens, output_tokens, "
            "estimated_cost_usd, started_at, ended_at, archived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s["id"], s.get("title", ""), s.get("message_count", 0),
             s.get("input_tokens", 0), s.get("output_tokens", 0),
             s.get("estimated_cost_usd", 0), s.get("started_at", 0),
             s.get("ended_at", 0), s.get("archived", 0)),
        )
    if messages:
        for m in messages:
            cur.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (m["session_id"], m["role"], m["content"], m.get("timestamp", 0)),
            )
    conn.commit()
    conn.close()


class TestReadSessions:
    def test_filters_by_since_ts(self, module, tmp_config, tmp_path):
        """只返回 since_ts 之后的 session。"""
        db_path = tmp_path / "state.db"
        now = time.time()
        sessions = [
            {"id": "old", "title": "旧的", "message_count": 20, "input_tokens": 5000,
             "started_at": now - 100000, "archived": 0},
            {"id": "new", "title": "新的", "message_count": 20, "input_tokens": 5000,
             "started_at": now - 100, "archived": 0},
        ]
        msgs = [
            {"session_id": "old", "role": "user", "content": "你好" * 500, "timestamp": now - 100000},
            {"session_id": "old", "role": "assistant", "content": "你好" * 500, "timestamp": now - 100000 + 1},
            {"session_id": "new", "role": "user", "content": "测试" * 500, "timestamp": now - 100},
            {"session_id": "new", "role": "assistant", "content": "测试" * 500, "timestamp": now - 100 + 1},
        ]
        _create_test_db(db_path, sessions, msgs)
        tmp_config["session"]["db_path"] = str(db_path)

        result = module.read_sessions(now - 200)
        assert len(result) == 1
        assert result[0]["id"] == "new"

    def test_filters_by_min_messages(self, module, tmp_config, tmp_path):
        """message_count 不足的被过滤。"""
        db_path = tmp_path / "state.db"
        now = time.time()
        sessions = [
            {"id": "s1", "title": "少消息", "message_count": 1, "input_tokens": 5000,
             "started_at": now, "archived": 0},
        ]
        _create_test_db(db_path, sessions)
        tmp_config["session"]["db_path"] = str(db_path)

        result = module.read_sessions(0)
        assert len(result) == 0

    def test_filters_by_min_tokens(self, module, tmp_config, tmp_path):
        """input_tokens 不足的被过滤。"""
        db_path = tmp_path / "state.db"
        now = time.time()
        sessions = [
            {"id": "s1", "title": "少 token", "message_count": 20, "input_tokens": 5,
             "started_at": now, "archived": 0},
        ]
        _create_test_db(db_path, sessions)
        tmp_config["session"]["db_path"] = str(db_path)

        result = module.read_sessions(0)
        assert len(result) == 0

    def test_archived_sessions_excluded(self, module, tmp_config, tmp_path):
        """已归档的 session 被排除。"""
        db_path = tmp_path / "state.db"
        now = time.time()
        sessions = [
            {"id": "archived", "title": "已归档", "message_count": 20, "input_tokens": 5000,
             "started_at": now, "archived": 1},
            {"id": "active", "title": "活跃", "message_count": 20, "input_tokens": 5000,
             "started_at": now, "archived": 0},
        ]
        msgs = [
            {"session_id": "active", "role": "user", "content": "x" * 2000, "timestamp": now},
            {"session_id": "active", "role": "assistant", "content": "x" * 2000, "timestamp": now + 1},
        ]
        _create_test_db(db_path, sessions, msgs)
        tmp_config["session"]["db_path"] = str(db_path)

        result = module.read_sessions(0)
        assert len(result) == 1
        assert result[0]["id"] == "active"

    def test_text_length_filter(self, module, tmp_config, tmp_path):
        """text_len 不足 2000 的被过滤。"""
        db_path = tmp_path / "state.db"
        now = time.time()
        sessions = [
            {"id": "short", "title": "短文本", "message_count": 20, "input_tokens": 5000,
             "started_at": now, "archived": 0},
        ]
        msgs = [
            {"session_id": "short", "role": "user", "content": "hi", "timestamp": now},
            {"session_id": "short", "role": "assistant", "content": "hello", "timestamp": now + 1},
        ]
        _create_test_db(db_path, sessions, msgs)
        tmp_config["session"]["db_path"] = str(db_path)

        result = module.read_sessions(0)
        assert len(result) == 0

    def test_tool_messages_excluded(self, module, tmp_config, tmp_path):
        """tool 消息不出现在 text 中。"""
        db_path = tmp_path / "state.db"
        now = time.time()
        sessions = [
            {"id": "s1", "title": "工具测试", "message_count": 10, "input_tokens": 5000,
             "started_at": now, "archived": 0},
        ]
        msgs = [
            {"session_id": "s1", "role": "user", "content": "a" * 1500, "timestamp": now},
            {"session_id": "s1", "role": "tool", "content": "b" * 5000, "timestamp": now + 1},
            {"session_id": "s1", "role": "assistant", "content": "c" * 1500, "timestamp": now + 2},
        ]
        _create_test_db(db_path, sessions, msgs)
        tmp_config["session"]["db_path"] = str(db_path)

        result = module.read_sessions(0)
        assert len(result) == 1
        text = result[0]["text"]
        assert "[用户]" in text
        assert "[助手]" in text
        assert "b" * 100 not in text  # tool 内容不应出现


class TestLastRunTimestamp:
    def test_save_and_get_roundtrip(self, module, tmp_config, tmp_path):
        """保存后能读取到相同时间戳。"""
        ts = 1234567890.123
        module.save_last_run_ts(ts)
        assert module.get_last_run_ts() == ts

    def test_missing_file_returns_24h_ago(self, module, tmp_config, tmp_path):
        """文件不存在时返回 24 小时前。"""
        import time as _t
        expected = _t.time() - 86400
        result = module.get_last_run_ts()
        assert abs(result - expected) < 2

    def test_corrupt_file_returns_24h_ago(self, module, tmp_config, tmp_path):
        """文件内容损坏时返回 24 小时前。"""
        verdict_dir = Path(tmp_config["cache"]["verdict_dir"])
        verdict_dir.mkdir(parents=True, exist_ok=True)
        ts_file = verdict_dir / "last_run.txt"
        ts_file.write_text("not a number", encoding="utf-8")

        import time as _t
        expected = _t.time() - 86400
        result = module.get_last_run_ts()
        assert abs(result - expected) < 2
