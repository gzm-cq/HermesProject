# HermesProject 飞轮系统全面 Review SPEC

## 目标
对 HermesProject 项目的路由、4路召回、飞书健康巡检、完整飞轮相关项目做全面代码 review，最后由 Hermes 核实汇总。

## Review 范围

### 1. 路由 + 4路召回系统
**路径**: `/mnt/d/HermesProject/plugins/knowledge-navigation/` + `/mnt/d/HermesProject/scripts/recall-eval/` + `/mnt/d/HermesProject/scripts/knowledge-tree-builder/`

**核心文件**:
- `knowledge-navigation/src/knowledge_navigation/core/router.py` — Router 核心逻辑
- `knowledge-navigation/src/knowledge_navigation/core/hooks.py` — Hook 系统（含4路召回）
- `knowledge-navigation/src/knowledge_navigation/core/skill_matcher.py` — Skill 匹配器
- `knowledge-navigation/src/knowledge_navigation/core/filtering.py` — 过滤逻辑
- `knowledge-navigation/src/knowledge_navigation/core/circuit_breaker.py` — 熔断器
- `knowledge-navigation/src/knowledge_navigation/core/source_defs.py` — 数据源定义
- `knowledge-navigation/src/knowledge_navigation/adapters/hindsight.py` — Hindsight 适配器
- `knowledge-navigation/src/knowledge_navigation/turn_gate.py` — Turn Gate
- `recall-eval/src/recall_eval/core/runner.py` — 召回评估运行器
- `recall-eval/src/recall_eval/core/metrics.py` — 评估指标
- `knowledge-tree-builder/src/knowledge_tree_builder/` — 知识树构建

**Review 重点**:
- Router gate 逻辑是否正确（开/关每路，不选N个）
- 4路召回（Hindsight BGE+BM25+实体图 + SAG）的合并去重逻辑
- Score normalization（BGE-M3余弦 vs softmax vs rerank_score）
- Circuit breaker 状态机是否正确
- Skill matcher 三阶段（keyword/embedding/LLM）是否完整
- Error handling：API超时、429、空结果处理
- Config 读取是否健壮（env var fallback）

### 2. 飞书健康巡检 + 系统健康
**路径**: `/mnt/d/HermesProject/scripts/cron-wrappers/` + `/mnt/d/HermesProject/scripts/system-health-check/` + `/mnt/d/HermesProject/scripts/flywheel-health-report/`

**核心文件**:
- `cron-wrappers/health-check-cron.sh` — 健康巡检cron脚本
- `cron-wrappers/cron-periodic-detect.sh` — 周期性检测脚本
- `cron-wrappers/kn-router-health-check.sh` — Router健康检查
- `system-health-check/health-check-all.py` — 全量健康检查
- `system-health-check/health-check-run.py` — 单次健康检查运行器
- `flywheel-health-report/src/flywheel_health_report/runner.py` — 飞轮报告运行器
- `flywheel-health-report/src/flywheel_health_report/analyzers/*.py` — 各分析器

**Review 重点**:
- Cron脚本的幂等性（flock、锁文件）
- Feishu通知逻辑：正常时不推送（"no news is good news"原则）
- Error handling：API失败、网络超时、token失效(code=19001)
- Health check覆盖范围是否完整（所有飞轮组件）
- Report生成逻辑是否正确（P0/P1优先级排序）

### 3. 完整飞轮系统
**路径**: `/mnt/d/HermesProject/scripts/flywheel-orchestrator/` + `/mnt/d/HermesProject/scripts/dream-synth/` + `/mnt/d/HermesProject/scripts/clustering-analysis-v3/` + `/mnt/d/HermesProject/scripts/self-evolving/` + `/mnt/d/HermesProject/scripts/skillopt-runner/` + `/mnt/d/HermesProject/scripts/memory-cleanup/` + `/mnt/d/HermesProject/scripts/daily-learn/` + `/mnt/d/HermesProject/scripts/backfill-k-vector/`

**Review 重点**:
- Flywheel orchestrator的任务编排逻辑是否正确
- Dream-synth的4阶段流水线（synthesize→patterns→pro...）是否完整
- Clustering-analysis的Phase 6基线反馈环是否修复（之前有断裂问题）
- Self-evolving的三大进化算子是否实现正确
- Skillopt-runner的denylist匹配和batch切片逻辑
- Memory-cleanup的MemoryStore round-trip guard是否正确实现
- Daily-learn的数据流是否正确

## Review 方法

每个 subagent:
1. 使用 codegraph MCP tools (codegraph_search, codegraph_explore) 探索代码结构
2. 使用 read_file 读取关键文件内容
3. 使用 terminal 运行 pytest tests（如有）验证代码可执行性
4. 输出结构化 review report，包含：问题列表（按严重程度P0-P3）、改进建议、代码质量评分

## Review Report 格式

```markdown
## [组件名称] Review Report

### P0 (Critical) - 必须修复
1. [问题描述] → [文件:行号] → [修复建议]

### P1 (High) - 建议修复  
1. [问题描述] → [文件:行号] → [修复建议]

### P2 (Medium) - 可优化
1. [问题描述] → [文件:行号] → [优化建议]

### P3 (Low) - 可选改进
1. [问题描述] → [文件:行号] → [改进建议]

### Code Quality Score: X/10
### Summary: [一句话总结]
```

## Hermes Verification Steps

每个 subagent 完成后，Hermes:
1. 读取各 subagent 的 review report（通过 session_search）
2. 对 P0/P1 问题抽样验证（读源码确认）
3. 汇总所有 review report，去重合并同类问题
4. 生成最终汇总报告，按优先级排序所有问题
