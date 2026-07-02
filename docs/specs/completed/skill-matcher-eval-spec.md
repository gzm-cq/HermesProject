# Skill Matcher 评估飞轮 SPEC 实施计划

> **审计状态**：已分析源码 + trace.log 数据（2026-07-01）
> **实施状态**：✅ Ring 1（评估环）已实施 — `run_skill_eval.py`、`skill_eval_queries.json`、`run-skill-eval.sh` cron wrapper 均已就位
> **原则**：不修改 Hermes Gateway 源码，所有优化在 knowledge-navigation 插件层面实施
> **前序工作**：Router 全查率降低 SPEC 已编写但尚未实施（`router-alltrue-reduction-spec.md`），collect_baseline.py 已修复

---

## 一、问题全景

### 现状

```
用户消息 → LLM 精排(352技能全量) → 选 1-3 个注入 → 模型回复
                                  ↑
                    1.4-2.1s / 次，无反馈信号
```

### 事实数据（trace.log，最近 24h）

| 指标 | 值 |
|:----|:----:|
| 总匹配次数 | 195 |
| 命中技能数 | 128/352 |
| 单次延迟 | 1.4-2.1s |
| 费用 | ~200 次/天 × ~13K tokens/次（352 技能 × 描述 120 字） |
| Top 1 技能 | `enterprise-ai-construction-report` (22次) |

### 当前流程（`skill_matcher.py` 第 710-747 行）

```
match_skills() 
  → ensure_index() 加载 352 个 skill（懒加载/增量更新）
  → _llm_match(query, top_k=3)
    → _build_skill_prompt(pool) 格式化 352 个 skill → 约 13K tokens 的 prompt
    → 调用 LiteLLM（s-deepseek-v4-flash, temperature=0.1, max_tokens=200）
    → 解析 JSON 响应，返回 1-3 个 skill 名称
```

### LLM prompt 规则（第 631-652 行）

```
1. 意图解析：提炼核心概念
2. 概念扩展：中英文同义词/近义词
3. 技能匹配：优先 name → 其次 description
4. 输出：JSON 数组（1-3 个），宁可少选不可错选
```

**prompt 质量本身不差**，问题是没有评估信号验证它做得对不对。

---

## 二、断裂点

| 缺什么 | 为什么 | 影响 |
|:------|:-------|:----:|
| ❌ **无匹配质量信号** | trace.log `skill_match` 只记录"选了谁"，不记录"选对没有" | 无法知道 19% 的匹配是错配 |
| ❌ **无标准评估集** | 没有"这个 query 应该命中哪些 skill"的 ground truth | 无法量化当前准确率 |
| ❌ **无优化验证** | prompt 改了之后是好是坏？只能凭感觉 | 不敢改 prompt |

---

## 三、实现方案

### Ring 1 — 评估环

#### 3.1 构建评估集 `skill_eval_queries.json`

格式（新增文件，independent.eval_queries.json）：

```json
[
  {
    "query_id": "eval_skill_01",
    "query": "LiteLLM 配置相关的问题怎么处理",
    "dimension": "skill",
    "expected_skills": ["hermes-fallback-model-troubleshooting", "hermes-integration-field-alignment-guidelines", "cost-aware-llm-pipeline"],
    "expected_categories": ["software-development", "mlops"]
  },
]
```

字段说明：
- `expected_skills`：正确答案（技能名称，必须与 SKILL.md frontmatter name 完全一致）
- `expected_categories`：可选的兜底维度，当 expected_skills 为空时按分类打分

**数据来源**：从 trace.log 提取 `skill_match` + 对应的 `recall_success` 事件，人工标注。

#### 3.2 评估脚本 `run_skill_eval.py`

用法：
```bash
python3 scripts/run_skill_eval.py                         # 跑评估集输出报表
python3 scripts/run_skill_eval.py --json                   # JSON 输出（用于对比）
python3 scripts/run_skill_eval.py --compare before.json after.json  # 对比两次评估
```

评估指标：

| 指标 | 定义 | 说明 |
|:----|:-----|:-----|
| **Precision@3** | 返回的技能中属于 expected_skills 的比例 | 越高越精准 |
| **Recall@3** | expected_skills 中被命中的比例 | 越高越全面 |
| **F1@3** | 2 * P * R / (P + R) | 综合质量 |
| **Latency** | 单次匹配平均延迟 | 性能监控 |
| **Empty rate** | 返回 [] 的 query 比例 | 过高说明漏查 |

#### 3.3 评估数据流

```
trace.log (skill_match 事件)
  → query_trunc → 人工标注 expected_skills
    → skill_eval_queries.json 迭代扩充
```

### Ring 2 — 优化环

评估数据产出后，可执行三类优化：

| 优先级 | 优化项 | 方法 |
|:-----:|:-------|:----:|
| **P1** | 优化 LLM prompt | 基于 eval 失败案例（漏选的 skill），向 prompt 追加该 skill 的描述或规则 |
| **P2** | 预筛选管道权重调整 | 关键词预筛选的权重配比（name +10, category +3, description +1）可调 |
| **P3** | 降本：embedding 替代 LLM | 如果 embedding 余弦 top-K 与 LLM 匹配有 >90% 一致性，可跳过 LLM 调用 |

---

## 四、实施步骤（Phase 1 — 建立基线）

| 步 | 内容 | 产出 | 预计耗时 |
|:--:|:-----|:----|:--------:|
| 1 | 从 trace.log 提取最近 30 条 `skill_match` 事件的 `user_message_trunc` | 原始 query 列表 | 5min |
| 2 | 手工为每条 query 标注 2-5 个 expected_skills（凭领域知识判断） | `skill_eval_queries.json`（30 条） | ~30min |
| 3 | 写 `run_skill_eval.py` | 评估脚本 | ~15min |
| 4 | 跑第一版评估，输出 Precision@3 / Recall@3 / F1 | 基线数据 | ~2min |
| 5 | 同步源码 + 部署到 `/root/.hermes/plugins/` | — | ~2min |
| 6 | 增加到每日 cron（跟随 skillopt 或独立凌晨跑） | — | ~2min |

---

## 五、验收标准

| 指标 | Phase 1 目标 | Phase 2 目标 |
|:----|:-----------:|:-----------:|
| Precision@3 | >0.50 | >0.70 |
| Recall@3 | >0.50 | >0.65 |
| F1@3 | >0.50 | >0.67 |
| 评估集大小 | 30 queries | 100+ queries |
| Latency | — | <1500ms (avg) |

---

## 六、依赖

- `skill_matcher.py` 的 `llm_match()` 函数可直接被 `run_skill_eval.py` 复用（`from knowledge_navigation.core.skill_matcher import match_skills, ensure_index`）
- `ensure_index()` 在测试前必须调用一次（懒加载 ~50ms）
- `LITELLM_MASTER_KEY` 环境变量需可用（用于 LiteLLM gateway 调用）

---

## 七、回退方案

评估脚本是只读的（不修改 skills 目录），无回退风险。