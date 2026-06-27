#!/usr/bin/env python3
"""
StateGraph 规划管线独立测试 — 只测 5 节点规划层。
不涉及内容生成、图表渲染、评估，预计 3 次 LLM 调用 ≈ 90s。

用法: cd /mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system && timeout 180 python3 scripts/test_stategraph.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# 只需要加项目根路径，用 ai_report.graph 完整路径导入
# 这样 src/__init__.py 顶级包可被识别，相对导入 ..hermes_tools 正常工作
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from ai_report.graph.report_graph import run_planning, run_goal_definition


def run_goal_phase(topic: str, source: str) -> dict:
    """只跑 Node 1: define_goal，返回 report_goal"""
    print("🚀 [Phase 1] 提取报告目标 + 写作角色...")
    start = time.time()
    goal = run_goal_definition(topic=topic, source_content=source)
    elapsed = time.time() - start
    print(f"  完成 ({elapsed:.1f}s)")
    return goal


def run_full_pipeline(topic: str, source: str, goal: dict | None = None) -> dict:
    """跑剩余管线（search_refs → synthesize → curate → prompt_review）"""
    mode = "（使用已确认目标，跳过 define_goal）" if goal else "（全流程 5 节点）"
    print(f"🚀 [Phase 2] 启动规划管线 {mode}")
    print("   2. search_refs     → web_search + web_extract 搜索参考大纲")
    print("   3. synthesize      → LLM 合成层级章节结构")
    print("   4. curate          → 按意图筛选素材（纯逻辑）")
    print("   5. prompt_review   → LLM 自检优化 + 必须覆盖对账")
    print()

    start = time.time()
    try:
        result = run_planning(
            topic=topic,
            source_content=source,
            report_type="tech",
            language="zh",
            report_goal=goal,
        )
    except Exception as e:
        print(f"\n❌ StateGraph 运行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    elapsed = time.time() - start
    print(f"\n✅ StateGraph 规划完成 ({elapsed:.1f}s)")
    return result


def main(goal: dict | None = None) -> int:
    print("=" * 60)
    print("🧪 StateGraph 规划管线独立测试")
    print("=" * 60)

    # 读取源文档
    source_path = _project_root / "data" / "source_zhinengzhuanxing.md"
    if not source_path.exists():
        print(f"❌ 未找到源文档: {source_path}")
        sys.exit(1)

    source_content = source_path.read_text(encoding="utf-8")
    print(f"📄 源文档: {source_path.name} ({len(source_content)} chars)")
    print()

    # 运行 StateGraph（支持两步流程或一步全流程）
    try:
        if goal:
            # 已确认目标，直接跑剩余管线
            result = run_full_pipeline("央企智能化转型建设规划", source_content, goal=goal)
        else:
            # 先提取目标，再跑全流程
            start_time = time.time()
            result = run_planning(
            topic="央企智能化转型建设规划",
            source_content=source_content,
            report_type="tech",
            language="zh",
        )
    except Exception as e:
        print(f"\n❌ StateGraph 运行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    elapsed = time.time() - start_time
    print(f"\n✅ StateGraph 规划完成 ({elapsed:.1f}s)")
    print()

    # 解析输出
    report_goal = result.get("report_goal") or {}
    chapter_prompts = result.get("chapter_prompts") or result.get("optimized_prompts") or []
    outlines = result.get("reference_outlines") or []

    # ── 报告目标 ──
    print("#" * 50)
    print("# 📋 报告目标")
    print("#" * 50)
    goal_title = report_goal.get("title", "N/A")
    goal_purpose = report_goal.get("purpose", "N/A")[:120]
    goal_audience = report_goal.get("target_audience", "N/A")
    goal_strategy = report_goal.get("overall_strategy", "N/A")[:120]
    print(f"  标题:   {goal_title}")
    print(f"  目的:   {goal_purpose}")
    print(f"  读者:   {goal_audience}")
    print(f"  策略:   {goal_strategy}")

    # ── 参考大纲来源 ──
    if outlines:
        print(f"\n{'#' * 50}")
        print(f"# 🔗 搜索参考大纲: {len(outlines)} 个来源")
        print(f"{'#' * 50}")
        for i, o in enumerate(outlines, 1):
            first_line = o.split("\n")[0][:80]
            print(f"  {i}. {first_line}")

    # ── 章节结构 ──
    print(f"\n{'#' * 50}")
    print(f"# 📖 章节结构 ({len(chapter_prompts)} 章)")
    print(f"{'#' * 50}")

    has_issues = False
    for i, cp in enumerate(chapter_prompts, 1):
        title = cp.get("title", f"第{i}章")
        level = cp.get("level", 1)
        intent = cp.get("writing_intent", "")[:60]
        key_pts = cp.get("key_points", [])
        avoid = cp.get("avoid_topics", [])
        chart = cp.get("chart_spec")
        est_words = cp.get("estimated_words", 0)
        sub_sections = cp.get("sub_sections", [])

        # 检测问题
        if "第" in title and "章" in title:
            has_issues = True
            print(f"  ❌ [{i}] {title} ← 禁止使用「第X章」占位符！")
        elif level == 1:
            print(f"\n  ┌─ [{i}] H1: {title} (~{est_words}字)")
        else:
            print(f"  ├─ [{i}] H{level}: {title} (~{est_words}字)")

        if intent:
            intent = intent if len(intent) <= 60 else intent[:57] + "..."
            print(f"  │  意图: {intent}")

        if key_pts:
            pts = key_pts[:3]
            pts_str = "; ".join(pts)
            print(f"  │  要点: {pts_str}")

        if avoid:
            print(f"  ⚠️  避免: {', '.join(avoid[:2])}")

        if chart:
            chart_type = chart.get("type", "?")
            has_data = bool(chart.get("data"))
            data_status = "✅ 有数据" if has_data else "⚠️ 无数据"
            print(f"  │  图表: {chart_type} {data_status}")

        # 子章节
        for j, sub in enumerate(sub_sections, 1):
            sub_title = sub.get("title", "")
            sub_intent = sub.get("writing_intent", "")[:50]
            sub_chart = sub.get("chart_spec")
            if sub_title:
                print(f"  ├─ H2: {sub_title}")
                if sub_intent:
                    print(f"  │     意图: {sub_intent}")
                if sub_chart and sub_chart.get("data"):
                    print(f"  │     图表: {sub_chart.get('type')} ✅ 有数据")
                if "第" in sub_title and "节" in sub_title:
                    has_issues = True
                    print(f"  │  ❌ ← 禁止使用「第X节」占位符！")

    # ── 质量检查摘要 ──
    print(f"\n{'#' * 50}")
    print("# 📊 质量检查")
    print(f"{'#' * 50}")
    print(f"  总章节: {len(chapter_prompts)}")

    h1_count = sum(1 for cp in chapter_prompts if cp.get("level") in (1, "1"))
    h2_count = sum(len(cp.get("sub_sections", [])) for cp in chapter_prompts)
    print(f"  H1: {h1_count}, H2: {h2_count}")

    with_intent = sum(1 for cp in chapter_prompts if cp.get("writing_intent"))
    print(f"  有写作意图: {with_intent}/{len(chapter_prompts)}")

    with_chart = sum(1 for cp in chapter_prompts if cp.get("chart_spec"))
    with_data = sum(1 for cp in chapter_prompts if cp.get("chart_spec") and cp.get("chart_spec").get("data"))
    print(f"  图表规划: {with_chart} 个, 有数据注入: {with_data} 个")

    with_avoid = sum(1 for cp in chapter_prompts if cp.get("avoid_topics"))
    print(f"  有避让清单: {with_avoid}/{len(chapter_prompts)}")

    if has_issues:
        print("\n⚠️  检测到「第X章」占位符，违反了标题规则！")

    ref_count = result.get("reference_outlines", [])
    print(f"  参考大纲: {len(ref_count)} 个来源")

    # ── 结论 ──
    print(f"\n{'=' * 60}")
    all_ok = (len(chapter_prompts) >= 4 and with_intent >= len(chapter_prompts) // 2)
    if all_ok:
        print(f"✅ StateGraph 规划测试通过 ({elapsed:.1f}s)")
    else:
        print(f"❌ StateGraph 规划测试失败 — 章节数不足或意图缺失")
    print(f"{'=' * 60}")

    return 0 if all_ok else 1


def print_goal_summary(goal: dict) -> None:
    """打印完整的写作目标+角色摘要（供交互确认）。"""
    print(f"\n{'=' * 60}")
    print("📋 报告目标摘要")
    print(f"{'=' * 60}")
    for k in ("title", "purpose", "target_audience", "overall_strategy"):
        v = goal.get(k, "")
        if v:
            print(f"  {k}: {v[:150]}")
    wr = goal.get("writing_role", {})
    if wr:
        print(f"\n  【写作角色】")
        print(f"  role: {wr.get('role', 'N/A')}")
        print(f"  tone: {wr.get('tone', 'N/A')}")
        print(f"  voice: {wr.get('voice', 'N/A')}")
        print(f"  conventions: {wr.get('output_conventions', 'N/A')}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    # 支持 CLI 参数: --goal-file <path> 传入已确认的 goal JSON
    goal: dict | None = None
    if len(sys.argv) > 2 and sys.argv[1] == "--goal-file":
        import json as _json
        goal_path = Path(sys.argv[2])
        if goal_path.exists():
            goal = _json.loads(goal_path.read_text())
            print(f"📥 加载已确认目标: {goal_path}")
    sys.exit(main(goal))
