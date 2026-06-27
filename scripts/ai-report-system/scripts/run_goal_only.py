#!/usr/bin/env python3
"""
第一步：单独运行 goal 提取 + 保存。

用法:
    python3 scripts/run_goal_only.py [topic]

输出: reports/<topic>/report_goal.json

你可以反复跑直到满意，确认后第二步再进排程。
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from ai_report.graph.report_graph import run_goal_definition
from ai_report.core.report_goal_helpers import save_report_goal

topic = sys.argv[1] if len(sys.argv) > 1 else None
if not topic:
    print("❌ 用法: python3 scripts/run_goal_only.py <topic>")
    sys.exit(1)

# 1. 加载源文档
source_path = Path(f"data/source_{topic}.md")
if not source_path.exists():
    print(f"❌ 未找到源文档: {source_path}")
    print(f"   请将源文档保存为 data/source_{topic}.md")
    sys.exit(1)

source = source_path.read_text(encoding="utf-8")
print(f"📄 源文档: {source_path.name} ({len(source)} chars)")

# 2. 提取 goal（只跑 define_goal）
print(f"\n🚀 提取报告目标 + 写作角色...")
goal = run_goal_definition(topic=topic, source_content=source)

# 3. 展示完整结果
print(f"\n{'=' * 60}")
print("📋 报告目标摘要")
print(f"{'=' * 60}")
for k in ("title", "purpose", "target_audience", "overall_strategy"):
    v = goal.get(k, "")
    print(f"  {k}: {str(v)[:100]}")
print(f"\n写作角色: {goal.get('writing_role', {}).get('role', 'N/A')}")
print(f"  基调: {goal.get('writing_role', {}).get('tone', 'N/A')}")

# 4. 保存
save_report_goal(topic, goal)
print(f"\n💾 已保存至: reports/{topic}/report_goal.json")
