"""Safety tests for clustering weekly cron wrapper."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT_ROOT / "scripts" / "cron_wrapper.sh"


def _script_text() -> str:
    return WRAPPER.read_text(encoding="utf-8")


def test_cron_wrapper_defaults_to_dry_run() -> None:
    """Weekly wrapper must default to dry-run rather than destructive apply."""
    text = _script_text()

    assert 'MODE="dry-run"' in text
    assert 'run(apply=False, dry_run=True' in text


def test_cron_wrapper_apply_requires_explicit_confirmation_env() -> None:
    """Destructive apply mode must require a confirmation env variable."""
    text = _script_text()

    assert "--apply" in text
    assert "CONFIRM_APPLY" in text
    assert "I_UNDERSTAND_THIS_WRITES_HINDSIGHT" in text
    assert "run(apply=True, dry_run=False" in text
