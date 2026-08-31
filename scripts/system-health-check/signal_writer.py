#!/usr/bin/env python3
"""Write structured health-check signal JSON to SIGNAL_FILE_PATH.

Reads health_check_raw from stdin, signal parameters from environment.
"""
import json
import os
import sys


def parse_extra(extra_str):
    """Parse SIGNAL_EXTRA entries of form 'name:status(detail)'.

    Entries separated by whitespace (matching bash EXTRA_CHECKS format).
    Returns list of dicts.
    """
    checks = []
    if not extra_str:
        return checks
    for part in extra_str.split():
        part = part.strip()
        if not part:
            continue
        if "(" in part and ")" in part:
            head, tail = part.rsplit("(", 1)
            detail = tail.rstrip(")")
        else:
            head, detail = part, ""
        if ":" in head:
            name, status = head.split(":", 1)
        else:
            name, status = head, ""
        checks.append({
            "name": name.strip(),
            "status": status.strip(),
            "detail": detail.strip(),
        })
    return checks


def names_to_services(names_str, svc_map):
    """Convert whitespace-separated service names to list of {name, unit}.

    Matches bash FAILED_SERVICES format (space-separated names).
    """
    services = []
    if not names_str:
        return services
    for name in names_str.split():
        name = name.strip()
        if not name:
            continue
        services.append({"name": name, "unit": svc_map.get(name, "")})
    return services


def main():
    raw_text = sys.stdin.read()
    try:
        health_check_raw = json.loads(raw_text) if raw_text.strip() else {}
    except json.JSONDecodeError:
        health_check_raw = {"raw": raw_text}

    signal_failed = os.environ.get("SIGNAL_FAILED", "")
    signal_warn = os.environ.get("SIGNAL_WARN", "")
    signal_extra = os.environ.get("SIGNAL_EXTRA", "")
    infra_status = os.environ.get("SIGNAL_INFRA_STATUS", "")
    timestamp = os.environ.get("SIGNAL_NOW_ISO", "")
    file_path = os.environ.get("SIGNAL_FILE_PATH", "/tmp/health_signal.json")
    cron_errors = os.environ.get("SIGNAL_CRON_ERRORS", "")

    svc_map = {
        "hermes": "hermes-gateway",
        "bifrost": "bifrost",
        "hindsight": "hindsight-daemon",
        "sag": "sag",
        "postgres": "docker",
        "dashboard": "hermes-dashboard",
    }

    failed_services = names_to_services(signal_failed, svc_map)
    warn_services = names_to_services(signal_warn, svc_map)
    extra_checks = parse_extra(signal_extra)

    ok_statuses = {"ok", "active"}
    has_bad_extra = any(c["status"] not in ok_statuses for c in extra_checks)
    all_ok = (infra_status == "ok" and not failed_services and not has_bad_extra)

    signal = {
        "timestamp": timestamp,
        "all_ok": all_ok,
        "needs_agent": not all_ok,
        "infra_status": infra_status,
        "failed_services": failed_services,
        "warn_services": warn_services,
        "extra_checks": extra_checks,
        "cron_errors": cron_errors,
        "svc_map": svc_map,
        "health_check_raw": health_check_raw,
    }

    with open(file_path, "w") as f:
        json.dump(signal, f, indent=2, ensure_ascii=False)

    print(file_path)


if __name__ == "__main__":
    main()
