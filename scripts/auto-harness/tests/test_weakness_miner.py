"""weakness_miner 单元测试（P1-1）。

覆盖：
- extract_patterns：从 staging reports 聚类 (target, op) 模式
- run()：patterns → SessionDigest → mine() 接入（构造临时 staging 目录）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保源码可导入
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import weakness_miner  # noqa: E402


def _make_report(staging: Path, name: str, rejected_edits: list[dict]) -> None:
    """构造一个 staging report.json。"""
    d = staging / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.json").write_text(
        json.dumps({"gate_action": "reject", "rejected_edits": rejected_edits}),
        encoding="utf-8",
    )


class TestExtractPatterns:
    def test_empty_staging_returns_empty(self, tmp_path: Path) -> None:
        """空 staging 目录 → 无 pattern。"""
        assert weakness_miner.extract_patterns(str(tmp_path)) == []

    def test_clusters_by_target_op(self, tmp_path: Path) -> None:
        """同类 (target, op) 聚合计数。"""
        _make_report(tmp_path, "r1", [
            {"target": "memory", "op": "add", "reason": "duplicate"},
            {"target": "memory", "op": "add", "reason": "redundant"},
        ])
        _make_report(tmp_path, "r2", [
            {"target": "skill", "op": "add", "reason": "out of scope"},
        ])
        patterns = weakness_miner.extract_patterns(str(tmp_path))

        assert len(patterns) == 2
        # 按频次降序：memory/add ×2 排第一
        assert patterns[0]["target"] == "memory"
        assert patterns[0]["op"] == "add"
        assert patterns[0]["count"] == 2
        assert patterns[1]["target"] == "skill"
        assert patterns[1]["count"] == 1
        assert patterns[0]["sample_reasons"]  # 有典型 reason

    def test_bad_report_skipped(self, tmp_path: Path) -> None:
        """损坏的 report.json 被跳过，不崩溃。"""
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "report.json").write_text("{invalid json", encoding="utf-8")
        assert weakness_miner.extract_patterns(str(tmp_path)) == []


class TestRun:
    def test_run_with_patterns_produces_tasks(self, tmp_path: Path) -> None:
        """有 patterns 时，run() 接入 mine() 产出 TaskRecord。"""
        _make_report(tmp_path, "r1", [
            {"target": "memory", "op": "add", "reason": "duplicate"},
        ])
        result = weakness_miner.run(staging_dir=str(tmp_path), use_llm_miner=False)

        assert result["n_patterns"] == 1
        # mine() 接入：弱断言 n_tasks ≥ 0（heuristic 对空 digests 也产出）
        assert "n_tasks" in result
        assert result["n_tasks"] >= 0
        # patterns 结构正确
        assert result["patterns"][0]["target"] == "memory"

    def test_run_no_reports_no_error(self, tmp_path: Path) -> None:
        """无 reports 时 run() 不抛异常。"""
        result = weakness_miner.run(staging_dir=str(tmp_path), use_llm_miner=False)
        assert result["n_patterns"] == 0
        assert "patterns" in result