# 飞轮 Cron 重构方案 v6

> 状态: **已确认** | 创建: 2026-07-01 | 更新: 2026-07-01

---

## 1. 目标

将 12 个散乱的 cron 任务按「4 层 + 3 路并行」架构梳理，消除冗余，补全缺口，确保飞轮稳定闭环。

### 设计原则

| 原则 | 说明 |
|:---|:---|
| **3 路自治并行** | 记忆路 / 知识路 / Skill 路各自独立调度，互不阻塞 |
| **串行只限于飞轮内因果链** | Layer 3 依赖 Layer 2 产出，但通过状态文件检查，不强耦合 timer |
| **层间隔离** | 维护层故障不影响数据层，数据层故障不影响业务层 |
| **无单点编排** | 不建单一 `hermes-flywheel.sh` 入口，各路用自己的 timer |
| **状态锁+告警** | 每路执行完写状态文件，依赖方（Layer 3）检查后决定是否执行 |

---

## 2. 架构总览

```
Layer 1 (维护, 09:00 串行 <5min)          Layer 4 (业务产出, 独立 cron)
┌─ health-check (08:00 周一~五)            ├─ 每日在线学习 (09:00 周一~五)
├─ memory-cleanup (13:00 每日)              ├─ 每周深度研究 (09:00 周日)
│                                           └─ 论文提醒 (一次性)
Layer 2 (数据飞轮，3 路并行)
┌─ Path A: 记忆路                          Layer 3 (能力飞轮, 串行)
│  └─ clustering-analysis-v3 (每周一 10:00)  ┌─ Router 飞轮
│     └─ [P1] Phase 6: 基线→调参告警        │  ├─ 巡检 (14:00 每日) ← [P0] 先修错误
├─ Path B: 知识路                           │  ├─ 基线 (12:00 每日)
│  ├─ daily-learn (09:00 周一~五)            │  └─ [P3] Router 评估 (待建)
│  ├─ consolidate (每周一 11:00)             │
│  └─ kvector-maintenance (每周六 09:00)     └─ Skill Matcher 飞轮
│                                           ├─ [P1] Skill 评估 (12:00 每日)
├─ Path C: Skill 路                         └─ 优化 (15:00 每日, skillopt)
│  └─ skillopt-runner (15:00 每日)
│     ├─ harvest+rank (Phase 1-2)
│     └─ optimize (Phase 3, 同属 Layer 3 消费)
```

---

## 3. 现有 Cron 映射

| # | 任务名称 | 当前排期 | 脚本路径 | 归属层 | 状态 |
|:-:|:---|:---:|:---|---:|:---:|
| 1 | system-health-check | 08:00 周一~五 | `health-check-cron.sh` | Layer 1 | ✅ 保留 |
| 2 | memory-cleanup-daily | 13:00 每日 | `memory-cleanup/daily_dryrun.sh` | Layer 1 | ✅ 保留 |
| 3 | 聚类分析每周跑 | 周一 10:00 | `clustering-analysis-cron.sh` | Layer 2 Path A | ✅ 保留 + [P1] 加 Phase 6 |
| 4 | 每日在线学习 | 09:00 周一~五 | `daily-learn/daily_learn.sh` | Layer 2 Path B | ✅ 保留 |
| 5 | 知识树维护每日 | 周一 11:00 | `knowledge-tree-consolidate.sh` | Layer 2 Path B | ✅ 保留 |
| 6 | 知识树k_vector每周兜底 | 周六 09:00 | `knowledge-tree-kvector-maintenance.sh` | Layer 2 Path B | ✅ 保留 |
| 7 | skillopt-nightly-run | 15:00 每日 | `skillopt-nightly-run.sh` | Layer 2 Path C | ✅ 保留 |
| 8 | 知识导航评估基线 | 12:00 每日 | `knowledge-navigation-baseline.sh` | Layer 3 Router | ✅ 保留 |
| 9 | 知识导航Router健康巡检 | 14:00 每日 | `kn-router-health-check.sh` | Layer 3 Router | 🔴 P0 先修错误 |
| 10 | cron-periodic-detect | 每小时 | `cron-periodic-detect.sh` | — | 🔴 P2 冗余，下线 |
| 11 | 每周深度研究 | 周日 09:00 | (agent prompt) | Layer 4 | ✅ 保留 |
| 12 | 论文投稿提醒 | 2026-08-06 | (agent prompt) | Layer 4 | ✅ 保留 |

---

## 4. 需要新建/修改的任务

### [P0] 修 Router 巡检 ERROR

**现状:** Router 巡检报 `Script exited with code 1`，日志显示 `Router JSON 解析失败: 4 次 (24h)`
**可能原因:**
- `journalctl -u hermes-gateway` 查到的失败日志格式与 grep 正则匹配
- 或 router.py 中 LLM 返回的 JSON 格式异常
- 或日志中 `"Router JSON 解析失败"` 文本不准确

**修复步骤:**
1. 确认 journalctl 单元名是否正确 → 手动跑一次诊断命令
2. 检查 `router.py` 中 JSON 解析逻辑
3. 修复后手动验证巡检可通过
4. 巡检脚本退出码应为 0（当前是 1）

### [P1] 聚类 Phase 6: 基线反馈闭环

**设计决策（第一版：只检测告警，不做自动调参）**

| 原因 | 说明 |
|:---|:---|
| 召回率下降的信号源可能不是聚类参数 | 可能是 Hindsight 服务不稳定、rerank 模型变化、测试偏移 |
| 自动调参可能掩盖真正问题 | 如服务 bug 导致召回下降，自动调参反而让聚类参数跑偏 |
| 需观察 1-2 周数据确认因果关系 | 有数据后才能定阈值和策略 |

**实现:**
1. 聚类执行完成后读取 `~/.hermes/plugins/knowledge-navigation/baselines/baseline_latest.json`
2. 与上周基线对比 `avg_score` 维度的变化
3. 如果 `avg_score` 下降超过 10% → 飞书告警「🔴 聚类后召回率下降，建议人工排查」
4. 如果连续 3 周下降 → 告警升级为「🔥 聚类参数可能需要调整」

**接入点:** `cron_wrapper.sh` 新增 `--skip-steps` 兼容步骤 6

### [P1] Skill Matcher 评估加 cron wrapper

**现状:** `run_skill_eval.py` 脚本已存在于 `plugins/knowledge-navigation/scripts/`，有评估集 `skill_eval_queries.json`，但无 cron 调度

**实现:**
- 新建 wrapper: `scripts/cron-wrappers/run-skill-eval.sh`
- 排期: `0 12 * * *`（每日 12:00）
- 评估结果写入 `~/.hermes/plugins/knowledge-navigation/baselines/skill_eval_latest.json`
- 退化逻辑: 与上周对比，退化 >10% 飞书告警

**依赖:**
- `python3 ~/.hermes/plugins/knowledge-navigation/scripts/run_skill_eval.py`
- 结果与 `collect_baseline.py` 类似格式

### [P3] Router 评估集（后续）

**现状:** Router 目前只有巡检（检测异常）和基线（采集 recall）但没有标准化的评估集
**计划:** 建 30 条 Router eval queries，覆盖各注入路径的组合
**优先级:** P3，先让现有飞轮跑起来再补

---

## 5. 状态锁机制（Layer 3 消费 Layer 2 产出的依赖检查）

各路 timer 执行成功后写状态文件到 `/tmp/hermes-flywheel/`：

```bash
# 示例：memory 路写完状态文件
echo "$(date +%s)" > /tmp/hermes-flywheel/memory_last_ok
echo "$(date -Iseconds)" >> /tmp/hermes-flywheel/memory_last_ok
```

| 文件 | 写入方 | 消费方 |
|:---|:---|:---|
| `memory_last_ok` | clustering-analysis-v3 | Router 巡检（仅参考） |
| `knowledge_last_ok` | daily-learn | (消费方待定) |
| `skill_last_ok` | skillopt-runner | Skill Matcher eval |

Layer 3 执行前检查依赖状态：
- 任一状态文件 > 48 小时未更新 → 发飞书告警但是不阻塞
- 仅当依赖定时执行报错时跳过消费步骤

---

## 6. 下线计划

| 任务 | 下线时机 | 验证方式 |
|:---|:---|:---|
| `cron-periodic-detect` | 立即 | 确认 `nonebot` 框架已停用，`cron_periodic_detect.py` 无上游依赖 |
| 旧 cron 脚本管理 (jobs.json) | 验证 3 天后 | 各 timer 稳定运行后逐个移除 |

---

## 7. 实施步骤

```
Step 1 [P0] 修 Router 巡检错误
  ├─ 确认 journalctl 日志路径
  ├─ 检查 router.py JSON 解析逻辑
  └─ 修复并验证巡检通过

Step 2 [P1] 聚类 Phase 6
  ├─ 修改 cron_wrapper.sh 加步骤 6
  ├─ 基线读取 + delta 比较
  └─ 退化告警逻辑

Step 3 [P1] Skill Eval cron
  ├─ 建 run-skill-eval.sh wrapper
  ├─ 注册 cron 12:00 每日
  └─ 退化告警逻辑

Step 4 [P2] 下线 cron-periodic-detect
  ├─ 确认无依赖
  └─ 移除 cron

Step 5 [P3] Router 评估集（后续迭代）
```

---

## 8. 风险登记

| 风险 | 概率 | 影响 | 缓解措施 |
|:---|:---:|:---:|:---|
| Phase 6 自动调参误判 | 中 | 聚类参数劣化 | 第一版只检测告警，不做自动调参 |
| 单入口脚本（hermes-flywheel.sh）引入 SPOF | 低 | 3 路全停 | ✅ 已放弃该设计，各路独立 timer |
| Layer 3 消费到老数据 | 低 | 评估不准 | 状态锁 48 小时阈值 |
| journalctl 日志路径不匹配 | 低 | 巡检误报 | P0 修复时先手动确认 |
