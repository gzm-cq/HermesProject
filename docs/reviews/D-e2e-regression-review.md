# Agent D 端到端集成与回归测试审查报告

> **文档状态：历史审查报告 / 修复前回归门禁**  
> 本文记录当时 E2E Release Gate FAIL 的原因，修复后最终状态见 `03-post-fix-audit-2026-06-15.md`。


审查日期：2026-06-15  
源码根目录：`/mnt/d/HermesProject`  
输入报告：

- `docs/reviews/A-plugin-review.md`
- `docs/reviews/B-builder-clustering-review.md`
- `docs/reviews/C-runtime-memory-review.md`

审查方式：读取 A/B/C 三份报告并整合端到端链路测试策略；未修改业务代码、未部署、未执行生产写入。本文仅写入本报告文件。

---

## 1. 端到端链路范围

本报告覆盖以下必须链路，并将 A/B/C 中确认的阻塞项映射到测试 gate：

```text
用户消息
  → knowledge-navigation pre_llm_call
  → Hindsight recall + knowledge-tree recall
  → LLM
  → knowledge-tree-plugin post_llm_call
  → 知识树写入

离线/定时入树：
daily-learn / knowledge-tree-builder → knowledge_tree / source_articles / k_vector

Hindsight 维护：
clustering 标记 / 因果链 / entity/link 权重 → navigation 过滤与排序

记忆清理：
memory-cleanup MEMORY/USER → retain 到 Hindsight → navigation 后续召回
```

---

## 2. 当前端到端结论

### 2.1 总体 gate 结论

| Gate | 当前结论 | 是否可放行端到端上线 | 主要原因 |
|---|---:|---:|---|
| 用户消息 → pre recall → LLM | **FAIL** | 否 | Hindsight 失败/空结果会导致知识树结果被丢弃；KT 无独立熔断。 |
| LLM → post_llm_call → 知识树在线写入 | **FAIL** | 否 | 在线新增知识点未写自身 `k_vector`，写入后不可召回。 |
| daily-learn / builder 入树 | **FAIL** | 否 | builder 非原子写入、k_vector 缺失积压；review_queue status P0 不一致。 |
| clustering 标记/因果链影响 navigation | **FAIL** | 否 | 聚类 cron 当前超时失败；wrapper 含 destructive apply；标记过滤存在尾部 100 字符失效风险。 |
| memory-cleanup 迁移到 Hindsight | **FAIL** | 否 | dryrun cron 实际 apply，且 retain 与 MEMORY/USER 修改非事务；USER 保护缺硬 gate。 |
| 最小 import / 单元 smoke | **PARTIAL FAIL** | 否 | navigation 单测绿，但 tree plugin 2 failed、clustering 3 failed、self-evolving 1 failed。 |

### 2.2 发布判定

**当前不建议执行端到端生产写入或部署。** 需要先处理 P0/P1 阻塞项，并补齐 KT-enabled、Hindsight-failover、post-write-recall、cleanup-retain 等回归测试后再进入生产 smoke。

---

## 3. E2E 测试矩阵

### 3.1 主对话链路：用户消息 → pre_llm_call → recall → LLM

| ID | 场景 | 前置条件 | 操作 | 期望 | 当前风险/状态 | Gate |
|---|---|---|---|---|---|---|
| E2E-CHAT-01 | 普通用户消息触发 pre hook | `platform=cli/feishu/weixin`，非系统 prompt，非工具输出 | 调 `knowledge_navigation.core.hooks.pre_llm_call` | pre hook 放行并尝试 Hindsight + KT 并行 recall | navigation 单测默认禁用 KT，缺集成覆盖 | FAIL until test added |
| E2E-CHAT-02 | Hindsight 有结果，KT 有结果 | mock 两侧 recall 均返回 | pre hook 返回 `<memory-context>`，包含两侧来源或合并结果 | 验证合并、去重、排序、格式化 | 可作为新增单元/集成测试 | REQUIRED |
| E2E-CHAT-03 | Hindsight 失败/超时，KT 有结果 | Hindsight future 抛异常或超时；KT future 返回 | 仍返回 KT-only `<memory-context>`，不取消 KT | A-P1-2：当前 Hindsight 失败会取消/丢弃 KT | **FAIL** |
| E2E-CHAT-04 | Hindsight 空结果，KT 有结果 | Hindsight 返回空 `raw_results`；KT 返回 | 仍注入 KT context | A-P1-2：当前 `raw_results` 空时直接 `return None` | **FAIL** |
| E2E-CHAT-05 | Hindsight circuit open，KT 可用 | 打开 Hindsight 熔断 | 只跳过 Hindsight，不跳过 KT | 当前全局熔断直接 `return None`，误伤 KT | **FAIL** |
| E2E-CHAT-06 | KT timeout 连续发生 | KT recall 连续超时；Hindsight 正常 | KT 独立熔断/冷却；Hindsight 继续可用 | A-P1-5：KT 无独立熔断/指标 | **FAIL** |
| E2E-CHAT-07 | 标记过滤 | Hindsight memory text 含 `[标记: 错误/作废/可疑/待验证]` | navigation 过滤不注入 | 尾部标记短 note 可过滤 | PARTIAL |
| E2E-CHAT-08 | 长 note 标记过滤 | 标记后 note 超过 100 字符或标记不在尾部 | 仍应过滤 | C-P1-06：当前只看尾部 100 字符，可能失效 | **FAIL** |

### 3.2 LLM → post_llm_call → 知识树在线写入 → 后续可召回

| ID | 场景 | 前置条件 | 操作 | 期望 | 当前风险/状态 | Gate |
|---|---|---|---|---|---|---|
| E2E-POST-01 | assistant 响应触发 post hook | 用户轮次、allowlist platform、响应非工具/日志/代码块 | 调 `knowledge_tree_plugin.hooks.post_llm_call` | cheap gate 后非阻塞入队 | 需 smoke 确认 hook 放行/跳过矩阵 | REQUIRED |
| E2E-POST-02 | 在线提取新知识点 | mock LLM extraction 返回原子知识点 | 后台 placement 写入 `knowledge_tree` + `knowledge_point_texts` | 新节点、文本、source 信息一致 | post 后台链路需集成测试 | REQUIRED |
| E2E-POST-03 | 新知识点写自身 `k_vector` | embedding mock 返回 1024 维向量 | 写库后新 knowledge_point `k_vector IS NOT NULL` | A-P0-1：当前只更新父节点，新节点不写 vector | **FAIL / P0** |
| E2E-POST-04 | 写入后立即可被 KT recall | E2E-POST-03 成功 | 调 KT recall，query 命中新知识点 | 新知识点进入 attention filter 并可召回 | 因新节点缺 `k_vector` 当前不可召回 | **FAIL / P0** |
| E2E-POST-05 | public_api 高频调用连接策略 | 连续多轮 pre hook 调 KT recall | 连接复用/关闭策略符合设计且无泄漏 | A-P1-1/A-P1-3：测试与源码冲突；每次建连压力大 | FAIL until resolved |
| E2E-POST-06 | 插件加载顺序 | 单独加载 tree plugin / navigation plugin | import 稳定，依赖显式 | A-P1-4：隐式依赖未声明 | FAIL until manifest/deps fixed |

### 3.3 daily-learn / builder 入树

| ID | 场景 | 前置条件 | 操作 | 期望 | 当前风险/状态 | Gate |
|---|---|---|---|---|---|---|
| E2E-BUILD-01 | builder dry-run 全链路 | test_articles 或临时输入目录 | `knowledge_tree_builder.cli run --merged --dry-run` | 完成抽取、准入、定位，不写库 | B 已实测 dry-run 成功 | PASS smoke |
| E2E-BUILD-02 | builder 单元测试 | 无生产写入 | `PYTHONPATH=src python3 -m pytest tests -q` | 265 passed | B 已实测 265 passed | PASS |
| E2E-BUILD-03 | 入库事务原子性 | 使用测试 DB/事务回滚环境 | 模拟 `_write_to_db()` 中途失败 | 不留下无 vector zombie node | B-P1：当前多隐式事务 | **FAIL** |
| E2E-BUILD-04 | source_articles 幂等 | 同一输入重复运行 | 不重复插 source_articles / point_texts | B-P1：insert 无 ON CONFLICT | **FAIL** |
| E2E-BUILD-05 | subject k_vector 完整性 | 测试 DB 有无向量 subject | 匹配时无 vector subject 不应导致错误新科目泛滥 | B-P1：subject 缺 vector 被跳过 | **FAIL** |
| E2E-BUILD-06 | review_queue 可见性 | 插入 review item | `review list` 能看到待审项 | B-P0：`pending` vs `pending_review` 不一致 | **FAIL / P0** |
| E2E-BUILD-07 | daily-learn 失败可排障 | 故意触发 builder 错误 | cron/log 保留完整 traceback 与输入 | C-P1-07：tail -5 + TMP 删除 | FAIL until logging fixed |

### 3.4 clustering 标记/因果链 → navigation

| ID | 场景 | 前置条件 | 操作 | 期望 | 当前风险/状态 | Gate |
|---|---|---|---|---|---|---|
| E2E-CLUSTER-01 | clustering 单元测试 | 无生产写入 | `PYTHONPATH=src python3 -m pytest tests -q` | 全绿 | B 已实测 68 passed, 3 failed | **FAIL** |
| E2E-CLUSTER-02 | mark_memory 单元测试 | 无生产写入 | `python3 -m pytest tests/test_mark_memory.py -q` | 全绿 | C 已实测 23 passed | PASS |
| E2E-CLUSTER-03 | 标记后 navigation 过滤 | 测试 DB memory text 添加标记 | navigation recall 不注入错误/作废项 | 短标记路径基本一致 | REQUIRED |
| E2E-CLUSTER-04 | 长 note 标记过滤 | 标记 note 超长 | navigation 仍过滤 | C-P1-06 当前可能失效 | **FAIL** |
| E2E-CLUSTER-05 | 因果链/links 影响 navigation 排序 | 测试 DB 生成 entities/memory_links/causal links | navigation 排序/上下文能反映 links 权重或至少不被 inflated mention_count 扭曲 | B-P1：mention_count 非幂等；因果测试 3 failed | **FAIL** |
| E2E-CLUSTER-06 | HDBSCAN 聚类质量 guard | 输入非归一化向量 | 聚类前 L2 normalize 或明确 metric 适配 | B-P1：未 normalize | FAIL |
| E2E-CLUSTER-07 | cron wrapper 安全 | no-agent cron 环境 | 不应默认执行 destructive apply；超时可观测 | C-P0：当前 weekly cron timeout 且 wrapper 多项 apply | **FAIL / P0** |

### 3.5 memory-cleanup → Hindsight → navigation

| ID | 场景 | 前置条件 | 操作 | 期望 | 当前风险/状态 | Gate |
|---|---|---|---|---|---|---|
| E2E-CLEAN-01 | memory-cleanup 单元测试 | 无生产写入 | `PYTHONPATH=src python3 -m pytest tests -q` | 全绿 | C 已实测 123 passed | PASS |
| E2E-CLEAN-02 | dry-run 安全 | 真实或测试 MEMORY/USER，只读 | 不加 `--apply` 运行 | 输出待迁移/删除报告，不改文件/DB | 可作为 smoke，但会调用真实 LLM | REQUIRED |
| E2E-CLEAN-03 | retain 成功后删除 | 测试文件 + mock Hindsight | retain 成功才从 MEMORY/USER 删除 | 源码有顺序保护，但非事务 | PARTIAL |
| E2E-CLEAN-04 | retain 失败不删除 | mock Hindsight 失败 | 条目仍保留，报告失败 | 需新增测试覆盖 | REQUIRED |
| E2E-CLEAN-05 | USER 长期偏好硬保护 | USER 中含偏好/规则/个人背景/工作风格 | 不允许 remove/hindsight，除非人工批准 | C-P1-02：当前依赖 prompt，缺硬 gate | **FAIL** |
| E2E-CLEAN-06 | AUTO_REMOVE_PATTERNS 安全 | USER/MEMORY 含“方法论”等长期价值内容 | 不应自动直删 | C-P1-01：当前过宽 | **FAIL** |
| E2E-CLEAN-07 | cron 命名与实际一致 | cron job 名称为 dryrun | runtime 不应 `--apply` | C-P0-01：当前 dryrun 名称实际 apply | **FAIL / P0** |
| E2E-CLEAN-08 | 迁移后 Hindsight 可被 recall | 测试 Hindsight retain 成功 | pre hook 后续能召回迁移内容 | 需测试 DB/mock Hindsight 环境 | REQUIRED |

---

## 4. PASS / FAIL Gate

### 4.1 必须全部通过的发布 gate

| Gate ID | Gate 内容 | PASS 标准 | 当前状态 |
|---|---|---|---:|
| G-01 | `knowledge-navigation` 单元测试 | `pytest -q` 全绿；新增 KT-enabled 测试全绿 | PARTIAL：现有 86 passed，但缺 KT 场景 |
| G-02 | `knowledge-tree-plugin` 单元测试 | 全绿；`test_public_api` 与实现一致 | **FAIL：35 passed, 2 failed** |
| G-03 | post 写入 vector gate | 在线新增 knowledge_point 必须写自身 `k_vector` | **FAIL / P0** |
| G-04 | KT-only fallback gate | Hindsight fail/empty/circuit open 时 KT 结果仍可注入 | **FAIL / P1** |
| G-05 | KT independent breaker gate | KT timeout/异常有独立熔断，不影响 Hindsight | **FAIL / P1** |
| G-06 | builder 入树完整性 gate | 无 zombie node；新写节点/subject 有 vector；review_queue 可见 | **FAIL / B-P0/P1** |
| G-07 | clustering test gate | clustering-analysis-v3 测试全绿 | **FAIL：68 passed, 3 failed** |
| G-08 | 标记过滤 gate | 长 note / 结构化标记不会绕过 navigation 过滤 | **FAIL / C-P1** |
| G-09 | cleanup 安全 gate | cron 不自动 apply；USER hard gate；retain/file 修改有 journal 或测试保护 | **FAIL / C-P0/P1** |
| G-10 | destructive apply gate | `cron_wrapper.sh`、dedup、long governance 默认不做生产 apply；apply 需人工审批 | **FAIL / C-P0** |
| G-11 | 最小 import smoke | navigation/tree/builder 关键模块可 import | PASS：A 已实测 `imports ok` |
| G-12 | no production write in CI smoke | smoke 默认 mock DB/测试 DB/dry-run，不触碰生产 | 需建立脚本 | REQUIRED |

### 4.2 FAIL 即阻塞项

以下任一项失败均应阻塞端到端写入上线：

1. 在线 post 写入的新 knowledge_point 无 `k_vector`。
2. Hindsight 失败/空结果导致 KT 结果被丢弃。
3. `knowledge-tree-plugin` 单测红，尤其 public_api 策略未定。
4. `review_queue` status 不一致导致待审项不可见。
5. memory-cleanup cron 名称 dryrun 但实际 `--apply`。
6. clustering weekly cron 默认 destructive apply 且 120s timeout。
7. USER.md 条目可被自动 remove/hindsight 且无硬 gate/人工审批。
8. 任何 smoke 使用生产 DB 执行 `--apply`、deploy 或 destructive delete。

---

## 5. 阻塞项对端到端链路的影响

| 阻塞项 | 来源 | 影响链路 | 端到端后果 | 优先级 |
|---|---|---|---|---:|
| 在线新增知识点未写自身 `k_vector` | A-P0-1 | LLM → post_llm_call → 知识树写入 → 后续 KT recall | “学到但不可召回”，用户以为知识已入树但 navigation 永远过滤掉无 vector 节点 | P0 |
| Hindsight 与 KT recall 降级耦合 | A-P1-2 | 用户消息 → pre_llm_call → recall | Hindsight 短故障/空结果时，KT 也不可用，整体上下文注入失败 | P1/阻塞 |
| KT 无独立熔断 | A-P1-5 | pre_llm_call 高频路径 | KT 持续 timeout 每轮重复拖慢；Hindsight 熔断又会误伤 KT | P1 |
| public_api 测试与实现冲突 | A-P1-1 | pre recall 高频 KT API | 发布 gate 不可信；连接策略未定，性能/稳定性不可评估 | P1/阻塞 |
| 插件隐式依赖 | A-P1-4 | 插件加载与部署 | 加载顺序/PYTHONPATH 变化导致 import fail，E2E smoke 不稳定 | P1 |
| builder 非原子写入 + k_vector 积压 | B-P1 | daily-learn/builder 入树 → KT recall | 离线入树产生 zombie nodes；subject 匹配失败，树质量下降 | P1/阻塞入树 |
| review_queue status 不一致 | B-P0 | builder/consolidate/review | 待审项不可见，人工治理链路断裂 | P0 |
| clustering HDBSCAN 未 normalize | B-P1 | clustering → links/entities/因果链 | 聚类质量不稳定，间接影响 navigation 权重/排序 | P1 |
| batch_embed 部分失败 zip 截断 | B-P1 | clustering embedding 更新 | 部分 memory_units embedding 静默过期，召回/聚类依据错误 | P1 |
| mention_count 非幂等 | B-P1 | clustering entities → navigation ranking | 重复运行 inflate 权重，navigation 排序被污染 | P1 |
| dedup_minhash 硬删除 | B-P1/C-P0 | clustering maintenance → Hindsight | 误删不可恢复，后续 navigation 永久丢上下文 | P1/阻塞 apply |
| memory-cleanup dryrun cron 实际 apply | C-P0-01 | MEMORY/USER → Hindsight | 每日自动修改记忆，误删/误迁移会影响所有后续对话 | P0 |
| clustering weekly cron timeout + destructive apply | C-P0-02/03 | Hindsight 维护 → navigation | 维护失败且难定位；中途 apply 可能部分写入 | P0 |
| USER 保护缺硬 gate | C-P1-02 | memory-cleanup → USER/Hindsight → system context | 长期偏好/规则迁出 USER，导致每轮稳定上下文丢失 | P1/阻塞 apply |
| 标记只看尾部 100 字符 | C-P1-06 | mark_memory → navigation filtering | 错误/作废记忆仍可能被召回注入 | P1 |
| daily-learn 日志不足 | C-P1-07 | daily-learn/builder 入树 | 入树失败不可复现，影响回归定位 | P1 |

---

## 6. 最小 smoke 脚本建议

> 原则：默认只读、dry-run、mock 或测试 DB；禁止生产 `--apply`、deploy、硬删除。以下是建议脚本结构，不应直接在生产环境执行写入路径。

### 6.1 `smoke_e2e_readonly.sh`：只读/导入/单元 smoke

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT=/mnt/d/HermesProject
export PYTHONPATH="$ROOT/plugins/knowledge-navigation/src:$ROOT/plugins/knowledge-tree-plugin/src:$ROOT/scripts/knowledge-tree-builder/src"

cd "$ROOT"
python3 - <<'PY'
from knowledge_navigation.core.hooks import pre_llm_call
from knowledge_tree_plugin.hooks import post_llm_call
from knowledge_tree_plugin.public_api import recall_from_tree_raw
print('imports ok')
PY

cd "$ROOT/plugins/knowledge-navigation"
KN_TRACE_LOG_PATH=/tmp/knowledge-navigation-pytest-trace.log pytest -q

cd "$ROOT/plugins/knowledge-tree-plugin"
PYTHONPATH="src:$ROOT/plugins/knowledge-navigation/src:$ROOT/scripts/knowledge-tree-builder/src" pytest -q

cd "$ROOT/scripts/knowledge-tree-builder"
PYTHONPATH=src python3 -m pytest tests -q
PYTHONPATH=src python3 -m knowledge_tree_builder.cli run --input-dir test_articles --merged --dry-run

cd "$ROOT/scripts/clustering-analysis-v3"
PYTHONPATH=src python3 -m pytest tests -q
PYTHONPATH=src python3 -m pytest tests/test_mark_memory.py -q

cd "$ROOT/scripts/memory-cleanup"
PYTHONPATH=src python3 -m pytest tests -q
```

当前预期：该脚本会在 `knowledge-tree-plugin` 和 `clustering-analysis-v3` 处失败；这是正确 gate 行为。

### 6.2 `smoke_pre_recall_fallback.py`：pre hook fallback 最小回归

建议新增到测试目录或 smoke 目录，使用 monkeypatch，不访问真实 Hindsight/DB：

```python
"""验证 Hindsight fail/empty 时 KT-only context 不丢失。"""

from knowledge_navigation.core import hooks


def test_hindsight_failure_keeps_kt(monkeypatch):
    monkeypatch.setattr(hooks, "HAS_KNOWLEDGE_TREE", True, raising=False)

    def fake_hindsight(*args, **kwargs):
        raise TimeoutError("mock hindsight timeout")

    def fake_kt(*args, **kwargs):
        return [{"text": "KT fact", "score": 0.9, "source": "knowledge_tree"}]

    monkeypatch.setattr(hooks, "_do_hindsight_recall", fake_hindsight, raising=False)
    monkeypatch.setattr(hooks, "_do_kt_recall", fake_kt, raising=False)

    result = hooks.pre_llm_call({"platform": "cli", "messages": [{"role": "user", "content": "问一个会命中知识树的问题"}]})
    assert result is not None
    assert "KT fact" in str(result)
```

当前预期：在未修复 A-P1-2 前应失败。

### 6.3 `smoke_post_write_vector.py`：post 写入 vector 回归

建议使用测试 DB 或 adapter mock：

```python
"""验证在线写入的新 knowledge_point 持久化自身 k_vector。"""

from knowledge_tree_plugin.placement import place_new_knowledge_points


def test_online_insert_writes_child_k_vector(fake_adapter, monkeypatch):
    monkeypatch.setattr("knowledge_tree_plugin.placement.batch_embed", lambda texts, *a, **k: [[0.1] * 1024 for _ in texts])

    node_ids = place_new_knowledge_points(
        points=[{"name": "测试知识", "text": "这是一个必须可召回的知识点"}],
        adapter=fake_adapter,
    )

    assert node_ids
    # 关键断言：不是只更新 parent，而是每个新 child node 都 update/write k_vector
    assert fake_adapter.child_k_vector_written(node_ids[0])
```

当前预期：在未修复 A-P0-1 前应失败。

### 6.4 `smoke_mark_filter.py`：标记过滤最小回归

```python
from knowledge_navigation.core.filtering import should_exclude_memory


def test_long_note_mark_still_excluded():
    text = "有问题的记忆\n[标记: 错误] " + ("很长的说明" * 80)
    assert should_exclude_memory(text) is True
```

当前预期：在过滤逻辑只看尾部 100 字符时可能失败。

### 6.5 `smoke_cleanup_retain.py`：cleanup retain 安全回归

```python
"""使用临时 MEMORY/USER 文件和 mock Hindsight；禁止真实文件路径。"""


def test_retain_failure_does_not_delete(tmp_path, monkeypatch):
    memory = tmp_path / "MEMORY.md"
    memory.write_text("- 应保留直到 retain 成功的条目\n", encoding="utf-8")

    def fail_retain(*args, **kwargs):
        return False

    # 运行 cleanup apply 到临时文件 + mock retain
    # 断言 retain 失败后原条目仍在文件中
    assert "应保留" in memory.read_text(encoding="utf-8")
```

### 6.6 `smoke_cron_config_readonly.sh`：cron 配置只读检查

```bash
#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
jobs=json.load(open('/root/.hermes/cron/jobs.json'))['jobs']
for job in jobs:
    name=job.get('name','')
    script=job.get('script','')
    if 'dryrun' in name.lower() or 'dry-run' in name.lower():
        print(job['id'], name, script)
        # gate: dryrun job 的 runtime script 不应包含 --apply；需读取 wrapper 内容检查
PY
```

---

## 7. 建议执行顺序

### Phase 0：冻结高风险生产写入

1. 暂停或改造 `memory-cleanup-daily-dryrun`，确保 dryrun job 不执行 `--apply`。
2. 暂停 weekly clustering destructive apply，或拆为 dry-run 报告 + 人工批准。
3. 禁止 review/CI smoke 执行以下命令：
   - `memory_cleanup ... --apply`
   - `mark_memory.py ... --apply`
   - `dedup_minhash.py --apply`
   - `long_memory_governance.py --apply`
   - `cron_wrapper.sh` 默认 apply 管线
   - `deploy/deploy.sh deploy ...`

### Phase 1：修复主链路 P0/P1 正确性

1. 修复在线新增 knowledge_point 写自身 `k_vector`。
2. 修复 Hindsight 与 KT recall 降级耦合：Hindsight fail/empty/circuit open 时保留 KT-only path。
3. 为 KT recall 增加独立熔断、指标与冷却。
4. 统一 `knowledge-tree-plugin` public_api 连接策略与测试。
5. 显式化插件依赖/加载顺序。

### Phase 2：修复 builder / daily-learn 入树可靠性

1. 统一 `review_queue` status。
2. `_write_to_db()` 原子化或增加 journal/断点续写。
3. 回填并 gate 所有新写 `knowledge_point` / `subject` 的 `k_vector`。
4. `source_articles` / `knowledge_point_texts` 增加幂等保护。
5. daily-learn 失败保留完整日志和临时输入。

### Phase 3：修复 clustering 对 navigation 的污染风险

1. clustering 测试恢复全绿。
2. HDBSCAN 输入向量 L2 normalize 或更换/明确 metric。
3. `batch_embed` 部分失败时禁止 zip 静默截断，必须 fail-fast 或按 id 显式记录失败。
4. `mention_count` 改幂等更新。
5. dedup apply 增加软删除/备份/人工确认 gate。
6. 标记过滤改为结构化列或至少扫描最后一行/更大窗口，覆盖长 note 测试。

### Phase 4：修复 memory-cleanup 到 Hindsight 的安全迁移

1. USER hard denylist：长期偏好、规则、沟通风格、个人背景不得自动 remove/hindsight。
2. 收窄 `AUTO_REMOVE_PATTERNS`，禁用对 USER 的“方法论”等宽泛直删。
3. retain 与文件修改引入 journal：plan → retain → diff → commit → verify → rollback。
4. cron 只生成 dry-run 报告；apply 必须人工审批。
5. 增加 retain 失败不删除、USER 不误迁、备份可恢复的测试。

### Phase 5：执行完整 E2E smoke

按以下顺序执行，任一步失败即停止：

1. import smoke。
2. 单元测试 smoke：navigation、tree plugin、builder、clustering、memory-cleanup。
3. builder dry-run。
4. pre recall fallback mock 测试。
5. post write vector mock/测试 DB 测试。
6. mark/filter 长 note 测试。
7. cleanup retain mock 测试。
8. 使用隔离测试 DB 执行完整 “post 写入 → KT recall → navigation 注入” E2E。
9. 仅在全部通过后，才考虑受控生产 dry-run；生产 apply 仍需人工批准。

---

## 8. 建议新增的回归测试清单

| 测试位置 | 新增测试 | 覆盖问题 |
|---|---|---|
| `plugins/knowledge-navigation/tests/test_hooks.py` | `HAS_KNOWLEDGE_TREE=True`，Hindsight exception + KT result | A-P1-2 |
| `plugins/knowledge-navigation/tests/test_hooks.py` | Hindsight empty + KT result | A-P1-2 |
| `plugins/knowledge-navigation/tests/test_hooks.py` | Hindsight circuit open + KT-only | A-P1-2 |
| `plugins/knowledge-navigation/tests/test_hooks.py` | KT timeout N 次触发独立 breaker，Hindsight 不受影响 | A-P1-5 |
| `plugins/knowledge-tree-plugin/tests/test_placement.py` | 新节点插入后写自身 `k_vector` | A-P0-1 |
| `plugins/knowledge-tree-plugin/tests/test_public_api.py` | 按最终策略验证 adapter 缓存/关闭/健康检查 | A-P1-1/A-P1-3 |
| `scripts/knowledge-tree-builder/tests` | `_write_to_db()` 中途失败不留 zombie node | B-P1 |
| `scripts/knowledge-tree-builder/tests` | review_queue 插入与查询 status 一致 | B-P0 |
| `scripts/clustering-analysis-v3/tests` | HDBSCAN 前 normalize | B-P1 |
| `scripts/clustering-analysis-v3/tests` | batch_embed 部分失败不 zip 截断 | B-P1 |
| `scripts/clustering-analysis-v3/tests/test_mark_memory.py` | 长 note 标记仍能被 navigation 过滤 | C-P1-06 |
| `scripts/memory-cleanup/tests` | retain 失败不删除 MEMORY/USER | C-P1-03 |
| `scripts/memory-cleanup/tests` | USER 长期偏好/规则不能自动 remove/hindsight | C-P1-02 |
| `scripts/memory-cleanup/tests` | AUTO_REMOVE 不误删“方法论”长期价值条目 | C-P1-01 |

---

## 9. 最终建议

当前系统已具备多个局部可用组件，但端到端链路存在关键断点：**在线学到的知识不可召回、Hindsight 故障会拖垮 KT fallback、定时维护任务存在生产写入安全风险**。因此 Agent D 的总 gate 为：

```text
E2E RELEASE GATE: FAIL
```

建议先按 Phase 0 冻结高风险 apply，再按 Phase 1 修复主对话链路正确性。只有当 `knowledge-tree-plugin` 单测全绿、post 写入 vector gate 通过、KT-only fallback gate 通过、memory-cleanup/clustering destructive apply 被移出自动 cron 后，才应进入隔离测试 DB 的完整端到端 smoke。