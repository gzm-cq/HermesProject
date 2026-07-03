# 团队技术提升 — Qoder 规则与技能导入概述

> 完成时间：2026-07-03
> 执行者：Senior Developer（高级开发工程师）

## 做了什么

从项目 `.qoder/` 目录导入了 6 个开发规范和 4 个专业技能到 WorkBuddy 项目工作区（`.workbuddy/`），为团队建立统一的代码质量标准和开发流程指导。

## 导入内容

### 开发规范（6 个规则 → 1 个整合文档）

来源：`.qoder/rules/` → 写入 `.workbuddy/memory/DEVELOPMENT_STANDARDS.md`

| 规范 | 核心内容 |
|------|----------|
| 架构规范 [ARCH] | 5 层记忆体系、三模块职责边界、目录结构、部署约束、兼容层规则、依赖管理 |
| 部署规范 [DEPLOY] | deploy.sh 唯一入口、常用命令、manifest 修改、Skill 文件管理、备份恢复 |
| Git 工作流 [GIT] | 提交信息格式 `<type>(<scope>): <描述>`、分支策略、CRLF 处理 |
| 命名规范 [CONVENTION] | Python snake_case、Shell kebab-case、测试命名规则 |
| 开发需求 [RULE] | 10 条规则：完整代码、复用模块、模块隔离、验证 API、一次修复 |
| 测试规范 [TEST] | 覆盖率目标 80%+、测试分层 70/20/10、Mock 规范、隔离要求 |

### 专业技能（4 个技能 → 4 个 SKILL.md）

来源：`.qoder/skills/` → 写入 `.workbuddy/skills/`

| 技能 | 用途 | 触发场景 |
|------|------|----------|
| `hermes-brainstorming` | 头脑风暴，想法转设计 | 新功能开发前 |
| `hermes-code-review` | 中文代码审查规范 | 执行代码审查时 |
| `hermes-commit-conventions` | 中文 Git 提交规范 | 执行 git commit 时 |
| `hermes-documentation` | 中文技术文档写作规范 | 编写文档时 |

## 文件结构

```
D:\HermesProject\.workbuddy\
├── memory/
│   ├── DEVELOPMENT_STANDARDS.md   # 整合的6大开发规范
│   └── MEMORY.md                  # 项目长期记忆
└── skills/
    ├── hermes-brainstorming/SKILL.md
    ├── hermes-code-review/SKILL.md
    ├── hermes-commit-conventions/SKILL.md
    └── hermes-documentation/SKILL.md
```

## 资深开发者指导要点

### 代码质量把控标准

1. **完整可运行**：不允许 TODO/占位符，代码必须可立即执行（RULE-1）
2. **API 验证**：导入前验证模块/类/方法是否真实存在，不编造（RULE-6）
3. **一次修复**：修复前理解报错根本原因，不靠多次迭代尝试（RULE-7）
4. **编译通过**：Python 确保语法正确，Shell 确保 `bash -n` 通过（RULE-8）

### 团队协作规范

1. **提交信息**：`<type>(<scope>): <中文描述>`，如 `fix(knowledge-navigation): 修正赋值位置`
2. **代码审查**：分级标注 [必须修复] / [建议修改] / [仅供参考]，用建议代替命令
3. **文档写作**：中英文之间加空格，中文语境用全角标点，术语保留英文

### 部署流程

1. 修改后确保 `deploy/deploy.sh plan <project>` 通过
2. 通过 `deploy/deploy.sh deploy <project>` 执行部署
3. 禁止直接操作 `/root/.hermes/` 目标目录

## 后续建议

- 团队成员开发前阅读 `DEVELOPMENT_STANDARDS.md`
- 新功能开发前使用 `hermes-brainstorming` 技能进行设计
- 代码提交时遵循 `hermes-commit-conventions` 规范
- 代码审查时使用 `hermes-code-review` 的分级标注体系
