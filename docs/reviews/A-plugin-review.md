# Agent A 插件链路审查报告（knowledge-navigation + knowledge-tree-plugin）

> **文档状态：历史审查报告 / 修复前问题发现**  
> 本文是 Agent A 的只读审查结果，问题是否已关闭请以 `03-post-fix-audit-2026-06-15.md` 和当前源码为准。


审查日期：2026-06-15  
源码根目录：`/mnt/d/HermesProject`  
审查方式：只读源码/测试/配置审查 + 单元测试/导入 smoke；未修改业务代码、未部署、未执行生产写入。

## 1. 范围

### 已审查模块

- `plugins/knowledge-navigation`
  - `src/knowledge_navigation/core/hooks.py`
  - `src/knowledge_navigation/core/filtering.py`
  - `src/knowledge_navigation/adapters/hindsight.py`
  - `src/knowledge_navigation/turn_gate.py`
  - `src/knowledge_navigation/config.py`
  - `src/knowledge_navigation/__init__.py`、插件根 `__init__.py`
  - `pyproject.toml`、`plugin.yaml`、`README.md`
  - `tests/test_hooks.py`、`tests/test_filtering.py`、`tests/test_config.py`、`tests/conftest.py`

- `plugins/knowledge-tree-plugin`
  - `src/knowledge_tree_plugin/hooks.py`
  - `src/knowledge_tree_plugin/public_api.py`
  - `src/knowledge_tree_plugin/recall.py`
  - `src/knowledge_tree_plugin/placement.py`
  - `src/knowledge_tree_plugin/adapters/database.py`
  - `src/knowledge_tree_plugin/config.py`
  - `src/knowledge_tree_plugin/__init__.py`、插件根 `__init__.py`
  - `config/default.yaml`
  - `pyproject.toml`、`plugin.yaml`
  - `tests/test_public_api.py`、`tests/test_hooks.py`、`tests/test_placement.py`、`tests/test_recall.py`、`tests/conftest.py`

### 核实的 review plan 疑点

| 疑点 | 结论 |
|---|---|
| `public_api` 测试与源码是否不一致 | **确认不一致**，测试失败。 |
| 新增知识点是否写 `k_vector` | **确认未写入新知识点自身 `k_vector`**，只更新父节点。 |
| 插件间隐式依赖 | **确认存在**：tree plugin 直接 import navigation `turn_gate`，navigation 又可选 import tree public_api；配置/依赖未显式声明。 |
| embedding key 不一致 | **确认存在**：navigation cross-domain embedding 用 `SILICONFLOW_API_KEY`，tree plugin 用 `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY`。 |
| 版本不一致 | **确认存在**：navigation README 写 1.2.1，但 `pyproject.toml/plugin.yaml` 为 1.1.0。另有依赖清单不一致。 |
| DB adapter 连接策略 | **确认高频 `public_api` 每次新建 DB adapter/连接**；测试却期待缓存。 |
| 熔断策略 | **确认 Hindsight 与知识树 recall 熔断/降级耦合不合理**：Hindsight 失败/空结果会导致知识树结果被丢弃；知识树失败不参与独立熔断。 |

---

## 2. 关键证据

### 2.1 `public_api` 测试与源码不一致

- `plugins/knowledge-tree-plugin/tests/test_public_api.py:9-19`
  - 测试调用 `public_api._adapter_cache.clear()` 和 `public_api._get_cached_adapter(...)`。
- `plugins/knowledge-tree-plugin/tests/test_public_api.py:22-31`
  - 测试期望 unhealthy adapter 被关闭并重建。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py:17-23`
  - 源码只有 `_api_config`，并明确注释：“每次调用创建新适配器，由调用方负责关闭”。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py:55-64`
  - `_recall_core()` 在未传入 adapter 时直接 `PluginDatabaseAdapter(cfg.db_url)`，未缓存。
- 实测：`knowledge-tree-plugin` 单测失败 2 项，错误为 `AttributeError: module 'knowledge_tree_plugin.public_api' has no attribute '_adapter_cache'`。

### 2.2 新增知识点未持久化自身 `k_vector`

- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py:83-90`
  - 对 `point_texts` 调用 `batch_embed()` 生成新知识点 embedding。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py:151-168`
  - embedding 仅放入内存中的 `pending_cache_nodes`，并用于父节点 EMA：`inserted_parent_embeddings.append(point_embedding)`。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py:171-181`
  - 批量写库调用 `adapter.batch_insert_knowledge_points(pending_records, parent_id=parent_id)`；`pending_records` 仅为 `(name, text)`，不包含 embedding。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py:187-194`
  - 只调用 `adapter.update_k_vector(node_id=context.parent_node["id"], ...)` 更新父节点 `k_vector`。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/adapters/database.py:128-168`
  - `batch_insert_knowledge_points()` 插入 `knowledge_tree (name, node_type, parent_id, display_order, source_ids)` 和 `knowledge_point_texts`，未写 `knowledge_tree.k_vector`。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/recall.py:205-210`
  - `attention_filter()` 只保留 `c.get("k_vector") is not None` 的子节点；新插入但 `k_vector=NULL` 的知识点后续无法被召回。
- 对照：离线 builder 的 `_write_to_db()` 在 `scripts/knowledge-tree-builder/src/knowledge_tree_builder/place.py:295-303` 会对每个新节点调用 `adapter.update_k_vector(..., placement_count=1)`，说明在线插件行为与离线建树不一致。

### 2.3 插件间隐式依赖

- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/hooks.py:21`
  - 直接 `from knowledge_navigation.turn_gate import skip_non_user, skip_post_llm_call, skip_system_prompt`。
- `plugins/knowledge-tree-plugin/pyproject.toml:15-22`
  - dependencies 未声明 `knowledge-navigation` 或等价包。
- `plugins/knowledge-tree-plugin/plugin.yaml:11-16`
  - dependencies 同样未声明 `knowledge-navigation`。
- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:70-80`
  - navigation 尝试 import `knowledge_tree_plugin.public_api`，失败后降级为空。
- `plugins/knowledge-navigation/src/knowledge_navigation/__init__.py:7-23`
  - navigation 通过硬编码/环境变量向 `sys.path` 注入 tree plugin src。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/__init__.py:14-23`
  - tree plugin 通过 `KT_BUILDER_SRC` 或相对路径注入 `knowledge-tree-builder` src；也未通过 package dependency 表达。

### 2.4 embedding key 不一致

- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:247-264`
  - `_batch_embed()` 供跨域 embedding 去重使用；读取 `SILICONFLOW_API_KEY`，固定请求 `https://api.siliconflow.cn/v1/embeddings` 和 `BAAI/bge-m3`。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/config.py:53-61`
  - tree plugin 配置中 `embed_api_key` 读取 `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY`。
- `plugins/knowledge-tree-plugin/config/default.yaml:25-29`
  - 配置注释也写 `embed_api_key` 默认来自 `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY`。
- 影响：开启 `KN_CROSS_DOMAIN_DEDUP_MODE=text_embedding` 时，navigation 可能因未设置 `SILICONFLOW_API_KEY` 静默回退到文本去重；tree recall/placement 则使用另一套 key。

### 2.5 版本/元数据不一致

- `plugins/knowledge-navigation/pyproject.toml:5-8`：`version = "1.1.0"`。
- `plugins/knowledge-navigation/plugin.yaml:1-3`：`version: 1.1.0`。
- `plugins/knowledge-navigation/README.md:116`：写 `*版本：1.2.1 | 最后更新：2026-06-14*`。
- `plugins/knowledge-navigation/pyproject.toml:26-30` 包含 `psycopg2-binary>=2.9.0`，但 `plugins/knowledge-navigation/plugin.yaml:12-15` 未列出 `psycopg2-binary`。
- `plugins/knowledge-tree-plugin/pyproject.toml:20` 包含 `pgvector`，但 `plugins/knowledge-tree-plugin/plugin.yaml:11-16` 未列出 `pgvector`。
- `plugins/knowledge-tree-plugin/pyproject.toml:9` 声明 `readme = "README.md"`，但审查时该插件目录下未找到 `README.md`。

### 2.6 DB adapter 连接策略

- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py:20-23`
  - 注释明确公共 API 高频调用，但“不跨线程共享，每次调用创建新适配器”。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py:55-64`
  - 未传 adapter 时创建 `PluginDatabaseAdapter(cfg.db_url)`。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py:172-177`、`:197-204`
  - `recall_from_tree()` / `recall_from_tree_raw()` finally/尾部关闭本次创建的 adapter。
- `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/adapters/database.py:31-32`
  - `PluginDatabaseAdapter.__init__()` 直接包装 `knowledge_tree_builder.DatabaseAdapter(db_url)`，即新建底层连接。
- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:741-783`
  - 每次 pre hook 并行提交 Hindsight + KT recall；KT public API 因此在用户对话高频路径上持续建连/断连。

### 2.7 熔断/降级策略问题

- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:727-731`
  - 全局熔断打开时直接 `return None`，知识树 recall 也被跳过。
- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:745-768`
  - Hindsight 超时/异常时取消 KT future 并返回 `None`。
- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:769-781`
  - KT recall 超时/异常只记录 warning 并置空，不参与独立熔断/冷却。
- `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py:785-813`
  - `if not result` 或 Hindsight `raw_results` 为空时直接返回 `None`；即使 `kt_raw_results` 已经拿到，也不会注入知识树结果。
- `plugins/knowledge-navigation/tests/test_hooks.py:35-39`
  - autouse fixture 默认 `HAS_KNOWLEDGE_TREE=False`，现有 navigation 测试基本绕开了知识树启用场景。

---

## 3. P0/P1/P2 问题表

### P0

| ID | 模块 | 问题 | 影响 | 证据 | 建议修复 | 是否阻塞部署 |
|---|---|---|---|---|---|---|
| A-P0-1 | knowledge-tree-plugin | 在线新增知识点未写自身 `k_vector` | post learning “入库但不可召回”；后续 dedup/conflict 也会因 DB 读出的 `k_vector=NULL` 失效 | `placement.py:171-194` 只更新父节点；`database.py:128-168` batch insert 未写 k_vector；`recall.py:205-210` 过滤无 k_vector 节点 | 修改 `batch_insert_knowledge_points()` 支持 `(name,text,k_vector)` 或插入后批量 `update_k_vector(node_id, point_embedding, placement_count=1)`；补测试断言新节点写 vector | **是** |

### P1

| ID | 模块 | 问题 | 影响 | 证据 | 建议修复 | 是否阻塞部署 |
|---|---|---|---|---|---|---|
| A-P1-1 | knowledge-tree-plugin | `test_public_api.py` 与 `public_api.py` 不一致 | 测试红；无法作为发布 gate | `tests/test_public_api.py:9-31` 期待 `_adapter_cache/_get_cached_adapter`；`public_api.py:17-23` 明确每次新建 adapter；实测 2 failed | 二选一：恢复安全的 adapter 缓存/池并实现测试期望；或删除/改写过时测试并接受每次建连策略（需另设性能 gate） | 是 |
| A-P1-2 | knowledge-navigation + tree | Hindsight 失败/空结果会丢弃知识树结果；熔断打开也跳过 KT | 单侧可用降级失败：Hindsight 短故障时知识树也不可用 | `hooks.py:745-768` Hindsight fail 取消 KT；`:785-813` Hindsight empty 直接 return；`:727-731` 熔断直接 return | 将 Hindsight 与 KT 结果解耦：KT future 独立等待；Hindsight 失败时仍可注入 KT；熔断只跳过 Hindsight 或提供 KT-only path | 是（影响可用性） |
| A-P1-3 | knowledge-tree-plugin | 高频 public API 每次新建 DB adapter/连接 | 高并发/长对话下增加 PG 连接压力和 latency；且与测试期望冲突 | `public_api.py:20-23` 注释；`:55-64` 创建 adapter；`:172-177`/`:197-204` 关闭 | 引入线程本地连接、短 TTL 缓存、连接池或由 navigation 持有 adapter；需健康检查和关闭策略 | 否，但应修 |
| A-P1-4 | 两插件 | 插件间隐式依赖未声明 | 单独安装/加载顺序变化可能 import 失败；测试依赖 PYTHONPATH 才通过 | tree `hooks.py:21` import navigation；tree `pyproject.toml:15-22`/`plugin.yaml:11-16` 未声明；navigation `hooks.py:70-80` optional import tree | 抽出共享 `turn_gate` 小包/复制稳定门控/显式声明依赖与加载顺序；部署脚本校验 `KT_PLUGIN_SRC/KT_BUILDER_SRC` | 否，但影响部署稳定性 |
| A-P1-5 | knowledge-navigation | KT recall 异常只跳过，无独立熔断/指标 | KT 持续超时会每轮重复触发，缺少冷却、计数、告警；Hindsight 熔断又会误伤 KT | `hooks.py:769-781` KT timeout/exception 仅 warning；`_do_kt_recall()` `:685-696` 异常返回 [] | 为 KT recall 增加独立 circuit breaker、失败计数、冷却、日志字段；不要复用 Hindsight 熔断 | 否 |

### P2

| ID | 模块 | 问题 | 影响 | 证据 | 建议修复 | 是否阻塞部署 |
|---|---|---|---|---|---|---|
| A-P2-1 | 两插件 | embedding API key 命名不一致 | 配置困惑；开启 embedding 跨域去重时可能静默退化 | navigation `_batch_embed()` `hooks.py:247-264` 用 `SILICONFLOW_API_KEY`；tree `config.py:53-61` 用 `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY` | 统一环境变量，或支持兼容读取顺序：`KT_EMBED_API_KEY`/`HINDSIGHT...`/`SILICONFLOW...`，并在日志中显式说明 | 否 |
| A-P2-2 | knowledge-navigation | README 版本与元数据不一致 | 维护/发布判断混乱 | `pyproject.toml:7`、`plugin.yaml:2` 为 1.1.0；`README.md:116` 为 1.2.1 | 单一来源生成版本；发布 gate 检查 README/plugin/pyproject 一致 | 否 |
| A-P2-3 | 两插件 | `plugin.yaml` 依赖清单落后于 `pyproject.toml` | 插件安装器若读取 `plugin.yaml` 可能漏装运行依赖 | navigation `pyproject.toml:29` 有 psycopg2，`plugin.yaml:12-15` 无；tree `pyproject.toml:20` 有 pgvector，`plugin.yaml:11-16` 无 | 同步依赖；CI 检查 pyproject/plugin.yaml dependency 差异 | 否 |
| A-P2-4 | knowledge-tree-plugin | `pyproject.toml` 声明 README 但目录无 README | 打包/构建元数据可能失败或警告 | `pyproject.toml:9` `readme = "README.md"`；文件搜索未找到 README.md | 新增 README 或移除/改正 readme 字段 | 否 |
| A-P2-5 | tests | navigation 测试默认禁用 KT，缺少集成回归 | KT 启用路径、Hindsight empty + KT-only、KT timeout 场景未覆盖 | `plugins/knowledge-navigation/tests/test_hooks.py:35-39` autouse patch `HAS_KNOWLEDGE_TREE=False` | 增加 KT enabled 的单元/集成测试矩阵 | 否 |

---

## 4. Hook 触发矩阵

基于 `knowledge_navigation.turn_gate` 与两个 hook 的调用逻辑：

| 入口/来源 | `platform` 预期 | pre_llm_call（navigation） | post_llm_call（tree） | 证据 |
|---|---:|---|---|---|
| 用户 CLI | `cli` | 放行；再经过系统 prompt/文本门控 | 放行；再经过系统 prompt/响应门控/cheap gate | `turn_gate.py:23-39` allowlist；nav `hooks.py:699-725`；tree `hooks.py:272-317` |
| 飞书/微信用户 | `feishu`/`weixin` | 放行 | 放行 | `turn_gate.py:23-39` |
| cron / curator / subagent / 内部管线 | 非 allowlist | 跳过 | 跳过 | `turn_gate.py:23-39`；nav `hooks.py:701-707`；tree `hooks.py:287-292` |
| 第一轮长英文系统 prompt | `cli` 等 allowlist + `is_first_turn=True` | 跳过 | 跳过 | `turn_gate.py:58-79` |
| 操作型用户指令（执行/审查/部署等前缀） | allowlist | pre 跳过 recall | post 仍取决于 assistant_response；响应门控/cheap gate 通常跳过 | `turn_gate.py:85-158`；tree `hooks.py:301-317` |
| 工具输出/日志/代码块为主响应 | allowlist | N/A | 跳过提取 | `hooks.py:41-56`、`:100-126`、`:310-317` |

---

## 5. pre/post 性能预算观察

| 链路 | 当前策略 | 风险点 | 建议预算/修复方向 |
|---|---|---|---|
| navigation pre: Hindsight recall | `ThreadPoolExecutor(max_workers=2)`；Hindsight future 使用 `CONFIG.timeout_seconds` | Hindsight 是关键路径；失败会取消 KT 并返回 | Hindsight 超时不应阻断 KT-only；Hindsight timeout 维持 `KN_TIMEOUT_SECONDS`，但 KT 独立短超时 |
| navigation pre: KT recall | 与 Hindsight 并行；剩余时间等待 KT | public API 每次建连；无独立熔断 | KT 子预算建议 1-3s；连接池/TTL；独立 breaker |
| tree post: 入队 | post hook 只 `put_nowait`，主线程轻量 | 队列满只跳过；可观测性有限 | 保持非阻塞；增加 queue full 指标 |
| tree post: LLM 提取 | 后台 `_extract_executor`；hard timeout = timeout × retries + backoff + 5 | future.cancel 不一定终止底层 requests；但不阻塞主对话 | 保持后台；为连续提取失败加冷却/降级 |
| tree post: placement | 单 worker 调用共享 `_adapter`；批量插入 | 新知识点不写 vector 是主要正确性风险 | 修正 vector 写入后再做性能优化 |

---

## 6. 建议修复顺序

1. **先修 A-P0-1：在线新增知识点写自身 `k_vector`**
   - 修改 `PluginDatabaseAdapter.batch_insert_knowledge_points()` 接口，支持接收 embedding。
   - 或在 `place_new_knowledge_points()` 得到 `node_ids` 后对每个新节点批量/逐条 `update_k_vector(node_id, point_embedding, placement_count=1)`。
   - 增加测试：`test_new_nodes_inserted` 必须断言新节点 `update_k_vector` 或 batch SQL 包含 `k_vector`，不能只断言父节点更新。

2. **修复 public_api 测试/连接策略冲突**
   - 若保留“每次建连”：删除 `_adapter_cache` 相关过时测试，新增“adapter always closed”测试和连接压力评估。
   - 更建议实现短 TTL/线程本地缓存或轻量连接池：满足高频 pre recall，保留健康检查（`SELECT 1`）和 close。

3. **解耦 Hindsight 与知识树召回降级**
   - pre hook 中 Hindsight fail/empty 时仍读取已完成 KT future。
   - `raw_results` 为空但 `kt_raw_results` 非空时仍格式化 `<memory-context>`。
   - Hindsight circuit open 时可只跳过 Hindsight，不应无条件跳过 KT。

4. **补独立 KT 熔断/指标**
   - 新增 `_kt_circuit_*` 状态，记录 KT timeout/exception，冷却期只跳过 KT。
   - 日志中区分 `hindsight_*` 与 `knowledge_tree_*`，避免“recall_success”掩盖 KT 长期失败。

5. **显式化插件依赖**
   - 把 `turn_gate` 抽到共享包，或 tree plugin 内置一份稳定门控，避免运行时依赖 navigation。
   - pyproject/plugin.yaml/deploy manifest 明确 `knowledge-tree-builder` 与 `knowledge-navigation` 的加载/路径要求。

6. **统一配置与文档**
   - embedding key 增加兼容读取顺序并更新 README/default.yaml。
   - 同步 README/plugin.yaml/pyproject 版本与依赖。
   - tree plugin 增加 README 或修正 `pyproject.toml`。

---

## 7. 验证命令与本次实测结果

### 已执行命令

```bash
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin
PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src pytest -q
```

实测结果：

```text
35 passed, 2 failed
FAILED tests/test_public_api.py::test_get_cached_adapter_reuses_healthy_adapter
FAILED tests/test_public_api.py::test_get_cached_adapter_recreates_unhealthy_adapter
AttributeError: module 'knowledge_tree_plugin.public_api' has no attribute '_adapter_cache'
```

```bash
cd /mnt/d/HermesProject/plugins/knowledge-navigation
PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src \
KN_TRACE_LOG_PATH=/tmp/knowledge-navigation-pytest-trace.log \
pytest -q
```

实测结果：

```text
86 passed in 6.53s
```

```bash
cd /mnt/d/HermesProject
PYTHONPATH=/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src \
python3 - <<'PY'
import knowledge_navigation, knowledge_tree_plugin
from knowledge_navigation.core.hooks import pre_llm_call
from knowledge_tree_plugin.hooks import post_llm_call
from knowledge_tree_plugin.public_api import recall_from_tree_raw
print('imports ok')
PY
```

实测结果：

```text
imports ok
```

备注：review plan 中使用 `python` 的 smoke 命令在当前 WSL 返回 `python: command not found`，改用 `python3` 后通过。

### 修复后建议 gate

```bash
# tree plugin：应从当前 2 failed 修到全绿
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin
PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src pytest -q

# navigation：保持全绿，并新增 KT enabled 场景
cd /mnt/d/HermesProject/plugins/knowledge-navigation
PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src \
KN_TRACE_LOG_PATH=/tmp/knowledge-navigation-pytest-trace.log \
pytest -q

# import smoke
cd /mnt/d/HermesProject
PYTHONPATH=/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src \
python3 - <<'PY'
from knowledge_navigation.core.hooks import pre_llm_call
from knowledge_tree_plugin.hooks import post_llm_call
from knowledge_tree_plugin.public_api import recall_from_tree_raw
print('imports ok')
PY
```

### 需要新增的最小回归测试

1. `knowledge-tree-plugin/tests/test_placement.py`
   - 新节点插入后必须写自身 `k_vector`，不是只更新父节点。
2. `knowledge-navigation/tests/test_hooks.py`
   - `HAS_KNOWLEDGE_TREE=True` 且 Hindsight `None`、KT 有结果 → 仍返回 `<memory-context>`。
   - Hindsight `results=[]`、KT 有结果 → 仍返回 KT context。
   - KT timeout 连续 N 次 → KT 独立熔断，但 Hindsight 不受影响。
3. `knowledge-tree-plugin/tests/test_public_api.py`
   - 根据最终策略更新：adapter 缓存复用/健康检查，或每次建连且必 close。

---

## 8. 结论

本轮审查确认 review plan 中 Agent A 的主要疑点大多成立。最严重问题是 **在线新增知识点未持久化自身 `k_vector`**，会使 post_llm_call 学到的知识“写入但不可召回”，建议作为 P0 优先修复。其次，`public_api` 测试与源码实际策略冲突、Hindsight 与知识树 recall 的降级/熔断耦合、插件隐式依赖与连接策略问题，会影响部署 gate、可用性和高频调用稳定性，应在下一批修复中处理。
