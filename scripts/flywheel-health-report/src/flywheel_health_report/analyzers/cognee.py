"""cognee.py — Cognee MCP 轻量连通性健康检查。

黑盒检查：不参与 auto-tuner 调参（不写任何 auto-tuner feedback 字段）。
仅检查「能否连通并召回」四要素：
  1. config.yaml mcp_servers.cognee 是否注册
  2. wrapper 脚本是否可执行
  3. wrapper --help 能否启动（验证 PYTHONPATH / 依赖完好）
  4. cognee 数据目录是否非空（"返回非空"——已有索引数据可召回）
进程存活仅作 info（gateway 惰性拉起，不在 ≠ 故障）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - 防御性，config 解析失败则跳过
    yaml = None


def _read_cognee_cfg(home: Path) -> dict:
    cfg_path = home / "config.yaml"
    if not cfg_path.is_file() or yaml is None:
        return {}
    try:
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _find_data_dir(home: Path) -> Path | None:
    """探测 cognee 实际数据目录（多候选，兼容包内/默认 home）。"""
    candidates = [
        home / "cognee-mcp-pkg" / "cognee" / ".cognee_system",
        Path.home() / ".cognee_system",
        home / ".cognee_system",
    ]
    for d in candidates:
        if d.is_dir():
            return d
    return None


def analyze_cognee_health(home: Path) -> tuple[list[dict], dict, dict]:
    """Cognee MCP 轻量连通性健康检查（飞轮跨科语义召回依赖项）。

    返回 (issues, metrics, trend)，issues 并入 report 的 all_issues 后
    会自动出现在 P0/P1 表格；不写 auto-tuner 反馈字段，故不参与调参。
    """
    issues: list[dict] = []
    m: dict[str, Any] = {}
    trend: dict[str, Any] = {}

    cfg = _read_cognee_cfg(home)
    mcp = (cfg.get("mcp_servers") or {}).get("cognee") or {}
    registered = bool(mcp)
    m["registered"] = registered
    if not registered:
        # cognee 已于 2026-08-30 有意退役（neo4j 后端停用、KN 无 cognee 召回路、recall 挂起）
        # → "未注册"是设计状态而非故障，不产生 issue，仅记录退役标记供 report 渲染。
        m["retired"] = True
        m["retire_note"] = (
            "2026-08-30 有意移除：SAG 已覆盖横跳、KT 跨域边已建、"
            "KN 四路召回无 cognee 路、neo4j 8-25 主动停用、recall 45s 挂起"
        )
        return issues, m, trend

    cmd = mcp.get("command") or ""
    wrapper = Path(cmd)
    m["wrapper_path"] = cmd
    m["wrapper_exists"] = wrapper.is_file()
    m["wrapper_executable"] = bool(cmd) and wrapper.is_file() and os.access(str(wrapper), os.X_OK)
    if not m["wrapper_executable"]:
        issues.append({
            "severity": "P1",
            "flywheel": "Cognee",
            "desc": "cognee wrapper 脚本缺失或不可执行",
            "detail": f"command={cmd}",
        })
        return issues, m, trend

    # 启动能力（验证 PYTHONPATH / 依赖完好，不真正拉起 MCP 服务）
    try:
        proc = subprocess.run([str(wrapper), "--help"], capture_output=True, text=True, timeout=60)
        m["startup_ok"] = proc.returncode == 0
        if proc.returncode != 0:
            issues.append({
                "severity": "P1",
                "flywheel": "Cognee",
                "desc": "cognee wrapper 启动失败 (--help 非零退出)",
                "detail": (proc.stderr or proc.stdout or "")[:200],
            })
    except Exception as e:
        m["startup_ok"] = False
        issues.append({
            "severity": "P1",
            "flywheel": "Cognee",
            "desc": f"cognee wrapper 启动异常: {type(e).__name__}",
            "detail": str(e)[:200],
        })

    # 数据非空（"返回非空"——已有索引数据可召回）
    data_dir = _find_data_dir(home)
    if data_dir is None:
        m["data_dir"] = "NOT_FOUND"
        m["data_nonempty"] = False
        issues.append({
            "severity": "P1",
            "flywheel": "Cognee",
            "desc": "cognee 数据目录不存在",
            "detail": "尚无索引数据，跨科召回将返回空",
        })
    else:
        m["data_dir"] = str(data_dir)
        nonempty = False
        try:
            for p in data_dir.rglob("*"):
                if p.is_file() and p.stat().st_size > 0:
                    nonempty = True
                    break
        except Exception:
            pass
        m["data_nonempty"] = nonempty
        if not nonempty:
            issues.append({
                "severity": "P1",
                "flywheel": "Cognee",
                "desc": "cognee 数据目录为空（无索引数据）",
                "detail": "跨科语义召回将返回空结果",
            })

    # 进程存活（info，不报错：gateway 惰性拉起，不在 ≠ 故障）
    try:
        proc = subprocess.run(["pgrep", "-f", "cognee-mcp"], capture_output=True, text=True, timeout=10)
        m["process_alive"] = bool(proc.stdout.strip())
    except Exception:
        m["process_alive"] = None

    return issues, m, trend
