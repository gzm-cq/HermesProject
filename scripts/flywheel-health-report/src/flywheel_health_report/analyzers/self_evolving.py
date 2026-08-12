"""self_evolving.py — 能力飞轮 / Self-Evolving 健康检查。

监控 Self-Evolving 自动写回闭环（F-5 + B）的运行与产出：

  - 驱动产出：HERMES_HOME/self-evolving/output/<skill>_<task_id>.json
      * auto_applied=True  → 声明已写回对应 SKILL.md
      * refined_content 非空但 auto_applied=False → 精炼产出但未落地（dry-run 或写回被拒）
  - 实际写回证据：扫描 HERMES_HOME/skills/**/SKILL.md 中的 SE-APPLIED 块
      * <!-- SE-APPLIED id=<task_id> ts=<iso> --> … <!-- /SE-APPLIED -->
      * 这是「是否真的进化了 skill」的权威信号（比 output JSON 更可靠）
  - 统一账本（F-1，best-effort）：HERMES_HOME/data/flywheel/ledger.jsonl
      * event=="self_evolving" 的事件，聚合 applied/blocked 计数
      * 注意：生产机当前 ledger.py 未随 hermes-common 部署，账本可能为空/缺失，
        此时仅作信息提示，不报错。

返回 (issues, metrics, trend)，与 report.py 其余 analyzer 接口一致。
任何解析异常均兜底为 no_data，绝不让健康报告整体失败。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import TH, SELF_EVOLVING_OUTPUT_SUBPATH, LEDGER_SUBPATH

# <!-- SE-APPLIED id=<task_id> ts=<iso> --> … <!-- /SE-APPLIED -->
_SE_BLOCK_RE = re.compile(
    r'<!--\s*SE-APPLIED\s+id=([^>\s]+).*?ts=([0-9T:+\-\.Z]+).*?-->.*?<!--\s*/SE-APPLIED\s*-->',
    re.DOTALL,
)
# 仅匹配 ts（用于从块文本里提取时间戳）
_SE_TS_RE = re.compile(r'ts=([0-9T:+\-\.Z]+)')


def _parse_iso(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def analyze_self_evolving(home: Path) -> tuple[list[dict], dict, dict]:
    """分析 Self-Evolving 闭环运行与写回情况。"""
    home = Path(home)
    out_dir = home / SELF_EVOLVING_OUTPUT_SUBPATH
    skills_dir = home / "skills"
    ledger_path = home / LEDGER_SUBPATH

    # === 1) 驱动产出 JSON ===
    output_files: list[Path] = []
    if out_dir.is_dir():
        output_files = sorted(out_dir.glob("*.json"))

    processed = 0
    applied_from_output = 0
    refined_not_applied = 0
    last_run_dt: datetime | None = None
    for f in output_files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        processed += 1
        if rec.get("auto_applied"):
            applied_from_output += 1
        elif rec.get("refined_content"):
            refined_not_applied += 1
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        if last_run_dt is None or dt > last_run_dt:
            last_run_dt = dt

    # === 2) SKILL.md 中的 SE-APPLIED 块（权威写回证据）===
    se_applied_skills: set[str] = set()
    se_ts_list: list[datetime] = []
    if skills_dir.is_dir():
        for skill_md in skills_dir.rglob("SKILL.md"):
            try:
                text = skill_md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in _SE_BLOCK_RE.finditer(text):
                tid = m.group(1)
                tsm = _SE_TS_RE.search(m.group(0))
                if tsm:
                    parsed = _parse_iso(tsm.group(1))
                    if parsed:
                        se_ts_list.append(parsed)
                # skill 名取相对 skills/ 的父目录
                try:
                    rel = skill_md.relative_to(skills_dir)
                    se_applied_skills.add(str(rel.parent))
                except ValueError:
                    se_applied_skills.add(tid)

    # === 3) 统一账本（best-effort）===
    ledger_events = 0
    ledger_applied = 0
    ledger_blocked = 0
    ledger_deployed = ledger_path.is_file()
    if ledger_deployed:
        try:
            for line in ledger_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("event") == "self_evolving" or ev.get("source") == "self_evolving":
                    ledger_events += 1
                    ledger_applied += int(ev.get("applied", 0) or 0)
                    ledger_blocked += int(ev.get("blocked", 0) or 0)
        except (OSError, UnicodeDecodeError):
            ledger_deployed = False

    # 无任何信号 → 视为尚未运行
    if processed == 0 and not se_applied_skills and ledger_events == 0:
        return [], {"status": "no_data"}, {}

    # 最近一次实际写回时间
    last_se_applied_dt = max(se_ts_list) if se_ts_list else None

    issues: list[dict] = []
    now = datetime.now(timezone.utc)

    if last_run_dt and (now - last_run_dt).total_seconds() > TH["se_stale_hours"] * 3600:
        hours = (now - last_run_dt).total_seconds() / 3600
        issues.append({
            "severity": "P1",
            "flywheel": "能力飞轮",
            "desc": f"Self-Evolving 已 {hours:.0f} 小时未产出（疑似停滞）",
            "detail": f"最近一次产出: {last_run_dt.isoformat()}，阈值 {TH['se_stale_hours']}h（每日 17:30 调度）",
        })

    # 精炼产出大量但未落地：可能是写回被安全护栏拒绝（blocked）或 dry-run 未启用 --auto-apply
    if refined_not_applied > 0 and se_applied_skills and refined_not_applied >= len(se_applied_skills) * 2:
        issues.append({
            "severity": "P1",
            "flywheel": "能力飞轮",
            "desc": f"Self-Evolving 精炼产出 {refined_not_applied} 项但未写回 SKILL.md",
            "detail": "可能原因：--auto-apply 未启用，或写回被 HARD_BLOCK 安全护栏拦截（见 ledger.blocked）。建议检查调度参数与安全日志。",
        })

    metrics = {
        "status": "active",
        "output_files": processed,
        "applied_from_output": applied_from_output,
        "refined_not_applied": refined_not_applied,
        "last_run": last_run_dt.isoformat() if last_run_dt else None,
        "se_applied_skill_count": len(se_applied_skills),
        "se_applied_skills": sorted(se_applied_skills),
        "last_se_applied": last_se_applied_dt.isoformat() if last_se_applied_dt else None,
        "ledger_deployed": ledger_deployed,
        "ledger_events": ledger_events,
        "ledger_applied": ledger_applied,
        "ledger_blocked": ledger_blocked,
    }

    trend: dict = {}
    return issues, metrics, trend
