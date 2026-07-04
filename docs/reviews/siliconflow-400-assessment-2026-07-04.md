# SiliconFlow Rerank HTTP 400 — 归档评估

**日期**: 2026-07-04
**结论**: 本次改动（HEAD~12..HEAD）不涉及 SiliconFlow rerank 调用路径，无需处理。

## 验证

| 检查项 | 结果 |
|--------|------|
| hooks.py diff 涉及 rerank HTTP 调用？ | 否 — hooks.py 只消费 `extract_rerank_scores(trace_data)`，不直接调 rerank API |
| complex.py 有变更？ | 否 — `git diff` 为空 |
| 熔断器 (circuit_breaker.py) 完整？ | 是 — `circuit_record_failure/success` 调用点未减少 |
| 原 400 根因 | cli.py embedding `embed_fn` 未绑定 api_key (401)，已在 581658b 修复 |
| 生产 reranker 配置 | `HINDSIGHT_API_RERANKER_PROVIDER=siliconflow`, 模型 `Qwen/Qwen3-Reranker-0.6B`（直连 SiliconFlow，不走 LiteLLM） |

## 归档

SiliconFlow rerank 400 原根因是 cli.py 中 embed_fn 未绑定 api_key（已在 581658b 修复）。生产 reranker 一直使用 SiliconFlow Qwen/Qwen3-Reranker-0.6B，从未切换 RRF。本次 12 commits 未削弱熔断保护，不需要额外修复。
