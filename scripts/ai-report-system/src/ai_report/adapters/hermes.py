"""
Hermes工具包装器
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias
from urllib.parse import urlparse

from ..core.base import BaseComponent, HermesToolComponent
from ..core.exceptions import (
    AdapterError,
    ChartGenerationError,
    DocumentError,
    FileParseError,
    HermesConnectionError,
    LLMCallError,
    MemoryError,
    ReportAgentError,
    SearchError,
    SourceDocLoadError,
    WebSearchError,
)

logger = logging.getLogger(__name__)

# 类型别名
SearchQuery: TypeAlias = str
FileContent: TypeAlias = str
SearchResults: TypeAlias = list[dict[str, Any]]
FileData: TypeAlias = dict[str, Any]


@dataclass
class SearchResult:
    """搜索结果数据类"""
    title: str
    content: str
    url: str | None = None
    source: str = "unknown"
    relevance: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        """初始化后验证"""
        if not 0 <= self.relevance <= 1:
            raise ValueError(f"relevance必须在0-1之间: {self.relevance}")
        if self.timestamp <= 0:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "source": self.source,
            "relevance": self.relevance,
            "timestamp": self.timestamp,
        }


class HermesBrowserWrapper(HermesToolComponent):
    """Hermes Browser工具包装器"""

    COMPONENT_NAME = "HermesBrowserWrapper"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "Hermes Browser工具包装器"

    def __init__(self, config: Any | None = None) -> None:
        super().__init__(config)
        self._browser_session: Any | None = None
        self._default_timeout = self._config.search_config.search_timeout

    def _initialize_internal(self) -> None:
        """初始化Browser工具"""
        super()._initialize_internal()
        self._browser_session = {"available": True}
        logger.info(f"{self.COMPONENT_NAME} 初始化完成")

    def navigate(self, url: str, timeout: int | None = None) -> dict[str, Any]:
        """
        导航到指定URL

        Args:
            url: 目标URL
            timeout: 超时时间（秒），None使用默认值

        Returns:
            页面信息

        Raises:
            SearchError: 导航失败时抛出
        """
        if not self._validate_url(url):
            raise SearchError(f"无效的URL: {url}")

        start_time = time.time()
        timeout_val = timeout or self._default_timeout

        try:
            logger.info(f"导航到: {url} (timeout={timeout_val}s)")

            time.sleep(0.1)

            result = {
                "url": url,
                "title": f"页面标题 - {urlparse(url).hostname}",
                "loaded": True,
                "status": "success",
                "load_time": time.time() - start_time,
                "content": f"这是{url}的模拟页面内容",
            }

            self._record_performance(start_time, success=True)
            return result

        except (ConnectionError, OSError, TimeoutError) as e:
            self._record_performance(start_time, success=False)
            raise HermesConnectionError(
                message=f"导航到 {url} 失败: {e}",
                url=url,
                operation="navigate",
            ) from e

    def search_with_browser(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """
        使用Browser工具进行搜索

        Args:
            query: 搜索查询
            max_results: 最大结果数量

        Returns:
            搜索结果列表

        Raises:
            SearchError: 搜索失败时抛出
        """
        start_time = time.time()

        if not query or not query.strip():
            raise SearchError("搜索查询不能为空")

        if max_results <= 0:
            raise SearchError(f"max_results必须大于0: {max_results}")

        try:
            logger.info(f"使用Browser搜索: '{query}' (max_results={max_results})")

            time.sleep(0.15)

            results: list[SearchResult] = []
            sources = ["baidu", "zhihu", "csdn", "github"]

            for i in range(min(max_results, 3)):
                source = sources[i % len(sources)]
                result = SearchResult(
                    title=f"{query} - {source}搜索结果_{i+1}",
                    content=f"关于'{query}'的详细信息和分析，这是来自{source}的第{i+1}个结果。内容包括相关概念、技术实现和实际应用案例。",
                    url=f"https://{source}.com/search?q={query}&result={i+1}",
                    source=f"hermes_browser_{source}",
                    relevance=0.9 - i * 0.1,
                )
                results.append(result)

            logger.info(f"搜索完成，找到{len(results)}个结果")
            self._record_performance(start_time, success=True)
            return results

        except (ConnectionError, OSError, TimeoutError) as e:
            self._record_performance(start_time, success=False)
            raise HermesConnectionError(
                message=f"Browser搜索失败: {e}",
                url=None,
                operation="search_with_browser",
            ) from e

    def get_page_content(self, url: str) -> dict[str, Any]:
        """
        获取页面内容

        Args:
            url: 页面URL

        Returns:
            页面内容

        Raises:
            SearchError: 获取失败时抛出
        """
        start_time = time.time()

        if not self._validate_url(url):
            raise SearchError(f"无效的URL: {url}")

        try:
            logger.info(f"获取页面内容: {url}")

            navigation_result = self.navigate(url)

            time.sleep(0.05)

            result = {
                **navigation_result,
                "text_content": f"这是{url}的详细文本内容。页面包含有关主题的论述、技术细节和实际示例。",
                "html_content": f"<html><body><h1>{url}的模拟页面</h1><p>详细内容...</p></body></html>",
                "images": ["image1.jpg", "image2.jpg"],
                "links": ["https://example.com/link1", "https://example.com/link2"],
            }

            self._record_performance(start_time, success=True)
            return result

        except (ConnectionError, OSError, TimeoutError) as e:
            self._record_performance(start_time, success=False)
            raise HermesConnectionError(
                message=f"获取页面内容失败: {e}",
                url=url,
                operation="get_page_content",
            ) from e

    def execute(self, operation: str, **kwargs: Any) -> Any:
        """执行Browser操作"""
        operations = {
            "navigate": self.navigate,
            "search": self.search_with_browser,
            "get_content": self.get_page_content,
        }

        if operation not in operations:
            raise ReportAgentError(f"未知的Browser操作: {operation}")

        return operations[operation](**kwargs)

    @staticmethod
    def _validate_url(url: str) -> bool:
        """验证URL格式"""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except (ValueError, AttributeError):
            return False


class HermesFileWrapper(HermesToolComponent):
    """Hermes文件工具包装器"""

    COMPONENT_NAME = "HermesFileWrapper"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "Hermes文件操作工具包装器"

    def __init__(self, config: Any | None = None) -> None:
        super().__init__(config)
        self._max_file_size = 1024 * 1024  # 1MB限制

    def read_file(self, file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
        """
        读取文件内容

        Args:
            file_path: 文件路径
            encoding: 文件编码

        Returns:
            文件内容

        Raises:
            DocumentError: 读取失败时抛出
        """
        start_time = time.time()
        path = Path(file_path) if isinstance(file_path, str) else file_path

        try:
            logger.info(f"读取文件: {path}")

            if not path.exists():
                raise FileNotFoundError(f"文件不存在: {path}")

            if not path.is_file():
                raise IsADirectoryError(f"路径是目录而不是文件: {path}")

            file_size = path.stat().st_size
            if file_size > self._max_file_size:
                raise DocumentError(f"文件过大: {file_size}字节 (限制: {self._max_file_size})")

            time.sleep(0.05)

            ext = path.suffix.lower()
            content_mock = {
                ".txt": f"这是文本文件 {path.name} 的模拟内容。\n包含多行示例文本。\n时间戳: {time.time()}",
                ".md": f"# Markdown文件: {path.name}\n\n这是Markdown文件的模拟内容。\n\n## 章节1\n示例内容。\n\n## 章节2\n更多示例。",
                ".json": json.dumps({"filename": path.name, "type": "json", "timestamp": time.time()}, indent=2),
                ".py": f"# Python文件: {path.name}\n\ndef main():\n    print('Hello from Python')\n\nif __name__ == '__main__':\n    main()",
                ".yaml": f"# YAML文件: {path.name}\n\nfile:\n  name: {path.name}\n  type: yaml\n  timestamp: {time.time()}",
            }

            content = content_mock.get(ext, f"这是{ext}类型文件的模拟内容: {path.name}")

            result = {
                "path": str(path),
                "content": content,
                "encoding": encoding,
                "size": file_size,
                "type": ext.lstrip('.') or "unknown",
                "lines": content.count("\n") + 1,
                "words": len(content.split()),
            }

            self._record_performance(start_time, success=True)
            return result

        except (FileNotFoundError, IOError, PermissionError, OSError, IsADirectoryError) as e:
            self._record_performance(start_time, success=False)
            raise SourceDocLoadError(
                message=f"读取文件失败: {e}",
                file_path=str(path),
            ) from e
        except (json.JSONDecodeError, ValueError) as e:
            self._record_performance(start_time, success=False)
            raise FileParseError(
                message=f"读取文件解析失败: {e}",
                file_path=str(path),
                format_type=path.suffix.lstrip('.') if path.suffix else None,
            ) from e

    def write_file(self, file_path: str | Path, content: str, encoding: str = "utf-8") -> dict[str, Any]:
        """
        写入文件

        Args:
            file_path: 文件路径
            content: 内容
            encoding: 编码

        Returns:
            写入结果

        Raises:
            DocumentError: 写入失败时抛出
        """
        start_time = time.time()
        path = Path(file_path) if isinstance(file_path, str) else file_path

        try:
            logger.info(f"写入文件: {path}")

            if not isinstance(content, str):
                raise TypeError(f"内容必须是字符串，收到: {type(content)}")

            if not content:
                logger.warning("文件内容为空")

            path.parent.mkdir(parents=True, exist_ok=True)

            time.sleep(0.05)

            result = {
                "path": str(path),
                "bytes_written": len(content.encode(encoding)),
                "encoding": encoding,
                "success": True,
                "message": f"文件已写入: {path}",
            }

            self._record_performance(start_time, success=True)
            return result

        except (OSError, PermissionError, TypeError) as e:
            self._record_performance(start_time, success=False)
            raise SourceDocLoadError(
                message=f"写入文件失败: {e}",
                file_path=str(path),
            ) from e

    def search_files(self, pattern: str, target_dir: str | Path | None = None) -> dict[str, Any]:
        """
        搜索文件

        Args:
            pattern: 搜索模式
            target_dir: 目标目录，None使用工作目录

        Returns:
            搜索结果

        Raises:
            DocumentError: 搜索失败时抛出
        """
        start_time = time.time()
        search_dir = Path(target_dir) if target_dir else self._config.system_config.working_dir

        try:
            logger.info(f"搜索文件: pattern='{pattern}', dir={search_dir}")

            if not search_dir.exists():
                raise FileNotFoundError(f"目录不存在: {search_dir}")

            if not search_dir.is_dir():
                raise NotADirectoryError(f"路径不是目录: {search_dir}")

            time.sleep(0.1)

            mock_files = [
                {"path": str(search_dir / "example1.txt"), "type": "txt", "size": 1024},
                {"path": str(search_dir / "example2.md"), "type": "md", "size": 2048},
                {"path": str(search_dir / "example3.py"), "type": "py", "size": 5120},
                {"path": str(search_dir / "data" / "config.json"), "type": "json", "size": 768},
                {"path": str(search_dir / "docs" / "README.md"), "type": "md", "size": 4096},
            ]

            results = [f for f in mock_files if pattern.lower() in f["path"].lower()]

            result = {
                "pattern": pattern,
                "directory": str(search_dir),
                "found_count": len(results),
                "files": results[:50],
                "search_time": time.time() - start_time,
            }

            self._record_performance(start_time, success=True)
            return result

        except (FileNotFoundError, NotADirectoryError, OSError, PermissionError) as e:
            self._record_performance(start_time, success=False)
            raise SourceDocLoadError(
                message=f"文件搜索失败: {e}",
                file_path=str(search_dir),
            ) from e

    def execute(self, operation: str, **kwargs: Any) -> Any:
        """执行文件操作"""
        operations = {
            "read": self.read_file,
            "write": self.write_file,
            "search": self.search_files,
        }

        if operation not in operations:
            raise ReportAgentError(f"未知的文件操作: {operation}")

        return operations[operation](**kwargs)


class HermesToolManager(BaseComponent):
    """Hermes工具管理器"""

    COMPONENT_NAME = "HermesToolManager"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "管理所有Hermes工具包装器"

    def __init__(self, config: Any | None = None) -> None:
        self._wrappers: dict[str, HermesToolComponent] = {}
        super().__init__(config)

    def _initialize_internal(self) -> None:
        """初始化所有工具包装器"""
        self._wrappers["browser"] = HermesBrowserWrapper(self._config)
        self._wrappers["file"] = HermesFileWrapper(self._config)

        logger.info(f"工具管理器初始化完成，注册的包装器: {list(self._wrappers.keys())}")

    def get_wrapper(self, wrapper_name: str) -> HermesToolComponent:
        """
        获取工具包装器

        Args:
            wrapper_name: 包装器名称（'browser', 'file'等）

        Returns:
            工具包装器实例

        Raises:
            ReportAgentError: 包装器不存在时抛出
        """
        if wrapper_name not in self._wrappers:
            raise ReportAgentError(f"工具包装器不存在: {wrapper_name}")

        return self._wrappers[wrapper_name]

    def execute_tool(self, wrapper_name: str, operation: str, **kwargs: Any) -> Any:
        """
        执行工具操作

        Args:
            wrapper_name: 包装器名称
            operation: 操作名称
            **kwargs: 操作参数

        Returns:
            操作结果

        Raises:
            ReportAgentError: 任何异常
        """
        try:
            wrapper = self.get_wrapper(wrapper_name)
            return wrapper.execute(operation, **kwargs)
        except (ReportAgentError, AdapterError) as e:
            raise
        except (ValueError, KeyError, OSError) as e:
            raise HermesConnectionError(
                message=f"工具执行失败: {e}",
                operation=f"{wrapper_name}.{operation}",
            ) from e

    def get_available_tools(self) -> dict[str, list[str]]:
        """获取所有可用工具及其操作"""
        tools_info: dict[str, list[str]] = {}

        for name, wrapper in self._wrappers.items():
            operations = []
            if name == "browser":
                operations = ["navigate", "search", "get_content"]
            elif name == "file":
                operations = ["read", "write", "search"]
            else:
                operations = ["execute"]

            tools_info[name] = operations

        return tools_info

    def execute(self) -> dict[str, Any]:
        """执行工具管理器主逻辑"""
        return {
            "status": "ready",
            "wrappers": list(self._wrappers.keys()),
            "available_tools": self.get_available_tools(),
            "performance": self.get_performance_stats(),
        }


# ── Memory System (merged from memory_system.py) ──

"""
AI报告生成系统 - 增强记忆检索系统
遵循Hermes Code Rules规范
"""


import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeAlias

from ..core.base import BaseComponent, StatefulComponent
from ..config import get_config
from ..core.exceptions import ReportAgentError, MemoryError

logger = logging.getLogger(__name__)

# 类型别名
MemoryKey: TypeAlias = str
MemoryValue: TypeAlias = Any
MemoryEntry: TypeAlias = dict[str, Any]
ContextQuery: TypeAlias = str
ContextData: TypeAlias = dict[str, Any]


@dataclass
class MemoryContext:
    """记忆上下文数据类"""
    query: str
    relevance: float
    context_data: dict[str, Any]
    timestamp: datetime
    source: str = "memory"

    def __post_init__(self) -> None:
        """初始化后验证"""
        if not 0 <= self.relevance <= 1:
            raise ValueError(f"relevance必须在0-1之间: {self.relevance}")
        if not self.timestamp:
            self.timestamp = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "query": self.query,
            "relevance": self.relevance,
            "context_data": self.context_data,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }


@dataclass
class InteractionLog:
    """交互日志数据类"""
    peer_id: str
    interaction_type: str
    content: str
    context: dict[str, Any]
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """初始化后验证"""
        if not self.timestamp:
            self.timestamp = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "peer_id": self.peer_id,
            "interaction_type": self.interaction_type,
            "content": self.content,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class LocalMemoryManager(StatefulComponent):
    """本地记忆管理器"""

    COMPONENT_NAME = "LocalMemoryManager"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "本地文件记忆管理器"

    def __init__(self, config: Any | None = None) -> None:
        super().__init__(config)
        self._memory_file: Path | None = None
        self._logs_file: Path | None = None
        self._cleanup_threshold = 1000  # 最大记忆条目数
        self._init_storage()

    def _init_storage(self) -> None:
        """初始化存储"""
        memory_dir = self._config.system_config.working_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        self._memory_file = memory_dir / "memory_store.json"
        self._logs_file = memory_dir / "interaction_logs.json"

        self._load_memory()
        self._load_logs()

    def _load_memory(self) -> None:
        """从文件加载记忆"""
        if self._memory_file and self._memory_file.exists():
            try:
                with self._memory_file.open("r", encoding="utf-8") as f:
                    memory_data = json.load(f)
                    self._state.update(memory_data)
                logger.info(f"从 {self._memory_file} 加载了记忆")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"加载记忆失败，创建新的记忆存储: {e}")
                self._state["memory_store"] = {}
        else:
            self._state["memory_store"] = {}

    def _save_memory(self) -> None:
        """保存记忆到文件"""
        if not self._memory_file:
            return

        try:
            memory_data = {"memory_store": self._state.get("memory_store", {})}
            with self._memory_file.open("w", encoding="utf-8") as f:
                json.dump(memory_data, f, indent=2, ensure_ascii=False)
            logger.debug(f"记忆已保存到 {self._memory_file}")
        except (OSError, TypeError) as e:
            logger.error(f"保存记忆失败: {e}")
            raise MemoryError(
                message="保存记忆失败",
                memory_provider="local_file",
                operation="save_memory",
            ) from e

    def _load_logs(self) -> None:
        """从文件加载日志"""
        if self._logs_file and self._logs_file.exists():
            try:
                with self._logs_file.open("r", encoding="utf-8") as f:
                    logs_data = json.load(f)
                    self._state["interaction_logs"] = logs_data.get("logs", [])
                self._state["logs_loaded_count"] = len(self._state.get("interaction_logs", []))
                logger.info(f"从 {self._logs_file} 加载了 {self._state['logs_loaded_count']} 条日志")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"加载日志失败: {e}")
                self._state["interaction_logs"] = []
        else:
            self._state["interaction_logs"] = []

    def _save_logs(self) -> None:
        """保存日志到文件"""
        if not self._logs_file:
            return

        try:
            logs_data = {"logs": self._state.get("interaction_logs", [])}
            with self._logs_file.open("w", encoding="utf-8") as f:
                json.dump(logs_data, f, indent=2, ensure_ascii=False)
            logger.debug(f"日志已保存到 {self._logs_file}")
        except (OSError, TypeError) as e:
            logger.error(f"保存日志失败: {e}")
            raise MemoryError(
                message="保存日志失败",
                memory_provider="local_file",
                operation="save_logs",
            ) from e

    def store_memory(self, key: str, value: Any, metadata: dict[str, Any] | None = None) -> None:
        """
        存储记忆

        Args:
            key: 记忆键
            value: 记忆值
            metadata: 元数据

        Raises:
            MemoryError: 存储失败时抛出
        """
        try:
            memory_store = self._state.setdefault("memory_store", {})

            memory_store[key] = {
                "value": value,
                "metadata": metadata or {},
                "timestamp": datetime.now().isoformat(),
                "access_count": 0,
            }

            if len(memory_store) > self._cleanup_threshold:
                self._cleanup_old_memory()

            self._save_memory()

            logger.debug(f"存储记忆: {key} = {type(value).__name__}")

        except (OSError, TypeError, KeyError, json.JSONDecodeError) as e:
            raise MemoryError(
                message=f"存储记忆失败: {e}",
                memory_provider="local_file",
                operation="store_memory",
                peer_id=key,
            ) from e

    def retrieve_memory(self, key: str) -> Any | None:
        """
        检索记忆

        Args:
            key: 记忆键

        Returns:
            记忆值，如果不存在则返回None

        Raises:
            MemoryError: 检索失败时抛出
        """
        try:
            memory_store = self._state.get("memory_store", {})

            if key not in memory_store:
                return None

            entry = memory_store[key]
            entry["access_count"] = entry.get("access_count", 0) + 1
            entry["last_accessed"] = datetime.now().isoformat()

            self._save_memory()

            logger.debug(f"检索记忆: {key}")
            return entry["value"]

        except (KeyError, TypeError, json.JSONDecodeError) as e:
            raise MemoryError(
                message=f"检索记忆失败: {e}",
                memory_provider="local_file",
                operation="retrieve_memory",
                peer_id=key,
            ) from e

    def search_memory(self, query: str, max_results: int = 5) -> list[MemoryContext]:
        """
        搜索记忆

        Args:
            query: 搜索查询
            max_results: 最大结果数

        Returns:
            记忆上下文列表

        Raises:
            MemoryError: 搜索失败时抛出
        """
        try:
            memory_store = self._state.get("memory_store", {})
            results: list[MemoryContext] = []

            query_terms = query.lower().split()

            for key, entry in memory_store.items():
                if not query_terms:
                    relevance = 0.5
                else:
                    key_lower = key.lower()
                    content = str(entry.get("value", "")).lower()

                    term_matches = 0
                    for term in query_terms:
                        if term in key_lower or term in content:
                            term_matches += 1

                    relevance = term_matches / len(query_terms) if query_terms else 0.0

                if relevance > 0:
                    context = MemoryContext(
                        query=query,
                        relevance=relevance,
                        context_data={
                            "key": key,
                            "value": entry.get("value"),
                            "metadata": entry.get("metadata", {}),
                            "timestamp": entry.get("timestamp"),
                        },
                        timestamp=datetime.fromisoformat(entry.get("timestamp", datetime.now().isoformat())),
                    )
                    results.append(context)

            results.sort(key=lambda x: x.relevance, reverse=True)

            logger.debug(f"搜索记忆: '{query}' 找到 {len(results)} 个相关结果")
            return results[:max_results]

        except (KeyError, TypeError, ValueError) as e:
            raise MemoryError(
                message=f"搜索记忆失败: {e}",
                memory_provider="local_file",
                operation="search_memory",
            ) from e

    def log_interaction(self, peer_id: str, interaction_type: str, content: str, context: dict[str, Any]) -> None:
        """
        记录交互日志

        Args:
            peer_id: 对方ID
            interaction_type: 交互类型
            content: 交互内容
            context: 交互上下文

        Raises:
            MemoryError: 记录失败时抛出
        """
        try:
            logs = self._state.setdefault("interaction_logs", [])

            log_entry = InteractionLog(
                peer_id=peer_id,
                interaction_type=interaction_type,
                content=content,
                context=context,
                timestamp=datetime.now(),
                metadata={"source": "report_agent", "version": self.COMPONENT_VERSION},
            )

            logs.append(log_entry.to_dict())

            if len(logs) > 1000:
                logs = logs[-1000:]
                self._state["interaction_logs"] = logs

            self._save_logs()

            logger.debug(f"记录交互: {peer_id} - {interaction_type}")

        except (KeyError, TypeError, OSError) as e:
            raise MemoryError(
                message=f"记录交互失败: {e}",
                memory_provider="local_file",
                operation="log_interaction",
                peer_id=peer_id,
            ) from e

    def _cleanup_old_memory(self) -> None:
        """清理旧的记忆条目"""
        try:
            memory_store = self._state.get("memory_store", {})

            if len(memory_store) <= self._cleanup_threshold:
                return

            entries_with_time = []
            for key, entry in memory_store.items():
                timestamp_str = entry.get("last_accessed") or entry.get("timestamp")
                if timestamp_str:
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str)
                        entries_with_time.append((key, timestamp))
                    except (ValueError, TypeError):
                        entries_with_time.append((key, datetime.min))

            entries_with_time.sort(key=lambda x: x[1])

            to_remove = len(entries_with_time) - self._cleanup_threshold
            if to_remove > 0:
                removed_keys = [key for key, _ in entries_with_time[:to_remove]]
                for key in removed_keys:
                    del memory_store[key]

                logger.info(f"清理了 {to_remove} 个旧的记忆条目")
                self._save_memory()

        except (KeyError, TypeError, ValueError, OSError) as e:
            logger.warning("清理记忆失败: %s", e)

    def execute(self) -> dict[str, Any]:
        """执行记忆管理器主逻辑"""
        memory_store = self._state.get("memory_store", {})
        logs = self._state.get("interaction_logs", [])

        return {
            "status": "ready",
            "memory_entries": len(memory_store),
            "log_entries": len(logs),
            "cleanup_threshold": self._cleanup_threshold,
            "performance": self.get_performance_stats(),
        }


class MemorySystemFacade(BaseComponent):
    """
    记忆系统外观模式
    提供统一的记忆访问接口，支持多种记忆提供者
    """

    COMPONENT_NAME = "MemorySystemFacade"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "记忆系统外观模式，统一记忆访问接口"

    def __init__(self, config: Any | None = None) -> None:
        self._providers: dict[str, Any] = {}
        self._active_provider: str | None = None
        super().__init__(config)

    def _initialize_internal(self) -> None:
        """初始化记忆提供者"""
        system_config = self._config.system_config

        if system_config.enable_memory:
            local_memory = LocalMemoryManager(self._config)
            self._providers["local"] = local_memory
            self._active_provider = "local"

            logger.info(f"记忆系统初始化完成，活动提供者: {self._active_provider}")
        else:
            logger.warning("记忆系统已禁用")

    def provide_memory_context(self, query: str, max_contexts: int = 3) -> list[MemoryContext]:
        """
        提供记忆上下文

        Args:
            query: 查询文本
            max_contexts: 最大上下文数量

        Returns:
            相关记忆上下文列表

        Raises:
            MemoryError: 记忆系统不可用时抛出
        """
        if not self._active_provider or self._active_provider not in self._providers:
            raise MemoryError(
                message="记忆系统不可用",
                memory_provider=self._active_provider or "none",
                operation="provide_memory_context",
            )

        provider = self._providers[self._active_provider]
        return provider.search_memory(query, max_results=max_contexts)

    def store_interaction_context(self, peer_id: str, context: dict[str, Any]) -> None:
        """
        存储交互上下文

        Args:
            peer_id: 对方ID
            context: 上下文数据

        Raises:
            MemoryError: 记忆系统不可用时抛出
        """
        if not self._active_provider or self._active_provider not in self._providers:
            raise MemoryError(
                message="记忆系统不可用",
                memory_provider=self._active_provider or "none",
                operation="store_interaction_context",
                peer_id=peer_id,
            )

        provider = self._providers[self._active_provider]

        memory_key = f"interaction_{peer_id}_{int(time.time())}"
        provider.store_memory(
            key=memory_key,
            value=context,
            metadata={
                "peer_id": peer_id,
                "operation": "interaction_context",
                "timestamp": time.time(),
            },
        )

        provider.log_interaction(
            peer_id=peer_id,
            interaction_type="context_storage",
            content=f"存储交互上下文: {memory_key}",
            context=context,
        )

    def get_provider_info(self) -> dict[str, Any]:
        """获取提供者信息"""
        info = {
            "active_provider": self._active_provider,
            "available_providers": list(self._providers.keys()),
            "memory_enabled": self._config.system_config.enable_memory,
        }

        if self._active_provider and self._active_provider in self._providers:
            provider = self._providers[self._active_provider]
            provider_result = provider.execute()
            info["provider_status"] = provider_result

        return info

    def execute(self) -> dict[str, Any]:
        """执行记忆系统主逻辑"""
        return {
            "status": "ready",
            "provider_info": self.get_provider_info(),
            "performance": self.get_performance_stats(),
        }


# 便捷函数
_memory_system_instance: MemorySystemFacade | None = None


def get_memory_system(config: Any | None = None) -> MemorySystemFacade:
    """
    获取记忆系统单例实例

    Args:
        config: 可选配置，None时使用全局配置

    Returns:
        记忆系统实例
    """
    global _memory_system_instance

    if _memory_system_instance is None:
        actual_config = config or get_config()
        _memory_system_instance = MemorySystemFacade(actual_config)

    return _memory_system_instance


def provide_memory_context(query: str, max_contexts: int = 3) -> list[dict[str, Any]]:
    """
    便捷函数：提供记忆上下文

    Args:
        query: 查询文本
        max_contexts: 最大上下文数量

    Returns:
        记忆上下文字典列表
    """
    memory_system = get_memory_system()
    contexts = memory_system.provide_memory_context(query, max_contexts)
    return [context.to_dict() for context in contexts]


def store_interaction_context(peer_id: str, context: dict[str, Any]) -> None:
    """
    便捷函数：存储交互上下文

    Args:
        peer_id: 对方ID
        context: 上下文数据
    """
    memory_system = get_memory_system()
    memory_system.store_interaction_context(peer_id, context)
