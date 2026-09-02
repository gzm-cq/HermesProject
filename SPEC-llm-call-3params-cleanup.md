# SPEC: HermesProject LLM 调用请求体三参数收敛

## 目标

将 HermesProject 中所有 LLM 调用请求体统一收敛为 **3 个参数 + 1 个保留字段**：

    temperature / top_p / max_tokens   （三参数，按业务场景冷配置）
    response_format = {"type": "json_object"}  （保留字段，JSON 场景）

其余参数（thinking / extra_body / chat_template_kwargs）**一概不留**。
参数值不做中性默认，按**业务场景冷配置**（确定性场景取低温低 top_p）防止模型幻觉。

## 规则（宪法级）

1. 请求体只允许出现：`model`、`messages`、`temperature`、`top_p`、`max_tokens`、`response_format`。
2. `response_format` 只允许 `{"type": "json_object"}`（最大化兼容 OpenAI 兼容端点）；**不使用** `json_schema`。
3. thinking / extra_body / chat_template_kwargs 一律删除，不按模型前缀做特判，行为交回网关/模型服务端默认值。
4. 参数值按场景档位显式配置，禁止中性值（如 temperature=0.5 配 top_p=1.0 的"万金油"）。
5. `skillopt-sleep` 为独立自有生态（Qwen/MiniMax/Claude/Codex 后端），`chat_template_kwargs` 属其自身 API 栈，**不在本次范围**。

## 场景档位（冷配置基准）

| 档位 | 适用场景 | temperature | top_p | 说明 |
|------|----------|-------------|-------|------|
| A · 确定性 JSON | 路由判断 / 技能匹配 / 评分 / 校验 / 实体提取 / 范围回填 | 0 | 0.1 | 防幻觉核心：结构固定、可校验 |
| B · 摘要/反思/改写 | 反思 / 摘要 / 生成类 | 0.3 | 0.9 | 允许少量表达变化 |
| C · 创意生成 | eval 查询生成等 | 0.7 | 0.9 | 仅用户明确要多样性的场景 |

max_tokens 按各调用点业务量级显式配置（推理模型沿用 16384 下限，非推理模型按原值）。

## 现状审计结果（2026-08-31 codegraph 全量核查）

### 公共层（影响面最大）

`libs/hermes_common/hermes_common/llm_guard.py`
- `build_chat_body()`（L98-149）：当前注入 `thinking`（s-deepseek/agnes 强制 enabled，其他默认 disabled）+ `response_format`（json_mode 时）
- `guarded_chat_completion()`（L451）：签名**无 top_p 透传**
- 传输层 `make_requests_post` / `make_urllib_post`：干净，仅 post body + headers ✓

### 直连接口调用点（请求体含多余参数）

| # | 文件:行 | 场景 | 当前多余参数 | 目标档位 |
|---|---------|------|--------------|----------|
| 1 | plugins/knowledge-navigation/.../core/router.py:72 | 路由判断 | thinking | A |
| 2 | plugins/knowledge-navigation/.../core/skill_matcher.py:1161 | 技能匹配 | thinking | A |
| 3 | plugins/knowledge-navigation/scripts/_router_stability_check.py:36 | 稳定性检查 | thinking | A |
| 4 | plugins/knowledge-navigation/scripts/generate_eval_queries.py:59 | eval 查询生成 | thinking | C |
| 5 | plugins/knowledge-navigation/scripts/collect_baseline.py:893 | judge 评分 | thinking | A |
| 6 | scripts/dream-synth/scripts/dream-daily.py:209 | 摘要/反思 | extra_body.thinking | B |
| 7 | scripts/knowledge-tree-builder/scripts/backfill_entities.py:77 | 实体提取 | extra_body.thinking | A |
| 8 | scripts/clustering-analysis-v3/.../core/quality.py:214 | 质量评分 | thinking | A |
| 9 | scripts/clustering-analysis-v3/.../core/embeddings.py:34,144 | 实体/因果提取 | thinking | A |
| 10 | scripts/flywheel-health-report/.../kn_judge.py:250 | 评分 | thinking | A |
| 11 | scripts/cron-wrappers/backfill-scope.py:148 | 范围回填 | thinking | A |

### 走 guarded_chat_completion 的调用点（薄封装，仅需 llm_guard 透传 top_p）

| # | 文件 | 当前 temperature | 目标 |
|---|------|------------------|------|
| 12 | scripts/memory-cleanup/.../adapters/llm_client.py:115 | 0.05 | A 档 (0, 0.1) |
| 13 | scripts/recall-eval/.../adapters/llm_client.py:133 | 0.0 | A 档 |
| 14 | scripts/self-evolving/.../adapters/llm_client.py:111 | 0.3 | B 档 |
| 15 | scripts/self-evolving/.../kanban_reflection/adapters/llm_client.py | 0.3 | B 档 |
| 16 | scripts/knowledge-tree-builder/.../llm/client.py | 0.3 | B 档 |
| 17 | scripts/knowledge-tree-builder/.../core/{extractor,namer,validator}.py | 0 | A 档 |
| 18 | scripts/self-evolving/.../operators/{refinement,revision,recombination}.py | 0.1-0.3 | B 档 |

### 已合规（无需改动）

- `skillopt_sleep/backend.py` LiteLLMBackend：刻意不发送 thinking（注释已说明理由）✓
- 各 embedding / rerank 调用：仅 model+input，本就不该带生成参数 ✓

## 设计方案

### 1. 公共层 llm_guard.py 改造

`build_chat_body()`：
- 新增 `top_p: float | None = None` 参数；非 None 时写入 body。
- **保留** L145 `response_format = {"type": "json_object"}`（json_mode 时注入）—— 最大化兼容。
- **删除** L132-140 thinking 注入逻辑（s-deepseek/agnes 强制 enabled + 其他 disabled）。
- **保留** `JSON_ONLY_SYSTEM` prompt 约束、`clamp_max_tokens`（max_tokens 取值策略，非额外字段）、`parse_json_response` 兜底。
- 删除不再使用的 `_is_thinking_required_model`、`_thinking_disabled`，避免死代码。

`guarded_chat_completion()`：
- 新增 `top_p: float | None = None` 参数，透传给 build_chat_body。

### 2. 直连接口调用点（11 处）

统一模式：请求体 `json=` 中删掉 `thinking` / `extra_body`，补 `top_p`，按档位写 temperature：

```python
# 档位 A 示例（router.py）
json={
    "model": model,
    "messages": [...],
    "temperature": 0.0,
    "top_p": 0.1,
    "max_tokens": _rt_mt,   # 保留原 max_tokens 策略
}
```

```python
# 档位 B 示例（dream-daily.py）—— 删 extra_body
payload = {
    "model": model,
    "messages": [...],
    "max_tokens": _dd_max,
    "temperature": 0.3,
    "top_p": 0.9,
}
```

```python
# 档位 C 示例（generate_eval_queries.py）
json={
    "model": model,
    "messages": [...],
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": ...,
}
```

### 3. 薄封装调用点（7 处）

在调用 `guarded_chat_completion(...)` 时补 `top_p=` 实参（档位见上表），temperature 同步到档位值。
`extractor.py / namer.py / validator.py` 走本项目 `llm/client.py` 薄封装，仅需确认封装的 `chat_completion` 签名透传 `top_p`；若签名无此参数，在 `client.py` 补一个可选 `top_p: float | None = None` 并转发。

## 文件清单（共 19 个源文件 + 1 公共库）

```
修改: libs/hermes_common/hermes_common/llm_guard.py
修改: plugins/knowledge-navigation/src/knowledge_navigation/core/router.py
修改: plugins/knowledge-navigation/src/knowledge_navigation/core/skill_matcher.py
修改: plugins/knowledge-navigation/scripts/_router_stability_check.py
修改: plugins/knowledge-navigation/scripts/generate_eval_queries.py
修改: plugins/knowledge-navigation/scripts/collect_baseline.py
修改: scripts/dream-synth/scripts/dream-daily.py
修改: scripts/knowledge-tree-builder/scripts/backfill_entities.py
修改: scripts/clustering-analysis-v3/src/clustering_analysis/core/quality.py
修改: scripts/clustering-analysis-v3/src/clustering_analysis/core/embeddings.py
修改: scripts/flywheel-health-report/src/flywheel_health_report/analyzers/kn_judge.py
修改: scripts/cron-wrappers/backfill-scope.py
修改: scripts/memory-cleanup/src/memory_cleanup/adapters/llm_client.py
修改: scripts/recall-eval/src/recall_eval/adapters/llm_client.py
修改: scripts/self-evolving/src/self_evolving/adapters/llm_client.py
修改: scripts/self-evolving/src/kanban_reflection/adapters/llm_client.py
修改: scripts/knowledge-tree-builder/src/knowledge_tree_builder/llm/client.py
修改: scripts/knowledge-tree-builder/src/knowledge_tree_builder/core/extractor.py
修改: scripts/knowledge-tree-builder/src/knowledge_tree_builder/core/namer.py
修改: scripts/knowledge-tree-builder/src/knowledge_tree_builder/core/validator.py
修改: scripts/self-evolving/src/self_evolving/operators/refinement.py
修改: scripts/self-evolving/src/self_evolving/operators/revision.py
修改: scripts/self-evolving/src/self_evolving/operators/recombination.py
```

## 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 删除 thinking 后推理模型（s-deepseek/agnes）行为变化 | 输出质量/内容空 | 行为交回网关侧（Bifrost）默认；max_tokens 16384 下限保留兜底 content 为空问题 |
| 非推理模型不再显式 thinking:disabled | 延迟、行为恢复服务端默认 | 目标行为：与 Hermes 主配置一致，服务端默认值处理（skillopt 注释已证明此路径延迟最优） |
| top_p 新字段对不支持的服务端 | 被忽略/报错 | 均为 OpenAI 兼容端点，top_p 是标准字段；验收时抓包确认 |

## 验收标准

1. **语法/测试**：对每个改动模块跑 `python3 -m pytest <dir>/tests -q`（按目录跑，勿跑项目级全量，避免 conftest.py 同名 ImportPathMismatchError）。公共库 llm_guard 全量测试通过。
2. **请求体核验**：`grep -rn "thinking\|extra_body\|chat_template_kwargs" 改动文件` 结果为 **0 命中**（skillopt-sleep 除外）。
3. **三参数完备**：`grep -n "json={\|payload = {" 改动文件` 中每个请求体含 temperature + top_p + max_tokens；response_format 若出现必须是 `{"type": "json_object"}`。
4. **实弹验证**：跑一轮 `_router_stability_check.py`（OK/FAIL）与一条 dream-daily 摘要链路，确认 content 非空、无异常。
5. **回归**：改动前后各跑一次 `collect_baseline.py` 采样，JSON 解析成功率不下降（≥ 改动前 -2% 视为可接受）。

## 不做的事

- 不改 `skillopt-sleep/` 内部 `chat_template_kwargs`（独立生态）。
- 不动 Hermes `config.yaml`（本次只收敛项目源码请求体）。
- 不引入新的统一请求体封装（避免过度设计，保持每调用点显式三参数）。
