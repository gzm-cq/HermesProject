"""统一反馈账本（Unified Flywheel Ledger）。

背景（见 docs/architecture/flywheel-full-optimization-2026-08-11.md F-1）：
  数据飞轮写 daily-summary-history.jsonl；skillopt 写自己的 state.json；
  dream-synth 写 SAG；KT-builder 写知识树 DB。**三路各自独立，无跨循环关联**。
  本模块提供一个零依赖（仅标准库）的统一事件追加入口，所有循环在关键节点
  append 事件到 HERMES_HOME/data/flywheel/ledger.jsonl，构成单一健康视图 +
  跨循环因果关联（例如「改写后 neg 是否真的降」「SAG 生产/消费质量是否联动」）。

使用方式（各独立脚本通过向上查找 hermes_common 包导入，见各调用方 bootstrap）：
    append_ledger_event("kn_judge", {"relevant_rate_sag": 0.82, ...})

事件类型约定：
  kn_judge          — KN LLM Judge 各路 mask 指标（h/kt/sag 相关性）
  skillopt_patch    — SkillOpt 改写 SKILL.md（skill / neg 前后 / ts）
  dream_promote     — dream-synth 晋升（count / threshold / sag_rate）
  kt_build          — 知识树构建条目数
  self_evolving     — Self-Evolving Revision→Refinement 运行
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _ledger_path(home: Optional[str] = None) -> Path:
    hermes_home = home or os.environ.get("HERMES_HOME") or "/root/.hermes"
    return Path(hermes_home) / "data" / "flywheel" / "ledger.jsonl"


def append_ledger_event(
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    home: Optional[str] = None,
) -> bool:
    """追加一条账本事件（JSONL，单行）。任何异常都静默返回 False，不阻断主流程。

    Args:
        event_type: 事件类型（见模块 docstring 约定）。
        payload: 附加字段 dict（会被展平进同一行）。
        home: 可选显式 HERMES_HOME；默认取环境变量或 /root/.hermes。
    Returns:
        True 表示写入成功；False 表示失败（调用方应忽略，不影响主流程）。
    """
    rec: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
    }
    if payload:
        rec.update(payload)
    try:
        p = _ledger_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def recent_skill_patch_trend(
    skill_name: str,
    *,
    window: int = 10,
    neg_threshold: int = 3,
    home: Optional[str] = None,
) -> "tuple[int, int, float]":
    """读取账本中某 skill 近期 ``skillopt_patch`` 事件，返回 ``(次数, 高负向前置次数, 高负向率)``。

    用于 F-1 反向门控：若某 skill 近期多次在「仍携带较重负反馈(``neg_before`` >= ``neg_threshold``)
    时才被打补丁」，说明自动改写未能根治、陷入反复打补丁的循环，应暂停自动 patch 转人工审阅。

    Returns:
        ``(count, high_neg_count, ratio)``；任何异常返回 ``(0, 0, 0.0)``（best-effort，不阻断主流程）。
    """
    try:
        p = _ledger_path(home)
        if not p.exists():
            return (0, 0, 0.0)
        events: list[dict] = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("event") != "skillopt_patch":
                    continue
                if rec.get("skill") != skill_name:
                    continue
                events.append(rec)
        recent = events[-window:]
        count = len(recent)
        if count == 0:
            return (0, 0, 0.0)
        high_neg = 0
        for rec in recent:
            nb = rec.get("neg_before")
            if isinstance(nb, int) and nb >= neg_threshold:
                high_neg += 1
        ratio = high_neg / count
        return (count, high_neg, ratio)
    except Exception:
        return (0, 0, 0.0)
