---
alwaysApply: true
---

# 测试规范 — HermesProject
# ============================================
# Python + pytest 规范
# 最后更新：2026-05-27
# ============================================

## [TEST-1] 测试完整性（强制）

- 新功能必须包含对应单元测试
- Bug 修复必须包含回归测试
- 公共 API 变更必须更新集成测试
- 不允许提交未测试的代码

## [TEST-2] 测试框架

- 统一用 `pytest`（不使用 unittest）
- 配置文件：各子项目 `pyproject.toml` 中的 `[tool.pytest.ini_options]`
- 运行方式：`pytest -v tests/`

## [TEST-3] 覆盖率目标

| 项目类型 | 行覆盖率 | 关键路径 |
|----------|----------|----------|
| 主库代码（src/） | 80%+ | 90%+ |
| 兼容层（compat/） | 70%+ | 85%+ |
| CLI/脚本入口 | 60%+ | - |

## [TEST-4] 测试分层

- 单元测试（70%）：快速、隔离，针对单个函数/类
- 集成测试（20%）：验证模块间交互（如数据库连接）
- E2E 测试（10%）：核心流程端到端

## [TEST-5] Mock 规范

- 外部依赖（LLM API、数据库、文件系统）必须 Mock
- 使用 `unittest.mock` 或 `pytest-mock`
- 集成测试中尽量减少 Mock，用真实 SQLite 内存库

## [TEST-6] Fixture 管理

- 使用 `conftest.py` 定义共享 Fixture
- 测试数据用工厂函数，每个测试独立
- 避免共享状态，测试后清理

## [TEST-7] 测试命名

- 文件：`test_<module_name>.py`
- 类：`Test<ClassName>`
- 方法：`test_<behavior>`
- 示例：
```python
class TestMemoryClassifier:
    def test_classify_dry_run_does_not_modify(self):
        ...
    def test_classify_apply_removes_entries(self):
        ...
```

## [TEST-8] 边界条件

- 测试空值、空列表、异常输入
- 测试文件不存在、权限不足等错误路径
- 测试并发/竞态条件（如适用）

## [TEST-9] 测试隔离

- 每个测试独立运行，不依赖执行顺序
- `conftest.py` 中使用 `tmp_path` Fixture 隔离文件操作
- 不修改全局变量或单例

## [TEST-10] 测试性能

- 单元测试：每个 < 100ms
- 集成测试：每个 < 1s
- 整体测试套件 < 3 分钟
