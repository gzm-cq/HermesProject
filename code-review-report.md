# 知识导航插件 — 代码质量审查报告

> **审查时间**: 2026-07-03  
> **审查范围**: `plugins/knowledge-navigation/`  
> **审查标准**: `.workbuddy/memory/DEVELOPMENT_STANDARDS.md` 10 条核心规则  
> **审查人**: Senior Developer (高级开发工程师)

---

## 审查结论

| 级别 | 数量 | 说明 |
|------|------|------|
| 🔴 P0（必须修复） | 2 | RULE-2 模块隔离违反、配置硬编码 |
| 🟡 P1（建议修复） | 5 | 配置管理、错误处理、日志规范 |
| 🟢 P2（优化建议） | 3 | 代码质量、测试覆盖、文档完整性 |

**总体评价**: 代码质量良好，核心逻辑完整，但存在模块隔离违反和配置硬编码问题，需要修复以符合开发规范。

---

## 🔴 P0 — 必须修复

### P0-1: RULE-2 违反 — `core/` 直接依赖外部 I/O

**规则要求**: `core/` 禁止直接依赖外部 I/O（网络、数据库、文件系统）

**问题位置**:

| 文件 | 行号 | 问题 |
|------|------|------|
| `core/circuit_breaker.py` | 121-141 | 直接 `import requests` 调用飞书 API（`_get_feishu_token()`） |
| `core/circuit_breaker.py` | 205-225 | 直接 `import requests` 发送飞书消息（`_notify_feishu_circuit_open()`） |
| `core/hooks.py` | 257-279 | 直接 `import requests` 调用 SiliconFlow API（`_batch_embed()`） |
| `core/hooks.py` | 306-336 | 直接 `import psycopg2` 连接 PG 数据库（`_causal_boost()`） |

**修复建议**:

将外部 I/O 调用迁移到 `adapters/` 层：

```
adapters/
├── feishu.py          # 飞书 API 调用（token 获取 + 消息发送）
├── siliconflow.py      # SiliconFlow API 调用（embedding）
└── postgres.py        # PG 数据库连接池（替代 _get_cached_conn）
```

`core/circuit_breaker.py` → 调用 `adapters/feishu.py`  
`core/hooks.py` → 调用 `adapters/siliconflow.py` 和 `adapters/postgres.py`

---

### P0-2: 配置硬编码 — 飞书 API URL 写在代码中

**问题位置**: `core/circuit_breaker.py` 第 41-42 行

```python
_FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_FEISHU_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
```

**规则要求**: 配置必须通过 `CONFIG` 或环境变量管理，不得硬编码

**修复建议**:

在 `config.py` 的 `KnowledgeNavigationConfig` 中添加：

```python
feishu_token_url: str = field(default="https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal")
feishu_message_url: str = field(default="https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id")
```

然后修改 `circuit_breaker.py` 使用 `CONFIG.feishu_token_url` 和 `CONFIG.feishu_message_url`。

---

## 🟡 P1 — 建议修复

### P1-1: RULE-4/RULE-6 — API 响应格式验证不完整

**问题位置**: `core/circuit_breaker.py` 第 129-132 行

```python
data = resp.json()
if resp.status_code != 200 or "tenant_access_token" not in data:
    logger.warning("获取飞书 token 失败：HTTP %s", resp.status_code)
    return ""
```

**问题**: 只检查了 `tenant_access_token` 字段存在性，未验证字段类型（应为 `str`），若 API 返回 `{"tenant_access_token": 123}`（数字），后续使用会报 `TypeError`。

**修复建议**:

```python
data = resp.json()
if resp.status_code != 200:
    logger.warning("获取飞书 token 失败：HTTP %s", resp.status_code)
    return ""
token = data.get("tenant_access_token")
if not isinstance(token, str) or not token:
    logger.warning("获取飞书 token 失败：响应格式异常 %s", data)
    return ""
```

---

### P1-2: 配置管理 — `circuit_breaker.py` 的飞书通知限频硬编码

**问题位置**: `core/circuit_breaker.py` 第 38 行

```python
_NOTIFICATION_MIN_INTERVAL: float = 300.0  # 同 session 至少间隔 5 分钟
```

**问题**: 限频值是硬编码的，无法通过环境变量调整。

**修复建议**: 添加到 `CONFIG`:

```python
feishu_notification_min_interval: float = field(default=300.0)
```

---

### P1-3: 错误处理 — `_batch_embed()` 异常只 debug 日志

**问题位置**: `core/hooks.py` 第 277-279 行

```python
except Exception as e:
    logger.debug("_batch_embed 异常: %s", e)
    return None
```

**问题**: 异常只记录 debug 级别，生产环境默认 INFO 级别会丢失错误信息，导致 embedding 降级时无法排查。

**修复建议**:

```python
except Exception as e:
    logger.warning("_batch_embed 异常: %s", e)  # 改为 warning
    return None
```

---

### P1-4: 日志规范 — 中文字符串混入代码

**问题位置**: `core/circuit_breaker.py` 第 185-191 行

```python
"content": (
    f"**\u7194\u65ad\u8def\u5f00\u542f**\n"
    f"\u8fde\u7eed {CONFIG.circuit_breaker_threshold} \u6b21 recall \u5931\u8d25\uff0c"
    ...
)
```

**问题**: 飞书卡片内容硬编码中文，若需支持多语言或调整文案，需改代码。

**修复建议**: 抽取为配置或常量（低优先级，当前可接受）。

---

### P1-5: 测试覆盖 — 部分核心逻辑未测试

**问题**: `test_hooks.py` 测试了 Hook 注册和 recall 流水线，但以下核心逻辑未覆盖：

- `_causal_boost()` — 因果链 boost 逻辑
- `_CompactionTracker` — Compaction 阈值逻辑
- `_HitCounter` — 高频记忆 boost 逻辑
- `_TaskTracker` — 任务回述逻辑
- 熔断器飞书通知 — `_notify_feishu_circuit_open()`

**修复建议**: 补充单元测试，使用 `unittest.mock` 模拟外部依赖。

---

## 🟢 P2 — 优化建议

### P2-1: 代码质量 — `hooks.py` 过长（946 行）

**问题**: `hooks.py` 单文件 946 行，包含多个职责（Hook 入口、PG 连接缓存、Compaction、HitCounter、TaskTracker、eval 匹配）。

**建议**: 拆分为多个模块：

```
core/
├── hooks.py              # Hook 入口（pre_llm_call）
├── pg_cache.py          # PG 连接缓存
├── compaction.py         # CompactionTracker
├── hit_counter.py       # HitCounter
├── task_tracker.py     # TaskTracker
└── eval_matcher.py     # eval query 匹配
```

---

### P2-2: 配置管理 — `CONFIG` 字段过多（50+ 个）

**问题**: `KnowledgeNavigationConfig` dataclass 有 50+ 个字段，管理困难。

**建议**: 按功能分组为子配置类：

```python
@dataclass
class CircuitBreakerConfig:
    threshold: int = 3
    cooldown: int = 120

@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    token_url: str = "..."
    message_url: str = "..."
    notification_min_interval: float = 300.0

@dataclass
class KnowledgeNavigationConfig:
    hindsight_api_url: str = "..."
    max_results: int = 3
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
```

---

### P2-3: 文档完整性 — `README.md` 缺少故障排查指南

**问题**: `README.md` 有配置表格和使用示例，但缺少常见故障排查（如：熔断器误触发、embedding API 失败降级、知识树 recall 返回空）。

**建议**: 添加 "故障排查" 章节，列出常见错误和解决方法。

---

## 规则检查清单

| 规则 | 状态 | 说明 |
|------|------|------|
| RULE-1: 完整代码 | ✅ | 无 TODO/占位符 |
| RULE-2: 模块隔离 | ❌ **P0 违反** | `core/` 直接依赖外部 I/O |
| RULE-4: 验证 API | ⚠️ **P1 部分不符合** | API 响应格式验证不完整 |
| RULE-6: 验证 API | ⚠️ **P1 部分不符合** | 同 RULE-4 |
| RULE-8: 编译通过 | ✅ | 语法正确，测试通过 |
| 测试覆盖率 | ⚠️ **P1 部分不符合** | 核心逻辑未完全覆盖 |
| 错误处理 | ⚠️ **P1 部分不符合** | 部分异常日志级别过低 |
| 日志规范 | ⚠️ **P1 部分不符合** | 中文硬编码 |
| 配置管理 | ❌ **P0 违反** | 硬编码 URL 和限频值 |
| 文档完整性 | ⚠️ **P2 部分不符合** | 缺少故障排查指南 |

---

## 修复优先级建议

| 优先级 | 任务 | 预估工作量 |
|---------|------|--------------|
| **P0** | 修复 RULE-2 违反：迁移外部 I/O 到 `adapters/` | 4-6 小时 |
| **P0** | 修复配置硬编码：飞书 URL 迁入 `CONFIG` | 1 小时 |
| **P1** | 补充 API 响应格式验证 | 2 小时 |
| **P1** | 提升日志级别（`debug` → `warning`） | 0.5 小时 |
| **P1** | 补充单元测试覆盖 | 4-6 小时 |
| **P2** | 拆分 `hooks.py` 为多个模块 | 3-4 小时 |
| **P2** | 重构 `CONFIG` 为子配置类 | 2-3 小时 |

---

## 下一步

需要我：

**A)** 立即修复 P0 问题（RULE-2 违反 + 配置硬编码）  
**B)** 先修复 P1 问题（API 验证 + 日志级别 + 测试覆盖）  
**C)** 生成修复计划文档，等你审批后再动手  
**D)** 继续审查其他项目（knowledge-tree-plugin、clustering-analysis-v3）

请告诉我你的选择。
