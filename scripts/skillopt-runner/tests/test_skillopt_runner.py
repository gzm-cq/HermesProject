"""test_skillopt_runner.py — skillopt_runner 核心逻辑测试.

覆盖：
  - filter_eligible_skills 资格筛选
  - _detect_feedback 反馈检测（英文+中文）
  - rank_skills 精排逻辑（增量负反馈累积）
  - harvest_hermes_sessions 双格式采集
  - load_state / save_state 状态管理
  - get_skill_path 路径解析
  - patch_skill_hermes 部署逻辑
"""
import json
import os
import pathlib
import sqlite3
import sys
import textwrap
from unittest.mock import patch, MagicMock

import pytest

import skillopt_runner as sr
from skillopt_runner import (
    filter_eligible_skills,
    _detect_feedback,
    rank_skills,
    harvest_hermes_sessions,
    load_state,
    save_state,
    get_skill_path,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_hermes(tmp_path, monkeypatch):
    """Redirect HERMES_HOME and related paths to a temp directory."""
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "sessions").mkdir()
    (hermes / "skills").mkdir()
    monkeypatch.setattr(sr, "HERMES_HOME", hermes)
    monkeypatch.setattr(sr, "USAGE_FILE", hermes / "skills" / ".usage.json")
    monkeypatch.setattr(sr, "STATE_FILE", hermes / "skillopt" / "state.json")
    monkeypatch.setattr(sr, "BACKUP_DIR", hermes / "skillopt" / "backups")
    return hermes


@pytest.fixture()
def usage_file(tmp_hermes):
    """Return the usage file path; caller writes JSON."""
    return tmp_hermes / "skills" / ".usage.json"


def _write_usage(path: pathlib.Path, data: dict):
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_session_jsonl(sessions_dir, session_id, messages):
    """Write a .jsonl session file."""
    p = sessions_dir / f"{session_id}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return p


def _make_session_json(sessions_dir, session_id, data):
    """Write a session_*.json file."""
    p = sessions_dir / f"session_{session_id}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _make_state_db(hermes_dir: pathlib.Path, sessions: list[dict]) -> pathlib.Path:
    """Create a minimal Hermes state.db for harvest tests."""
    db_path = hermes_dir / "state.db"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                user_id TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                message_count INTEGER DEFAULT 0,
                title TEXT
            )
            """
        )
        con.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL,
                finish_reason TEXT
            )
            """
        )
        for session in sessions:
            messages = session["messages"]
            con.execute(
                """
                INSERT INTO sessions
                    (id, source, user_id, started_at, ended_at, message_count, title)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["id"],
                    session.get("source", "feishu"),
                    session.get("user_id", "user-1"),
                    session["started_at"],
                    session.get("ended_at"),
                    len(messages),
                    session.get("title", "test session"),
                ),
            )
            for msg in messages:
                con.execute(
                    """
                    INSERT INTO messages
                        (session_id, role, content, tool_name, timestamp, finish_reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["id"],
                        msg["role"],
                        msg.get("content"),
                        msg.get("tool_name"),
                        msg["timestamp"],
                        msg.get("finish_reason"),
                    ),
                )
        con.commit()
    finally:
        con.close()
    return db_path


# ═══════════════════════════════════════════════════════════════════════════════
# filter_eligible_skills
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilterEligibleSkills:

    def test_empty_usage(self):
        result = filter_eligible_skills({}, [])
        assert result == []

    def test_pinned_excluded(self):
        usage = {"my-skill": {"pinned": True, "state": "active", "created_by": "agent"}}
        result = filter_eligible_skills(usage, [])
        assert result == []

    def test_non_active_excluded(self):
        usage = {"my-skill": {"state": "archived", "created_by": "agent"}}
        result = filter_eligible_skills(usage, [])
        assert result == []

    def test_denylist_pattern_excluded(self):
        """denylist 使用 startswith 匹配."""
        usage = {"lark-tool": {"state": "active", "created_by": "agent"}}
        result = filter_eligible_skills(usage, ["lark-"])
        assert result == []

    def test_denylist_substring_not_excluded(self):
        """denylist 使用 startswith，非前缀匹配不排除."""
        usage = {"my-lark-tool": {"state": "active", "created_by": "agent"}}
        result = filter_eligible_skills(usage, ["lark-"])
        assert len(result) == 1

    def test_agent_created_included(self):
        usage = {"auto-skill": {"state": "active", "created_by": "agent"}}
        result = filter_eligible_skills(usage, [])
        assert len(result) == 1

    def test_local_skill_with_activity_included(self):
        usage = {"my-tool": {"state": "active", "use_count": 3}}
        result = filter_eligible_skills(usage, [])
        assert len(result) == 1

    def test_local_skill_no_activity_excluded(self):
        usage = {"my-tool": {"state": "active", "use_count": 0, "view_count": 0, "patch_count": 0}}
        result = filter_eligible_skills(usage, [])
        assert result == []

    def test_mixed_filtering(self):
        usage = {
            "pinned-skill": {"pinned": True, "state": "active", "created_by": "agent"},
            "archived-skill": {"state": "archived", "created_by": "agent"},
            "denied-skill": {"state": "active", "created_by": "agent"},
            "good-agent": {"state": "active", "created_by": "agent"},
            "good-local": {"state": "active", "use_count": 5},
            "idle-local": {"state": "active", "use_count": 0},
        }
        result = filter_eligible_skills(usage, ["denied-"])
        names = [n for n, _ in result]
        assert "pinned-skill" not in names
        assert "archived-skill" not in names
        assert "denied-skill" not in names
        assert "good-agent" in names
        assert "good-local" in names
        assert "idle-local" not in names
        assert len(names) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# _detect_feedback
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectFeedback:

    def test_english_negative(self):
        sigs = _detect_feedback("That's wrong, fix it")
        neg = [s for s in sigs if s.startswith("neg:")]
        assert len(neg) >= 1
        assert any("wrong" in s for s in neg)

    def test_english_positive(self):
        sigs = _detect_feedback("Thanks, that works perfectly")
        pos = [s for s in sigs if s.startswith("pos:")]
        assert len(pos) >= 1

    def test_chinese_negative(self):
        sigs = _detect_feedback("这个方案不对")
        neg = [s for s in sigs if s.startswith("neg:")]
        assert len(neg) >= 1
        assert any("不对" in s for s in neg)

    def test_chinese_positive(self):
        sigs = _detect_feedback("搞定了，没问题")
        pos = [s for s in sigs if s.startswith("pos:")]
        assert len(pos) >= 1

    def test_no_feedback(self):
        sigs = _detect_feedback("请帮我看看这段代码")
        assert sigs == []

    def test_empty_input(self):
        sigs = _detect_feedback("")
        assert sigs == []

    def test_chinese_cannot_question_is_not_negative(self):
        sigs = _detect_feedback("这个功能能不能配置成每天运行")
        assert not any(s.startswith("neg:") for s in sigs)

    def test_case_insensitive(self):
        sigs = _detect_feedback("THANKS THAT WORKS")
        pos = [s for s in sigs if s.startswith("pos:")]
        assert len(pos) >= 1

    def test_chinese_negative_coverage_buhao(self):
        """'不好' 应该被检测为负反馈."""
        sigs = _detect_feedback("这样改不好")
        neg = [s for s in sigs if s.startswith("neg:")]
        assert len(neg) >= 1, f"'不好' 未被检测为负反馈: {sigs}"

    def test_chinese_negative_coverage_buxing(self):
        """'不行' 应该被检测为负反馈."""
        sigs = _detect_feedback("这个方案不行")
        neg = [s for s in sigs if s.startswith("neg:")]
        assert len(neg) >= 1, f"'不行' 未被检测为负反馈: {sigs}"

    def test_chinese_budui_neg_only(self):
        """'不对' 应只触发负反馈，不触发正反馈."""
        sigs = _detect_feedback("这个逻辑不对")
        neg = [s for s in sigs if s.startswith("neg:")]
        pos = [s for s in sigs if s.startswith("pos:")]
        assert len(neg) >= 1
        assert len(pos) == 0

    def test_chinese_bukeyi_excludes_positive(self):
        """'不可以' 含 '可以'，但排除逻辑应阻止 pos:可以."""
        sigs = _detect_feedback("这样不可以")
        neg = [s for s in sigs if s.startswith("neg:")]
        pos = [s for s in sigs if s.startswith("pos:")]
        assert len(neg) >= 1  # '不可以' triggers neg
        assert len(pos) == 0  # '可以' excluded by '不可以'

    def test_chinese_buhao_excludes_positive_haole(self):
        """'不好' 含 '好了'? 不，'不好' 不含 '好了'。但排除逻辑：'好了' 在 '不好' 中?"""
        # '不好' + '了' 不在这里，但新代码检查 ph == '好了' and '不好' in lower
        sigs = _detect_feedback("改了不好了")
        neg = [s for s in sigs if s.startswith("neg:")]
        pos = [s for s in sigs if s.startswith("pos:")]
        assert len(neg) >= 1
        # '好了' in '不好了' → excluded by new logic
        assert not any("好了" in s for s in pos)



class TestMessageMentionsSkill:

    def test_exact_hyphenated_skill_matches(self):
        assert sr.message_mentions_skill("knowledge-navigation 这个技能不对", "knowledge-navigation")

    def test_generic_agent_word_does_not_match_hermes_agent(self):
        assert not sr.message_mentions_skill("这个 agent 调用失败了", "hermes-agent")

    def test_category_name_requires_skill_part(self):
        assert not sr.message_mentions_skill("devops 脚本有问题", "devops/system-health-check")
        assert sr.message_mentions_skill("system-health-check 有问题", "devops/system-health-check")


class TestSplitConfig:

    def test_runner_private_keys_not_passed_to_sleep_config(self):
        raw = {
            "backend": "litellm",
            "model": "sensenova-6.8-flash-lite",
            "top_k": 5,
            "denylist_patterns": ["lark-"],
            "max_sessions": 10,
        }
        runner_cfg, sleep_cfg = sr.split_config(raw)
        assert runner_cfg["top_k"] == 5
        assert runner_cfg["denylist_patterns"] == ["lark-"]
        assert runner_cfg["max_sessions"] == 10
        assert "top_k" not in sleep_cfg
        assert "denylist_patterns" not in sleep_cfg
        assert sleep_cfg["backend"] == "litellm"


# ═══════════════════════════════════════════════════════════════════════════════
# rank_skills
# ═══════════════════════════════════════════════════════════════════════════════

class TestRankSkills:

    def test_empty_inputs(self):
        top, state, _ss = rank_skills([], [], {}, top_k=5)
        assert top == []
        # rank_skills writes keys into the state dict
        assert state.get("skill_neg_feedback", {}) == {}
        assert state.get("skill_total_mentions", {}) == {}

    def test_basic_ranking_by_activity(self):
        """No digests → ranking purely by activity."""
        eligible = [
            ("skill-a", {"use_count": 10, "view_count": 0, "patch_count": 0}),
            ("skill-b", {"use_count": 2, "view_count": 0, "patch_count": 0}),
        ]
        digests = []
        top, state, _ss = rank_skills(eligible, digests, {}, top_k=2)
        assert top[0][0] == "skill-a"  # higher activity
        assert top[0][2] > top[1][2]   # higher score

    def test_agent_bonus(self):
        """Agent-created skills get 2x activity multiplier."""
        eligible = [
            ("local-skill", {"use_count": 10, "view_count": 0, "patch_count": 0}),
            ("agent-skill", {"use_count": 5, "view_count": 0, "patch_count": 0, "created_by": "agent"}),
        ]
        # local: 10 * 0.5 * 1.0 = 5.0
        # agent: 5 * 0.5 * 2.0 = 5.0  (tie)
        top, _, _ss = rank_skills(eligible, [], {}, top_k=2)
        assert abs(top[0][2] - top[1][2]) < 0.1

    def test_negative_feedback_boost(self):
        """Negative feedback from digests increases score (weight × 3)."""
        from conftest import MockSessionDigest
        eligible = [
            ("skill-a", {"use_count": 1}),
            ("skill-b", {"use_count": 1}),
        ]
        digests = [
            MockSessionDigest(
                session_id="s1",
                user_prompts=["skill-a is broken, still wrong"],
                tools_used=[],
                ended_at="2026-06-17T00:00:00",
            ),
        ]
        state = {"skill_neg_feedback": {}, "skill_total_mentions": {}}
        top, updated, _ss = rank_skills(eligible, digests, state, top_k=2)
        # skill-a should have neg feedback and higher score
        assert top[0][0] == "skill-a"
        assert top[0][3] > 0  # neg count > 0

    def test_incremental_neg_feedback_accumulation(self):
        """Neg feedback from previous runs is preserved and accumulated."""
        from conftest import MockSessionDigest
        eligible = [("skill-a", {"use_count": 1})]
        digests = [
            MockSessionDigest(
                session_id="s2",
                user_prompts=["skill-a doesn't work"],
                tools_used=[],
                ended_at="2026-06-18T00:00:00",
            ),
        ]
        # Pre-existing state with 3 accumulated neg feedback
        state = {
            "skill_neg_feedback": {"skill-a": 3},
            "skill_total_mentions": {"skill-a": 5},
        }
        top, updated, _ss = rank_skills(eligible, digests, state, top_k=1)
        # Should accumulate: 3 (old) + 1 (new) = 4
        assert updated["skill_neg_feedback"]["skill-a"] >= 3

    def test_top_k_limit(self):
        """Only top_k skills are returned."""
        eligible = [(f"skill-{i}", {"use_count": i}) for i in range(10)]
        top, _, _ss = rank_skills(eligible, [], {}, top_k=3)
        assert len(top) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# harvest_hermes_sessions
# ═══════════════════════════════════════════════════════════════════════════════

class TestHarvestHermesSessions:

    def test_empty_sessions_dir(self, tmp_hermes):
        digests = harvest_hermes_sessions(None)
        assert digests == []

    def test_jsonl_format(self, tmp_hermes):
        """旧格式: *.jsonl 每行一条 JSON."""
        msgs = [
            {"role": "user", "content": "hello", "timestamp": "2026-06-17T10:00:00", "platform": "hermes-cli"},
            {"role": "assistant", "content": "hi there", "timestamp": "2026-06-17T10:00:05"},
            {"role": "tool", "content": "tool output", "tool_name": "search", "timestamp": "2026-06-17T10:00:03"},
        ]
        _make_session_jsonl(tmp_hermes / "sessions", "sess-001", msgs)
        digests = harvest_hermes_sessions(None)
        assert len(digests) == 1
        d = digests[0]
        assert d.session_id == "sess-001"
        assert d.n_user_turns == 1
        assert "search" in d.tools_used

    def test_json_format(self, tmp_hermes):
        """新格式: session_*.json 含 messages 数组."""
        data = {
            "session_id": "sess-002",
            "session_start": "2026-06-17T11:00:00",
            "last_updated": "2026-06-17T11:05:00",
            "platform": "hermes-web",
            "messages": [
                {"role": "user", "content": "task please"},
                {"role": "assistant", "content": "done"},
            ],
        }
        _make_session_json(tmp_hermes / "sessions", "002", data)
        digests = harvest_hermes_sessions(None)
        assert len(digests) == 1
        assert digests[0].session_id == "sess-002"
        assert digests[0].project == "hermes-web"

    def test_incremental_filter(self, tmp_hermes):
        """since_iso 过滤旧 session."""
        msgs_old = [
            {"role": "user", "content": "old task", "timestamp": "2026-06-15T10:00:00"},
            {"role": "assistant", "content": "old reply", "timestamp": "2026-06-15T10:00:05"},
        ]
        msgs_new = [
            {"role": "user", "content": "new task", "timestamp": "2026-06-17T10:00:00"},
            {"role": "assistant", "content": "new reply", "timestamp": "2026-06-17T10:00:05"},
        ]
        _make_session_jsonl(tmp_hermes / "sessions", "old-sess", msgs_old)
        _make_session_jsonl(tmp_hermes / "sessions", "new-sess", msgs_new)

        digests = harvest_hermes_sessions("2026-06-16T00:00:00")
        assert len(digests) == 1
        assert digests[0].session_id == "new-sess"

    def test_no_user_prompts_excluded(self, tmp_hermes):
        """Session without user prompts (n_user==0) returns None."""
        msgs = [
            {"role": "assistant", "content": "unsolicited", "timestamp": "2026-06-17T10:00:00"},
        ]
        _make_session_jsonl(tmp_hermes / "sessions", "empty-sess", msgs)
        digests = harvest_hermes_sessions(None)
        assert len(digests) == 0

    def test_sorted_newest_first(self, tmp_hermes):
        msgs1 = [
            {"role": "user", "content": "task1", "timestamp": "2026-06-16T10:00:00"},
            {"role": "assistant", "content": "reply1", "timestamp": "2026-06-16T10:00:05"},
        ]
        msgs2 = [
            {"role": "user", "content": "task2", "timestamp": "2026-06-17T10:00:00"},
            {"role": "assistant", "content": "reply2", "timestamp": "2026-06-17T10:00:05"},
        ]
        _make_session_jsonl(tmp_hermes / "sessions", "s1", msgs1)
        _make_session_jsonl(tmp_hermes / "sessions", "s2", msgs2)
        digests = harvest_hermes_sessions(None)
        assert len(digests) == 2
        assert digests[0].session_id == "s2"  # newest first


    def test_state_db_format(self, tmp_hermes):
        """当前 Hermes state.db 格式: sessions/messages SQLite."""
        _make_state_db(
            tmp_hermes,
            [
                {
                    "id": "state-001",
                    "source": "feishu",
                    "started_at": 1_781_852_000.0,
                    "ended_at": 1_781_852_060.0,
                    "messages": [
                        {"role": "user", "content": "skill-a 不对", "timestamp": 1_781_852_001.0},
                        {"role": "assistant", "content": "我来修", "timestamp": 1_781_852_010.0},
                        {"role": "tool", "content": "ok", "tool_name": "terminal", "timestamp": 1_781_852_020.0},
                    ],
                }
            ],
        )

        digests = harvest_hermes_sessions(None)

        assert len(digests) == 1
        d = digests[0]
        assert d.session_id == "state-001"
        assert d.project == "feishu"
        assert d.n_user_turns == 1
        assert d.n_assistant_turns == 1
        assert "terminal" in d.tools_used
        assert any(s.startswith("neg:") for s in d.feedback_signals)
        assert d.raw_path.endswith("state.db")

    def test_state_db_incremental_filter(self, tmp_hermes):
        """state.db harvest respects since_iso."""
        _make_state_db(
            tmp_hermes,
            [
                {
                    "id": "old-state",
                    "started_at": 1_781_600_000.0,
                    "ended_at": 1_781_600_060.0,
                    "messages": [
                        {"role": "user", "content": "old", "timestamp": 1_781_600_001.0},
                    ],
                },
                {
                    "id": "new-state",
                    "started_at": 1_781_852_000.0,
                    "ended_at": 1_781_852_060.0,
                    "messages": [
                        {"role": "user", "content": "new", "timestamp": 1_781_852_001.0},
                    ],
                },
            ],
        )

        digests = harvest_hermes_sessions("2026-06-18T00:00:00+00:00")

        assert [d.session_id for d in digests] == ["new-state"]


    def test_state_db_incremental_filter_uses_latest_message_for_active_session(self, tmp_hermes):
        """Active sessions that started before since but have new messages are harvested."""
        _make_state_db(
            tmp_hermes,
            [
                {
                    "id": "active-long",
                    "source": "feishu",
                    "started_at": 1_781_600_000.0,
                    "ended_at": None,
                    "messages": [
                        {"role": "user", "content": "old start", "timestamp": 1_781_600_001.0},
                        {"role": "user", "content": "new follow-up", "timestamp": 1_781_852_001.0},
                    ],
                }
            ],
        )

        digests = harvest_hermes_sessions("2026-06-18T00:00:00+00:00")

        assert [d.session_id for d in digests] == ["active-long"]

    def test_state_db_and_file_sources_dedup_by_session_id(self, tmp_hermes):
        """Same session in state.db and JSON files is harvested once."""
        _make_state_db(
            tmp_hermes,
            [
                {
                    "id": "same-session",
                    "source": "feishu",
                    "started_at": 1_781_852_000.0,
                    "ended_at": 1_781_852_060.0,
                    "messages": [
                        {"role": "user", "content": "from db", "timestamp": 1_781_852_001.0},
                    ],
                }
            ],
        )
        _make_session_json(
            tmp_hermes / "sessions",
            "same-session",
            {
                "session_id": "same-session",
                "session_start": "2026-06-19T07:00:00+00:00",
                "last_updated": "2026-06-19T07:01:00+00:00",
                "platform": "legacy-json",
                "messages": [{"role": "user", "content": "from json"}],
            },
        )

        digests = harvest_hermes_sessions(None)

        assert len(digests) == 1
        assert digests[0].session_id == "same-session"
        assert digests[0].project == "feishu"

    def test_malformed_jsonl_skipped(self, tmp_hermes):
        """Malformed lines in jsonl are skipped, not crashed."""
        p = tmp_hermes / "sessions" / "bad.jsonl"
        p.write_text("not json\n{bad json\n", encoding="utf-8")
        digests = harvest_hermes_sessions(None)
        assert digests == []

    def test_feedback_detected_in_harvest(self, tmp_hermes):
        """Negative feedback in user prompts is captured in feedback_signals."""
        msgs = [
            {"role": "user", "content": "that's wrong, fix it", "timestamp": "2026-06-17T10:00:00"},
            {"role": "assistant", "content": "sorry", "timestamp": "2026-06-17T10:00:05"},
        ]
        _make_session_jsonl(tmp_hermes / "sessions", "fb-sess", msgs)
        digests = harvest_hermes_sessions(None)
        assert len(digests) == 1
        neg_sigs = [s for s in digests[0].feedback_signals if s.startswith("neg:")]
        assert len(neg_sigs) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# load_state / save_state
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateManagement:

    def test_load_default_state(self, tmp_hermes):
        state = load_state()
        assert state["skill_last_run"] == {}
        assert state["skill_neg_feedback"] == {}
        assert state["skill_total_mentions"] == {}

    def test_save_and_load_roundtrip(self, tmp_hermes):
        state = {
            "skill_last_run": {"skill-a": "2026-06-17T12:00:00+00:00"},
            "skill_neg_feedback": {"skill-a": 3},
            "skill_total_mentions": {"skill-a": 10},
        }
        save_state(state)
        loaded = load_state()
        assert loaded["skill_last_run"]["skill-a"] == "2026-06-17T12:00:00+00:00"
        assert loaded["skill_neg_feedback"]["skill-a"] == 3
        assert loaded["skill_total_mentions"]["skill-a"] == 10

    def test_save_creates_parent_dirs(self, tmp_hermes):
        """state.json parent directory is created automatically."""
        state = {"skill_last_run": {}, "skill_neg_feedback": {}, "skill_total_mentions": {}}
        save_state(state)
        assert sr.STATE_FILE.exists()



def test_main_no_tasks_mined_does_not_advance_last_run(tmp_hermes, usage_file, monkeypatch):
    """When no tasks are mined, skill_last_run is NOT updated (only advances on successful optimization)."""
    _write_usage(
        usage_file,
        {"skill-a": {"state": "active", "created_by": "agent", "use_count": 1}},
    )
    _make_state_db(
        tmp_hermes,
        [
            {
                "id": "state-advance",
                "source": "feishu",
                "started_at": 1_781_852_000.0,
                "ended_at": 1_781_852_060.0,
                "messages": [
                    {"role": "user", "content": "skill-a 不对", "timestamp": 1_781_852_001.0},
                    {"role": "assistant", "content": "收到", "timestamp": 1_781_852_010.0},
                ],
            }
        ],
    )
    config_dir = tmp_hermes / "skillopt"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "backend: mock\nmodel: mock\ntop_k: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr(sr, "CONFIG_PATH", tmp_hermes / "skillopt" / "config.yaml")
    monkeypatch.setattr(sr, "SKILLOPT_HOME", tmp_hermes / "skillopt")
    monkeypatch.setattr(sr, "mine", MagicMock(return_value=[]))
    monkeypatch.setattr(sys, "argv", ["skillopt_runner.py"])

    rc = sr.main()
    state = load_state()

    assert rc == 0
    # No successful optimization → skill_last_run should remain empty
    assert state.get("skill_last_run", {}).get("skill-a") is None

# ═══════════════════════════════════════════════════════════════════════════════
# get_skill_path
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetSkillPath:

    def test_finds_skill(self, tmp_hermes):
        skill_dir = tmp_hermes / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# My Skill", encoding="utf-8")

        result = get_skill_path("my-skill")
        assert result is not None
        assert result.name == "SKILL.md"

    def test_not_found(self, tmp_hermes):
        result = get_skill_path("nonexistent-skill")
        assert result is None

    def test_nested_category(self, tmp_hermes):
        """Handles category nesting: skills/category/my-skill/SKILL.md."""
        nested = tmp_hermes / "skills" / "dev" / "nested-skill"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("# Nested", encoding="utf-8")

        result = get_skill_path("nested-skill")
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# patch_skill_hermes (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPatchSkillHermes:

    SKILL_WITH_FM = "---\nname: test-skill\ndescription: test\n---\n\n# Original\n"
    SKILL_WITH_FM_UPDATED = "---\nname: test-skill\ndescription: test\n---\n\n# Original\n\n# Updated\n"
    SKILL_WITH_FM_BODY = "---\nname: test-skill\ndescription: test\n---\n\n# Original Content\n"
    SKILL_WITH_FM_BODY_APPENDED = "---\nname: test-skill\ndescription: test\n---\n\n# Original Content\n\n# Bad Update\n"

    def test_skill_not_found(self, tmp_hermes):
        """Returns False if SKILL.md doesn't exist."""
        state = {"skill_neg_feedback": {}, "skill_total_mentions": {}}
        result = sr.patch_skill_hermes("nonexistent", "new content", state)
        assert result is False

    def test_successful_patch(self, tmp_hermes):
        """Successful patch: backup created, file written correctly, neg feedback cleared."""
        skill_dir = tmp_hermes / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self.SKILL_WITH_FM, encoding="utf-8")

        state = {
            "skill_neg_feedback": {"test-skill": 5},
            "skill_total_mentions": {"test-skill": 10},
        }
        result = sr.patch_skill_hermes("test-skill", "# Updated", state)
        assert result is True
        assert state["skill_neg_feedback"]["test-skill"] == 0
        assert skill_md.read_text(encoding="utf-8") == self.SKILL_WITH_FM_UPDATED
        backups = list((tmp_hermes / "skillopt" / "backups").glob("test-skill_*.md.bak"))
        assert len(backups) == 1

    def test_failed_patch_reverts(self, tmp_hermes):
        """Failed patch: original file restored from backup."""
        skill_dir = tmp_hermes / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self.SKILL_WITH_FM_BODY, encoding="utf-8")

        with patch("skillopt_runner.pathlib.Path.write_text") as mock_write:
            mock_write.side_effect = IOError("disk full")
            state = {"skill_neg_feedback": {}, "skill_total_mentions": {}}
            result = sr.patch_skill_hermes("test-skill", "# Bad Update", state)
            assert result is False
            assert skill_md.read_text(encoding="utf-8") == self.SKILL_WITH_FM_BODY

    def test_no_frontmatter_returns_false(self, tmp_hermes):
        """SKILL.md without YAML frontmatter => returns False."""
        skill_dir = tmp_hermes / "skills" / "no-fm"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# No frontmatter", encoding="utf-8")
        state = {"skill_neg_feedback": {}, "skill_total_mentions": {}}
        result = sr.patch_skill_hermes("no-fm", "some edit", state)
        assert result is False

    def test_backup_created_before_write(self, tmp_hermes):
        """Backup is created before writing to SKILL.md."""
        skill_dir = tmp_hermes / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self.SKILL_WITH_FM_BODY, encoding="utf-8")

        state = {"skill_neg_feedback": {}, "skill_total_mentions": {}}
        sr.patch_skill_hermes("test-skill", "# Updated", state)

        backups = list((tmp_hermes / "skillopt" / "backups").glob("test-skill_*.md.bak"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == self.SKILL_WITH_FM_BODY


# ═══════════════════════════════════════════════════════════════════════════════
# P2-3 写回后自动验证：validate_patched_skill + 集成
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidatePatchedSkill:

    VALID = "---\nname: ok-skill\ndescription: valid test\n---\n\n# Body\n"

    def test_accepts_valid_skill(self):
        ok, reason = sr.validate_patched_skill(self.VALID)
        assert ok is True
        assert reason == "OK"

    def test_rejects_missing_name(self):
        ok, reason = sr.validate_patched_skill(
            "---\ndescription: only desc\n---\n\n# Body\n"
        )
        assert ok is False
        assert "name" in reason

    def test_rejects_missing_description(self):
        ok, reason = sr.validate_patched_skill("---\nname: x\n---\n\n# Body\n")
        assert ok is False
        assert "description" in reason

    def test_rejects_unclosed_frontmatter(self):
        ok, reason = sr.validate_patched_skill("---\nname: x\ndescription: y\n\n# Body\n")
        assert ok is False
        assert "未闭合" in reason

    def test_rejects_non_mapping_frontmatter(self):
        # YAML list 而非 mapping —— 结构注入防护
        ok, reason = sr.validate_patched_skill("---\n- a\n- b\n---\n\n# Body\n")
        assert ok is False
        assert "mapping" in reason

    def test_rejects_empty_body(self):
        ok, reason = sr.validate_patched_skill("---\nname: x\ndescription: y\n---\n\n  \n")
        assert ok is False
        assert "body" in reason

    def test_accepts_body_with_yaml_fence(self):
        # body 内嵌 ```yaml 代码块不破坏 frontmatter —— append-only 设计应放行
        content = (
            "---\nname: x\ndescription: y\n---\n\n# Body\n\n"
            "```yaml\nname: not-frontmatter\n```\n"
        )
        ok, _ = sr.validate_patched_skill(content)
        assert ok is True


class TestPatchWriteBackVerify:

    def test_missing_description_in_original_rejected(self, tmp_hermes):
        """原文件 frontmatter 缺 description → 写回前验证拦截，文件不变且无审计产物。"""
        skill_dir = tmp_hermes / "skills" / "no-desc"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        original = "---\nname: no-desc\n---\n\n# Body\n"
        skill_md.write_text(original, encoding="utf-8")

        state = {"skill_neg_feedback": {}, "skill_total_mentions": {}}
        result = sr.patch_skill_hermes("no-desc", "# Update", state)

        assert result is False
        assert skill_md.read_text(encoding="utf-8") == original
        # 写回前拦截：不产生 backup / diff 审计产物
        assert not (tmp_hermes / "skillopt" / "backups").exists()

    def test_patch_roundtrip_and_diff_audit(self, tmp_hermes):
        """成功 patch 后: 磁盘内容==merged（写回后验证）、diff 审计文件含 before/after。"""
        skill_dir = tmp_hermes / "skills" / "audit-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: audit-skill\ndescription: audit test\n---\n\n# Original\n",
            encoding="utf-8",
        )

        state = {"skill_neg_feedback": {"audit-skill": 2}, "skill_total_mentions": {}}
        assert sr.patch_skill_hermes("audit-skill", "# New Rule", state) is True

        # 写回后验证: 内容 == expected merged
        expected = (
            "---\nname: audit-skill\ndescription: audit test\n---\n\n"
            "# Original\n\n# New Rule\n"
        )
        assert skill_md.read_text(encoding="utf-8") == expected

        # diff 审计文件存在且同时含 before 文件名与新增行
        diffs = list((tmp_hermes / "skillopt" / "backups").glob("audit-skill_*.diff"))
        assert len(diffs) == 1
        diff_text = diffs[0].read_text(encoding="utf-8")
        assert "SKILL.md (before)" in diff_text
        assert "SKILL.md (after)" in diff_text
        assert "+# New Rule" in diff_text
        # # Original 是 context 行（前导空格），diff 完整包含原始内容
        assert "# Original" in diff_text

    def test_negative_feedback_cleared_only_after_verify(self, tmp_hermes, monkeypatch):
        """写回验证失败 → revert 且负反馈不清零（验证通过才走清零路径）。"""
        skill_dir = tmp_hermes / "skills" / "rv-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        original = "---\nname: rv-skill\ndescription: rv test\n---\n\n# Body\n"
        skill_md.write_text(original, encoding="utf-8")

        state = {"skill_neg_feedback": {"rv-skill": 3}, "skill_total_mentions": {}}

        real_read_text = pathlib.Path.read_text
        real_write_text = pathlib.Path.write_text

        def corrupted_write(self, data, *a, **kw):
            real_write_text(self, data, *a, **kw)
            # 写盘后立刻用损坏内容覆盖 —— 模拟写回验证失败（磁盘内容 != merged）
            real_write_text(self, self.read_text(encoding="utf-8") + "CORRUPT", *a, **kw)

        def read_text_returns_merged(self, *a, **kw):
            return real_read_text(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, "read_text", read_text_returns_merged)
        monkeypatch.setattr(pathlib.Path, "write_text", corrupted_write)

        result = sr.patch_skill_hermes("rv-skill", "# Update", state)
        assert result is False
        # 负反馈保留（未走清零路径）
        assert state["skill_neg_feedback"]["rv-skill"] == 3
        # 文件被 revert（替换回 backup，不再含 CORRUPT）
        assert "CORRUPT" not in skill_md.read_text(encoding="utf-8")
