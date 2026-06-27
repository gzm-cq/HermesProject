# Router 注入：LLM 驱动的三路开关

> ✅ **已完成**：本方案已于 2026-06-27 实施完成，代码已合入。
>
> **Goal**：用 LLM Router 替代当前 turn_gate + _classify_intent 规则体系，由 Router 决策 Hindsight/KT/Skill 三路是否注入，各路内部检索策略自治。
> **Architecture**：pre_llm_call 入口保留来源门控 + 系统提示词门控（零成本预检），之后调用 LLM Router 输出 {h, kt, s} mask，只执行 mask 激活的路径。
> **Tech Stack**：Python, LiteLLM (through local gateway), sensenova-6.7-flash-lite
---

## 当前状态

```
pre_llm_call():
  来源门控 (skip_non_user)           → 非用户平台跳过
  系统提示词门控 (skip_system_prompt) → 系统构造的第一轮长英文跳过
  文本门控 (skip_pre_llm_call)       → 操作型/短/确认消息跳过
  熔断器检查
  _classify_intent (纯规则)           → generic=只跑skill / personal=全跑 / task=全跑
  并行三路 (HS+KT+SK)                → 全量并跑三条路
  后处理 (过滤/去重/注入)
```

**问题**：
1. **只有 3 档（generic/personal/task）不够细**。generic=只跑skill，但"解释一下之前那个方案的原理"需要 H+KT。personal=全跑，但"记得上次的配置"只需要 H+S。
2. **规则无法覆盖中间地带**（"用 MCP 接入 Dify" 既不是纯操作也不是纯知识 — 需要 H+S）
3. **generic 一刀切只跑 skill**，丢失了 "解释一下上次的配置" 这种 KT 也有用的场景

---

## 目标状态

```
pre_llm_call():
  来源门控 (skip_non_user)            → 非用户跳过 (保留)
  系统提示词门控 (skip_system_prompt)  → 系统构造跳过 (保留)
  文本门控 (skip_pre_llm_call)        → 操作型/短/确认跳过 (保留, 原样)
  ↓
  熔断器检查 (保留)
  ↓
  [LLM Router] → 替代 _classify_intent → {h: bool, kt: bool, s: bool}
  ↓
  全 false? → return None
  ↓
  只执行 mask 激活的路径 → HS/KT/SK
  ↓
  后处理 (过滤/去重/注入 — 全部保留不变) → 最终注入
```

**变化**：
- turn_gate.py → **无改动**，三条门控全部保留。文本门控由"跳过 recall 流水线"变为"跳过 Router"（行为一致）
- hooks.py _classify_intent → **删除，被 Router 替代**。不搬到任何文件，规则逻辑散入 Router prompt 判据
- 三路定义（H/KT/S 各自的域和判据）→ 放共享位置供 Router prompt + turn_gate 共用

---

## 任务清单

### Task 1: 新增 core/source_defs.py — 三路定义共享

| 字段 | 内容 |
|------|------|
| 来源 | 三路 H/KT/S 的定义既被 Router prompt 用，也被 turn_gate 的文本门控用 |
| 当前状态 | 定义散落在 SKILL.md 和 SPEC 文档中，代码里没有统一位置 |
| 目标状态 | 一个共享模块定义三路名称、域、判据说明，Router prompt 从这里拼接，turn_gate 可引用 |
| 代码位置 | 新建 `/mnt/d/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/source_defs.py` |
| 工作量 | < 0.5h |
| 前置依赖 | 无（第一个创建，供 Task 2 引用） |

```python
"""三路注入源定义 — Router prompt 拼接 + turn_gate 引用"""

from dataclasses import dataclass

@dataclass
class SourceDef:
    key: str          # "h" / "kt" / "s"
    name: str         # "Hindsight" / "Knowledge Tree" / "Skill"
    domain: str       # "经验" / "知识" / "能力"
    description: str  # 判据说明（供 LLM Router 用）
    examples: tuple[str, ...]  # 典型 query 示例

SOURCES = {
    "h": SourceDef(
        key="h", name="Hindsight", domain="经验",
        description="回答这个问题是否需要参考过去做过的类似事情、之前遇到的方案和教训、历史经验？",
        examples=("上次 LiteLLM 怎么修的", "之前那个方案结果怎样", "gateway 为啥崩了"),
    ),
    "kt": SourceDef(
        key="kt", name="Knowledge Tree", domain="知识",
        description="回答这个问题是否需要引用客观的概念定义、原理、公式、架构说明、事实关系？需要"这个东西是什么、怎么工作的"这类知识？",
        examples=("RRF 融合公式", "Hindsight 的架构", "什么是原子性知识点"),
    ),
    "s": SourceDef(
        key="s", name="Skill", domain="能力",
        description="回答这个问题是否需要参考操作步骤、配置方法、部署流程、工具用法？需要"这个事怎么做"这类指南？",
        examples=("怎么部署插件", "如何配置 Hindsight", "用什么工具查日志"),
    ),
}

def build_router_prompt() -> str:
    """从 SOURCES 拼接 Router system prompt（need analysis 版）。"""
    lines = ["你是一个注入路由判断器。",
             "判断：为了准确回答用户消息，是否需要从以下知识源补充信息？\n"]
    for s in SOURCES.values():
        lines.append(f"{s.key.upper()} — {s.domain}/{s.name}")
        lines.append(f"  {s.description}\n")
    lines.append("输出 JSON：{\"h\": bool, \"kt\": bool, \"s\": bool}")
    lines.append("")
    lines.append("要求：")
    lines.append("- 思考问题是\"本质需要哪种知识\"")
    lines.append("- 宁可多开不遗漏")
    lines.append("- 只输出 JSON，不要任何包裹格式")
    lines.append("- 相同语义的问题输出一致")
    return "\n".join(lines)
```

### Task 2: 新增 core/router.py — LLM Router 模块

| 字段 | 内容 |
|------|------|
| 来源 | 新增文件，实现 LLM-driven 三路 mask 决策 |
| 当前状态 | 无 Router 模块，三路判断由 _classify_intent（规则）做 |
| 目标状态 | 独立 router 模块，通过 source_defs.build_router_prompt() 生成 prompt + httpx 调用 + 缓存 + JSON 解析兜底 |
| 期望目标 | LLM 根据语义精确分配 H/KT/S mask，不再依赖 keyword 规则 |
| 代码位置 | 新建 `/mnt/d/HermesProject/plugins/knowledge-navigation/src/knowledge_navigation/core/router.py` |
| 改动位置 | 新建文件 |
| 工作量 | ~1h |
| 检查方法 | test_nav_hooks.py 中 mock Router 输出验证 mask 是否正确传递到执行路径 |
| 实现方式 | httpx 调本地 LiteLLM gateway (127.0.0.1:4142)，JSON 输出 {h, kt, s} |
| 前置依赖 | Task 1（引用 source_defs.build_router_prompt()） |

**核心实现**：

```python
"""LLM-driven Router for 3-way injection mask."""

import json, logging, httpx

from knowledge_navigation.core.source_defs import build_router_prompt

logger = logging.getLogger(__name__)

_router_cache: dict[str, dict[str, bool]] = {}  # {(session_id, message): mask}
_ROUTER_CACHE_MAX = 64  # LRU 上限，超限清空最早的一半

_ROUTER_SYSTEM_PROMPT = build_router_prompt()

def _parse_mask(text: str) -> dict[str, bool] | None:
    """从 LLM 响应解析 mask JSON，含 JSON 块提取和字段缺失兜底。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        import re
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if m:
            try: data = json.loads(m.group(1))
            except json.JSONDecodeError: return None
        else: return None
    if not isinstance(data, dict): return None
    return {"h": bool(data.get("h", False)), "kt": bool(data.get("kt", False)), "s": bool(data.get("s", False))}

def route(session_id: str, message: str, model: str, api_url: str, api_key: str, timeout: int) -> dict[str, bool]:
    """LLM Router 决策三路 mask。缓存 key=(session_id, message) 精确匹配，本轮 tool call 复用，新 message 重走。"""
    cache_key = (session_id, message)
    cached = _router_cache.get(cache_key)
    if cached:
        return cached
    
    # 安全过滤：截断长消息（首 300 + 尾 200 保留背景+问题）、替换换行防注入
    safe_msg = message[:300] + message[-200:] if len(message) > 500 else message
    safe_msg = safe_msg.replace("\n", " ").replace("\r", " ")
    try:
        resp = httpx.post(
            f"{api_url.rstrip('/')}/chat/completions",
            json={
                "model": model, "temperature": 0.1, "max_tokens": 64,
                "messages": [
                    {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"消息：{safe_msg}\n\nJSON 输出："},
                ],
            },
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("Router 调用失败 (%s)，fallback 全开", e)
        _router_cache[cache_key] = {"h": True, "kt": True, "s": True}
        return _router_cache[cache_key]
    
    mask = _parse_mask(raw)
    if mask is None:
        logger.warning("Router JSON 解析失败, fallback 全开")
        mask = {"h": True, "kt": True, "s": True}
    
    _router_cache[cache_key] = mask
    # LRU 清理：超限时淘汰最早的条目（普通 dict，迭代顺序=插入顺序）
    if len(_router_cache) > _ROUTER_CACHE_MAX:
        _evict = _ROUTER_CACHE_MAX // 2
        for _k in list(_router_cache.keys())[:_evict]:
            del _router_cache[_k]
    return mask
```

### Task 3: 重构 hooks.py — 插入 Router 替代 _classify_intent

| 字段 | 内容 |
|------|------|
| 来源 | hooks.py pre_llm_call() 当前 ~1120 行 |
| 当前状态 | turn_gate → 熔断器 → _classify_intent → 并行三路 → 后处理 |
| 目标状态 | turn_gate → 熔断器 → **Router** → 按 mask 条件执行 → 后处理全部保留 |
| 改动位置 | hooks.py |
| 工作量 | 1 天（含 _classify_intent 代码清理） |
| 检查方法 | test_nav_hooks.py 全部测试通过 |

**具体改动**（含废弃代码清理）：

```python
# 改动 1：import 新增
from knowledge_navigation.core.router import route

# 改动 2：删除 _INTENT_KEYWORDS (line 596-612) — 整段删除
# 改动 3：删除 _classify_intent 函数 (line 615-658) — 整段删除
# 改动 3.5：删除注释头 "C-P0: 意图分类（参考 memory-level-plan.md）" (line 594)
# 废弃规则不搬到任何文件，规则逻辑散入 Router prompt 作为判据参考

# 改动 4：在 pre_llm_call 中替换 _classify_intent 调用 (line 747-757)  
# 原:
#   intent = _classify_intent(user_message)
#   if intent == "generic":
#       ... 只跑 skill ...
# 替换为:
mask = route(session_id, user_message, CONFIG.router_model, 
             CONFIG.router_api_url, CONFIG.router_api_key, CONFIG.router_timeout)

# 改动 5：按 mask 条件执行 — mask 开 2+ 路时保留并行，单路串行
# 设计原则：mask 全开（fallback 或 Router 判定全需要）时行为与改造前完全一致（并行），
#          mask 只开部分路时串行执行，减少不必要的线程开销。
# 原: 三路并行 ThreadPoolExecutor (固定 max_workers=3)
# 新:
_active_count = sum([mask["h"] and not _hs_circuit_open, mask["kt"] and HAS_KNOWLEDGE_TREE, mask["s"]])

if _active_count >= 2:
    # 多路并行（与原行为一致，避免延迟退化）
    hs_future = kt_future = sk_future = None
    executor = ThreadPoolExecutor(max_workers=_active_count)
    hs_future = executor.submit(_do_hindsight_recall, user_message) if mask["h"] and not _hs_circuit_open else None
    kt_future = executor.submit(_do_kt_recall, session_id, user_message) if mask["kt"] and HAS_KNOWLEDGE_TREE else None
    sk_future = executor.submit(_do_skill_match, user_message) if mask["s"] else None
    # ... 结果收集（与原 line 775-841 逻辑完全一致）...
    # finally: executor.shutdown(wait=False, cancel_futures=True)
else:
    # 单路串行（节省线程开销，延迟无差别）
    hs_result = _do_hindsight_recall(user_message) if mask["h"] and not _hs_circuit_open else None
    kt_raw_results = (_do_kt_recall(session_id, user_message) if mask["kt"] and HAS_KNOWLEDGE_TREE else [])
    skill_context = _do_skill_match(user_message) if mask["s"] else ""

# 改动 6：后处理—跨域去重需要在 HS+KT 都开时执行
if mask["h"] and mask["kt"] and hs_result and kt_raw_results and kept:
    kt_raw_results, kt_dedup_removed = cross_domain_dedup(...)
```

**熔断器逻辑**：保留不变。熔断器只控制 Hindsight 路是否执行，不影响 KT 和 skill。

```python
# 原熔断器检查 (line 742-745) 保留
_hs_circuit_open = False
if circuit_is_open():
    logger.info("熔断器跳过 Hindsight recall，知识树和 skill 不受影响")
    _hs_circuit_open = True
```

### Task 4: 更新 config.py — 添加 Router 配置

| 字段 | 内容 |
|------|------|
| 来源 | 新增 Router 需要配置信息 |
| 目标状态 | 新增 4 项配置，支持 ENV 覆盖 |
| 改动位置 | config.py KnowledgeNavigationConfig + from_env() |
| 工作量 | < 0.5h |
| 前置依赖 | Task 2（Router 使用这些配置） |

```python
# 新增 dataclass 字段
router_model: str = field(default="sensenova-6.7-flash-lite")
router_api_url: str = field(default="http://127.0.0.1:4142/v1")
router_api_key: str = field(default="")
router_timeout: int = field(default=5)

# from_env() 新增
if env := os.getenv("KN_ROUTER_MODEL"):       values["router_model"] = env
if env := os.getenv("KN_ROUTER_API_URL"):     values["router_api_url"] = env
if env := os.getenv("KN_ROUTER_API_KEY"):     values["router_api_key"] = env
if env := os.getenv("KN_ROUTER_TIMEOUT"):     values["router_timeout"] = int(env)
```

### Task 5: 更新 test_nav_hooks.py

| 字段 | 内容 |
|------|------|
| 来源 | 当前测试覆盖 _classify_intent + turn_gate + pre_llm_call |
| 目标状态 | 保留门控测试；替换 _classify_intent 测试为 Router mask 行为测试；新增 Router 异常/全 false/单路 H/KT/S 测试 |
| 改动位置 | test_nav_hooks.py |
| 工作量 | 半天 |
| 前置依赖 | Task 3 |

**测试变更**：
1. **保留**：Test 5 (内部 prompt)、Test 8 (平台门控)、Test 6 (空结果)、Test 7 (XML escape)
2. **替换**：Test 1 (28 条 _classify_intent 规则测试) → Router mask 测试（mock route() 返回值）
3. **新增**：
   - Router {h:1, kt:0, s:0} → HS 跑，KT 不跑，skill 不跑  
   - Router {h:0, kt:1, s:0} → KT 跑，HS 不跑
   - Router {h:0, kt:0, s:1} → 只跑 skill（与旧的 generic 行为等价）
   - Router {h:0, kt:0, s:0} → return None
   - Router 异常 → fallback 全开，三路都跑
   - turn_gate 跳过后 → mock route 不应被调用
4. **删除**：Test 1 中 28 条 _classify_intent 规则测试
5. **清理**：删除 test_nav_hooks.py 中 `from ... import _classify_intent`（如果存在）

---

## 实施路线图

| 顺序 | 任务 | 预计 | 文件冲突 |
|------|------|------|----------|
| Day 1 | Task 1: core/source_defs.py (新建) | ~0.5h | 无 |
| Day 1 | Task 2: core/router.py (新建，引用 Task 1) | ~1h | 无 |
| Day 1 | Task 4: config.py 新增配置 | ~0.5h | config.py（仅加字段） |
| Day 2 | Task 3: hooks.py 重构（含 _classify_intent 清理） | ~3h | hooks.py（一次改完） |
| Day 2 | Task 5: 测试更新 | ~2h | test_nav_hooks.py |
| Day 2 | 部署 + 验证 | ~1h | - |

**部署验证命令**：

```bash
# 1. 预览部署清单（不动文件）
./deploy/deploy.sh plan knowledge-navigation

# 2. 执行部署
./deploy/deploy.sh deploy knowledge-navigation --yes

# 3. 验证 import 无报错
python3 -c "from knowledge_navigation.core.hooks import pre_llm_call; print('deploy OK')"

# 4. 运行测试
python3 /mnt/d/HermesProject/test_nav_hooks.py

# 5. 重启 gateway（使新插件代码生效）
systemctl restart hermes-gateway.service

# 6. 检查 trace.log 确认 Router mask 在日志中
tail -5 /root/.hermes/plugins/knowledge-navigation/trace.log | python3 -m json.tool
```

**关键点**：
- turn_gate.py **无改动**（保持原样）
- _classify_intent **不搬到任何文件**，直接删除
- 三路定义（H/KT/S）放 source_defs.py 共享，Router 和 turn_gate 都引用
- hooks.py 的改动只有：import + 替换调用 + 删除 _classify_intent

---

## 三路注入意义总结（供 Router prompt 参考）

| 源 | 域 | 核心回答 | 典型 query |
|----|-----|---------|-----------|
| **H** hindsight | 经验域 — 发生过的事、历史行为 | "之前怎么做的/为什么这样/结果如何" | "上次 LiteLLM 崩溃怎么修的" |
| **KT** knowledge_tree | 知识域 — 客观结构化的原理与事实 | "这是什么/怎么工作的/定义是什么" | "Hindsight 的 RRF 融合公式是什么" |
| **S** skill | 能力域 — 可复用的操作流程与工具用法 | "怎么做/配置什么/用什么工具/步骤" | "怎么部署 knowledge-navigation 插件" |

**关键原则**：三个域的边界不依赖模型强弱，是三域分别由三个独立系统管理。Router 只决定"是否电通这条路"，不决定"这条路怎么跑"。

---

## 风险与应对

| 风险 | 应对 |
|------|------|
| Router LLM 调用失败导致注入中断 | fallback 全开 h=kt=s=True，保证不丢 recall（但损失 routing 收益） |
| Router 额外延迟 (1-3s/次) | ① 短超时 5s；② 缓存 key=(session_id, message) 精确匹配，同轮 tool call 复用，新 message 重走 |
| 缓存内存持续增长 | LRU 上限 64 条，超限时淘汰最早的一半；cache key 按 (session_id, message) 精确匹配，无过期问题 |
| Router 错误关闭某路导致信息缺失 | trace.log 记录 mask 值，可通过离线 baseline 对比召回率变化 |
| skillopt-runner/curator/cron 到 pre_llm_call | 来源门控已拦截，不经过 Router |