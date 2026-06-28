---
name: subagent-driven-development
description: 每个任务派遣一个隔离的子 agent，两阶段审查（规格合规 + 代码质量）
---
---
name: subagent-driven-development
description: 每个任务派遣一个隔离的子 agent，两阶段审查
---
# 子 Agent 驱动开发
每个工程任务派遣一个隔离的子 agent，完成后两阶段审查。

## 流程
1. **派遣** — 为每个计划任务创建一个隔离子 agent
2. **执行** — 子 agent 实现任务，包含测试
3. **审查** — 两阶段审查：先检查规格合规，再检查代码质量
4. **合入** — 审查通过后合并结果
