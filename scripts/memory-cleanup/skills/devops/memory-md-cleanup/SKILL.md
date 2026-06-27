---
name: memory-md-cleanup
description: MEMORY.md + USER.md 分类清理管线——LLM JSON mode 分类 + 纯 LLM 验证（session_search 辅助），干跑不动数据
tags: [memory, cleanup, MEMORY.md, USER.md, hindsight, LLM, json-mode]
related_skills: [hermes-infrastructure, kanban-system-design]
---

# MEMORY.md + USER.md 分类清理管线 V6

## 整体流程

```
load_from_disk() → entries[]（按 § 分割 → 去重 → 数组）
     │
     ▼
Phase 1: LLM 全量分类（分批并行，JSON mode + 四路径解析）
     │  batch_size=10（MEMORY）/ user_batch_size=10（USER）
     │  两个文件各自 8 线程，外层 2 个文件并行
     │  MEMORY.md 和 USER.md 用不同的 prompt
     │  可选 --vote N 多轮投票（remove 并集，其他决策取交集）（降低 LLM 方差）
     │  批失败自动单条重试，不丢数据
     │
     ├─ merge:   [{indices, "合并为"}]     — 碎片合并成一条精简版
     ├─ remove:  [{index, "原因"}]         — 不该留的文件中
     ├─ compress:[{index, "精简为"}]       — 有用但长，输出压缩版
     ├─ flagged: [{range, count, reason}]  — 批失败且单条重试仍失败的条目
     └─ 未在任一数组的 = 保留
     │
     ▼
Phase 2: 纯 LLM 验证（session_search 辅助提供证据）
     │  所有 remove 候选都走 LLM（无 confidence 跳过门限）
     │  confidence >= 0.3: session snippet 作为额外证据传给 LLM
     │  confidence < 0.3: 仅传条目+理由，LLM 采取保守策略
     │  时间窗口软降权：>90天 gap → confidence × 0.5
     │  corrected_text 经 Python 层校验（关键词重叠+占位文本检测）
     │
     ├─ correct:   retrospect_retain(原文) → memory(remove)
     ├─ corrected: hindsight_retain(修正版) → memory(remove)
     └─ keep:      放回保留列表，不动
     │
     ▼
输出 6 + 2 + 3 数组：
   mem_merge / mem_remove / mem_compress (+ flagged)
   user_merge / user_remove / user_compress (+ flagged)
   mem_v2 (correct/corrected/keep)
   user_v2 (correct/corrected/keep)
```

## LLM 调用特性

- **JSON mode**: `response_format: {"type": "json_object"}`，稳定输出 JSON
- **四路径解析**: 正常 json.loads → strip/clean → 栈匹配嵌套 JSON → 正则提取回退
- **重试**: 3 次指数退避 + jitter（1s → 2s → 4s + random），timeout=120s
- **温度**: 0.05（低温提高一致性）
- **模型**: s-deepseek-v4-flash（通过 LiteLLM 网关 :4142）

## Phase 1 质量校验

LLM 输出经过 Python 层过滤，拦截低质量 merge/compress：

**merge 校验**：
- 合并文本长度 < 原文总长度 × 0.8（拦截简单拼接）
- 日期抽象度检查（所有原文含日期时，合并文本不应含具体日期，除非已充分压缩 < 50%）
- 中文关键词覆盖率（≤3 条: 任一原文重叠即放行; >3 条: avg ≥ 0.15）

**compress 校验**：
- 长度 ≥ 10 字符
- 压缩比 ≤ 12:1
- 关键实体（IP/端口/版本号）100% 保留
- 非关键实体（URL/路径）允许最多 70% 遗漏
- 中文关键词重叠 ≥ 20%

## Prompt 设计（关键）

MEMORY.md 和 USER.md 的 prompt **必须分开**：

**MEMORY.md prompt**：激进分类
- 绝对不能标 remove：工具特性/经验教训/架构约定/环境配置/用户偏好
- 应该标 remove：业务数据/个人陈述素材/论文信息/过程记录/空条目/清理自身记录
- 结尾："宁少标勿错标——不确定的标 compress 或不管"

**USER.md prompt**：保守分类
- 绝对不能标 remove：个人背景/工作习惯/技术能力/项目状态/用户规则
- 仅以下几种才标 remove：完全重复/清理临时记录/已被合并替代
- 结尾："USER.md 内容几乎都应保留"
- compress: 句式精简不做含义丢弃，不设字符硬限

## Phase 2 验证流程

所有 remove 候选都走 LLM，无 confidence 跳过门限：

- **confidence ≥ 0.3**: prompt 含条目原文 + session snippet + 移除原因
- **confidence < 0.3**: prompt 仅含条目原文 + 移除原因，保守策略（不确定时 keep）
- 两套 prompt 模板，根据是否有 session 上下文自动选择

**corrected_text Python 校验**（防止 LLM 跑偏）：
```python
orig_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", original))
corr_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", corrected))
kw_overlap = len(orig_kw & corr_kw) / max(len(orig_kw), 1) if corrected else 0

has_real_fix = (
    corrected and len(corrected) > 10
    and corrected != text[:len(corrected)]
    and "修正" not in corrected[:20]
    and "需补充" not in corrected[:20]
    and kw_overlap > 0.2  # 中文/英文关键词重叠
)
# 无效 corrected → 降级为 correct（retain 原文 + remove）
```

**corrected_text 校验关键词正则已包含英文兜底**：纯英文条目（如 `API key rotated`）会被 `[a-zA-Z]{4,}` 捕获，防止纯英文条目的 `kw_overlap` 计算失效。

## 并发控制

| 阶段 | 机制 | 最大并发 |
|------|------|---------|
| Phase 1 MEMORY.md | 内层 8 线程池 × batch_size=10 | 8 |
| Phase 1 USER.md | 内层 8 线程池 × user_batch_size=10 | 8（与 MEMORY 并行） |
| Phase 2 | 共用 8 线程池，MEMORY/USER 流水线并行 | 8 |

流水线效果：MEMORY Phase 1 完成即启动 MEMORY Phase 2，不等 USER Phase 1。

## 批失败处理

批失败后**自动单条重试**（逐条调用 LLM），一条失败不污染其他条目：
- 单条重试成功 → 正常归入 merge/remove/compress
- 单条重试仍失败 → 加入 `flagged` 列表，不丢数据

## 多轮投票（--vote N）

当 LLM 方差较大时，可用 `--vote N`（N > 1）跑 N 轮分类取交集：
- **remove**: 任一轮标 remove 的 index 都保留，后续交给 Phase 2 验证
- **compress**: 所有轮次都标 compress 的 index 才保留（取第一轮的精简版本）
- **merge**: 所有轮次都标 merge 的 indices 组合才保留（取第一轮的合并版本）
- **flagged**: 取所有轮次的并集

效果：N=2 时 remove 从 16-23 条收敛到稳定值，适合 cron 自动化。

## 关键依赖

- Prompt 模板参见 `src/memory_cleanup/core/prompts.py`
- `CLUSTERING_DB_URL` 环境变量连 PG
- `LITELLM_MASTER_KEY` 环境变量调 LLM（走 LiteLLM 网关 :4142，模型 s-deepseek-v4-flash）
- Hindsight recall 端点：`POST http://127.0.0.1:9177/v1/default/banks/hermes/memories/recall`
- Session DB：`~/.hermes/state.db`（SQLite FTS5），`messages_fts` 表
- PG `memory_units`：`SELECT text FROM memory_units WHERE bank_id='hermes' AND text ILIKE '%keyword%'`

## 注意事项

- 始终 **干跑（不修改文件）**，只输出数组结果给人确认
- PG 的 tsvector 对中文无效（`to_tsvector('english', ...)`），查重用 ILIKE 中文关键词
- session_search 毫秒级（FTS5），但中文关键词匹配率不稳定，故 Phase 2 改为纯 LLM 验证
- Hindsight recall 每条 10-15 秒，Phase 2 不再依赖 recall
- 清理自身记录（清理原则/三阶段流程/方案版本）、空条目（仅 §）、merge/compress 已替代的原条目 → **不需要 LLM 二次判断**，直接硬逻辑处理
- 入口：`bash run.sh`（推荐）或 `python -m memory_cleanup`
- 两个数组（MEMORY+USER）完全独立，不会混在一起
- compress 后的条目必须保留完整含义（尤其 USER.md），不能缩成只有关键词的片段
- JSON mode 大幅减少解析失败率（从 ~15% 降到 ~2%）
- corrected=0 是常态（Python 层校验拦截了无效修正）

## CLI 参数

| 参数 | 说明 |
|------|------|
| `--apply` | 实际执行清理（默认 dry-run） |
| `--json` | JSON 格式输出（含 token 消耗、耗时） |
| `--vote N` | 投票轮数（0=使用配置，>1=多轮投票：remove 并集，其他决策取交集） |
| `--log-level` | 日志级别（DEBUG/INFO/WARNING） |
| `--config` | 配置文件路径（默认 config/default.yaml） |

## 配置文件（config/default.yaml）

```yaml
batch_size: 10           # MEMORY.md 每批条目数
user_batch_size: 10      # USER.md 每批条目数
vote_count: 1            # 投票轮数（cron 推荐 2）
# max_workers: 自动计算 min(32, cpu+4)
memory_char_limit: 50000
user_char_limit: 15000
llm_model: "s-deepseek-v4-flash"
```

所有字段均支持 `MEMORY_CLEANUP_*` 环境变量覆盖。

输出特性：
- 报告自动保存到 `~/.hermes/memories/cleanup-report-{ts}.json`
- 终端显示耗时统计 + token 消耗 + Phase 2 LLM 验证状态
- 支持 Ctrl+C 优雅中断