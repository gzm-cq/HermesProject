---
alwaysApply: true
scene: git_message
---

# Git 提交信息规范 — HermesProject

## 格式

```
<type>(<scope>): <中文描述>
```

类型和 scope 均为英文小写，描述用中文，不超过 72 字符。

## 类型（type）

| 类型 | 使用场景 |
|------|---------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改变外部行为） |
| `docs` | 文档变更 |
| `chore` | 杂项（构建、依赖、配置等） |
| `style` | 代码格式（缩进、分号等，不影响逻辑） |

## 作用域（scope）

对应子项目或模块名称：

- `deploy` — 部署系统（deploy.sh / manifests）
- `nav` — knowledge-navigation 插件
- `kt-plugin` — knowledge-tree-plugin
- `kt-builder` — knowledge-tree-builder
- `clustering` — clustering-analysis-v3
- `memory-cleanup` — memory-cleanup
- `drawio` — drawio-generator
- `health-check` — system-health-check
- `ai-report` — ai-report-system
- `self-evolve` — self-evolving
- `runner` — skillopt-runner
- `skillopt` — skillopt-sleep
- `config` — 配置文件
- `docs` — 项目文档（docs/）
- *其他 scope 按实际模块名 kebab-case 取*

## 描述规范

- 描述**做了什么**，而非为什么
- 用中文，不超过 72 字符
- 祈使句，不带句号
- 准确反映变更内容

✅ 正确示例：
```
fix(nav): 修复 user_message 未 HTML escape 的问题
feat(deploy): 新增 skill 文件自动部署
refactor(clustering): 提取公共 embedding 函数
```

❌ 错误示例：
```
fix bug
update code
feat(nav): 因为用户反馈说消息格式有问题所以加了个 escape（过长且解释为什么）
```

## 其他规则

- 不同功能的修改分开提交，不混在一个 commit 中
- 只暂存与本次修改相关的文件
- 提交前检查 `git status` 确认无遗漏
- 保持 main 分支始终可部署