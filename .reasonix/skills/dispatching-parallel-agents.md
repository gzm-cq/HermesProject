---
name: dispatching-parallel-agents
description: 多任务并发执行，每个任务一个独立子 agent，互不干扰
---
---
name: dispatching-parallel-agents
description: 多任务并发执行，每个任务一个独立子 agent
---
# 派遣并行 Agent
适用于多个独立任务可以同时执行时。

## 约束
- 只有无依赖的任务可以并行
- 每个子 agent 独立上下文
- 各自完成后分别审查
- 全部完成后合入
