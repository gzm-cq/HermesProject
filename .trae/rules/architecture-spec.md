---
alwaysApply: true
---

# Hermes 架构规范
# ============================================
# 5 层记忆体系 + 三模块协同 + 部署规范
# 最后更新：2026-05-27
# ============================================

## [ARCH-1] 5 层记忆体系

| 层级 | 组件 | 数据源 | 生命周期 |
|------|------|--------|----------|
| L1 | System Prompt | 静态代码 | 项目上线时确定 |
| L2 | MEMORY.md / USER.md | Agent 写入 | Session 初始化一次性注入，全程不变 |
| L3 | Hindsight RAG 库 | 历史对话 | 持续写入，聚类定期优化 |
| L4 | 当前会话上下文 | 本次对话 | 每条消息实时变更 |
| L5 | Session DB (SQLite) | 对话历史 | Session 完成后归档 |

## [ARCH-2] 三模块职责边界

- **聚类分析** (`clustering-analysis-v3`)：
  - 仅作用于 Hindsight RAG 库
  - DBSCAN 向量聚类 → 优化索引结构 → 提升 recall 率
  - 不直接参与对话，通过 Cron 定时触发

- **知识导航** (`knowledge-navigation`)：
  - Hermes Gateway 的 Hook 插件
  - 每条消息触发，从优化后的 RAG 库动态召回
  - 只注入本次对话需要的记忆片段

- **记忆清理** (`memory-cleanup`)：
  - 作用于 MEMORY.md / USER.md
  - LLM 分类 → retain/remove/merge/compress
  - 必要保留在 MEMORY/USER（控制 token），其余降级到 RAG

## [ARCH-3] 模块目录结构规范

每个子项目必须遵循：
```
<project-name>/
├── src/<package_name>/    # 主源码
├── compat/                # 兼容层（如有旧版）
│   └── <project_name>/    # shim 文件
├── config/                # 默认配置文件
├── tests/                 # 测试
├── skills/                # Skill 文件（可选）
├── pyproject.toml         # 构建配置
└── README.md
```

## [ARCH-4] 统一部署约束

- 任何子项目修改后，确保 `deploy/deploy.sh plan <project>` 能通过
- 修改 manifest 后 run plan 验证展开结果
- 部署系统相关修改（deploy.sh / manifest）必须包含在 commit 中
- 不要绕过 deploy.sh 直接操作目标目录

## [ARCH-5] 兼容层（Compat）规则

- compat 目录中的 shim 必须显式声明每个符号（不使用 `*` 通配 import）
- 旧类/函数名不一致时用别名映射
- compat 文件不应包含新业务逻辑，只做转发

## [ARCH-6] 依赖管理

- 每个子项目独立声明依赖（各自的 pyproject.toml）
- 根 pyproject.toml 只放公共依赖（pytest, pytest-cov 等）
- 部署脚本无依赖，纯 bash
