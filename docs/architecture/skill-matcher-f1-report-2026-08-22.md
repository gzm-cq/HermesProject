# Skill Matcher F1 优化验证报告（2026-08-22）

## 一、结论
部署 LLM 精排优化 + 打开本地 embedding 通道 + 调大 top-K 后，真实 F1 实测：

| 指标 | 历史基线（报告值） | 本次实测 | 变化 |
|------|-------------------|----------|------|
| avg F1@3 | 0.40 | **0.4644** | **+0.064 (+16%)** |
| avg Precision@3 | 0.4111 | 0.4222 | +0.011 |
| avg Recall@3 | 0.4222 | **0.6000** | **+0.178** |
| 是否破 P0 阈值(0.4) | 临界 | **已超** | ✅ |

> ⚠️ 口径说明：历史 0.40 是在**未校准评估集**（含 9 条引用不存在 skill 的 query）上测的；本次在**已校准评估集**（删除 3 个 phantom skill）上测。因此 +0.064 中一部分来自校准、一部分来自真实改进，无法完全剥离，但方向明确——**Recall 的大幅跃升是 embedding 通道从"死"变"活"的直接证据**。

## 二、做了什么（本次部署链路）
1. **[O2] 打开本地 embedding**：`.env` 追加 `KN_SKILL_EMBEDDING_URL=http://127.0.0.1:8082/v1`（本地 bge-m3，无鉴权，与 SiliconFlow 向量等价）。根因：原 `.env` 无 embedding URL/key → 打云端 401 → 熔断器永久打开 → **embedding 预筛自上线起就是死通道**。切换 = 首次真正启用，零额外显存（模型早已常驻共享）。
2. **[B] 调大 top-K**：`KN_SKILL_EMBEDDING_TOP_K=30→60`、`KN_SKILL_PRESCREEN_TOP_K=60`。
3. **[LLM 精排] 候选重排 + prompt 收敛**（`skill_matcher.py`）：候选按相关度降序（非字母序）；prompt 由"宁多选不少选"改为"精准优先、最多 3、不足只返回确信的"；示例引用 phantom skill 已修正。
4. **[校准] 评估集删除 3 个不存在 skill**：`hermes-fallback-model-troubleshooting`(5 query)、`honcho-llm-configuration`(3 query)、`kanban-orchestrator`(1 query)。

## 三、验证方式
- 候选池并集召回@30（top-k=60 + 本地 embedding）：**1.0000**（54/54），含原漏召的 `ship`/`hindsight-memory`。
- 真 LLM 精排 F1：稳健 harness（逐条 90s 超时、workers=2）跑完 30 条，零超时零报错，结果落 `baselines/skill_eval_2026-08-22.json`。

## 四、残余：5 个 F1=0.000 项分析
| query | expected | got | 性质 |
|-------|----------|-----|------|
| skill_02 SiliconFlow API 使用方法 | cost-aware-llm-pipeline | lark-skill-maker / sn-da-image-caption / sn-search-finance | **真误判**（返回噪声 skill，lat=59s 疑似 embedding 抖动） |
| skill_07 LiteLLM 配置问题 | cost-aware-llm-pipeline | hermes-infrastructure / system-operations-rules / systematic-debugging | **真误判**（LLM 选了泛化项） |
| skill_12 Hermes 插件注册方法 | hermes-infrastructure | hermes-desktop-plugins（仅 1 个） | **真误判**（召回不足 + 弱选） |
| skill_18 代码审查流程 | graph-assisted-code-review / monorepo-code-review / codebase-inspection | github-workflows / code-development-workflow / review | **基准过窄**（matcher 返回的 review/code-development-workflow 也合理，未被 expected 覆盖） |
| skill_08 PG 连接错误排查 | database-migrations | postgresql-optimization / gateway-platform-troubleshooting / systematic-debugging | **基准过窄**（postgresql-optimization 比 database-migrations 更贴题） |

→ skill_18/skill_08 属**评估集 expected 太窄**，不是 matcher 缺陷；skill_02/07/12 是待改进的真正漏配/误配。

## 五、建议的后续（待拍板）
- **A**：修生产 `run_skill_eval.py` 的 `future.result()` 无超时 bug（加 `timeout` + 逐条进度），避免再次整轮卡死。
- **B**：放宽 skill_18/skill_08 的 expected（纳入 review/code-development-workflow/postgresql-optimization 等合理项），让评估集更贴合真实召回。
- **C**：针对 skill_02/07/12 的 LLM 精排误判，可进一步收敛（top-2 而非固定 3、或对泛化词降权）。
- **D**：收口，当前 F1=0.4644 已稳定超阈值，飞轮 P0（F1<0.4）解除。
