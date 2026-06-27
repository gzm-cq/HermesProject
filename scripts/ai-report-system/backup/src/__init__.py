"""
AI报告生成系统主包。

基于Hermes工具集的AI报告生成系统，支持多种数据源的分析和报告生成。

主要功能模块：
- 数据收集和预处理
- AI分析和推理
- 报告格式化生成
- 输出导出（Word, PDF, PPT等）
"""

__version__ = "0.1.0"
__author__ = "报告团队"

import logging

# 配置项目日志
logging.getLogger(__name__).addHandler(logging.NullHandler())


def setup_logging(level: int = logging.INFO) -> None:
    """
    配置项目的日志系统。
    
    Args:
        level: 日志级别，默认为INFO
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


__all__ = ["__version__", "__author__", "setup_logging"]