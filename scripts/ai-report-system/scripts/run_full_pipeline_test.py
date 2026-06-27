# -*- coding: utf-8 -*-
"""
全管线报告生成脚本
用已有 goal 跑完整管线

用法：
    python3 scripts/run_full_pipeline_test.py [topic] [--test]

默认输出到 reports/<topic>/（正式交付）。
加 --test 输出到 test_outputs/<topic>/（验证用）。
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# ⚡ 设 dummy Dify 环境变量（StateGraph 路径不用 Dify KB）
os.environ["DIFY_DATASET_API_KEY"] = "dummy-test-skip"
os.environ["DIFY_DATASET_ID"] = "dummy-test-skip"
os.environ.setdefault("DIFY_API", "http://localhost:5001")
os.environ.setdefault("DIFY_COMPOSE", "/dev/null/docker-compose.yml")

# 加入项目根到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline_test")

from ai_report.core.orchestrator import ReportWorkflowOrchestrator
from ai_report.core.report_goal_helpers import load_report_goal

PROJECT_DIR = Path(__file__).resolve().parent.parent

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="全管线报告生成")
    parser.add_argument("topic", nargs="?", default="智能制造AI能力建设可行性分析报告",
                        help="报告主题（在 reports/<topic>/ 下查找 report_goal.json）")
    parser.add_argument("--test", action="store_true",
                        help="输出到 test_outputs/ 而非 reports/")
    return parser.parse_args()

def main():
    args = parse_args()
    TOPIC = args.topic

    # ── 门检查 ──────────────────────────────────────────
    topic_dir = PROJECT_DIR / "reports" / TOPIC
    report_goal_path = topic_dir / "report_goal.json"
    if not report_goal_path.exists():
        print(f"❌ 门检查失败: {report_goal_path} 不存在")
        print(f"   请先运行 define_goal 生成 report_goal.json")
        sys.exit(1)

    fact_bank_path = topic_dir / "fact_bank.json"
    if not fact_bank_path.exists():
        print(f"❌ 门检查失败: {fact_bank_path} 不存在")
        print(f"   请先运行 fact_bank 生成 fact_bank.json")
        sys.exit(1)

    import json as _json
    try:
        fb = _json.loads(fact_bank_path.read_text(encoding="utf-8"))
        conflicts = fb.get("conflicts", []) or []
        if conflicts:
            print(f"❌ 门检查失败: fact_bank.json 中有 {len(conflicts)} 个待处理冲突")
            for c in conflicts:
                print(f"   - {c.get('fact', '' )[:80]}")
            print(f"   请解决冲突后再运行管线")
            sys.exit(1)
        logger.info("✅ 门检查通过: report_goal / fact_bank / conflicts 均正常")
    except Exception as e:
        print(f"❌ 门检查失败: fact_bank.json 解析错误: {e}")
        sys.exit(1)
    # ── 门检查结束 ──────────────────────────────────────

    if args.test:
        OUTPUT_DIR = PROJECT_DIR / "test_outputs" / TOPIC
    else:
        OUTPUT_DIR = PROJECT_DIR / "reports" / TOPIC
    logger.info("=" * 50)
    logger.info("🚀 全管线测试启动: %s", TOPIC)
    logger.info("=" * 50)

    # Step 0: 加载已有 goal
    goal = load_report_goal(TOPIC)
    if not goal:
        logger.error("❌ goal 加载失败")
        return

    logger.info("✅ 目标: %s", goal.get("title", "")[:60])
    logger.info("   角色: %s", goal.get("writing_role", {}).get("role", ""))

    # 全管线
    start_all = time.time()
    orch = ReportWorkflowOrchestrator()

    logger.info("\n📋 参数: type=tech | lang=zh | output=%s", OUTPUT_DIR)
    logger.info("⏳ 预计 6-8 分钟，正在运行...\n")

    result = orch.run(
        topic=TOPIC,
        report_type="tech",
        language="zh",
        output_dir=OUTPUT_DIR,
        report_goal=goal,
        skip_evaluation=False,
        skip_quality=False,
    )

    elapsed = time.time() - start_all
    state = result.get("state")
    report = result.get("report")
    output_path = result.get("output_path")
    evaluation = result.get("evaluation")

    print("\n" + "=" * 50)
    print("📊 测试结果")
    print("=" * 50)
    print(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")

    if state:
        stages = {k: f"{v:.0f}s" for k, v in (state.stages or {}).items()}
        print(f"  阶段: {json.dumps(stages, ensure_ascii=False)}")
        if state.errors:
            print(f"  ⚠️ 错误: {state.errors}")

    if report:
        print(f"\n  报告字数: {report.total_words:,}")
        print(f"  章节: {len(report.sections)}")
        print(f"  质量: {report.metadata.get('avg_quality', 'N/A')}")

    if output_path:
        print(f"\n✅ 输出: {output_path}")
        md = Path(str(output_path))
        if md.exists():
            print(f"   .md: {md.stat().st_size:,} bytes")
        docx = md.with_suffix(".docx")
        if docx.exists():
            print(f"   .docx: {docx.stat().st_size:,} bytes")

    if evaluation:
        print(f"\n📈 评分: {evaluation.overall_score:.4f} | {evaluation.quality_grade}")

    print("\n✅ 全管线测试完成")
    return result

if __name__ == "__main__":
    main()
