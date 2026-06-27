"""
AI报告生成系统异常体系
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ReportAgentError(Exception):
    """AI报告生成系统基础异常"""

    def __init__(self, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        logger.error("ReportAgentError: %s %s", message, details)


# 适配器层异常
class AdapterError(ReportAgentError):
    """适配器层通用错误"""

    def __init__(self, message: str, details: Any | None = None) -> None:
        super().__init__(message, details)


class SourceDocLoadError(AdapterError):
    """源文档加载错误"""

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
    ) -> None:
        super().__init__(message, details={"file_path": file_path})
        self.file_path = file_path


class FileParseError(AdapterError):
    """文件解析错误"""

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        format_type: str | None = None,
    ) -> None:
        super().__init__(message, details={"file_path": file_path, "format_type": format_type})
        self.file_path = file_path
        self.format_type = format_type


class HermesConnectionError(AdapterError):
    """Hermes工具连接错误"""

    def __init__(
        self,
        message: str,
        url: str | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(message, details={"url": url, "operation": operation})
        self.url = url
        self.operation = operation


class WebSearchError(AdapterError):
    """Web搜索错误"""

    def __init__(
        self,
        message: str,
        query: str | None = None,
        engine: str | None = None,
    ) -> None:
        super().__init__(message, details={"query": query, "engine": engine})
        self.query = query
        self.engine = engine


class ChartGenerationError(AdapterError):
    """图表生成错误"""

    def __init__(
        self,
        message: str,
        chart_type: str | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(message, details={"chart_type": chart_type, "title": title})
        self.chart_type = chart_type
        self.title = title


class LLMCallError(AdapterError):
    """LLM调用错误"""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        attempt: int | None = None,
    ) -> None:
        super().__init__(message, details={"provider": provider, "attempt": attempt})
        self.provider = provider
        self.attempt = attempt


class ContentCleanError(ReportAgentError):
    """内容清洗错误"""

    def __init__(
        self,
        message: str,
        content_type: str | None = None,
    ) -> None:
        super().__init__(message, details={"content_type": content_type})
        self.content_type = content_type


class ConfigError(ReportAgentError):
    """配置相关异常"""

    def __init__(self, message: str, config_path: str | None = None) -> None:
        super().__init__(message)
        self.config_path = config_path


class SearchError(ReportAgentError):
    """搜索相关异常"""

    def __init__(
        self,
        message: str,
        query: str | None = None,
        engine: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.query = query
        self.engine = engine
        self.url = url


class DocumentError(ReportAgentError):
    """文档处理相关异常"""

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        format_type: str | None = None,
        parse_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.format_type = format_type
        self.parse_error = parse_error


class DiagramError(ReportAgentError):
    """图表生成相关异常"""

    def __init__(
        self,
        message: str,
        diagram_type: str | None = None,
        skill_name: str | None = None,
        fallback_used: bool = False,
    ) -> None:
        super().__init__(message)
        self.diagram_type = diagram_type
        self.skill_name = skill_name
        self.fallback_used = fallback_used


class QualityError(ReportAgentError):
    """质量检查相关异常"""

    def __init__(
        self,
        message: str,
        quality_level: str | None = None,
        failed_checks: list[str] | None = None,
        score: float | None = None,
    ) -> None:
        super().__init__(message)
        self.quality_level = quality_level
        self.failed_checks = failed_checks or []
        self.score = score


class MemoryError(ReportAgentError):
    """记忆系统相关异常"""

    def __init__(
        self,
        message: str,
        memory_provider: str | None = None,
        operation: str | None = None,
        peer_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.memory_provider = memory_provider
        self.operation = operation
        self.peer_id = peer_id


class ValidationError(ReportAgentError):
    """输入验证相关异常"""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: Any | None = None,
        expected: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.value = value
        self.expected = expected

    @classmethod
    def range_error(
        cls,
        field: str,
        value: Any,
        min_value: Any | None = None,
        max_value: Any | None = None,
    ) -> ValidationError:
        """创建范围错误异常"""
        min_str = f"≥ {min_value}" if min_value is not None else ""
        max_str = f"≤ {max_value}" if max_value is not None else ""
        constraint = f"{min_str}{'且' if min_str and max_str else ''}{max_str}"

        return cls(
            message=f"字段'{field}'的值{value}超出允许范围（{constraint}）",
            field=field,
            value=value,
            expected=(f"范围: {min_value} to {max_value}" if min_value is not None and max_value is not None else None),
        )

    @classmethod
    def type_error(cls, field: str, value: Any, expected_type: str) -> ValidationError:
        """创建类型错误异常"""
        return cls(
            message=f"字段'{field}'需要类型{expected_type}，但收到{type(value).__name__}",
            field=field,
            value=value,
            expected=expected_type,
        )


def handle_error(
    error: Exception,
    context: dict[str, Any] | None = None,
    raise_again: bool = True,
) -> ReportAgentError | None:
    """
    通用的错误处理函数

    Args:
        error: 捕获的异常
        context: 额外的上下文信息
        raise_again: 是否重新抛出异常

    Returns:
        转换后的ReportAgentError或None

    Raises:
        当raise_again=True时，重新抛出异常
    """
    if isinstance(error, ReportAgentError):
        logger.exception("Captured ReportAgentError: %s", error.message)
        if raise_again:
            raise
        return error

    error_type = type(error).__name__
    error_message = str(error) or f"Unknown {error_type}"
    context_str = f" (context: {context})" if context else ""

    system_error: ReportAgentError

    if isinstance(error, (ValueError, TypeError, AttributeError)):
        system_error = ValidationError(
            message=f"输入验证失败: {error_message}{context_str}",
        )
    elif isinstance(error, (OSError, IOError, FileNotFoundError)):
        system_error = DocumentError(
            message=f"文件操作失败: {error_message}{context_str}",
        )
    else:
        system_error = ReportAgentError(
            message=f"系统错误 [{error_type}]: {error_message}{context_str}",
            details={
                "original_error": error_type,
                "message": error_message,
                "context": context,
            }
        )

    logger.exception("Converted %s to %s", error_type, type(system_error).__name__)

    if raise_again:
        raise system_error from error

    return system_error
