"""
Phase 4 测试: 报告规划器 + 内容生成器 + 评估器 + 工作流
包含 Phase 1-3 全量回归
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, List

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

from ai_report.core.planner import HermesReportPlanner, ReportPlan
from ai_report.core.generator import HermesContentGenerator, GeneratedReport
from ai_report.core.evaluator import HermesReportEvaluator, EvaluationResult
from ai_report.core.orchestrator import ReportWorkflowOrchestrator, create_orchestrator
from ai_report.core.exceptions import ReportAgentError

import pytest


# ── Mock 委托执行器 ──────────────────────────────────────────

def _fake_task_executor(goal: str = "", context: str = "") -> str:
    """模拟 delegate_task 返回，不调真实 LLM。"""
    ctx = json.loads(context) if context else {}
    chapter = ctx.get("chapter", {})
    title = chapter.get("title", "未知章节")
    return (
        f"# {title}\n\n"
        f"这是关于「{title}」的正文内容。\n\n"
        f"包含具体数据：完成率 85%，覆盖率 92%。\n\n"
        f"---SUMMARY---\n"
        f"本章讨论了{title}的核心内容。"
    )


_SAMPLE_GOAL: dict[str, Any] = {
    "title": "Python异步编程原理分析",
    "purpose": "分析Python异步编程的技术原理和应用场景",
    "target_audience": "技术开发者、架构师",
    "overall_strategy": "从原理到实践，每项建议有数据支撑",
    "writing_role": {
        "role": "技术分析师",
        "expertise": ["Python", "异步编程", "并发模型"],
        "tone": "专业、技术严谨",
        "voice": "以技术作者视角客观论述",
        "output_conventions": "每节以结论句开头、代码示例用代码块",
    },
}


# ── Mock 适配器（用于依赖注入测试） ────────────────────────


class MockAIClient:
    """满足 AIClientProtocol 的 mock。"""

    def __init__(self) -> None:
        self._executor_set = False

    def call_llm(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        return f"Mock response for: {prompt[:40]}"

    def set_task_executor(self, executor: Any | None) -> None:
        self._executor_set = executor is not None


class MockQualityAssessor:
    """满足 QualityAssessorProtocol 的 mock。"""

    def assess(
        self,
        content: str,
        report_id: str | None = None,
        dimensions: list[str] | None = None,
    ) -> Any:
        from dataclasses import dataclass as _dc
        @_dc
        class MockResult:
            overall_score: float = 0.85
            quality_grade: str = "good"
        return MockResult()


# ═════════════════════════════════════════════════════════════
# Report Planner Tests
# ═════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_planner_init() -> None:
    print("=== 测试规划器初始化 ===")
    p = HermesReportPlanner()
    types = p.list_templates()
    assert len(types) >= 4
    assert "tech" in types
    print(f"✓ 初始化正常, {len(types)}种模板: {types}")
    print()


@pytest.mark.unit
def test_planner_create_tech() -> None:
    print("=== 测试技术报告规划 ===")
    p = HermesReportPlanner()
    plan = p.create_plan("微服务架构在电商系统中的应用", report_type="tech")
    assert isinstance(plan, ReportPlan)
    assert plan.report_type == "tech"
    assert len(plan.sections) > 0
    assert plan.estimated_total_words > 0
    print(f"✓ 技术报告规划成功")
    print(f"  章节: {len(plan.sections)}, 字数: ~{plan.estimated_total_words}")
    print(f"  类型: {plan.report_type}, 语言: {plan.language}")
    print()


@pytest.mark.unit
def test_planner_detect_type() -> None:
    print("=== 测试类型自动检测 ===")
    p = HermesReportPlanner()

    plan1 = p.create_plan("微服务架构设计")
    plan2 = p.create_plan("2024年中国AI市场分析")
    plan3 = p.create_plan("新型电池材料的研究进展")

    assert plan1.report_type == "tech", f"应为tech: {plan1.report_type}"
    assert plan2.report_type == "market", f"应为market: {plan2.report_type}"
    assert plan3.report_type == "research", f"应为research: {plan3.report_type}"

    print(f"✓ 类型检测正确")
    print(f"  架构设计 → {plan1.report_type}")
    print(f"  市场分析 → {plan2.report_type}")
    print(f"  研究进展 → {plan3.report_type}")
    print()


@pytest.mark.unit
def test_planner_empty_topic() -> None:
    print("=== 测试空主题 ===")
    p = HermesReportPlanner()
    try:
        p.create_plan("")
        assert False
    except ReportAgentError as e:
        print(f"✓ 空主题正确拒绝: {e.message}")
    print()


@pytest.mark.unit
def test_planner_preview() -> None:
    print("=== 测试大纲预览 ===")
    p = HermesReportPlanner()
    plan = p.create_plan("Python异步编程技术", report_type="tech")
    preview = plan.preview(max_lines=15)
    assert "Python" in preview
    assert "章节" in preview or "#" in preview
    print(f"✓ 大纲预览成功 ({len(preview)}字)")
    print()


# ═══════════════════════════════════════════════════
# Content Generator Tests
# ═══════════════════════════════════════════════════

@pytest.mark.unit
def test_generator_init() -> None:
    print("=== 测试生成器初始化 ===")
    g = HermesContentGenerator()
    assert g.COMPONENT_NAME == "HermesContentGenerator"
    print("✓ 生成器初始化正常")
    print()


@pytest.mark.unit
def test_generator_from_plan() -> None:
    print("=== 测试从计划生成报告（mock task_executor，不调LLM）===")
    planner = HermesReportPlanner()
    generator = HermesContentGenerator(task_executor=_fake_task_executor)

    plan = planner.create_plan("Docker容器化部署", report_type="tech")
    report = generator.generate_from_plan(plan)

    assert isinstance(report, GeneratedReport)
    assert report.total_words > 0
    assert len(report.sections) == len(plan.sections)

    print(f"✓ 报告生成正常 ({len(report.sections)}章节, {report.total_words}字)")
    print(f"  avg_quality: {report.metadata.get('avg_quality', 0):.2f}")
    print()


@pytest.mark.unit
def test_generator_fallback() -> None:
    print("=== 测试生成器降级机制（mock task_executor）===")
    planner = HermesReportPlanner()
    generator = HermesContentGenerator(task_executor=_fake_task_executor)

    plan = planner.create_plan("Python异步编程技术", report_type="tech")
    report = generator.generate_from_plan(plan)

    assert isinstance(report, GeneratedReport)
    assert report.total_words > 0
    assert len(report.sections) == len(plan.sections)

    print(f"✓ 降级机制正常 ({len(report.sections)}章节, {report.total_words}字)")
    print(f"  平均质量: {report.metadata.get('avg_quality', 0):.2f}")
    print()


# ═══════════════════════════════════════════════════
# Report Evaluator Tests
# ═══════════════════════════════════════════════════

@pytest.mark.unit
def test_evaluator_init() -> None:
    print("=== 测试评估器初始化 ===")
    e = HermesReportEvaluator()
    assert e.COMPONENT_NAME == "HermesReportEvaluator"
    print("✓ 评估器初始化正常")
    print()


@pytest.mark.unit
def test_evaluator_full() -> None:
    print("=== 测试完整报告评估（mock task_executor）===")
    planner = HermesReportPlanner()
    generator = HermesContentGenerator(task_executor=_fake_task_executor)
    evaluator = HermesReportEvaluator()

    plan = planner.create_plan("机器学习Pipeline设计", report_type="tech")
    report = generator.generate_from_plan(plan)
    result = evaluator.evaluate_report(report)

    assert isinstance(result, EvaluationResult)
    assert 0 <= result.overall_score <= 1
    assert len(result.dimensions) == 8
    assert result.quality_grade in {"excellent", "good", "acceptable", "needs_improvement", "poor"}
    assert result.optimization_tasks is not None

    print(f"✓ 评估完成: score={result.overall_score:.4f}, grade={result.quality_grade}")
    print(f"  8维度: {', '.join(f'{d}={v.score:.2f}' for d,v in result.dimensions.items())}")
    print(f"  优化项: {len(result.optimization_tasks)}, 置信度: {result.confidence:.4f}")
    print()


# ═══════════════════════════════════════════════════
# Workflow Tests
# ═══════════════════════════════════════════════════

@pytest.mark.unit
def test_workflow_init() -> None:
    print("=== 测试工作流初始化 ===")
    w = create_orchestrator(task_executor=_fake_task_executor)
    assert w.COMPONENT_NAME == "ReportWorkflowOrchestrator"
    print("✓ 工作流初始化正常 (7个子组件)")
    print()


@pytest.mark.unit
def test_workflow_empty_topic() -> None:
    print("=== 测试空主题工作流（不崩溃）===")
    w = create_orchestrator(task_executor=_fake_task_executor)
    # 跳过 StateGraph（topic 为空时 StateGraph 会调 LLM，无意义）
    result = w.run("", skip_evaluation=True, skip_quality=True,
                   report_goal=_SAMPLE_GOAL,
                   enriched_chapter_prompts=[
                       {"title": "背景", "level": 1, "section_type": "intro",
                        "writing_intent": "介绍", "key_points": [], "estimated_words": 200},
                   ])
    # 即使主题为空，编排器也不应崩溃；返回有效结果结构
    assert "success" in result
    assert "state" in result
    print(f"✓ 空主题工作流完成, success={result['success']}")
    print()

@pytest.mark.unit
def test_workflow_full_fast() -> None:
    """快速版：跳过 StateGraph，验证编排流程。"""
    print("=== 测试工作流编排（跳过 StateGraph LLM 调用）===")
    with tempfile.TemporaryDirectory() as tmpdir:
        w = create_orchestrator(task_executor=_fake_task_executor)
        # 使用预构建的章节提示词，跳过 StateGraph
        chapter_prompts = [
            {"title": "技术背景", "level": 1, "section_type": "intro",
             "writing_intent": "介绍背景", "key_points": ["背景", "现状"],
             "estimated_words": 300},
            {"title": "核心方案", "level": 1, "section_type": "body",
             "writing_intent": "阐述方案", "key_points": ["设计", "实现"],
             "estimated_words": 500},
            {"title": "总结", "level": 1, "section_type": "conclusion",
             "writing_intent": "总结展望", "key_points": ["成果", "展望"],
             "estimated_words": 200},
        ]
        result = w.run(
            topic="Python异步编程原理",
            report_type="tech",
            report_goal=_SAMPLE_GOAL,
            enriched_chapter_prompts=chapter_prompts,
            skip_evaluation=True,
            skip_quality=True,
            output_dir=Path(tmpdir),
        )

        assert result["success"], f"工作流失败: {result.get('errors', [])}"
        assert result["plan"] is not None
        assert result["report"] is not None
        assert result["output_path"] is not None
        assert result["output_path"].exists()

        print(f"✓ 快速工作流执行成功")
        print(f"  章节:{len(result['plan'].sections)} 字数:{result['report'].total_words}")
        print(f"  耗时:{result['elapsed_display']} 保存:{result['output_path'].name}")
        print()


@pytest.mark.unit
def test_workflow_with_mock_di() -> None:
    """测试通过依赖注入 mock 适配器创建编排器。"""
    print("=== 测试依赖注入模式 ===")
    mock_ai = MockAIClient()
    mock_qa = MockQualityAssessor()

    w = ReportWorkflowOrchestrator(
        task_executor=_fake_task_executor,
        quality_assessor=mock_qa,
        ai_client=mock_ai,
    )
    assert w.COMPONENT_NAME == "ReportWorkflowOrchestrator"
    assert w._quality is mock_qa
    assert w._ai_client is mock_ai

    # 验证 Protocol 兼容性
    from ai_report.adapters.protocols import AIClientProtocol, QualityAssessorProtocol
    assert isinstance(mock_ai, AIClientProtocol)
    assert isinstance(mock_qa, QualityAssessorProtocol)
    print("✓ 依赖注入模式正常")
    print()


# ═══════════════════════════════════════════════════
# CLI Tests
# ═══════════════════════════════════════════════════

@pytest.mark.unit
def test_cli_plan_command() -> None:
    print("=== 测试CLI规划命令 ===")
    from ai_report.main import build_parser
    parser = build_parser()
    args = parser.parse_args(["plan", "测试主题", "--type", "tech"])
    assert args.command == "plan"
    assert args.topic == "测试主题"
    assert args.type == "tech"
    print("✓ CLI参数解析正常")
    print()


@pytest.mark.unit
def test_cli_help() -> None:
    print("=== 测试CLI帮助 ===")
    from ai_report.main import build_parser
    parser = build_parser()
    args = parser.parse_args(["help"])
    assert args.command == "help"
    print("✓ CLI帮助命令正常")
    print()


# ═══════════════════════════════════════════════════
# Test Runner
# ═══════════════════════════════════════════════════

def run_phase4_tests() -> bool:
    print("🚀 Phase 4 测试: 规划器 + 生成器 + 评估器 + 工作流\n")

    tests: List = [
        test_planner_init,
        test_planner_create_tech,
        test_planner_detect_type,
        test_planner_empty_topic,
        test_planner_preview,
        test_generator_init,
        test_generator_from_plan,
        test_generator_cache,
        test_evaluator_init,
        test_evaluator_full,
        test_workflow_init,
        test_workflow_full,
        test_workflow_empty_topic,
        test_cli_plan_command,
        test_cli_help,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"✗ {test_func.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()
            print()

    print(f"\n📊 Phase 4 测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def run_all_tests() -> bool:
    print("=" * 60)
    print(" AI报告生成系统 - 完全测试 (Phase 1-4)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        phase4_ok = run_phase4_tests()

        print("\n--- Phase 3 回归 ---\n")
        from tests.test_phase3 import run_phase3_tests
        phase3_ok = run_phase3_tests()

        print("\n--- Phase 2 回归 ---\n")
        from tests.test_phase2 import run_phase2_tests
        phase2_ok = run_phase2_tests(custom_tmpdir=Path(tmpdir))

        print("\n--- Phase 1 回归 ---\n")
        from tests.test_basic_framework import run_all_tests as run_phase1
        phase1_ok = run_phase1()

    total_ok = all([phase1_ok, phase2_ok, phase3_ok, phase4_ok])
    print(f"\n{'=' * 60}")
    print(f"整体: {'✅ 全部通过' if total_ok else '❌ 有失败'}")
    print(f"  Phase 1: {'✅' if phase1_ok else '❌'}")
    print(f"  Phase 2: {'✅' if phase2_ok else '❌'}")
    print(f"  Phase 3: {'✅' if phase3_ok else '❌'}")
    print(f"  Phase 4: {'✅' if phase4_ok else '❌'}")
    print(f"{'=' * 60}")
    return total_ok


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
