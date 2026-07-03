# 知识树质量基线反馈闭环 实施计划

**Goal:** 给知识树 consolidate cron 增加质量基线采集、退化检测、飞书告警（类比聚类 Phase 6）
**Architecture:** ConsolidationEngine 加采集方法 → shell wrapper 加对比逻辑 → `_STEP_RESULTS` 复用现有飞书通知
**Tech Stack:** Python 3.11 / PostgreSQL 17 / bash (cron_common.sh)
**来源计划:** flywheel-cron-restructure-spec.md (P1 聚类已闭环，知识树环节缺基线反馈)

---

## 任务清单

### Task 1: 在 `ConsolidationEngine` 中新增 `collect_baseline_metrics()` 方法

**来源:** 源码分析确认 `ConsolidationEngine` 已有 `run()`、`merge_small_domains()`、`build_kp_edges()` 等方法
**当前状态:** 无任何基线采集代码
**目标状态:** consolidate 执行后可采集 4 个核心质量指标
**期望目标:** 用户能每周看到知识树健康度升降趋势
**改动位置:** `/mnt/d/HermesProject/scripts/knowledge-tree-builder/src/knowledge_tree_builder/core/consolidation.py`
**新增文件:** `/mnt/d/HermesProject/scripts/knowledge-tree-builder/tests/test_consolidation_baseline.py`
**工作量:** 半天
**检查方法:** `pytest tests/test_consolidation_baseline.py -v`
**实现方式:** 自实现（仅引用 psycopg2, stdlib datetime/json）
**前置依赖:** 无

指标设计（精简版，4个核心）：

| 指标 | SQL | 意义 |
|------|-----|------|
| avg_confidence | `SELECT AVG(retrieval_confidence) FROM knowledge_tree WHERE node_type='knowledge_point'` | 知识置信度 |
| total_kps | `SELECT COUNT(*) FROM knowledge_tree WHERE node_type='knowledge_point'` | 知识点总数 |
| fragment_domains | 递归 CTE 统计 <3 点的 domain | 领域碎片度 |
| orphan_kps | 左 join edges 找无边 kp | 连接健康度 |

### Task 2: 修改 `cmd_consolidate()` + shell 增加 Phase 6

**来源:** 聚类 Phase 6 的 `cron_wrapper.sh:352-449`
**当前状态:** `cmd_consolidate()` 执行完只 print 到 stdout 不保存；shell 只检查 exit code
**目标状态:** consolidate 跑完后采集基线、对比前一次、退化时注入 `_STEP_RESULTS` 告警
**期望目标:** consolidate cron 执行后用户收到飞书通知，含"质量稳定"或"退化告警"
**改动位置:** 
  - `/mnt/d/HermesProject/scripts/knowledge-tree-builder/src/knowledge_tree_builder/commands/complex.py`（改）
  - `/mnt/d/HermesProject/scripts/cron-wrappers/knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh`（改）
**工作量:** 半天
**检查方法:** 手动运行 cron 检查飞书通知
**实现方式:** 自实现 shell + Python 内联
**前置依赖:** Task 1

退化判定阈值：
- avg_confidence 下降 > 0.05 → 告警
- total_kps 下降 > 5% → 告警
- orphan_kps 上升 > 10% → 告警
- 连续 3 周退化 → 升级提醒

### Task 3: 验证集成

**来源:** 部署清单
**当前状态:** 代码未合并
**目标状态:** 手动运行验证全链路通
**改动位置:** 运行时路径
**工作量:** 1小时
**检查方法:** 手动跑 consolidate → 检查飞书通知
**前置依赖:** Task 1, 2

---

## 实施顺序

1. **Task 1** → TDD: 写测试 → 看失败 → 实现 → 看通过 → refactor
2. **Task 2a** → 改 `complex.py`（consolidate run 末尾保存基线）
3. **Task 2b** → 改 `knowledge-tree-consolidate.sh`（加载前次、对比、告警）
4. **Task 3** → 手动运行验证

## 风险与应对

| 风险 | 应对 |
|------|------|
| psycopg2 连接串未设 | 复用已有的 DatabaseAdapter 而非新连 |
| 基线文件被覆盖 | 前次基线存 `*_prev.json`，当前存 `*_latest.json` |
| shell 端解析 JSON 复杂 | 内联 Python 做对比，只返回退化结果给 shell |

## 设计原则

- **复用已有机制**：不新增独立的飞书发送代码，退化结果通过 `_STEP_RESULTS` 注入 → `cron_finish` 自动发通知
- **贴合现有模式**：基线文件路径 `/root/.hermes/data/flywheel/kt-baseline-prev.json`（与聚类同目录）
- **简单可逆**：退化只告警不下线，人工确认
