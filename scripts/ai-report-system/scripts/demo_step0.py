#!/usr/bin/env python3
"""Step 0 演示：加载已确认 goal → 跑管线

用法:
    python3 scripts/demo_step0.py [topic] [--test]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from ai_report.core.report_goal_helpers import load_report_goal

topic = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "央企智能化转型建设规划"
is_test = "--test" in sys.argv
output_base = Path("test_outputs") if is_test else Path("reports")

# Step 0: 加载已确认的 goal
goal = load_report_goal(topic)
if not goal:
    print(f"❌ 未找到 goal: reports/{topic}/report_goal.json")
    sys.exit(1)
print(f"📂 加载已确认目标: {goal.get('title', '')}")
print(f"   角色: {goal.get('writing_role', {}).get('role', '')}")
print()

# Step 1-5: 编排器跑管线（跳过 define_goal）
from ai_report.core.orchestrator import ReportWorkflowOrchestrator
orch = ReportWorkflowOrchestrator()
result = orch.run(
    topic=topic,
    report_type="tech",
    language="zh",
    output_dir=output_base / topic.replace(" ", "_")[:40],
    report_goal=goal,
)

print(f"✅ 管线完成: {result.get('output_path', 'N/A')}")
