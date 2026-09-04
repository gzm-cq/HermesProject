"""Tests for the daily memory-cleanup cron wrapper."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_wrapper_exists_and_applies() -> None:
    """The scheduled wrapper must explicitly pass --apply to memory-cleanup."""
    wrapper = PROJECT_ROOT / "daily_dryrun.sh"

    assert wrapper.exists()
    text = wrapper.read_text(encoding="utf-8")

    assert "bash run.sh --vote 1 --apply" in text


def test_daily_wrapper_enables_capacity_guard() -> None:
    """2026-09-04: wrapper must enable cold eviction + capacity guard env vars."""
    wrapper = PROJECT_ROOT / "daily_dryrun.sh"
    text = wrapper.read_text(encoding="utf-8")

    assert "MEMORY_CLEANUP_COLD_MEMORY_EVICTION" in text
    assert "MEMORY_CLEANUP_COLD_MEMORY_DAYS" in text
    assert "MEMORY_CLEANUP_MEMORY_CAPACITY_SAFE_RATIO" in text
    assert "0.85" in text
