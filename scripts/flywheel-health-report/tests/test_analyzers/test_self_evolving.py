"""Self-Evolving analyzer 离线回归测试（不依赖生产环境）。

覆盖：no_data 兜底、正常写回、停滞 P1、精炼未落地 P1、ledger best-effort。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flywheel_health_report.analyzers.self_evolving import analyze_self_evolving

_SE_BLOCK = (
    "<!-- SE-APPLIED id={tid} ts={ts} -->\n"
    "### 🔄 Self-Evolving 修正（task {tid}，待人工复核）\n\n"
    "refined content here\n"
    "<!-- /SE-APPLIED -->\n"
)


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    home.mkdir()
    return home


def test_no_data_returns_empty(tmp_path: Path):
    # 用一个确实不存在的 home 目录
    home = tmp_path / "nope" / "hermes"
    issues, metrics, trend = analyze_self_evolving(home)
    assert metrics.get("status") == "no_data"
    assert issues == []
    assert trend == {}


def test_no_data_empty_dir(tmp_path: Path):
    home = _make_home(tmp_path)
    issues, metrics, trend = analyze_self_evolving(home)
    assert metrics.get("status") == "no_data"


def test_active_writeback(tmp_path: Path):
    home = _make_home(tmp_path)
    out = home / "self-evolving" / "output"
    out.mkdir(parents=True)
    (out / "foo_1.json").write_text(json.dumps({
        "skill": "foo", "task_id": "1", "auto_applied": True,
        "refined_content": "x",
    }), encoding="utf-8")
    (out / "bar_2.json").write_text(json.dumps({
        "skill": "bar", "task_id": "2", "auto_applied": False,
        "refined_content": "y",
    }), encoding="utf-8")

    skills = home / "skills" / "foo-skill"
    skills.mkdir(parents=True)
    ts = datetime.now(timezone.utc).isoformat()
    (skills / "SKILL.md").write_text(
        "---\nname: foo\n---\n\n" + _SE_BLOCK.format(tid="1", ts=ts),
        encoding="utf-8",
    )

    issues, metrics, _ = analyze_self_evolving(home)
    assert metrics["status"] == "active"
    assert metrics["output_files"] == 2
    assert metrics["applied_from_output"] == 1
    assert metrics["refined_not_applied"] == 1
    assert metrics["se_applied_skill_count"] == 1
    assert metrics["se_applied_skills"] == ["foo-skill"]
    assert metrics["last_se_applied"] is not None
    # 正常写回不应报 P1
    assert [i for i in issues if i["severity"] == "P1"] == []


def test_stale_triggers_p1(tmp_path: Path):
    home = _make_home(tmp_path)
    out = home / "self-evolving" / "output"
    out.mkdir(parents=True)
    f = out / "foo_1.json"
    f.write_text(json.dumps({"skill": "foo", "task_id": "1", "auto_applied": True,
                             "refined_content": "x"}), encoding="utf-8")
    # 把文件 mtime 推到 48h 前（超过 se_stale_hours=36）
    old = (datetime.now() - timedelta(hours=48)).timestamp()
    import os
    os.utime(f, (old, old))

    issues, metrics, _ = analyze_self_evolving(home)
    p1 = [i for i in issues if i["severity"] == "P1"]
    assert p1, "停滞应触发 P1"
    assert "能力飞轮" == p1[0]["flywheel"]


def test_ledger_best_effort(tmp_path: Path):
    home = _make_home(tmp_path)
    ledger = home / "data" / "flywheel"
    ledger.mkdir(parents=True)
    (ledger / "ledger.jsonl").write_text(json.dumps({
        "event": "self_evolving", "applied": 3, "blocked": 1, "status": "ok",
    }) + "\n", encoding="utf-8")

    issues, metrics, _ = analyze_self_evolving(home)
    # ledger 单独存在但无 output/SE-APPLIED 时仍算 active（有信号）
    assert metrics["ledger_deployed"] is True
    assert metrics["ledger_events"] == 1
    assert metrics["ledger_applied"] == 3
    assert metrics["ledger_blocked"] == 1
