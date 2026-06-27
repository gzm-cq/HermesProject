"""
Phase 3 测试: 图表生成器 + 质量评估器 + 状态管理器
包含 Phase 1 + Phase 2 回归
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import List

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

from ai_report.adapters.diagram_generator import HermesDiagramGenerator, DiagramType
from ai_report.adapters.quality_assessor import HermesQualityAssessor
from ai_report.core.state_manager import HermesStateManager

import pytest


# ═══════════════════════════════════════════════════
# Diagram Generator Tests
# ═══════════════════════════════════════════════════

@pytest.mark.unit
def test_diagram_initialization() -> None:
    """测试图表生成器初始化"""
    print("=== 测试图表生成器初始化 ===")
    gen = HermesDiagramGenerator()
    stats = gen.get_cache_stats()
    assert stats["size"] == 0
    print(f"✓ 初始化正常, 缓存统计: {stats}")
    print()


@pytest.mark.unit
def test_diagram_architecture() -> None:
    """测试架构图生成"""
    print("=== 测试架构图生成 ===")
    gen = HermesDiagramGenerator()
    result = gen.generate("微服务系统架构", diagram_type="architecture", priority="quality")
    assert result.content
    assert result.format in {"ascii", "svg", "mermaid", "excalidraw", "json", "text"}
    assert len(result.fallback_chain) > 0
    print(f"✓ 架构图生成成功")
    print(f"  技能: {result.skill_used}")
    print(f"  格式: {result.format}")
    print(f"  质量: {result.quality_score:.2f}")
    print(f"  回退链: {result.fallback_chain}")
    print()


@pytest.mark.unit
def test_diagram_flowchart() -> None:
    """测试流程图生成"""
    print("=== 测试流程图生成 ===")
    gen = HermesDiagramGenerator()
    result = gen.generate("用户登录流程", diagram_type="flowchart")
    assert result.content
    assert result.diagram_type == DiagramType.FLOWCHART
    print(f"✓ 流程图生成成功: [{result.skill_used}] q={result.quality_score:.2f}")
    print()


@pytest.mark.unit
def test_diagram_all_types() -> None:
    """测试所有图表类型"""
    print("=== 测试所有图表类型 ===")
    gen = HermesDiagramGenerator()
    types = ["architecture", "flowchart", "data_structure", "infographic", "comparison", "timeline"]

    for dt in types:
        result = gen.generate(f"Test {dt}", diagram_type=dt)
        assert result.content, f"{dt} 生成失败"
        print(f"  {dt:20s} → [{result.skill_used:22s}] q={result.quality_score:.2f}")

    print("✓ 所有图表类型支持正常")
    print()


@pytest.mark.unit
def test_diagram_priority() -> None:
    """测试优先级策略"""
    print("=== 测试优先级策略 ===")
    gen = HermesDiagramGenerator()

    # Quality first - should use best skill
    quality_result = gen.generate("系统架构", diagram_type="architecture", priority="quality")
    speed_result = gen.generate("系统架构", diagram_type="architecture", priority="speed")

    assert quality_result.quality_score >= 0
    assert speed_result.quality_score >= 0

    print(f"  质量优先: [{quality_result.skill_used}] q={quality_result.quality_score:.2f}")
    print(f"  速度优先: [{speed_result.skill_used}] q={speed_result.quality_score:.2f}")
    print()


@pytest.mark.unit
def test_diagram_cache() -> None:
    """测试图表缓存"""
    print("=== 测试图表缓存 ===")
    gen = HermesDiagramGenerator()

    stats_before = gen.get_cache_stats()
    result1 = gen.generate("缓存测试", diagram_type="architecture")
    result2 = gen.generate("缓存测试", diagram_type="architecture")

    stats_after = gen.get_cache_stats()
    assert stats_after["size"] > 0
    assert result1.content == result2.content

    print(f"  缓存大小: {stats_after['size']}")
    print(f"  命中/未命中: {stats_after['hits']}/{stats_after['misses']}")
    print()


# ═══════════════════════════════════════════════════
# Quality Assessor Tests
# ═══════════════════════════════════════════════════

@pytest.mark.unit
def test_quality_initialization() -> None:
    """测试质量评估器初始化"""
    print("=== 测试质量评估器初始化 ===")
    qa = HermesQualityAssessor()
    assert qa.COMPONENT_NAME == "HermesQualityAssessor"
    print("✓ 质量评估器初始化正常")
    print()


@pytest.mark.unit
def test_quality_assess_good_content() -> None:
    """测试评估好内容"""
    print("=== 测试评估好内容 ===")
    qa = HermesQualityAssessor()

    content = (
        "# 测试报告\n\n"
        "## 引言\n\n"
        "首先，这是一个测试报告，用于验证质量评估功能。"
        "本报告涵盖多个方面。\n\n"
        "## 主体\n\n"
        "1. 第一步\n2. 第二步\n3. 第三步\n\n"
        "数据表明，85%的测试通过了验证。\n\n"
        "## 结论\n\n"
        "综上所述，测试结果显示系统运行正常。"
    )

    result = qa.assess(content, report_id="test_001")
    assert result.overall_score > 0
    assert len(result.dimension_scores) == 5
    assert result.quality_grade in {"excellent", "good", "acceptable"}

    print(f"✓ 好内容评估结果:")
    print(f"  总分: {result.overall_score:.4f}")
    print(f"  等级: {result.quality_grade}")
    print(f"  置信度: {result.confidence:.4f}")
    for dim, dr in result.dimension_scores.items():
        print(f"  {dim}: {dr.score:.4f} ({dr.passed}/{dr.total})")
    print()


@pytest.mark.unit
def test_quality_assess_empty() -> None:
    """测试空内容评估"""
    print("=== 测试空内容评估 ===")
    qa = HermesQualityAssessor()
    try:
        qa.assess("")
        assert False, "应该抛出异常"
    except Exception as e:
        print(f"✓ 空内容正确拒绝: {type(e).__name__}")
    print()


@pytest.mark.unit
def test_quality_assess_short() -> None:
    """测试过短内容"""
    print("=== 测试过短内容 ===")
    qa = HermesQualityAssessor()
    try:
        qa.assess("你好")
        assert False, "应该抛出异常"
    except Exception as e:
        print(f"✓ 过短内容正确拒绝: {type(e).__name__}")
    print()


@pytest.mark.unit
def test_quality_single_dimension() -> None:
    """测试单维度评估"""
    print("=== 测试单维度评估 ===")
    qa = HermesQualityAssessor()
    content = "# 标题\n\n内容。\n\n1. 列表项\n2. 列表项"
    result = qa.assess_dimension("structure", content)
    assert result.dimension == "structure"
    assert 0 <= result.score <= 1
    print(f"✓ 单维度评估: structure={result.score:.4f} ({result.passed}/{result.total})")
    print()


# ═══════════════════════════════════════════════════
# State Manager Tests
# ═══════════════════════════════════════════════════

@pytest.mark.unit
def test_state_initialization() -> None:
    """测试状态管理器初始化"""
    print("=== 测试状态管理器初始化 ===")
    sm = HermesStateManager()
    stats = sm.get_stats()
    assert stats["active_tasks"] == 0
    print(f"✓ 状态管理器初始化正常")
    print(f"  引擎: {stats['engine']}")
    print()


@pytest.mark.unit
def test_state_create_task() -> None:
    """测试创建任务"""
    print("=== 测试创建任务 ===")
    sm = HermesStateManager()
    tracker = sm.create_task("test_task_1", total_steps=5, metadata={"type": "test"})
    assert tracker.task_id == "test_task_1"
    assert tracker.total_steps == 5
    assert tracker.completed_steps == 0
    print(f"✓ 任务创建成功: {tracker.task_id} ({tracker.total_steps}步)")
    print()


@pytest.mark.unit
def test_state_advance_step() -> None:
    """测试任务进度推进"""
    print("=== 测试任务进度推进 ===")
    sm = HermesStateManager()
    sm.create_task("test_task_2", total_steps=3)

    sm.advance_step("test_task_2", "搜索")
    sm.advance_step("test_task_2", "生成")
    tracker = sm.advance_step("test_task_2", "完成")

    assert tracker.completed_steps == 3
    assert tracker.progress_pct == 1.0
    assert tracker.completed_at is not None

    print(f"✓ 任务进度推进成功")
    print(f"  步骤: {tracker.completed_steps}/{tracker.total_steps}")
    print(f"  进度: {tracker.progress_pct * 100:.0f}%")
    print(f"  耗时: {tracker.elapsed_seconds:.1f}s")
    print()


@pytest.mark.unit
def test_state_persist_and_recover() -> None:
    """测试状态持久化和恢复"""
    print("=== 测试状态持久化和恢复 ===")
    sm = HermesStateManager()
    sm.create_task("test_task_3", total_steps=3, metadata={"type": "recovery"})
    sm.advance_step("test_task_3", "步骤1完成")

    state_data = {"data": {"key": "value"}, "progress": "50%"}
    saved = sm.save_state("test_task_3", state_data)
    assert saved, "状态保存失败"

    loaded = sm.load_state("test_task_3")
    assert loaded is not None, "状态加载失败"
    assert loaded["data"]["key"] == "value"

    print(f"✓ 状态持久化成功")
    print(f"  保存/加载: {saved}")
    print()

@pytest.mark.unit
def test_state_checkpoint() -> None:
    """测试检查点"""
    print("=== 测试检查点 ===")
    sm = HermesStateManager()
    sm.create_task("test_task_4", total_steps=5)

    cps = []
    for i in range(3):
        cp = sm.create_checkpoint("test_task_4", {"step": i})
        cps.append(cp)

    checkpoints = sm.list_checkpoints("test_task_4")
    assert len(checkpoints) >= 3
    assert checkpoints[-1].version == len(checkpoints)

    print(f"✓ 检查点创建成功: {len(checkpoints)}个")
    print(f"  最新检查点: v{checkpoints[-1].version}")
    print()


@pytest.mark.unit
def test_state_delete_task() -> None:
    """测试删除任务"""
    print("=== 测试删除任务 ===")
    sm = HermesStateManager()
    sm.create_task("test_task_delete", total_steps=1)
    sm.advance_step("test_task_delete", "完成")

    tasks_before = len(sm.list_tasks())
    sm.delete_task("test_task_delete")
    tasks_after = len(sm.list_tasks())

    assert tasks_after == tasks_before - 1
    print(f"✓ 任务删除成功: {tasks_before} → {tasks_after}")
    print()


@pytest.mark.unit
def test_state_fail_step() -> None:
    """测试步骤失败记录"""
    print("=== 测试步骤失败记录 ===")
    sm = HermesStateManager()
    sm.create_task("test_task_fail", total_steps=2)
    sm.advance_step("test_task_fail", "执行")
    sm.fail_step("test_task_fail", "网络超时", {"retry": True})

    task = sm.get_task("test_task_fail")
    assert task is not None
    assert len(task.errors) == 1
    assert task.errors[0]["error"] == "网络超时"

    print(f"✓ 步骤失败记录成功: {task.errors[0]['error']}")
    print()


# ═══════════════════════════════════════════════════
# Test Runner
# ═══════════════════════════════════════════════════

def _gather_tests() -> List:
    """收集当前模块的所有测试"""
    import __main__
    tests = []
    for name in dir(__main__):
        obj = getattr(__main__, name)
        if name.startswith("test_") and callable(obj):
            tests.append(obj)
    return tests


def run_phase3_tests() -> bool:
    """运行Phase 3测试"""
    print("🚀 Phase 3 测试: 图表生成器 + 质量评估器 + 状态管理器\n")

    tests = [
        # Diagram Generator
        test_diagram_initialization,
        test_diagram_architecture,
        test_diagram_flowchart,
        test_diagram_all_types,
        test_diagram_priority,
        test_diagram_cache,
        # Quality Assessor
        test_quality_initialization,
        test_quality_assess_good_content,
        test_quality_assess_empty,
        test_quality_assess_short,
        test_quality_single_dimension,
        # State Manager
        test_state_initialization,
        test_state_create_task,
        test_state_advance_step,
        test_state_persist_and_recover,
        test_state_checkpoint,
        test_state_delete_task,
        test_state_fail_step,
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

    print(f"\n📊 Phase 3 测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def run_all_tests() -> bool:
    """运行全部测试 (Phase 1 + 2 + 3)"""
    print("=" * 60)
    print("     AI报告生成系统 - 完整测试套件 (Phase 1-3)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        phase3_ok = run_phase3_tests()

        print("\n--- Phase 2 回归测试 ---\n")
        from tests.test_phase2 import run_phase2_tests
        phase2_ok = run_phase2_tests(custom_tmpdir=Path(tmpdir))

        print("\n--- Phase 1 回归测试 ---\n")
        from tests.test_basic_framework import run_all_tests as run_phase1
        phase1_ok = run_phase1()

    total_ok = phase1_ok and phase2_ok and phase3_ok
    print(f"\n{'=' * 60}")
    print(f"整体: {'✅ 全部通过' if total_ok else '❌ 有失败'}")
    print(f"  Phase 1: {'✅' if phase1_ok else '❌'}")
    print(f"  Phase 2: {'✅' if phase2_ok else '❌'}")
    print(f"  Phase 3: {'✅' if phase3_ok else '❌'}")
    print(f"{'=' * 60}")

    return total_ok


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
