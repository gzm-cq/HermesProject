# 自进化项目 — 任务清单

> **版本**: v2.0  
> **创建时间**: 2026-05-30  
> **更新说明**: v1.1 → v2.0 基于 ROI 评估重写。Phase A（3.5h，高确定性高回报）/ Phase B（有条件执行）/ Phase C（暂缓）  
> **评估结论**: 前 3.5h 贡献约 80% 预期收益，剩余 ~22h 贡献约 20%。建议 Phase A 做完后用数据决定是否继续。

---

## 一、项目定义

| 项目名 | 实际路径 | 说明 |
|:---|:---|:---|
| **聚类脚本** | `~/.hermes/scripts/clustering-analysis-v3/` | Hindsight 优化工具集：实体提取、因果链、标记写入 |
| **知识导航插件** | `~/.hermes/plugins/knowledge-navigation/` | pre_llm_call Hook：recall → 排除 → 注入 |
| **自进化项目** | `/mnt/d/HermesProject/scripts/self-evolving/` | 三大算子设计文档+代码，仅含设计 |
| **Hermes Agent** | `/root/.hermes/hermes-agent/` | Hermes 核心运行时：`run_agent.py`、工具注册 |
| **PG 数据库** | `shared-postgres:5434` | SQL DDL 操作，PG 直连 |

---

## 二、评估结论速览

| 模块 | ROI | 成本 | 理由 |
|:---|:---:|:---:|:---|
| 记忆标记（排除错误记忆） | **最高** | 1.5h | 防止坏的记忆永久污染 recall |
| 时态衰减（时间加权） | **高** | 0.5h | 当前完全忽略时间，Mem0 同类改进 +29.6 分 |
| 评估基线（建立量化体系） | **高（赋能）** | 1.5h | 没有数据等于靠感觉飞 |
| 多信号融合 | 中 | 1h | 有效但依赖 failure_records 先有数据 |
| 待验证标记 | 中低 | 1h | 理论好但实际边际收益低 |
| Revision 接入 LLM | 中 | 1h | 60% 效果，比手动调试快但非革命 |
| 因果链相关 | **低** | 3h+ | RRF 单路径+同类型限制+cross-encoder 不 boost，上限天生低 |

---

## 三、Phase A：核心 3.5h（效果最确定，立即做）

这三个模块无依赖冲突，可并行。

| ID | 任务 | 工时 | 所属项目 | 修改文件 | 验证方式 |
|:---|:---|:---:|:---|:---|:---|
| **L2-D1** | **标记写入** | **0.5h** | **聚类脚本** | `clustering-analysis-v3/scripts/mark_memory.py` | 手动标记 1 条 |
| **L2-D2** | **排除 + top 3** | **1h** | **知识导航插件** | `plugins/knowledge-navigation/src/core/hooks.py` | T1：标记→确认排除 |
| **L2-K** | **时态衰减评分** | **0.5h** | **知识导航插件** | `plugins/knowledge-navigation/src/core/hooks.py` | 对比 rerank 排序 |
| **L2-M** | **评估基线** | **1.5h** | **知识导航插件** | `plugins/knowledge-navigation/src/core/hooks.py` + `config/layer2.yaml` | T5 |

**Phase A 总工时：3.5h**（4 个任务，无依赖冲突，可并行）

**Phase A 做完后，可回答的问题**：
1. 标记排除后，注入质量是否提升？（看 kept 数量变化 + 用户反馈）
2. 时态衰减后，近期记忆 vs 旧记忆的排序是否合理？（看 top-3 的 created_at 分布）
3. 基线数据是多少？（有了"之前"才能说有"之后"）

**只有 Phase A 数据确认有效，才进入 Phase B。**

---

## 四、Phase B：有条件执行（等 Phase A 数据）

| ID | 任务 | 工时 | 所属项目 | 修改文件 | 启动条件 |
|:---|:---|:---:|:---|:---|:---|
| L1-B | Revision 接入 LLM | 1h | **自进化项目** | `operators/revision.py` | Phase A 基线提升 ≥5% |
| L2-I | 多信号融合（quality+causal） | 1h | **知识导航插件** | `plugins/knowledge-navigation/src/core/hooks.py` | Phase A 基线稳定 |
| L1-D | 通用失败捕获（6 种来源） | 1.5h | **Hermes Agent** | `run_agent.py` | Revision LLM 集成完成 |
| L2-A | 失败模式库建表 + CRUD | 1h | **PG 数据库** | SQL: `CREATE TABLE evolution.failure_records` | 确定需要记录失败数据 |
| L2-C | 插件日志扩展（recalled_ids） | 0.5h | **知识导航插件** | `plugins/knowledge-navigation/src/core/hooks.py` | 确定需要反推可疑记忆 |

**Phase B 总工时：5h**

---

## 五、Phase C：暂缓（回报不确定）

| ID | 任务 | 工时 | 所属项目 | 修改文件 | 暂缓理由 |
|:---|:---|:---:|:---|:---|:---|
| L2-B | 因果链生成脚本 | 1.5h | **自进化项目** | `scripts/generate_causal_links.py` | RRF 单路径上限低，边际收益已耗尽 |
| L2-E | 可疑标记反推 | 1h | **自进化项目** | `operators/revision.py` | 等 L2-C 有数据 + 验证标记排除有效 |
| L2-F | 已解决标记 | 0.5h | **自进化项目** | `operators/failure_pattern_db.py` | 依赖 failure_records 有数据 |
| L2-L | 待验证标记自动管理 | 1h | **知识导航插件** | `plugins/knowledge-navigation/src/core/hooks.py` + 定时脚本 | 理论好但边际改进低 |
| L1-C | Revision 注册为 Hermes 工具 | 0.5h | **Hermes Agent** | 工具注册系统 | 手动调 `scripts/se_revision.py` 够用 |
| L1-F | Recombination 接入 LLM | 1h | **自进化项目** | `operators/recombination.py` | 无 Kanban 多轨迹需求 |
| L1-H | Refinement 接入 LLM | 1h | **自进化项目** | `operators/refinement.py` | 当前冗余检测够用 |
| L2-J | 聚类脚本调整 | 1h | **聚类脚本** | `clustering-analysis-v3/src/core/clustering.py` | 关键词因果链数据量小 |
| L2-G | 作废标记 | 2h | **自进化项目** | `scripts/mark_obsolete.py` | 极少触发 |
| L2-H | 第3层接口 | 0.5h | **PG 数据库** | SQL: `CREATE TABLE evolution.worker_performance` | 依赖 Kanban 实际使用 |

**Phase C 总工时：10h**

---

## 六、总工时

| 阶段 | 任务数 | 工时 | 含义 |
|:---|:---:|:---:|:---|
| **Phase A** | 4 | **3.5h** | **核心价值，80% 预期收益** |
| Phase B | 5 | 5h | 有条件，等 Phase A 数据决策 |
| Phase C | 10 | 10h | 暂缓，回报不确定 |
| **总计** | **19** | **18.5h** | |

---

## 七、实施节奏

### 7.1 第1天：Phase A（3.5h）

```
L2-D1 ─── 标记写入（聚类脚本）           0.5h  ────────────────┐
                                                             │
L2-D2 ─── 排除 + top 3（插件）           1h    ────────────────┤
                                                             ├──→ T1 手动标记验证 0.5h
L2-M ──── 评估基线定义 + 首次运行        1.5h  ────────────────┤
                                                             │
L2-K ──── 时态衰减评分                   0.5h  ────────────────┴──→ T5 基线首次运行 0.5h

依赖关系：
  T1 需要 L2-D1（有标记可测） + L2-D2（排除代码就绪） 都完成
  T5 需要 L2-M（50条查询定义） + L2-D2（排除 + top 3 运行中）
  L2-K 独立，无依赖
```

做完后评估 baseline 数据，决定是否进入 Phase B。

### 7.2 第2-3天：Phase B（如果 Phase A 验证通过）

```
L2-A ──── 失败模式库建表              1h
L2-C ──── 插件日志扩展                0.5h
L1-B ──── Revision 接入 LLM           1h
L2-I ──── 多信号融合                  1h
L1-D ──── 通用失败捕获                1.5h
    │
    └──→ T2 ──── 验证 LLM 调用        0.5h
```

### 7.3 以后：Phase C（等数据决策）

Phase A 跑 1 个月后，看 baseline 数据趋势，再决定要不要碰因果链。

---

## 八、Phase A 存储说明

Phase A 的标记功能不需要外部存储。标记信息直接写入 `memory_units.text`（`UPDATE SET text = text || '\n[标记: ...]'`），排除时做字符串匹配（`"[标记:" in text`）。PG 已有、零新建、零依赖。
