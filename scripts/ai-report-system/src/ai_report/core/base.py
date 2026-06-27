"""
AI报告生成系统基类设计
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, ClassVar

from ..config import get_config
from .exceptions import ReportAgentError

logger = logging.getLogger(__name__)

T = TypeVar("T")
P = TypeVar("P")


@dataclass
class ComponentMetadata:
    """组件元数据"""
    name: str
    version: str
    description: str | None = None
    author: str | None = None
    dependencies: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_modified: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "dependencies": self.dependencies,
            "created_at": self.created_at.isoformat(),
            "last_modified": self.last_modified.isoformat(),
        }


class BaseComponent(abc.ABC):
    """
    基础组件抽象类
    所有系统组件都应该继承自此类
    """

    COMPONENT_NAME: ClassVar[str] = "BaseComponent"
    COMPONENT_VERSION: ClassVar[str] = "1.0.0"
    COMPONENT_DESCRIPTION: ClassVar[str] = "基础组件抽象类"

    def __init__(self, config: Any | None = None) -> None:
        """
        初始化组件

        Args:
            config: 可选配置，如果为None则使用全局配置

        Raises:
            ReportAgentError: 配置验证失败时抛出
        """
        self._config = config or get_config()
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._initialized = False
        self._metadata = ComponentMetadata(
            name=self.COMPONENT_NAME,
            version=self.COMPONENT_VERSION,
            description=self.COMPONENT_DESCRIPTION,
        )
        self._performance_stats: dict[str, Any] = {}

        self._initialize()

    def _initialize(self) -> None:
        """组件初始化逻辑（保护方法）"""
        self._logger.info("正在初始化组件: %s v%s", self.COMPONENT_NAME, self.COMPONENT_VERSION)

        try:
            self._validate_config()
            self._initialize_internal()

            self._performance_stats = {
                "total_calls": 0,
                "success_calls": 0,
                "failed_calls": 0,
                "total_time": 0.0,
                "avg_time": 0.0,
            }

            self._initialized = True
            self._logger.info("组件初始化完成: %s", self.COMPONENT_NAME)

        except Exception as e:
            self._logger.error("组件初始化失败: %s", e, exc_info=True)
            raise

    def _validate_config(self) -> None:
        """验证配置（保护方法）"""
        if not hasattr(self._config, 'system_config'):
            raise ReportAgentError("配置缺少system_config信息")

    def _initialize_internal(self) -> None:
        """具体组件的初始化逻辑（抽象方法）"""
        pass

    @abc.abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """
        执行组件主要功能（抽象方法）

        Returns:
            执行结果

        Raises:
            组件的具体异常
        """
        pass

    def _record_performance(self, start_time: float, success: bool = True) -> None:
        """记录性能统计"""
        elapsed = time.time() - start_time

        self._performance_stats["total_calls"] += 1
        if success:
            self._performance_stats["success_calls"] += 1
        else:
            self._performance_stats["failed_calls"] += 1

        total_time = self._performance_stats["total_time"] + elapsed
        self._performance_stats["total_time"] = total_time
        self._performance_stats["avg_time"] = total_time / self._performance_stats["total_calls"]

    def get_performance_stats(self) -> dict[str, Any]:
        """获取性能统计"""
        return self._performance_stats.copy()

    def get_metadata(self) -> ComponentMetadata:
        """获取组件元数据"""
        return self._metadata

    def __enter__(self) -> BaseComponent:
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """上下文管理器出口"""
        if exc_type is not None:
            self._logger.error("组件执行异常: %s", exc_val, exc_info=(exc_type, exc_val, exc_tb))
        return False

    def __repr__(self) -> str:
        """组件表示"""
        return f"<{self.__class__.__name__} name={self.COMPONENT_NAME} v{self.COMPONENT_VERSION}>"


class StatefulComponent(BaseComponent, abc.ABC):
    """有状态组件基类"""

    def __init__(self, config: Any | None = None) -> None:
        super().__init__(config)
        self._state: dict[str, Any] = {}
        self._state_file: Path | None = None

    def set_state(self, key: str, value: Any) -> None:
        """设置状态值"""
        self._state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """获取状态值"""
        return self._state.get(key, default)

    def has_state(self, key: str) -> bool:
        """检查状态是否存在"""
        return key in self._state

    def clear_state(self, key: str | None = None) -> None:
        """清除状态"""
        if key is None:
            self._state.clear()
        elif key in self._state:
            del self._state[key]

    def save_state(self, file_path: Path | None = None) -> None:
        """保存状态到文件"""
        if file_path is None:
            if self._state_file is None:
                state_dir = self._config.system_config.working_dir / "states"
                state_dir.mkdir(parents=True, exist_ok=True)
                self._state_file = state_dir / f"{self.COMPONENT_NAME.lower()}_state.json"
            file_path = self._state_file

        try:
            import json
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            self._logger.info("状态已保存到: %s", file_path)
        except (OSError, TypeError) as e:
            self._logger.error("保存状态失败: %s", e)
            raise ReportAgentError(f"保存状态失败: {e}") from e

    def load_state(self, file_path: Path | None = None) -> None:
        """从文件加载状态"""
        if file_path is None:
            if self._state_file is None:
                raise ReportAgentError("没有指定状态文件路径")
            file_path = self._state_file

        if not file_path.exists():
            self._logger.warning("状态文件不存在: %s，使用空状态", file_path)
            return

        try:
            import json
            with file_path.open("r", encoding="utf-8") as f:
                self._state = json.load(f)
            self._logger.info("状态已从 %s 加载", file_path)
        except (OSError, json.JSONDecodeError) as e:
            self._logger.error("加载状态失败: %s", e)
            raise ReportAgentError(f"加载状态失败: {e}") from e


class HermesToolComponent(BaseComponent, abc.ABC):
    """Hermes工具组件基类"""

    def __init__(self, config: Any | None = None) -> None:
        super().__init__(config)
        self._tool_initialized = False
        self._available_tools: list[str] = []

    def _initialize_internal(self) -> None:
        """初始化Hermes工具"""
        self._available_tools = ["browser", "web_search", "read_file", "write_file"]
        self._tool_initialized = True
        self._logger.info("Hermes工具组件初始化完成，可用工具: %s", self._available_tools)

    def check_tool_available(self, tool_name: str) -> bool:
        """检查工具是否可用"""
        return tool_name in self._available_tools

    def get_available_tools(self) -> list[str]:
        """获取可用工具列表"""
        return self._available_tools.copy()

    def _call_hermes_tool(self, tool_name: str, **kwargs: Any) -> Any:
        """
        调用Hermes工具

        Args:
            tool_name: 工具名称
            **kwargs: 工具参数

        Returns:
            工具调用结果

        Raises:
            ReportAgentError: 工具不可用或调用失败时抛出
        """
        if not self.check_tool_available(tool_name):
            raise ReportAgentError(f"Hermes工具不可用: {tool_name}")

        self._logger.debug("调用Hermes工具: %s, 参数: %s", tool_name, kwargs)

        mock_results = {
            "browser": {"title": "Mock Browser Result", "content": "Mock content"},
            "web_search": [{"title": "Mock Search Result", "summary": "Mock summary"}],
            "read_file": {"content": "Mock file content", "lines": 10},
            "write_file": {"success": True, "bytes_written": 100},
        }

        return mock_results.get(tool_name, {"status": "unknown"})
