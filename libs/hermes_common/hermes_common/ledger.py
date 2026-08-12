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
