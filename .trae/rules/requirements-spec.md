---
alwaysApply: true
---

# 开发需求规范 — HermesProject
# ============================================
# 适用范围：Hermes 所有子项目（Python Monorepo）
# 最后更新：2026-05-27
# ============================================

## [RULE-1] 生成完整可运行代码（优先级：关键）
# 不允许 TODO/占位符

- 代码必须可立即执行，包含完整 import 和依赖
- 不允许 TODO、FIXME、"稍后实现" 等占位符
- 当涉及跨项目调用时，引用实际已有的模块路径

## [RULE-2] 复用现有代码和 API（优先级：高）
# 优先使用 Hermes 已有模块

- 操作 deploy.sh 前先读该脚本的用法说明
- 使用子项目模块时，以 `src/<package_name>/` 为入口
- 优先复用 `src/hermes_cli/` 中的公共工具

## [RULE-3] 最小化新增依赖（优先级：高）
# 优先使用项目现有依赖

- 添加新依赖前检查各子项目的 pyproject.toml
- 优先使用 stdlib（如 pathlib/shutil/json）
- 新依赖必须在对应子项目的 pyproject.toml 中声明

## [RULE-4] 模块边界隔离（优先级：高）
# 子项目间通过包名区分，不交叉引用内部模块

- ai-report-system → `src/ai_report/` 和 `compat/src/` 
- clustering-analysis-v3 → `src/clustering_analysis/` 和 `compat/clustering_analysis_v3/`
- memory-cleanup → `src/memory_cleanup/` 和 `compat/`
- knowledge-navigation → `src/knowledge_navigation/`
- 跨项目调用统一走顶层 API 或 CLI

## [RULE-5] 仅修改请求的内容（优先级：高）
# 不未经授权重构

- 只修改明确要求的内容
- 不重构可工作的代码
- 不修改非请求文件的代码

## [RULE-6] 验证所有 API 是否存在（优先级：关键）
# 不使用不存在的模块和方法

- 导入前验证模块/类/方法是否真实存在
- 不编造不存在的库方法
- Python 标准库方法不熟悉的，先确认签名

## [RULE-7] 第一次就完全修复错误（优先级：高）
# 解决根本原因，不是症状

- 修复前理解报错的根本原因
- 提供完整修复，不靠多次迭代尝试
- 修改后确保 `bash -n`（shell）或编译通过

## [RULE-8] 确保代码成功编译/运行（优先级：关键）
# 所有代码必须无错误

- Python 代码确保语法正确（`python -c "import <module>"`）
- Shell 脚本确保 `bash -n` 通过
- 部署脚本修改后跑一次 `plan` 验证

## [RULE-9] 功能优先于完美（优先级：中）
# 先让代码运行

- 可工作的代码 > 完美的代码
- 功能优先于优化
- 先跑通再重构

## [RULE-10] 尊重项目命名约定（优先级：中）
# 保留现有命名模式

- 不重命名已存在的变量/函数
- Python 用 snake_case
- 配置文件用 kebab-case（.yaml / .json）
- 见 naming-conventions.md

# ============================================
# 关键规则摘要（最高优先级）
# ============================================
# RULE-1  完整可运行代码（无 TODO/占位符）
# RULE-2  复用已有模块
# RULE-4  模块边界隔离
# RULE-6  验证 API 存在性
# RULE-8  确保编译/运行通过
