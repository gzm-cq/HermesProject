"""cli.py — 主入口（argparse）。

从 flywheel-health-report.py L2153-2184 搬入。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import DEFAULT_HERMES_HOME, OUTPUT_SUBPATH
from .report import generate_report


def main():
    parser = argparse.ArgumentParser(description="Flywheel Health Report Generator (v2)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode: print report to stdout, no file written",
    )
    parser.add_argument(
        "--home",
        default=DEFAULT_HERMES_HOME,
        help=f"Hermes home path (default: {DEFAULT_HERMES_HOME})",
    )
    args = parser.parse_args()

    home = Path(args.home)
    report, p0_issues = generate_report(home, dry_run=args.dry_run)

    print(report)

    if args.dry_run:
        print("\n[DRY-RUN] No file written.")
    else:
        output_dir = home / OUTPUT_SUBPATH
        output_dir.mkdir(parents=True, exist_ok=True)
        today_cn = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        output_path = output_dir / f"flywheel-report-{today_cn}.md"
        output_path.write_text(report, encoding="utf-8")
        print(f"\n[Report saved to: {output_path}]")

    sys.exit(1 if p0_issues else 0)


if __name__ == "__main__":
    main()
