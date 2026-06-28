"""
AI报告生成系统基础测试
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

# 添加项目到路径
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

from ai_report.config import ConfigManager, get_config
from ai_report.core.base import BaseComponent
from ai_report.adapters.hermes import HermesToolManager, get_memory_system, provide_memory_context, store_interaction_context
import pytest

from ai_report.core.exceptions import handle_error, ValidationError


class TestComponent(BaseComponent):
    """测试组件"""
    COMPONENT_NAME = "TestComponent"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "测试用组件"

    def execute(self) -> str:
        """执行测试"""
        import time
        start = time.time()
        self._record_performance(start, success=True)
        return "Test execution successful"


@pytest.mark.unit
def test_config_manager() -> None:
    """测试配置管理器"""
    print("=== 测试配置管理器 ===")

    config_manager = ConfigManager()

    # 测试单例模式
    config_manager2 = ConfigManager()
    assert config_manager is config_manager2, "配置管理器应该是单例"

    # 测试配置转换
    config_dict = config_manager.to_dict()
    assert "report" in config_dict
    assert "search" in config_dict
    assert "system" in config_dict

    # 测试报告配置
    report_config = config_manager.report_config
    assert report_config.language in ["zh", "en"]
    assert report_config.max_length > 0

    # 测试搜索配置
    search_config = config_manager.search_config
    assert search_config.search_timeout > 0
    assert search_config.max_results > 0

    print("✓ 配置管理器测试通过")
    print(f"  报告语言: {report_config.language}")
    print(f"  搜索超时: {search_config.search_timeout}s")
    print(f"  工作目录: {config_manager.system_config.working_dir}")
    print()


@pytest.mark.unit
def test_base_component() -> None:
    """测试基础组件"""
    print("=== 测试基础组件 ===")

    with TestComponent() as component:
        metadata = component.get_metadata()
        assert metadata.name == "TestComponent"
        assert metadata.version == "1.0.0"

        result = component.execute()
        assert result == "Test execution successful"

        stats = component.get_performance_stats()
        assert stats["total_calls"] > 0
        assert stats["success_calls"] > 0

        repr_str = repr(component)
        assert "TestComponent" in repr_str

    print("✓ 基础组件测试通过")
    print(f"  组件元数据: {metadata.name} v{metadata.version}")
    print(f"  执行结果: {result}")
    print(f"  统计: {stats['total_calls']}次调用")
    print()


@pytest.mark.unit
def test_hermes_tool_wrapper() -> None:
    """测试Hermes工具包装器"""
    print("=== 测试Hermes工具包装器 ===")

    config = get_config()
    tool_manager = HermesToolManager(config)

    available_tools = tool_manager.get_available_tools()
    assert "browser" in available_tools
    assert "file" in available_tools

    # 验证可用操作
    assert "search" in available_tools["browser"]

    manager_result = tool_manager.execute()
    assert manager_result["status"] == "ready"

    browser_wrapper = tool_manager.get_wrapper("browser")
    search_results = browser_wrapper.search_with_browser("AI技术", max_results=2)
    assert len(search_results) <= 2
    if search_results:
        assert hasattr(search_results[0], "title")
        assert hasattr(search_results[0], "relevance")

    file_wrapper = tool_manager.get_wrapper("file")
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp.write("测试内容")
        tmp_path = Path(tmp.name)

    try:
        search_result = file_wrapper.search_files("test", target_dir=tmp_path.parent)
        assert "found_count" in search_result
        assert isinstance(search_result["found_count"], int)
    finally:
        tmp_path.unlink(missing_ok=True)

    print("✓ Hermes工具包装器测试通过")
    print(f"  可用工具: {available_tools}")
    print(f"  管理器状态: {manager_result['status']}")
    print(f"  Browser搜索结果: {len(search_results)}个")
    print()


@pytest.mark.unit
def test_memory_system() -> None:
    """测试记忆系统"""
    print("=== 测试记忆系统 ===")

    config = get_config()
    memory_system = get_memory_system(config)

    system_info = memory_system.get_provider_info()
    assert system_info["memory_enabled"] == config.system_config.enable_memory

    test_context = {
        "test_id": "memory_test_1",
        "description": "记忆系统功能测试",
        "timestamp": "2025-04-25",
        "data": {"key1": "value1", "key2": 42},
    }

    store_interaction_context(
        peer_id="test_peer",
        context=test_context,
    )

    contexts = provide_memory_context("记忆系统", max_contexts=2)
    assert isinstance(contexts, list)

    execution_result = memory_system.execute()
    assert execution_result["status"] == "ready"

    print("✓ 记忆系统测试通过")
    print(f"  记忆已启用: {system_info['memory_enabled']}")
    print(f"  搜索上下文数: {len(contexts)}")
    print(f"  系统状态: {execution_result['status']}")
    print()


@pytest.mark.unit
def test_error_handling() -> None:
    """测试错误处理"""
    print("=== 测试错误处理 ===")

    try:
        raise ValidationError("测试验证异常", field="test_field", value=123)
        error_raised = False
    except ValidationError as e:
        error_raised = True
        assert e.field == "test_field"
        assert e.value == 123
        assert hasattr(e, "expected")

    assert error_raised, "应该抛出ValidationError异常"

    try:
        raise ValueError("测试值错误")
    except Exception as e:
        handled_error = handle_error(e, context={"test": True}, raise_again=False)
        assert handled_error is not None
        assert "输入验证失败" in str(handled_error)

    print("✓ 错误处理测试通过")
    print()


def run_all_tests() -> None:
    """运行所有测试"""
    print("🔍 开始AI报告生成系统第一阶段测试\n")

    test_functions = [
        test_config_manager,
        test_base_component,
        test_hermes_tool_wrapper,
        test_memory_system,
        test_error_handling,
    ]

    passed = 0
    failed = 0

    for test_func in test_functions:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"✗ {test_func.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()
            print()

    print(f"\n📊 测试结果: {passed} 通过, {failed} 失败")

    if failed == 0:
        print("\n✅ 所有测试通过! 第一阶段合并完成。")
        print("下一步: 实现Hermes搜索适配器模块（Phase 2）。")
    else:
        print(f"\n❌ 有 {failed} 个测试失败，需要修复。")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
