"""analyze_skill_usage 单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flywheel_health_report.analyzers.skill import analyze_skill_usage


# ========== analyze_skill_usage ==========

class TestAnalyzeSkillUsage:
    """测试 Skill 使用情况分析。"""

    def test_no_file_returns_no_data(self, tmp_path: Path) -> None:
        issues, metrics, _ = analyze_skill_usage(tmp_path / "missing.json",
                                                 datetime.now(timezone.utc))
        assert metrics.get("status") == "no_data"

    def test_normal_usage(self, tmp_path: Path) -> None:
        """正常 usage 数据应统计 active/used/never_used。"""
        usage_path = tmp_path / ".usage.json"
        # analyze_skill_usage 直接读取 data dict（key=skill name, value=skill dict）
        usage_path.write_text(json.dumps({
            "skill-a": {"state": "active", "use_count": 5, "last_used_at": "2026-07-09T10:00:00Z"},
            "skill-b": {"state": "active", "use_count": 1, "last_used_at": "2026-06-01T10:00:00Z"},
            "skill-c": {"state": "active", "use_count": 0, "last_used_at": None},
        }), encoding="utf-8")
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        issues, metrics, _ = analyze_skill_usage(usage_path, now)
        # 正常情况返回的 metrics 不含 "status" 键
        assert "status" not in metrics
        assert metrics["total_skills"] == 3
        # skill-c 从未使用
        assert metrics["never_used_count"] >= 1
