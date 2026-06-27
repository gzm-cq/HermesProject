"""
AI报告生成系统的测试包。

包含单元测试、集成测试和端到端测试。
"""

__version__ = "0.1.0"
__author__ = "报告团队"

import pytest


def test_configuration() -> None:
    """验证测试环境配置是否正确"""
    assert True  # 基础测试


# 配置pytest测试套件
def pytest_configure(config: pytest.Config) -> None:
    """配置pytest"""
    config.addinivalue_line(
        "markers", "unit: 标记为单元测试"
    )
    config.addinivalue_line(
        "markers", "integration: 标记为集成测试"
    )
    config.addinivalue_line(
        "markers", "e2e: 标记为端到端测试"
    )


__all__ = ["__version__", "__author__", "test_configuration"]