# Flywheel Health Report - 2026-08-09

**Generated**: 2026-08-10 00:00:00 UTC
**Home**: `/root/.hermes`
**Report type**: `daily`
**Data window**: `2026-08-09` (UTC, 完整 24h)
**Core cron tasks**: 12 个（排除 2 个基础设施 + 0 个孤儿 state）

## 概览

- P0 问题: **1**
- P1 问题: **2**
- ⚠️ 数据可信度警告: kn_judge 样本不足 50 条

## 🔴 P0 - 需要立即处理

| 飞轮 | 问题 | 详情 |
|------|------|------|
| Router | 错误率 35% 偏高 | router 模块超时 |

## 🟡 P1 - 需要关注

| 飞轮 | 问题 | 详情 |
|------|------|------|
| Router | 全关率 25% 偏高 | Router prompt 过度保守 |
| KN | unknown 维度占比 30% | 维度分类器覆盖不足 |
| Token | Token 预算耗尽率 60% | 可能导致召回截断 |

## 📊 任务可靠性

| 任务 | 飞轮 | 状态 | 上次运行 | 耗时 | 耗时异常 |
|------|------|------|---------|------|---------|
| flywheel-health-report | 系统 | ✅ 成功 | 2026-08-10 00:00 | 30s | — |
| knowledge-tree-builder | 知识树 | ❌ 失败 | 2026-08-09 02:00 | 120s | 超阈值 |
| clustering-analysis | 聚类 | ⚪ 跳过 | 2026-08-09 03:00 | — | — |
| kn-router-health-check | Router | ✅ 成功 | 2026-08-09 04:00 | 5s | — |

## 💡 优化方向

- **Router**: 错误率 35% 偏高，建议检查 Router LLM 调用稳定性
- **KN**: unknown 维度占比 30%，建议优化维度分类器
- **Token**: Token 预算耗尽率 60%，建议增加 total_budget

## 产出明细

(此处省略)
