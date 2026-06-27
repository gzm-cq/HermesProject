# Hermes 工程标准规范

> **版本**: 1.0  
> **适用范围**: 聚类分析 (clustering-analysis-v3)、知识导航 (knowledge-navigation)、AI报告生成 (ai-report)  
> **最后更新**: 2026-05-25

本文档定义 Hermes 项目下三个子项目重构时必须遵循的统一工程规范。所有新增代码和重构代码均须符合本文档要求。

---

## 目录

1. [项目结构标准](#1-项目结构标准)
2. [技术栈规范](#2-技术栈规范)
3. [pyproject.toml 模板](#3-pyprojecttoml-模板)
4. [Hermes 集成规范](#4-hermes-集成规范)
5. [代码规范](#5-代码规范)
6. [测试规范](#6-测试规范)

---

## 1. 项目结构标准

### 1.1 目录布局

所有子项目统一采用 **src layout**，源码包放在 `src/{package_name}/` 下：

```
project-name/
├── src/{package_name}/        # 源码包（src layout）
│   ├── __init__.py            # 干净的 __all__ 导出
│   ├── cli.py                 # CLI 入口（typer）
│   ├── config.py              # 配置管理（Dataclass + ENV 覆盖）
│   ├── core/                  # 核心业务逻辑
│   │   ├── __init__.py
│   │   └── ...
│   └── adapters/              # 外部集成适配层
│       ├── __init__.py
│       └── ...
├── tests/
│   ├── conftest.py            # 共享 fixtures
│   ├── test_config.py
│   ├── test_core/
│   │   └── ...
│   └── test_adapters/
│       └── ...
├── config/                    # 运行时配置文件（YAML/JSON）
├── plugin.yaml                # Hermes 插件元数据（如适用）
├── pyproject.toml
└── README.md
```

### 1.2 目录职责说明

| 目录 | 职责 | 约束 |
|------|------|------|
| `src/{package_name}/` | 项目源码根 | 必须是合法 Python 包 |
| `src/{package_name}/core/` | 核心业务逻辑 | **禁止**直接依赖外部 I/O（网络、数据库、文件系统） |
| `src/{package_name}/adapters/` | 外部系统集成 | 封装第三方 API、数据库驱动、文件操作等 |
| `tests/` | 测试代码 | 镜像 `src/` 结构，文件命名为 `test_{module}.py` |
| `config/` | 运行时配置文件 | 仅存放 YAML/JSON 默认配置，**禁止**存放密钥 |

### 1.3 各项目包名映射

| 项目 | 包名 (`package_name`) | 类型 |
|------|----------------------|------|
| clustering-analysis-v3 | `clustering_analysis` | 脚本模式 |
| knowledge-navigation | `knowledge_navigation` | 插件模式 |
| ai-report-system | `ai_report` | 脚本模式 |

---

## 2. 技术栈规范

### 2.1 核心依赖

| 类别 | 技术选型 | 版本要求 | 说明 |
|------|---------|---------|------|
| Python | `>=3.10` | 最低 3.10 | 使用 `match` 语句、`TypeAlias` 等特性 |
| 构建系统 | `setuptools>=45` + `wheel` | — | 后端 `setuptools.build_meta` |
| CLI 框架 | `typer>=0.9.0` | — | **所有项目统一**，禁止 argparse / click |
| 配置管理 | `dataclass` + ENV 覆盖 | 标准库 | 优先级：ENV > config file > default |
| 日志 | 标准 `logging` 模块 | 标准库 | 统一 JSON 格式（见 2.2） |
| 测试 | `pytest>=7.0` + `pytest-cov>=4.0` | — | 覆盖率目标 **80%+** |
| Lint | `ruff>=0.1.0` | — | 替代 flake8 / isort |
| Format | `black>=23.0` | — | line-length=100 |

### 2.2 日志格式

所有项目使用统一的 JSON 日志格式：

```python
import logging
import json
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """统一 JSON 日志格式器"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """初始化日志系统"""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
    )
```

### 2.3 配置管理模式

统一采用 `dataclass` + 环境变量覆盖模式：

```python
import os
from dataclasses import dataclass, field


@dataclass
class AppConfig:
    """应用配置，支持 ENV 变量覆盖"""

    # 优先级：ENV 变量 > 配置文件 > 默认值
    api_url: str = field(default="http://localhost:8080")
    timeout: int = field(default=30)
    debug: bool = field(default=False)

    @classmethod
    def from_env(cls, defaults: dict | None = None) -> "AppConfig":
        """从环境变量加载配置，覆盖默认值"""
        values = defaults or {}
        if env_url := os.getenv("APP_API_URL"):
            values["api_url"] = env_url
        if env_timeout := os.getenv("APP_TIMEOUT"):
            values["timeout"] = int(env_timeout)
        if env_debug := os.getenv("APP_DEBUG"):
            values["debug"] = env_debug.lower() in ("1", "true", "yes")
        return cls(**values)
```

---

## 3. pyproject.toml 模板

以下是标准 `pyproject.toml` 模板，各项目根据实际情况填充具体值：

```toml
[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "project-name"                    # 小写 + 连字符
version = "0.1.0"
description = "项目简短描述"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"
authors = [
    {name = "Hermes Team", email = "team@hermes.ai"},
]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
keywords = ["hermes", "keyword1", "keyword2"]
dependencies = [
    "typer>=0.9.0",
    # 在此添加运行时依赖
]

# ============================================================
# 入口点配置
# ============================================================

# 脚本模式：注册 CLI 入口
[project.entry-points.console_scripts]
"project-name" = "package_name.cli:app"

# 插件模式：注册 Hermes 插件（仅插件项目需要）
# [project.entry-points."hermes.plugins"]
# "plugin-id" = "package_name.main:register"

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
    "mypy>=1.0.0",
    "pre-commit>=3.0.0",
]

[project.urls]
Homepage = "https://github.com/hermes-ai/project-name"
Repository = "https://github.com/hermes-ai/project-name"

# ============================================================
# Setuptools 配置（src layout 必需）
# ============================================================

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"*" = ["*.json", "*.yaml", "*.yml"]

# ============================================================
# 工具配置
# ============================================================

[tool.black]
line-length = 100
target-version = ['py310']

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "B",    # flake8-bugbear
    "C4",   # flake8-comprehensions
    "UP",   # pyupgrade
    "SIM",  # flake8-simplify
]
ignore = [
    "E501",  # line too long, handled by black
    "B008",  # function call in argument defaults
]

[tool.ruff.lint.isort]
known-first-party = ["package_name"]

[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
no_implicit_optional = true
warn_redundant_casts = true
strict_equality = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "--strict-markers",
    "--strict-config",
    "--tb=short",
]
markers = [
    "unit: unit tests",
    "integration: integration tests (require external services)",
    "e2e: end-to-end tests",
]
```

### 3.1 模板使用说明

| 配置项 | 脚本模式 | 插件模式 |
|--------|---------|---------|
| `console_scripts` | **必须** | 不需要 |
| `hermes.plugins` | 不需要 | **必须** |
| `plugin.yaml` | 不需要 | **必须** |
| `typer` 依赖 | **必须** | 可选 |

---

## 4. Hermes 集成规范

### 4.1 插件模式

适用于 `knowledge-navigation` 等作为 Hermes 网关插件运行的项目。

#### plugin.yaml 格式

```yaml
name: plugin-id                    # 插件唯一标识，小写 + 连字符
version: "1.0.0"                   # 语义化版本
description: "插件功能描述"
author: Hermes Team
license: MIT
homepage: https://github.com/hermes-ai/plugin-name
hooks:
  pre_llm_call:                    # 钩子名称
    callback: pre_llm_call         # 回调函数名（在包内定义）
    description: "钩子功能描述"
    enabled: true                  # 是否默认启用
dependencies:                       # 运行时依赖
  - requests>=2.25.0
```

#### register() 函数

每个插件**必须**在 `src/{package_name}/main.py` 中实现 `register()` 函数：

```python
def register(ctx) -> None:
    """Hermes 插件注册入口。

    Args:
        ctx: Hermes 插件上下文对象，提供以下方法：
            - register_hook(hook_name: str, callback: Callable)
            - logger: 插件日志器
    """
    ctx.register_hook("pre_llm_call", pre_llm_call)
```

#### 插件入口点注册

在 `pyproject.toml` 中声明：

```toml
[project.entry-points."hermes.plugins"]
"plugin-id" = "package_name.main:register"
```

### 4.2 脚本模式

适用于 `clustering-analysis-v3`、`ai-report-system` 等独立运行的脚本。

#### CLI 入口

统一使用 `typer` 框架，入口文件为 `src/{package_name}/cli.py`：

```python
"""CLI 入口"""
from typing import Optional

import typer

app = typer.Typer(
    name="project-name",
    help="项目描述",
    add_completion=False,
)


@app.command()
def run(
    config: str = typer.Option("config/default.yaml", help="配置文件路径"),
    dry_run: bool = typer.Option(False, "--dry-run", help="试运行模式"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
) -> None:
    """执行主流程"""
    # 实现逻辑
    ...


if __name__ == "__main__":
    app()
```

#### console_scripts 入口点

```toml
[project.entry-points.console_scripts]
"project-name" = "package_name.cli:app"
```

#### 部署

通过 `deploy/deploy.sh` 部署到 Hermes 环境：

```bash
./deploy/deploy.sh list                # 列出可部署项目
./deploy/deploy.sh plan <project>      # 预览将部署的文件
./deploy/deploy.sh deploy <project>    # 部署指定项目（文件级备份 + 回滚）
```

### 4.3 配置查找顺序

所有项目统一遵循以下配置优先级（从高到低）：

```
环境变量 (ENV) > 项目 config/ 目录 > 代码默认值 (dataclass default)
```

| 优先级 | 来源 | 示例 | 说明 |
|--------|------|------|------|
| 1 (最高) | 环境变量 | `APP_API_URL=http://...` | 格式：`{PREFIX}_{KEY}` |
| 2 | 配置文件 | `config/default.yaml` | 项目级默认配置 |
| 3 (最低) | 代码默认值 | `api_url: str = "localhost"` | dataclass 字段默认值 |

### 4.4 adapters/ 目录规范

所有与外部系统交互的代码**必须**放在 `adapters/` 目录下，包括但不限于：

- 数据库连接（PostgreSQL、SQLite 等）
- HTTP API 调用（REST、GraphQL 等）
- 文件系统操作（读写配置、持久化数据）
- 第三方 SDK 集成（OpenAI、Hindsight 等）

```python
# src/{package_name}/adapters/db.py
"""数据库适配器 — 封装所有数据库操作"""
from typing import Any


class DatabaseAdapter:
    """数据库操作适配器，隔离底层驱动细节"""

    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string
        # ...

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """执行查询并返回结果"""
        # 实现细节...
        ...
```

**核心原则**：`core/` 中的业务逻辑**不得**直接 import 外部驱动库，必须通过 `adapters/` 层间接访问。

---

## 5. 代码规范

### 5.1 模块导入顺序

使用三段式导入，每组之间空一行，组内按字母序排列：

```python
# 1. 标准库
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 2. 第三方库
import typer
from rich.console import Console

# 3. 本项目模块
from package_name.config import AppConfig
from package_name.core.engine import Engine
from package_name.adapters.db import DatabaseAdapter
```

> ruff 的 `isort` 配置 (`I` 规则) 会自动检查和修复导入顺序。

### 5.2 类型注解

**所有函数签名必须包含类型注解**：

```python
# ✅ 正确
def process_data(items: list[str], max_count: int = 10) -> dict[str, Any]:
    ...

# ❌ 错误
def process_data(items, max_count=10):
    ...

# ✅ 正确 — 使用 Python 3.10+ 内置泛型
def find_user(name: str) -> User | None:
    ...

# ❌ 错误 — 不要使用 typing 旧式泛型（除非需要 from __future__ import annotations）
from typing import List, Optional, Dict
def find_user(name: str) -> Optional[User]:
    ...
```

类型注解规范：

| 场景 | 推荐写法 | 不推荐 |
|------|---------|--------|
| 可选类型 | `X \| None` | `Optional[X]` |
| 列表 | `list[X]` | `List[X]` |
| 字典 | `dict[str, X]` | `Dict[str, X]` |
| 元组 | `tuple[X, Y]` | `Tuple[X, Y]` |
| 联合类型 | `X \| Y` | `Union[X, Y]` |

### 5.3 异常处理规范

#### 自定义异常继承层次

所有项目**必须**定义统一的异常层次结构，放在 `src/{package_name}/core/exceptions.py`：

```python
"""项目异常定义"""


class ProjectError(Exception):
    """项目基础异常 — 所有自定义异常的基类"""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)


class ConfigError(ProjectError):
    """配置相关错误（解析失败、缺失必填项等）"""


class ConnectionError(ProjectError):
    """外部连接错误（API 不可达、数据库断连等）"""


class ValidationError(ProjectError):
    """数据验证错误（输入参数不合法等）"""


class ProcessingError(ProjectError):
    """业务处理错误（聚类失败、生成中断等）"""
```

#### 异常处理原则

```python
# ✅ 正确 — 捕获具体异常，提供上下文信息
try:
    result = adapter.call_api(url)
except ConnectionError as e:
    logger.error("API 调用失败", extra={"url": url, "error": str(e)})
    raise ProcessingError(f"处理中断: {e}", detail=str(e)) from e

# ❌ 错误 — 禁止裸 except
try:
    result = adapter.call_api(url)
except:
    pass

# ❌ 错误 — 禁止捕获 Exception 后忽略
try:
    result = adapter.call_api(url)
except Exception:
    pass
```

### 5.4 `__init__.py` 规范

#### 必须使用 `__all__` 显式导出

```python
"""包描述"""

from package_name.core.engine import Engine
from package_name.config import AppConfig

__all__ = [
    "AppConfig",
    "Engine",
]
```

#### 禁止 `__getattr__` 延迟加载

```python
# ❌ 禁止 — 使用 __getattr__ 延迟加载
def __getattr__(name: str) -> Any:
    if name == "Engine":
        from .core.engine import Engine
        return Engine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ✅ 正确 — 显式导入 + __all__ 声明
from package_name.core.engine import Engine

__all__ = ["Engine"]
```

> **理由**：`__getattr__` 延迟加载破坏了 IDE 自动补全、静态类型检查和代码可读性。如果模块导入开销较大，应通过优化模块结构解决，而非使用延迟加载。

#### `__init__.py` 内容要求

- 文件顶部包含模块级 docstring
- 仅导入和导出包的公共 API
- 禁止在 `__init__.py` 中执行副作用操作（如 `logging.basicConfig()`）
- 禁止在 `__init__.py` 中定义业务逻辑

---

## 6. 测试规范

### 6.1 测试文件命名

| 文件类型 | 命名规则 | 示例 |
|---------|---------|------|
| 测试文件 | `test_{module}.py` | `test_config.py`, `test_engine.py` |
| 测试类 | `Test{Feature}` | `TestConfigLoader`, `TestEngine` |
| 测试函数 | `test_{behavior}` | `test_load_config_from_env`, `test_process_empty_input` |
| Fixtures 文件 | `conftest.py` | 放在 `tests/` 根目录或子目录 |

### 6.2 测试目录结构

```
tests/
├── conftest.py                    # 全局 fixtures
├── test_config.py                 # 对应 src/{pkg}/config.py
├── test_cli.py                    # 对应 src/{pkg}/cli.py
├── core/
│   ├── __init__.py
│   ├── conftest.py                # core 模块专用 fixtures
│   └── test_engine.py            # 对应 src/{pkg}/core/engine.py
└── adapters/
    ├── __init__.py
    ├── conftest.py                # adapters 模块专用 fixtures
    └── test_db.py                # 对应 src/{pkg}/adapters/db.py
```

### 6.3 Fixtures 规范

所有共享 fixtures 放在 `conftest.py` 中：

```python
# tests/conftest.py
"""全局测试 fixtures"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from package_name.config import AppConfig


@pytest.fixture
def default_config() -> AppConfig:
    """返回默认测试配置"""
    return AppConfig()


@pytest.fixture
def mock_db_adapter() -> MagicMock:
    """返回 mock 的数据库适配器"""
    adapter = MagicMock()
    adapter.query.return_value = [{"id": 1, "name": "test"}]
    return adapter


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> Path:
    """创建临时配置文件"""
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text("api_url: http://test.local\ntimeout: 5\n")
    return config_file
```

### 6.4 Mock 外部依赖

**所有外部依赖必须在测试中 Mock**，包括：

- 数据库（PostgreSQL、SQLite）
- HTTP API 调用
- 文件系统读写（使用 `tmp_path` fixture）
- 第三方 SDK

```python
# ✅ 正确 — Mock 外部依赖
from unittest.mock import patch, MagicMock

def test_fetch_user_from_api(mock_db_adapter: MagicMock) -> None:
    """测试从 API 获取用户数据"""
    mock_db_adapter.query.return_value = [{"id": 1, "name": "Alice"}]
    result = fetch_user(mock_db_adapter, user_id=1)
    assert result["name"] == "Alice"

# ✅ 正确 — 使用 patch 替换模块级依赖
@patch("package_name.adapters.api.requests.get")
def test_api_adapter_call(mock_get: MagicMock) -> None:
    """测试 API 适配器调用"""
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "ok"}
    adapter = ApiAdapter("http://test.local")
    result = adapter.health_check()
    assert result is True
```

### 6.5 集成测试标记

需要外部服务（数据库、API）的测试**必须**标记为 `@pytest.mark.integration`：

```python
import pytest

@pytest.mark.integration
def test_real_database_connection() -> None:
    """集成测试 — 需要 PostgreSQL 运行"""
    adapter = DatabaseAdapter(os.getenv("TEST_DB_URL"))
    result = adapter.query("SELECT 1")
    assert result is not None
```

运行测试时排除集成测试：

```bash
# 仅运行单元测试
pytest -m "not integration"

# 仅运行集成测试
pytest -m "integration"

# 运行所有测试
pytest
```

### 6.6 覆盖率要求

- **目标覆盖率**: 80%+
- **核心模块** (`core/`): 建议覆盖率 90%+
- **适配器模块** (`adapters/`): 可以较低，但接口必须测试

运行覆盖率检查：

```bash
pytest --cov=package_name --cov-report=term-missing --cov-fail-under=80
```

### 6.7 测试编写原则

| 原则 | 说明 |
|------|------|
| Arrange-Act-Assert | 测试体按三段式组织 |
| 单一断言优先 | 每个测试函数验证一个行为 |
| 测试行为而非实现 | 不测试私有方法，测试公共接口 |
| 确定性 | 测试结果不依赖时间、随机数、网络 |
| 独立性 | 测试之间无依赖关系，可任意顺序执行 |

---

## 附录：重构检查清单

重构每个子项目时，对照以下清单逐项确认：

- [ ] 目录结构符合 src layout 规范
- [ ] `pyproject.toml` 使用标准模板，配置完整
- [ ] Python 版本要求 `>=3.10`
- [ ] CLI 使用 `typer` 框架
- [ ] 配置管理使用 dataclass + ENV 覆盖
- [ ] 日志使用标准 `logging` + JSON 格式
- [ ] 外部集成代码放在 `adapters/` 目录
- [ ] `__init__.py` 使用 `__all__` 导出，无 `__getattr__`
- [ ] 函数签名均有类型注解
- [ ] 自定义异常继承自 `ProjectError`
- [ ] 导入顺序：stdlib → third-party → local
- [ ] 测试覆盖率 >= 80%
- [ ] 集成测试标记 `@pytest.mark.integration`
- [ ] ruff lint 通过
- [ ] black format 通过（line-length=100）
- [ ] mypy 类型检查通过
