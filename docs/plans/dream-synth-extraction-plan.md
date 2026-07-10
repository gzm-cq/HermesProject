# gbrain 梦境引擎提取方案：Hermes 对话记录 → SAG 知识提炼

## 背景

gbrain 的 `dream` 循环包含两阶段最有价值：**synthesize**（对话→反思笔记）和 **patterns**（跨 session 主题发现）。但这 960 行 TypeScript 强绑定 gbrain 的 PGLite/PostgreSQL 引擎、MinionQueue 子 agent 调度、Anthropic SDK，无法直接用于我们的环境。

目标：提取核心逻辑，写一个 Python cron 脚本，从 Hermes `state.db` 读 session，经 LLM 提炼，通过 MCP 写入 SAG。

---

## 一、输入来源：Hermes Session

**位置：** `/root/.hermes/state.db`（SQLite）

**sessions 表字段：** `id`, `title`, `message_count`, `input_tokens`, `output_tokens`, `estimated_cost_usd`, `started_at`, `ended_at`, `archived` ...

**messages 表字段：** `session_id`, `role (user/assistant/tool)`, `content`, `timestamp`

**统计：** 584 个 session，最早的 2026-04-25

**一个 transcript = 一个 session 的完整对话**（user + assistant 交替，过滤 tool 输出）

---

## 二、每日流水线：synthesize → patterns → Wiki promote → 飞书推送

三个任务每日串行，一个脚本，一条 cron：

```
cron 每日 16:00 → python3 dream-daily.py
  ↓
┌─ Phase 1: synthesize ─────────────────────────────┐
│  Step 1: 从 state.db 读当天新增的 session         │
│    - created_at >= last_run                       │
│    - message_count >= 10                          │
│    - input_tokens >= 2000                         │
│    - 排除 archived 的                              │
│  Step 2: 提取对话文本（去 tool output）            │
│    - 只保留 user + assistant 的消息                │
│    - tool 输出只保留调用名，不保留结果              │
│  Step 3: significanceFilter（LLM 过滤）            │
│    - 调 LiteLLM（模型可配，推荐 yd- 低成本系列）    │
│    - 输出 {worth: true/false, reason: str}          │
│    - 结果缓存到 /tmp/dream-verdicts/{id}.json       │
│  Step 4: synthesize（LLM 提炼）                    │
│    - 调 LiteLLM（推荐 s-deepseek-v4-flash）         │
│    - 输出 markdown                                 │
│    - 写入 SAG：sag_ingest_document(tag=dream-synth) │
└────────────────────────────────────────────────────┘
  ↓
┌─ Phase 2: patterns ───────────────────────────────┐
│  Step 1: 从 SAG 查近期反思笔记                     │
│    - sag_search(query="dream-synth", topK=100)     │
│  Step 2: 调 LLM 发现重复主题                       │
│    - 问："以下 N 篇反思中，有哪些重复出现的主题？"  │
│    - 输出 {patterns: [{topic, count, ids}]}        │
│  Step 3: 写入 SAG（tag=dream-pattern）              │
└────────────────────────────────────────────────────┘
  ↓
┌─ Phase 3: Wiki promote ───────────────────────────┐
│  Step 1: 从 SAG 读本周未归档的反思笔记              │
│    - sag_search(query="dream-synth", topK=50)      │
│    - 排除已写入 Wiki 的（查 /tmp/dream-promote-log）│
│  Step 2: LLM 判断"值得归档吗"                      │
│    - 入选：确定性知识（配置、架构决策、API、经验）   │
│    - 排除：日常记录、临时讨论、已过时信息            │
│    - 输出 {promote: bool, category, wiki_path}      │
│  Step 3: 写入 axiom-wiki                           │
│    - 路径：concepts/ | analyses/ | decisions/       │
│    - 调 MCP write_page + update_index + update_moc  │
│  Step 4: 记入去重日志                               │
└────────────────────────────────────────────────────┘
  ↓
┌─ Phase 4: 飞书推送 ───────────────────────────────┐
│  从本次处理中未被归档的反思笔记里，取 top-5 最有    │
│  价值的（按 LLM score 排序），推送飞书消息          │
│  格式：                                            │
│  · 标题：<提炼标题>                                │
│  · 摘要：<一句话>                                  │
│  · 未归档原因：<score 不够高 / 内容偏临时>          │
│  · 用户可复制后手动告诉 axiom 归档                  │
└────────────────────────────────────────────────────┘
```

### 用户标记

用户标记（"记下来/记入 Wiki"）不由本脚本处理。用户通过对话让 axiom 直接写入 Wiki，与本流水线正交。

---

## 三、prompt 设计

### significanceFilter prompt

```
你是一个对话分析师。分析以下 AI Agent 与用户的对话记录。
判断这段对话是否包含值得长期记录的知识、决策或见解。

评分标准：
- 5：包含具体技术决策、架构设计、根因分析
- 3：包含一般性讨论、方案评估
- 1：日常闲聊、简单问答、错误排查（不记录）

输出 JSON：{"score": 1-5, "reason": "一句话理由"}
```

### synthesize prompt

```
你是一个知识整理助手。分析以下 AI Agent 会话，提取关键信息写成
长期可读的反思笔记（markdown 格式）。

包含：
1. 摘要：一句话概括本次对话
2. 关键决策：做了什么决策？为什么？
3. 知识要点：值得长期记录的技术知识
4. 待办事项：需要后续跟进的内容

对话：
{session_text}
```

### promote judge prompt

```
判断这条反思笔记是否值得写入长期知识库（Wiki）。

入选标准（满足任一即可）：
- 具体的技术配置、架构决策、API 用法
- 经验教训、根因分析
- 明确可复用的知识点

排除：
- 日常记录、临时讨论、个人感想
- 已过时的信息
- 纯操作步骤

输出 JSON：{"promote": true/false, "category": "concepts|analyses|decisions", "reason": "理由"}
```

---

## 四、与 gbrain 对比

| 维度 | gbrain synthesize/patterns | 独立 cron 脚本 |
|------|--------------------------|----------------|
| **总代码量** | 960 行（含大量 gbrain 基础设施） | ~400 行 Python |
| **数据源** | `.txt` 文件必须放入指定目录 | 直接读 `state.db`，零配置 |
| **模型** | 硬编码 Anthropic Haiku + Sonnet | LiteLLM 任意模型，配置可改 |
| **输出** | gbrain `put_page` → PGLite/PostgreSQL | SAG `sag_ingest_document` |
| **依赖** | gbrain 整个引擎 + Bun + MinionQueue | 纯 Python + requests + sqlite3 |
| **部署** | gbrain dream 命令（需 PGLite 脑） | Hermes cron，统一管理 |
| **幂等** | verdict 缓存 + job idempotency_key | 文件缓存 session_id 列表 |
| **并发** | MinionQueue 子 agent fan-out（复杂） | 单线程串行（cron 不需要） |
| **synthesize 核心价值** | ✅ 全 | ✅ 全（prompt 复用了） |
| **patterns 核心价值** | ✅ 全 | ✅ 全 |
| **搜索能力** | gbrain 搜索（弱中文） | SAG 搜索（强混合） |
| **维护难度** | 改源码需懂 gbrain 架构 | 改 prompt 即可 |

---

## 五、全链路架构

```
用户对话
  ↓
Session（state.db）          ← 原始素材
  ↓ dream-daily.py（每日 16:00 串行）
  1. synthesize → SAG 反思笔记（tag: dream-synth）
  2. patterns  → SAG 模式发现（tag: dream-pattern）
  3. promote   → axiom-wiki（concepts/analyses/decisions）
  4. 飞书推送  → top-5 未归档反思
```

数据从 Session 出发，经过 synthesize 提炼进入 SAG，高频/高价值的再精炼沉淀到 Wiki。不是替换关系，是上下游关系。

---

## 六、知识导航插件：增加第四路 SAG 召回

当前知识导航插件 `pre_llm_call` 的三路召回：

| 路 | 来源 | 召回方式 |
|----|------|---------|
| 1 | Hindsight 语义 | bge-m3 向量 + BM25 + RRF |
| 2 | Hindsight 实体图 | entity → unit_entities → memory_links |
| 3 | Hindsight 因果链 | causal/prevents/enables → memory_links |

新增第四路：

| 路 | 来源 | 召回方式 |
|----|------|---------|
| **4** | **SAG** | **sag_search(query, topK=3, searchMode=fast)** |

**改动点：**
- `hooks.py` 的 `pre_llm_call()` 中，在所有 Hindsight recall 之后，增加 SAG 搜索
- SAG 搜索结果与 Hindsight 结果合并，去重后注入上下文
- 使用 `searchMode=fast`（比 standard 快 300-500ms，少了 rerank 阶段）
- 设置 `topK=3` 控制 token 开销

---

## 七、文件清单

```
/mnt/d/HermesProject/scripts/dream-synth/    ← HermesProject 子项目
  ├── README.md
  ├── scripts/
  │   ├── dream-daily.py          ← 每日流水线（synthesize + patterns + promote + 推送）
  │   └── dream-daily.sh          ← cron wrapper
  ├── prompts/
  │   ├── significance-filter.txt
  │   ├── synthesis.txt
  │   ├── pattern-discovery.txt
  │   └── promote-judge.txt
  └── config.yaml
```

### Cron 配置

| 时间 | 任务 | 说明 |
|------|------|------|
| 每日 16:00 | `dream-daily.sh` | 4 阶段串行：synthesize → patterns → promote → 飞书推送 |

### 部署

```
./deploy/deploy.sh deploy dream-synth --yes
```