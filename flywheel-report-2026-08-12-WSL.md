# Flywheel Health Report - 2026-08-11

**Generated**: 2026-08-12 00:05 UTC
**Home**: `/root/.hermes`
**Report type**: `scheduled`
**Data window**: `2026-08-11` (UTC, 完整 24h)
**Core cron tasks**: 11 个（排除 4 个基础设施 + 1 个孤儿 state）

## 概览

- P0 问题: **0**
- P1 问题: **3**
- 📝 知识树基线数据采集于 2026-07-24T03:41（已过期 452h，阈值 48h）

## 🔴 P0 - 需要立即处理

✅ 无 P0 问题

## 🟡 P1 - 需要关注

| 飞轮 | 问题 | 详情 |
|------|------|------|
| Router | 平均得分偏低 0.4302 (阈值 0.5) | 召回结果相关性不足 |
| SAG | SAG 召回异常 1 次 | SAG 服务可能不稳定，已触发熔断器或网络异常 |
| Router | 产出文件 baseline_latest.json 缺失或为空 | 路径: /root/.hermes/plugins/knowledge-navigation/baselines/baseline_latest.json |

## 📊 任务可靠性

| 任务 | 飞轮 | 状态 | 上次运行 | 耗时 | 耗时异常 |
|------|------|------|---------|------|---------|
| daily-learn | 知识路 | ✅ 成功 | 2026-08-11T17:27 | 79s | — |
| dream-daily | 知识路 | ✅ 成功 | 2026-08-11T16:11 | — | — |
| kn-router-health-check | Router | ✅ 成功 | 2026-08-11T15:40 | 66s | — |
| knowledge-navigation-baseline | Router | 🔄 内部执行中 | 2026-08-12 08:05 | — | 本次内部执行 @ report:run_judge_within_window｜report 内建 judge 替代原 collect_baseline.py --judge，避免 2 倍 LLM 消耗 |
| knowledge-tree-consolidate | 知识树 | ✅ 成功 | 2026-08-10T11:00 | 53s | — |
| knowledge-tree-kvector | 知识树 | ✅ 成功 | 2026-08-08T16:03 | — | — |
| memory-cleanup | 记忆 | ✅ 成功 | 2026-08-11T17:28 | 125s | — |
| run-skill-eval | Skill | ❌ 失败 | 2026-08-12 08:05 | — | 本次内部执行 @ runner:_run_skill_eval｜执行超时 (300s) |
| skillopt-nightly-run | Skill | ✅ 成功 | 2026-08-11T17:33 | 453s | — |
| system-health-check | 系统 | ✅ 成功 | 2026-08-12T08:00 | 10s | — |
| 每周深度研究-知识树学习 | 知识树 | ✅ 成功 | 2026-08-09T09:09 | — | — |

## 🔍 产出明细

### Router 飞轮

- 路由总次数: 152（真实 55，eval 测试 97）| 样本量: 充足
- 全关率: 1.8% (1/55) | 全开率: 16.4% (9)
- Hindsight 开启: 45 | 知识树: 46 | Skill: 48 | SAG: 11 (20.0%)
- 召回成功: 132 | 空结果: 13 | 超时: 0 | 错误: 0 | KT降级: 0
- 成功率: 91.0% | 空结果率: 9.0% | 错误率: 0.0% | KT降级率: 0.0%
- 平均延迟: 9195ms | p50: 6654ms | p95: 26027ms | p99: 35568ms | 最大: 45012ms
- 平均得分: 0.4302 | 多跳展开: 29 次
- 决策置信度: N/A | 低置信度率: 0% | 决策 fallback 率: 0.0% (0 次) 原因: 无

**Token 实际消耗（纯观测，无预算控制）:**
- 事件数: 132 | 累计消耗: 529,863 tokens

| 来源 | avg | p50 | p90 | max | 占比 |
|------|-----|-----|-----|-----|------|
| Hindsight | 766 | 360 | 1555 | 4753 | 19.1% |
| SAG | 100 | 0 | 386 | 688 | 2.5% |
| 知识树 | 60 | 0 | 264 | 359 | 1.5% |
| Skill | 3088 | 3159 | 5426 | 7234 | 76.9% |
| **合计** | 4014 | 4224 | 6696 | 7581 | 100% |

**SAG 专项:**
- Router 召回尝试: 55 | 异常: 1 | 非空: 42 | 累计注入: 111 条
- 平均延迟: 1613ms | p50: 0ms | p95: 7135ms
- 成功召回: 54 次 (零结果 12), 平均 2.9 sections, 总计 157
- 召回异常: 1 次 (已计入上方尝试数)
- SAG 合并量: 42 次，平均 2.6 条，零结果率: 0.0%

**KN LLM Judge 召回质量评估 (LLM 评估, 200 样本):**
- 样本量: 70 条 
- 相关率 (评分 ≥ 0.5): 65.7%
- 平均 relevance: 0.5386
- 相关性[hindsight]: 81.7% (样本 60 ✓)
- 相关性[knowledge_tree]: 13.3% (样本 15 ✓)
- 相关性[sag]: 26.1% (样本 23 ✓)
- Bootstrap 95% CI: [0.4921, 0.58]

**参数优化现状 (Auto-Tuner):**

| 参数 | 当前值 | 区间 | 步长 | 初值→当前 | 历史 | 状态 | 说明 |
|------|--------|------|------|-----------|------|------|------|
| KN_MIN_SCORE | 0.5 | [0.4, 0.65] | 0.05 | 0.45 → 0.5 (+0.05) | 5 条（no_change=0 / degrad=0） | applied | 上次调优改善指标，继续同方向 (down) |
| KN_MAX_RESULTS | 4.0 | [2.0, 8.0] | 1.0 | 3.0 → 4.0 (+1.0) | 1 条（no_change=0 / degrad=1） | applied | 当前值离最小值较近，向上调整 |
| KN_MAX_TEXT_LENGTH | 200.0 | [120.0, 400.0] | 50.0 | — → 200.0 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_TEMPORAL_HALFLIFE | 30.0 | [14.0, 90.0] | 7.0 | — → 30.0 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_TEMPORAL_FLOOR_WEIGHT | 0.5 | [0.3, 0.8] | 0.1 | — → 0.5 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_SAG_MAX_INJECT | 3.0 | [2.0, 6.0] | 1.0 | 3.0 → 3.0 (+0.0) | 0 条（no_change=0 / degrad=1） | 调优中 | 无调优历史 |
| KN_SAG_SEARCH_TOP_K | 3.0 | [3.0, 10.0] | 1.0 | 3.0 → 3.0 (+0.0) | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_SAG_MIN_SCORE | 0.5 | [0.3, 0.8] | 0.05 | 0.5 → 0.5 (+0.0) | 0 条（no_change=0 / degrad=1） | 调优中 | 无调优历史 |
| KN_SAG_POINTER_THRESHOLD | 300.0 | [150.0, 800.0] | 100.0 | — → 300.0 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_CROSS_DOMAIN_DEDUP_DEMOTE_FACTOR | 0.5 | [0.3, 0.8] | 0.1 | — → 0.5 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_LAMBDA_MRR | 0.55 | [0.35, 0.7] | 0.05 | — → 0.55 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_SCORE_SPAN_TOP3_THRESHOLD | 0.85 | [0.8, 0.95] | 0.05 | — → 0.85 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_SCORE_SPAN_HALF_THRESHOLD | 0.65 | [0.6, 0.85] | 0.05 | — → 0.65 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_CAUSAL_BOOST_ALPHA | 0.05 | [0.02, 0.2] | 0.01 | — → 0.05 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |
| KN_CAUSAL_BOOST_CAP | 1.1 | [1.05, 1.3] | 0.05 | — → 1.1 | 0 条（no_change=0 / degrad=0） | 调优中 | 无调优历史 |

- 最近一次调优: 2026-08-09 KN_MAX_RESULTS: 3.0 → 4.0 (applied)

### KN 基线

- 无 baseline 数据

### Skill 飞轮

- **匹配质量 (eval)**: F1=0.4967 | Precision=0.4667 | Recall=0.5889
- 评估查询数: 30 | 时间: 2026-08-11T08:04:10+08:00

- **真实使用**: 总 Skill 298 个 | active 232 | 已使用 210 | 从未使用 22
- 总使用次数: 5366 | 总浏览: 5405
- 超 30 天未使用: 52 个

**Top 10 使用最多:**

| # | Skill | 使用 | 浏览 | 最后使用 |
|---|-------|------|------|---------|
| 1 | knowledge-navigation | 551 | 551 | 2026-08-11 |
| 2 | ai-report-generation-system-implementation | 341 | 392 | 2026-07-19 |
| 3 | hindsight-memory | 269 | 269 | 2026-08-11 |
| 4 | document-audit-optimization | 217 | 216 | 2026-08-06 |
| 5 | system-health-check | 200 | 200 | 2026-08-11 |
| 6 | system-operations-rules | 163 | 164 | 2026-08-11 |
| 7 | hermes-infrastructure | 140 | 140 | 2026-08-11 |
| 8 | clustering-analysis | 134 | 134 | 2026-08-07 |
| 9 | hermes-parameter-config | 117 | 117 | 2026-08-11 |
| 10 | knowledge-tree-builder | 115 | 115 | 2026-08-11 |

**近 7 天活跃 (10 个):**
ai-coding-agent-config, ai-platform-deployment, code-modification-workflow, coding-agent-delegation, computer-use, docker-patterns, enterprise-ai-infrastructure-architecture, enterprise-data-governance

### 知识树飞轮

- 知识点总量: 8819
- 孤立知识点: 0 (0.0%)
- 平均置信度: 0.9818 | 碎片域: 7
- 采集时间: 2026-07-24T03:41:55.328957+00:00

### 聚类飞轮

- 噪声率: 0.3%
- 聚类数: 22 | Memory Links: 9
- 总单元: 64907
- 噪声率变化: -0.2%
- 时间: 2026-08-03T02:03:13.570752+00:00

### 记忆清理

- MEMORY.md: 16,873/50,000 chars (33.7%)
- USER.md:   3,623/15,000 chars (24.2%)
- 清理产出: compress 6 | hindsight 0 | remove 11 | merge 7
- Phase 2 正确率: 27.3%
- Token 消耗: 101,639
- 耗时: 125.6s | 模式: apply

### 全局错误监控

- 当日问题日志: 229 条 (ERROR 9 | WARNING 209)
- 已过滤重启级联噪音: 11 条
- ERROR 占比: 4.1%

**Top 10 错误模块:**

| # | 模块 | 条数 |
|---|------|------|
| 1 | tools.mcp_tool | 59 |
| 2 | knowledge_tree_plugin.hooks | 36 |
| 3 | plugins.memory.hindsight | 34 |
| 4 | tools.registry | 26 |
| 5 | hermes.security_audit | 15 |
| 6 | agent.relay_runtime | 12 |
| 7 | gateway.platforms.api_server | 7 |
| 8 | gateway.run | 5 |
| 9 | Lark | 5 |
| 10 | gateway.platforms.weixin | 5 |

**关键词分布**: failed(103), error(68), connection(59), not found(4), exception(1), timeout(1)

## 📈 变化趋势

| 指标 | 变化 |
|------|------|
| MEMORY占用率 | 40.1% → 33.7% (-6.4%) |
| Router 全关率 | 5.3% → 1.8% (-3.5%) |
| Router 平均延迟 | 9308ms → 9195ms (-113ms) |
| Router 空结果率 | 20.6% → 9.0% (-11.6%) |
| SAG 召回量 | 61 → 111 (+50) |
| SAG 开启率 | 26.3% → 20.0% (-6.3%) |
| USER占用率 | 23.7% → 24.2% (+0.5%) |
| 噪声率 | 0.5%（3次均值）→ 0.3% (-0.2%) |

## 📊 7 天滚动趋势

| 日期 | P0/P1 | Router得分 | 全关% | 空结果% | 错误% | KT降级 | Token消耗avg | Skill占比% | SAG开启% | SAG召回量 | SAG延迟ms | Skill F1 | Skill活跃 | Skill调用次数 | KN unknown% | KN均分 | 聚类噪声% | KT孤立% | MEM占用% | USER占用% | Hindsight产出 | ERROR数 |
|------|-------|-----------|-------|---------|-------|--------|-------------|-----------|----------|-----------|----------|----------|----------|------------|-------------|--------|-----------|---------|---------|---------|--------------|--------|
| 2026-08-04 | 0/2 | 0.4918 | 0.0 | 1.1 | 0.0 | 2 | - | - | 27.5 | 72 | 23 | 0.6133 | 214 | 5068 | 0.0 | 0.5341 | 0.3 | 0.0 | 38.6 | 29.2 | 10 | 10 |
| 2026-08-05 | 0/4 | 0.551 | 0.0 | 2.9 | 0.0 | 33 | - | - | 61.4 | 174 | 771 | 0.5956 | 214 | 4902 | 40.0 | 0.5296 | 0.3 | 0.0 | 49.5 | 32.4 | 3 | 32 |
| 2026-08-06 | 1/3 | 0.5371 | 3.5 | 3.3 | 0.0 | 32 | - | - | 60.5 | 281 | 771 | 0.2956 | 217 | 4990 | 66.7 | 0.5272 | 0.3 | 0.0 | 55.1 | 34.5 | 3 | 50 |
| 2026-08-07 | 0/6 | 0.4757 | 6.7 | 2.9 | 0.0 | 1 | - | - | 50.0 | 230 | 575 | 0.5467 | 224 | 5062 | 40.0 | 0.5575 | 0.3 | 0.0 | 58.2 | 37.8 | 4 | 160 |
| 2026-08-08 | 0/6 | 0.4537 | 5.0 | 1.3 | 0.0 | 0 | - | - | 50.0 | 108 | 474 | 0.5467 | 224 | 5062 | 40.0 | 0.5575 | 0.3 | 0.0 | 42.9 | 32.8 | 2 | 734 |
| 2026-08-09 | 0/1 | 0.8518 | 0 | 0.0 | 0.0 | 0 | 0 | 0 | 0 | 3 | 0 | 0.5167 | 229 | 5294 | 0 | - | 0.3 | 0.0 | 39.7 | 27.6 | 1 | 16 |
| 2026-08-10 | 0/4 | 0.4557 | 5.3 | 20.6 | 0.0 | 0 | 3791 | 77.5 | 26.3 | 61 | 1246 | 0.4967 | 229 | 5296 | 0 | - | 0.3 | 0.0 | 40.1 | 23.7 | 2 | 35 |

## ⚠️ 数据可信度

- 📝 知识树基线数据采集于 2026-07-24T03:41（已过期 452h，阈值 48h）
- 📝 非 飞轮 state 文件: memory-cleanup-daily

## 💡 优化方向

- **Router**: 平均延迟 9195ms 偏高，建议排查 Hindsight daemon 连接池或 Reranker 超时
- **SAG**: SAG 召回异常 1 次，建议检查 SAG 服务健康状态和熔断器日志
- **Skill**: F1=0.4967 有提升空间，建议关注 Precision/Recall 差异，优化 skill_matcher 关键词扩展
- **聚类**: Memory Links 仅 9，聚类间关联稀疏，建议检查 memory_links 写入逻辑
- **趋势**: USER占用率 恶化 (23.7% → 24.2% (+0.5%))，建议关注并排查根因
- **维护**: 发现 1 个非飞轮 state 文件 (memory-cleanup-daily)，建议清理以减少噪音
