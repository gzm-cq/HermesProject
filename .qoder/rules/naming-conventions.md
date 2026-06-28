---
trigger: always_on
alwaysApply: true
---

# 命名规范 — HermesProject
# ============================================
# 适用范围：Python 项目整体
# 最后更新：2026-05-27
# ============================================

## [CONVENTION-1] Python 命名（强制）

| 元素 | 命名风格 | 示例 |
|------|----------|------|
| 包名 | snake_case | `ai_report/`, `clustering_analysis/` |
| 模块名 | snake_case | `classifier.py`, `llm_client.py` |
| 类名 | PascalCase | `MemoryClassifier`, `LLMClient` |
| 函数/方法 | snake_case | `expand_manifest()`, `require_project()` |
| 变量 | snake_case | `total_files`, `backup_dir` |
| 常量 | UPPER_SNAKE | `BACKUP_ROOT`, `C_RED` |
| 私有方法 | _prefix | `_validate()`, `_cleanup_temp()` |

## [CONVENTION-2] Shell 命名（强制）

| 元素 | 命名风格 | 示例 |
|------|----------|------|
| 脚本文件 | kebab-case | `deploy.sh`, `run.sh` |
| 函数 | snake_case | `cmd_deploy()`, `expand_manifest()` |
| 全局变量 | UPPER_SNAKE | `PROJECT_ROOT`, `MANIFEST_DIR` |
| 局部变量 | snake_case | `backup_dir`, `file_count` |

## [CONVENTION-3] 配置文件命名（强制）

- YAML/JSON 配置文件：`kebab-case`
- 示例：`default.yaml`, `gateway.yaml.example`
- Maniifest 文件：`<project-name>.manifest`

## [CONVENTION-4] Git 提交信息（强制）

- 格式：`<type>(<scope>): <简短描述>`
- 类型：`feat`, `fix`, `docs`, `refactor`, `chore`, `style`
- 示例：`feat(deploy): 新增 skill 文件部署`
- 示例：`fix(cli): 修复旧路径引用`
- 描述用中文，不超过 72 字符

## [CONVENTION-5] 测试命名（强制）

- 测试文件：`test_<module_name>.py`
- 测试类：`Test<ClassName>`
- 测试方法：`test_<behavior>`
- 示例：`test_classifier.py` → `TestMemoryClassifier.test_classify_dry_run()`

## [CONVENTION-6] 目录命名（推荐）

| 层级 | 风格 | 示例 |
|------|------|------|
| 项目子模块 | kebab-case | `ai-report-system`, `memory-cleanup` |
| 包目录 | snake_case | `src/ai_report/`, `src/clustering_analysis/` |
| 兼容层 | kebab-case | `compat/clustering_analysis_v3/` |
| 配置目录 | 原样 | `config/`, `deploy/manifests/` |
