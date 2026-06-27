"""Backward-compatible CLI shim.

Provides `build_parser()` for tests that expect argparse interface.
Actual CLI uses typer in ai_report.cli.
"""
from __future__ import annotations

import argparse
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    """Build an argparse parser for backward compatibility."""
    parser = argparse.ArgumentParser(prog="ai-report")
    subparsers = parser.add_subparsers(dest="command")

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("topic")
    plan_parser.add_argument("--type", "-t", dest="type", default="tech")

    subparsers.add_parser("help")

    return parser
