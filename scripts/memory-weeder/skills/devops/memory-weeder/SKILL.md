---
name: memory-weeder
title: Vestige 遗忘机制运维 — 访问衰减审计与记忆再激活
description: Use when auditing long-untouched Hindsight memories, checking recall decay weights, or reactivating forgotten memories via access-count reset.
version: 1.0.0
author: Hermes Agent
license: MIT
tags: [memory, forgetting, vestige, recall, decay, maintenance]
metadata:
  hermes:
    tags: [memory, forgetting, vestige, recall, decay, maintenance]
    related_skills: [dream-synth, knowledge-navigation]
---

# memory-weeder — Vestige 遗忘机制运维

## 作用

Hindsight 记忆只增不减，24K+ 记忆会挤占高价值注入。Vestige 在 recall 阶段按
**访问衰减**对长期未访问的记忆软降权（不删除），本 skill 提供审计与再激活手段。

## 衰减公式

```
weight = 0.9 ** days_since_last_access
```

- 新记忆 / 首次出现：weight = 1.0（不惩罚）
- 30 天未访问：weight ≈ 0.04
- weight < 0.2（约 15 天）标记为 `_low_priority`，recall 阶段 rerank_score 乘权重

## 用法

```bash
# 报告当前衰减状态（哪些记忆已 low_priority）
python scripts/memory-weeder/weed.py

# 汇总统计
python scripts/memory-weeder/weed.py --stats

# 重置某记忆访问状态（重新激活，weight 回到 1.0）
python scripts/memory-weeder/weed.py --reset <memory_id>
```

## 与 memory-cleanup 的区别

- `memory-cleanup`：LLM 驱动主动整理（retain/remove/merge），由 cron 跑。
- `memory-weeder`（Vestige）：被动衰减，recall 阶段动态降权，不删数据。
- 二者互补：cleanup 做减法整理，Vestige 做优先级调度。

## 约束

- 纯运维工具，不修改 Hindsight 生产数据。
- 状态文件：`~/.hermes/knowledge-navigation/vestige_state.json`（插件自动维护）。
- 可通过 ENV 调参：`KN_VESTIGE_DECAY_BASE`（默认 0.9）、`KN_VESTIGE_LOW_THRESHOLD`（默认 0.2）、`KN_VESTIGE_ENABLED=0` 关闭。
