#!/usr/bin/env python3
"""验证管线清理后的全流程：search_refs→synthesize→content_gen

用法:
    python3 scripts/verify_pipeline.py [topic] [--test]
"""
from __future__ import annotations

import sys, time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from ai_report.core.report_goal_helpers import load_report_goal

topic = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "智能制造企业转型建设规划"
is_test = "--test" in sys.argv
output_base = Path("test_outputs") if is_test else Path("reports")

# 加载已确认 goal
goal = load_report_goal(topic)
if not goal:
    print(f"❌ 未找到 goal: reports/{topic}/report_goal.json")
    sys.exit(1)
print(f"📂 加载已确认目标: {goal.get('title', '')}")
print(f"   角色: {goal.get('writing_role', {}).get('role', '')}")
print()

# 跑全管线
from ai_report.core.orchestrator import ReportWorkflowOrchestrator
orch = ReportWorkflowOrchestrator()
t0 = time.time()
result = orch.run(
    topic=topic,
    report_type="tech",
    language="zh",
    output_dir=output_base / topic.replace(" ", "_")[:40],
    report_goal=goal,
    skip_quality=True,  # 跳过质量闭环（更快验证核心流程）
    skip_evaluation=True,
)
elapsed = time.time() - t0
print(f"⏱ 耗时: {elapsed:.0f}s")
print(f"✅ 输出: {result.get('output_path', 'N/A')}")
