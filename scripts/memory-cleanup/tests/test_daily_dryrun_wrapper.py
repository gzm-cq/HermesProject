"""Tests for the daily memory-cleanup cron wrapper."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_wrapper_exists_and_applies() -> None:
    """The scheduled wrapper must explicitly pass --apply to memory-cleanup."""
    wrapper = PROJECT_ROOT / "daily_dryrun.sh"

    assert wrapper.exists()
    text = wrapper.read_text(encoding="utf-8")

    assert "bash run.sh --vote 1 --apply" in text
