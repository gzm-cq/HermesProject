# Flywheel Health Report - Implementation Plan (v2)

**项目**: cron-wrappers（已有 manifest/project）
**语言**: Python
**脚本名**: `flywheel-health-report.py`
**Shell 包装**: `flywheel-health-report.sh`
**部署路径**: `/root/.hermes/scripts/`
**输出**: `/root/.hermes/logs/reports/flywheel-report-YYYYMMDD.md`
**调度**: `0 17 * * *`（每日 17:00，所有 cron 跑完后）

## 1. 跟踪范围（9 个核心飞轮任务）

排除 3 项非飞轮任务：system-health-check（环境巡检）、cron-boot-detect（自愈）、cron-periodic-detect（自愈）。

| 飞轮 | 任务 | 说明 |
|------|------|------|
| Router | knowledge-navigation-baseline | KN 基线采集 |
| Router | kn-router-health-check | Router 健康巡检 |
| Skill | run-skill-eval | Skill 匹配评估 |
| Skill | skillopt-nightly-run | Skill 夜间优化 |
| 知识树 | knowledge-tree-consolidate | 知识树合并 |
| 知识树 | knowledge-tree-kvector | k_vector 兜底维护 |
| 聚类 | clustering-analysis | 聚类分析 |
| 记忆 | memory-cleanup | 记忆清理 |
| 知识路 | daily-learn | 每日在线学习 |

## 2. 4 大类分析框架

### 类别一：任务可靠性 — 稳定吗？

| 维度 | 数据源 | 分析逻辑 | 阈值 |
|------|--------|---------|------|
| 执行成功/失败 | cron-state/*.json | status == fail → P0 | fail → P0 |
| 执行耗时异常 | cron-state.elapsed_seconds | 与历史均值对比 > 2σ → P1 | 偏离 > 2σ |
| 日志中隐藏错误 | logs/cron/<task>-<date>.log | status=success 但日志含 ERROR/异常 | 每次运行检查最新日志 |
| 按时运行检查 | cron-state.run_at vs schedule | 无计划偏差检查（精度不够） | V2 再引入 |

### 类别二：产出质量 — 数据对吗？

| 维度 | 数据源 | 分析逻辑 | 阈值 |
|------|--------|---------|------|
| Router 全关率 | trace.log | 三路全关占比 | >30% → P0 |
| 召回成功率 | trace.log | success/(success+empty+timeout) | — |
| 空结果率 | trace.log | empty 占比 | >20% → P1 |
| 召回延迟 | trace.log | avg/max latency | — |
| KN 维度覆盖率 | baselines/baseline_latest.json | unknown 占比 | >50% → P1 |
| 各维度均分 | baselines/baseline_latest.json | 非 unknown 维度均分 | <0.5 → P1 |
| Skill F1 | data/flywheel/skill_eval_prev.json | F1 值 | <0.4 → P0 |
| 聚类 Silhouette 绝对值 | data/flywheel/clustering_baseline_prev.json | Silhouette 接近零 | <0.1 → P1 |
| 聚类 Silhouette 退化 | data/flywheel/clustering_baseline_prev.json | delta 下降 | <-0.05 → P1 |
| 知识树孤立率 | data/flywheel/kt-baseline-latest.json | 孤立点数占比 | >90% → P1 |

### 类别三：变化趋势 — 变好还是变差？

| 维度 | 数据源 | 分析逻辑 |
|------|--------|---------|
| 全关率趋势 | 本次 vs 上期 trace | 全关率上升/下降/持平标注 |
| Silhouette 趋势 | 本次 vs 上期 clustering | 连续下降标注 |
| F1 趋势 | 本次 vs 上期 skill_eval | 上升/下降标注 |
| 孤立率趋势 | 本次 vs 上期 kt-baseline | 上升/下降标注 |
| 执行耗时趋势 | cron-state.elapsed 多期 | 越跑越慢标注 |

趋势对比方法：baseline 目录中有 `*_prev.json` 和 `*_latest.json` 成对文件，直接读上期值做 delta 比较。如果 prev 文件不存在，跳过趋势分析。

### 类别四：数据可信度 — 分析本身可靠吗？

| 维度 | 数据源 | 分析逻辑 |
|------|--------|---------|
| 样本量标注 | trace.log 事件数 | < 50 时标注"样本不足，统计结果仅供参考" |
| 基线新鲜度 | baseline 文件的 collected_at | 距离当前 > 48h 标注"数据已过期" |
| 日志连续性 | logs/cron/ 文件日期 | 过去 7 天应有 >= 5 条日志 |

## 3. 完整数据源清单

| 数据源 | 路径 | 当前状态 | 分析类别 |
|--------|------|---------|---------|
| cron-state/*.json | /root/.hermes/lib/cron-state/ | ✅ 已用 | 任务可靠性 |
| trace.log | /root/.hermes/plugins/knowledge-navigation/trace.log | ✅ 已用 | 产出质量 |
| baselines/baseline_latest.json | /root/.hermes/plugins/knowledge-navigation/baselines/ | ✅ 已用 | 产出质量 |
| baselines/baseline_prev.json | 同上目录 | ❌ 未用 | 变化趋势 |
| skill_eval_prev.json | /root/.hermes/data/flywheel/ | ✅ 已用 | 产出质量 |
| kt-baseline-latest.json | /root/.hermes/data/flywheel/ | ✅ 已用 | 产出质量 |
| kt-baseline-prev.json | /root/.hermes/data/flywheel/ | ❌ 未用 | 变化趋势 |
| clustering_baseline_prev.json | /root/.hermes/data/flywheel/ | ✅ 已用（部分） | 产出质量+趋势 |
| logs/cron/<task>-<date>.log | /root/.hermes/logs/cron/ | ❌ 未用 | 任务可靠性 |
| eval_match.log | /root/.hermes/plugins/knowledge-navigation/ | ❌ V1跳过 | 产出质量（V2） |
| clustering_audit.log | /root/.hermes/plugins/knowledge-navigation/ | ❌ V1跳过 | 产出质量（V2） |

暂不纳入（V2 再考虑）：
- eval_match.log（16M 大文件，格式未知）
- clustering_audit.log（格式未知）
- errors.log/agent.log（Gateway/Agent 层面，非飞轮）
- curator 报告（技能治理，月度级别）
- 飞书消息历史（无本地归档）

## 4. 报告输出格式

```markdown
# Flywheel Health Report - 2026-07-05

## 概览
- P0 问题: 1
- P1 问题: 3
- 核心任务: 9 个（排除 3 个非飞轮）
- 数据置信度: ⚠️ 样本充足 / ⚠️ 部分基线已过期

## 🔴 P0 - 需要立即处理
| 飞轮 | 问题 | 阈值 |
|------|------|------|
| Router | 全关率 42.7% | >30% |

## 🟡 P1 - 需要关注
| 飞轮 | 问题 | 阈值 |
|------|------|------|
| Router | 空结果率 70.3% | >20% |
| 聚类 | Silhouette 0.05（接近零） | <0.1 |
| 知识树 | 孤立率 98.8% 持续上升 | >90% |

## 📊 任务可靠性
| 任务 | 飞轮 | 状态 | 上次 | 耗时 | 日志异常 |
|------|------|------|------|------|---------|
| clustering-analysis | 聚类 | ✅ | 07-05 02:08 | 91s | 无 |

## 🔍 产出明细 — Router 飞轮
...

## 📈 趋势对比
| 指标 | 上期 | 本期 | 变化 |
|------|------|------|------|
| 全关率 | 35.0% | 42.7% | ↑ 恶化 |
| Silhouette | 0.0421 | 0.0494 | ↑ 改善 |
| F1 | 0.48 | 0.50 | ↑ 改善 |

## ⚠️ 数据可信度
- 基线数据 collected_at 2026-07-03（已过 48h）
- trace.log 事件数 131（充足）
```

## 5. 阈值表

| 指标 | 阈值 | 等级 | 说明 |
|------|------|------|------|
| Router 全关率 | >30% | P0 | 三路全关直接跳过召回 |
| 空结果率 | >20% | P1 | 路由启用但没召回结果 |
| Skill F1 低 | <0.4 | P0 | 技能匹配效果差 |
| KN 均分低 | <0.5 | P1 | 各维度均分不足 |
| unknown 维度占比 | >50% | P1 | 基线数据质量问题 |
| 聚类 Silhouette 绝对值 | <0.1 | P1 | 聚类几乎无效 |
| 聚类 Silhouette 退化 | <-0.05 | P1 | Delta 下降阈值 |
| 知识树孤立率 | >90% | P1 | 知识树孤立节点过多 |
| 执行耗时偏离 | > 历史均值 2σ | P1 | 任务越来越慢 |
| 基线数据过期 | >48h | 标注 | 不报警但标记 |
| 样本量不足 | <50 | 标注 | 统计结果仅供参考 |

## 6. 部署顺序

1. 更新 plan 文档（本文件） ✅
2. 修改 `/mnt/d/HermesProject/scripts/cron-wrappers/flywheel-health-report.py`
   - 排除非飞轮任务
   - 加 Silhouette 绝对值检查
   - 加耗时异常检查
   - 加日志隐藏错误扫描
   - 加趋势对比（读 prev 文件）
   - 加数据可信度标注
   - 重构报告输出为 4 段式
3. `deploy.sh deploy cron-wrappers --yes`
4. 手动 dry-run 验证