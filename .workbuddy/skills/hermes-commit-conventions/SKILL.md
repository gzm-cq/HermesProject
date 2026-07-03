---
name: hermes-commit-conventions
description: |
  Hermes 项目中文 Git 提交规范 — Conventional Commits 中文适配、提交信息格式、Breaking Change 标注、Issue 关联。在执行 git commit 时使用。从 .qoder/skills/chinese-commit-conventions 导入。
agent_created: true
---

# 中文 Git 提交规范

## 提交信息格式（强制）

```
<type>(<scope>): <subject>

<body>

<footer>
```

## 类型定义

| 类型 | 说明 | 示例场景 |
|------|------|----------|
| `feat` | 新功能 | 添加用户注册模块 |
| `fix` | 修复缺陷 | 修复登录页白屏问题 |
| `docs` | 文档变更 | 更新 API 接口文档 |
| `style` | 代码格式（不影响逻辑） | 调整缩进、补充分号 |
| `refactor` | 重构（非新功能、非修复） | 拆分过长的服务类 |
| `perf` | 性能优化 | 优化首页列表查询速度 |
| `test` | 测试相关 | 补充用户模块单元测试 |
| `chore` | 构建/工具/依赖变更 | 升级 webpack 到 v5 |
| `ci` | 持续集成配置 | 修改 GitHub Actions 流程 |
| `revert` | 回滚提交 | 回滚 v2.1.0 的登录重构 |

## 原则

- type 保留英文关键字（工具链兼容性好）
- scope 和 description 使用中文
- body 使用中文完整描述

## Subject 行规范

- **type**: 必填，从类型表中选取
- **scope**: 选填，使用中文模块名（如 `用户模块`、`订单`、`支付`）
- **description**: 必填，中文简述，不超过 50 字符
  - 使用动宾短语：「添加 xxx」「修复 xxx」「优化 xxx」
  - 不加句号结尾

### 好的示例

```
feat(权限): 添加基于 RBAC 的细粒度权限控制
fix(支付): 修复微信支付回调签名验证失败的问题
perf(列表页): 优化大数据量表格的虚拟滚动渲染
refactor(网关): 将单体网关拆分为独立微服务
```

### 反面示例

```
fix: 修了一个 bug
feat: 更新代码
chore: 改了点东西
```

## Body 编写规范

- 说明**为什么**要做这个改动（背景/原因）
- 说明**怎么做**的（技术方案摘要）
- 说明**影响范围**（哪些模块、接口受影响）
- 每行不超过 72 个字符（中文约 36 个汉字）
- 正文与标题之间空一行

### 完整示例

```
fix(订单): 修复并发下单导致库存超卖的问题

在高并发场景下，原有的库存扣减逻辑存在竞态条件。
改用 Redis 分布式锁 + 数据库乐观锁双重保障。

影响范围：订单服务、库存服务
测试确认：已通过 500 并发压测验证

Closes #256
```

## Breaking Changes 标注

涉及以下变更必须标注：
- 数据库表结构变更
- 公共 API 参数/返回值变更
- 配置文件格式变更

```
feat(接口)!: 重构用户信息返回结构

BREAKING CHANGE: /api/user/info 返回结构变更
- avatar 字段移入 profile 对象
- 移除已废弃的 nickname 字段，统一使用 displayName
```

## 提交前自查

- [ ] type 是否正确选择
- [ ] scope 是否准确描述了影响模块
- [ ] subject 是否为动宾短语且不超过 50 字符
- [ ] subject 末尾是否去掉了句号
- [ ] body 是否说明了变更原因和方案
- [ ] 不兼容变更是否标注了 BREAKING CHANGE
- [ ] 一次提交是否只做了一件事（原子性）
