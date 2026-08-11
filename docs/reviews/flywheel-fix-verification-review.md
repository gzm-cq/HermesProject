# 飞轮修复全面核实 Review 报告

> 生成时间: 2026-08-10
> 核实对象: `docs/reviews/flywheel-fix-report.md`（声称修复 35 项）
> 方法: 2 个并行 agent 分域拉网式源码核实 + 主 session 对高危 P0/推翻项亲自抽查（trust-but-verify）
> 核实范围: 16 个源文件，逐项比对"报告声称改动"与"仓库真实代码"

---

## 1. 总体结论

| 维度 | 结论 |
|------|------|
| **完整性** | ✅ 35 项声称改动**全部在源码中落地**，0 项缺失（30 项修复 + 5 项"保留/无需改"均存在） |
| **正确性** | ⚠️ 30 项修复中 **27 项正确完整**；**3 项 Partial**（改动写对但配套链缺失，引入残留/回归）；另有 3 处关联瑕疵（C3 dashboard 盲区、H7 残留、H2/H3 轻微不一致） |
| **报告文档错误** | ❌ **2 项报告结论/论证有误**：C2（论证全反）、P3-1（结论错判，实为真实活跃 bug） |
| **新引入风险** | P3-8 修复导致"无 API key 时每天推飞书告警"，与 C4 的 no-news-good-news 目标直接冲突 |

**一句话**：修复"都做了"，但**做了不等于做对了**——存在 1 个当前就在生效的真 bug（P3-1）、1 个新引入的告警刷屏回归（P3-8），以及若干残留边缘风险需补刀。

---

## 2. 全量核实总表（35 项）

| 条目 | 文件 | 落地 | 正确性 | 关键证据 / 风险 |
|------|------|------|--------|----------------|
| C1 | router.py | ✅ | ✅ Confirmed | `choices=data.get("choices",[])` 空则 fallback+break（L306-312） |
| H1 | router.py | ✅ | ✅ Confirmed | OrderedDict + move_to_end + popitem(last=False)（L41-59） |
| H4 | router.py | ✅ | ✅ Confirmed | 缺 key 补 False；阈值改 0.3 对齐 prompt（L171-184） |
| P2-1 | router.py | ✅ | ✅ Confirmed | 花括号深度计数解析嵌套 JSON（L87-122） |
| H2 | env_loader.py | ✅ | ✅ Confirmed（轻微） | 60s TTL 手动缓存；但全局无锁，且仍是**模块导入期**求值 |
| H3 | skill_matcher.py | ✅ | ✅ Confirmed（轻微） | 5 常量改 `KN_SKILL_*` env；但仍 import 期一次性求值，与 H2 动态刷新意图不完全对齐 |
| P2-2 | skill_matcher.py | ✅ | ✅ Confirmed | STOPWORDS 补中文语气词+英文 be/have/do |
| H5 | filtering.py | ✅ | ✅ Confirmed | `math.isclose` 替代 `<1e-6`（L287） |
| P2-3 | filtering.py | ✅ | ✅ Confirmed（注释错） | 改 `_demote_factor` 延后降权；**注释指向不存在的 `apply_demote_factors` 函数** |
| P2-11 | filtering.py | ✅ | ✅ Confirmed | 兼容 Z 后缀 + 无时区补 UTC（L74-78） |
| P2-3(hooks) | hooks/router.py | ✅ | ✅ Confirmed | boost 之后应用降权（L968-973） |
| P2-4 | hooks/router.py | ✅ | ✅ 保留合理 | if/else 互斥赋值，无混乱（L1090-1115） |
| C2 | hindsight.py | ✅ | ❌ **报告论证错误** | 见 §3.1 |
| H6 | circuit_breaker.py | ✅ | ✅ 保留合理 | 锁顺序一致无死锁（L105-116） |
| P2-5 | circuit_breaker.py | ✅ | ✅ Confirmed | `_NOTIFICATION_LOCK` 保护检查+更新原子性（L172/268-272） |
| C3 | health-check-run.py | ✅ | ⚠️ **dashboard 盲区** | 条件推送写对，但 services 列表（L38）**不含 dashboard**，dashboard 宕机被静默吞掉 |
| P3-7 | health-check-run.py | ✅ | ✅ Confirmed | 注释 9:00→8:00（L145） |
| H7 | health-check-all.py | ✅ | ✅ Confirmed（残留） | 列表传参+--max-time；**仍有 3 处字符串 curl 未改**（L198/243/402） |
| H8 | health-check-all.py | ✅ | ✅ Confirmed | `df --output=pcent`（L331） |
| P3-6 | health-check-all.py | ✅ | ✅ Confirmed | `min(os.cpu_count() or 4, 8)`（L595） |
| C4 | kn-router-health-check.sh | ✅ | ✅ Confirmed | 注释+`CRON_SKIP_FINISH_NOTIFY=true`（L12/27） |
| H9 | kn-router-health-check.sh | ✅ | ✅ Confirmed | heredoc 改环境变量传递（L155-169） |
| P3-8 | kn-router-health-check.sh | ✅ | ⚠️ **Partial→回归** | 见 §3.2 |
| H10 | cron_common.sh | ✅ | ⚠️ **Partial→不可达** | 见 §3.3 |
| C5 | memory_store.py | ✅ | ⚠️ **Partial→回滚不全** | 见 §3.4 |
| P2-6 | metrics.py | ✅ | ✅ Confirmed | 单字+bigram 混合分词（L77-94） |
| P2-7 | runner.py | ✅ | ✅ 保留合理 | 日志间隔非确定是并行固有特性 |
| P2-8 | config.py | ✅ | ✅ Confirmed | ENV 类型转换 int/bool/list（L149-170） |
| P2-9 | cron-periodic-detect.sh | ✅ | ✅ Confirmed | error_key 改 MD5（L151） |
| P2-10 | kn_judge.py | ✅ | ✅ Confirmed | 保存恢复 os.environ + timeout=30（L175-216） |
| P2-12 | tuner.py | ✅ | ✅ Confirmed | locked 时清 suspended（L894-899） |
| P3-2 | skillopt_runner.py | ✅ | ✅ Confirmed | 基于 HERMES_HOME 计算路径（L28-31） |
| P3-3 | dream-daily.py | ✅ | ✅ Confirmed | session 加 DCL 锁（L52-61） |
| P3-4 | revision.py | ✅ | ✅ Confirmed | max_tokens 提至 4096 可配（L167/241） |
| P3-5 | clustering.py | ✅ | ✅ Confirmed | docstring 补 seen_pairs 说明（L613-623） |
| P3-1 | tasks.py | ✅ | ❌ **报告结论错误（真 bug）** | 见 §3.5 |

**统计**：Confirmed 30 · Partial（残留/回归）3 · 文档错判 2（C2 论证反、P3-1 结论反）· 关联瑕疵 3（C3 盲区、H7 残留、H2/H3 轻微）。

---

## 3. 必须纠正的问题（按严重度）

### 3.1 C2 — 报告论证完全反了（代码本身 ok，文档误导）
**报告原话**："循环后 raise 确实不会被执行（429 限流路径已在循环内正确 raise），作为兜底边界标识保留合理。"
**真实代码**（hindsight.py:106-110, 142）：
```python
elif response.status_code == 429:
    time.sleep(wait_time)
    continue          # ← 是 continue，不是 raise！
...
raise HindsightClientError(f"重试 {CONFIG.max_retries} 次后仍失败（429 限流）", status_code=429)
```
429 分支是 `continue`，重试耗尽后**循环正常退出，L142 必然可达**——它是唯一的 429 终态出口。若有人信了报告"死代码"的说法把它删了，函数会隐式返回 `None`，破坏 `-> dict` 契约。
**结论**：保留没错，但理由写反了。应更正文档：L142 是重试耗尽后的可达终态，非死代码。

### 3.2 P3-8 — 修复引入"无 key 天天告警"回归（与 C4 目标冲突）
P3-8 把无 API key 改为 `STABILITY_SKIPPED=true` 并 `cron_warn`，本意正确。但汇总段未同步：
```bash
STABILITY_PASS=0            # 默认（L116）
_STABILITY_MIN=4            # （L133）
# 汇总（L220）：
if [[ "$STABILITY_PASS" -lt "$_STABILITY_MIN" ]]; then
    HAS_ISSUES=true
fi
# L231：HAS_ISSUES=true → 进入告警分支 → 推飞书
```
无 key 时 `STABILITY_PASS=0 < 4` → `HAS_ISSUES=true` → **每次无 key 都推飞书告警**，正好抵消 C4 想要的 no-news-good-news。

### 3.3 H10 — 错误码诊断分支基本不可达
cron_common.sh:246-263 改了 `--markdown` 并解析 `"code"` 错误码，逻辑本身对。但：
- 脚本头部 `set -euo pipefail`（L35），L247 `_lark_out=$(lark-cli ...)` 是普通赋值，lark-cli 非零即触发 `set -e` 中止脚本；
- `cron_finish` 裸调用本脚本时直接退出，跑不到 L251-263 的 webhook 降级；
- 仅 `cron_notify ... || true` 调用点（如 cron-periodic-detect.sh:265）才生效。
**结论**：诊断/降级代码写对了但绝大多数路径执行不到。

### 3.4 C5 — 回滚不完整，残留数据丢失边缘风险
memory_store.py:184-214 的 merge 回滚：
```python
if _add(merged):
    added = True
    for j in indices:
        if not _remove(j):
            raise Exception(...)   # 中途某个 remove 失败
except Exception:
    if added:
        store.remove(target, merged)   # 只撤销"新增的 merged"
```
若 `indices=[3,5]`，`remove(3)` 成功、`remove(5)` 失败 → 抛异常 → 撤销 merged，但**已删的 entries[3] 不会恢复** → 数据[3]永久丢失。单线程、低概率，但回滚语义不完整。

### 3.5 P3-1 — 报告判"无需修改"实为真实活跃 bug
**报告原话**："tasks.py 是独立编排器，注释指向旧 cli.py 但实际不影响飞轮主流程。"
**真实代码**（tasks.py:297-302）：
```python
args=["scripts/collect_baseline.py", "--delta", "--trigger",
      "--since", "$(date -u +%Y-%m-%dT00:00:00)"]
# 我们在实际 PythonTask.run 的 pre_hook 中处理。
```
PythonTask 用 `subprocess.run(cmd, shell=False)` 列表调用，`$(date ...)` 是 shell 语法、在列表里**不会展开**，会被原样当作字面量传给 `collect_baseline.py`。全仓 `grep pre_hook` 仅命中此注释——**承诺的 pre_hook 从未实现**。结果：KN 基线 delta 检测的 `--since` 时间窗从未生效。这是一个当前就在跑坏的功能缺陷，应修复而非"无需修改"。

---

## 4. 关联瑕疵（非阻断，建议顺带修）

| 项 | 问题 | 建议 |
|----|------|------|
| C3 | health-check-run.py services 列表（L38）不含 `dashboard`，dashboard 宕机被 all_ok 门控静默吞掉（health-check-all.py:459 确有 dashboard 检查项） | 在 services 列表加入 "dashboard" |
| H7 | 已改 1 处 curl 为列表传参，但 L198/243/402 仍有 3 处字符串 curl 未覆盖 | 同法改造剩余 3 处 |
| H2/H3 | H2 用 60s TTL 动态刷新，但 H3 的 `KN_SKILL_*` 仍是模块导入期一次性求值，env 改了不生效 | ✅ 已按方案 B 改为运行期读取，见 §7.3 |
| P2-3 | filtering.py:47 注释指向不存在的 `apply_demote_factors` 函数 | 修正注释为"hooks/router.py 内联降权" |

---

## 5. 修复优先级建议

1. **【P0·当前生效 bug】P3-1**：在 Python 内计算 `--since` 时间戳（如 `datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")`）传入，删除 pre_hook 空注释。
2. **【P1·回归】P3-8**：汇总段对 `STABILITY_SKIPPED` 短路，`SKIPPED` 时不将 HAS_ISSUES 置 true。
3. **【P2】C3 盲区 / H10 不可达 / C5 回滚不全 / H7 残留**：分别补 dashboard、解耦 set -e、恢复已删条目、改造剩余 curl。
4. **【文档】C2 / P2-3**：更正 C2 论证（raise 可达非死代码）、修正 P2-3 注释函数名。

---

## 6. 核实方法与可信度

- 16 个文件全部经 `ast.parse` 语法校验通过（agent 报告）。
- 行号最大偏移 ≤4 行，报告行号参考可信。
- 主 session 亲自抽查 5 个高危点（C2/H2 域、C5/P3-1/P3-8/C3），结论与 agent 一致，其中 C2、P3-1、P3-8 已用 Read 直接读源码二次确认。
- **未修改任何仓库文件**，本报告仅作核实结论。

**结论**：修复覆盖完整（35/35 落地），但"做完了"≠"做对了"——建议按 §5 优先级补 1 个真 bug + 1 个回归 + 若干残留，并更正 2 处文档错判。

---

## 7. 补刀修复闭环状态（2026-08-10 19:30 更新）

按 §5 优先级已全部执行并部署至 WSL 运行环境（`/root/.hermes`），逐项状态：

| 优先级 | 条目 | 修复内容 | 部署项目 | 状态 |
|--------|------|----------|----------|------|
| P0 | **P3-1** | `--since` 改 Python 内计算 `datetime.now(timezone.utc).strftime(...)`，删除 pre_hook 空注释 | flywheel-orchestrator（**新建部署通道**） | ✅ 已部署 |
| P1 | **P3-8** | 汇总段 L221 加 `STABILITY_SKIPPED != true` 短路；L245 报告文案同步为"跳过（无 API key）" | cron-wrappers | ✅ 已部署 |
| P2 | **C3** | services 列表补入 `"dashboard"`，消除宕机静默盲区 | system-health-check | ✅ 已部署 |
| P2 | **H7** | 残留字符串 curl 全部改列表传参（实测 **5 处**，非报告所述 3 处：L198/243/245/247/401） | system-health-check | ✅ 已部署 |
| P2 | **H10** | 诊断/降级段加 `set +e` / `set -e` 守卫，确保错误码解析与 webhook 降级路径可达 | cron-common | ✅ 已部署 |
| P2 | **C5** | merge 分支引入 `removed_ok` 追踪，回滚时 re-add 已删条目，实现完整原子性 | memory-cleanup | ✅ 已部署 |
| 文档 | **P2-3** | filtering.py:47 注释改为指向"hooks/router.py 内联降权逻辑" | knowledge-navigation | ✅ 已部署 |
| 文档 | **C2** | flywheel-fix-report.md 更正论断：L142 `raise` 是重试耗尽后的可达终态，非死代码 | — | ✅ 已更正 |
| 收尾 | **H2/H3** | `KN_SKILL_*` 改运行期 accessor + 4 处 def 期默认参数解固化 + clamp 保护 + 5 个回归测试 | knowledge-navigation | ✅ 已部署（见 §7.3） |

### 7.1 复验方式
仓库 ↔ 运行副本全量 `diff` 比对，6 个脚本文件 + knowledge-navigation 插件 src 全目录：

```
[1] cron_common.sh SAME      [4] health-check-all.py SAME
[2] kn-router-health-check.sh SAME   [5] memory_store.py SAME
[3] health-check-run.py SAME         [6] tasks.py SAME
knowledge-navigation/src: 仅 circuit_breaker.json 不同（运行时状态文件，本就不同步）
```
`hermes-gateway.service` 重启后 `active`。全部文件通过 `py_compile` / `bash -n` 与 CRLF 校验。

### 7.2 C5 compress 分支说明（复核结论：无需修改）
compress 分支（memory_store.py:206-222）只有**单次** `_remove(idx)`，失败即代表尚未删除，撤销新增的 compressed 已构成完整回滚。原子性缺陷仅存在于 merge 的**多 index** 循环，已修复。

### 7.3 H2/H3 env 求值时机 —— 已按方案 B 闭环（2026-08-10 19:36）

**原问题**：H2 的 `env_loader` 已改 60s TTL 动态刷新，但 H3 的 `KN_SKILL_*` 常量仍是**模块导入期一次性求值**，导致 H2 的 TTL 机制对它们完全失效，改 env 必须重启 gateway。

**采纳方案 B（统一为运行期读取）**，改造 `skill_matcher.py`：

1. **新增 6 个 accessor**（`_get_top_k` / `_get_max_skills` / `_get_prescreen_top_k` / `_get_embedding_top_k` / `_get_embedding_batch_size` / `_get_llm_timeout`），每次调用经 `get_env_int()` 读取，链路 `get_env_int → get_env → os.environ → _read_env_file()(60s TTL)`，改 `.env` 后最多 60s 生效。
2. **4 处函数默认参数**（`_embedding_prescreen` / `_keyword_prescreen` / `_llm_match` / `match_skills`）由 `top_k: int = _常量` 改为 `top_k: int | None = None` + 体内解析。**这是关键**：默认参数在 `def` 期求值，是本问题最隐蔽的固化点，仅改常量定义并不能解决。
3. **新增 `_clamp_positive()` 保护**：所有调优参数 clamp 到 `>= 1`，误配 `0`/负数/非数字回退默认值。顺带修掉一个潜在崩溃 —— `KN_SKILL_EMBEDDING_BATCH_SIZE=0` 会让 `range(0, n, 0)` 抛 `ValueError`。
4. **保留导入期快照**（`_TOP_K` 等模块级常量）：`tests/test_skill_matcher.py` 直接 `import _TOP_K` 并断言取值，删除会导致 `ImportError`。快照仅供向后兼容，不参与运行时决策。
5. **新增回归测试** `TestTuningParamsRuntimeRead`（5 个用例）：锁死"默认参数不得在 def 期固化"这一约束——若有人改回 `top_k: int = _TOP_K` 写法会立即失败。

**验证**：`pytest tests/` 全量 **242 passed**；运行副本实证 accessor 读到 `.env` 的 `KN_SKILL_EMBEDDING_TOP_K=30`，运行期改值即时生效，`batch_size=0` 正确 clamp 为 20。

**过程中的意外发现**：编写测试时断言 `_EMBEDDING_TOP_K == 20` 失败（实际 30）——线上 `~/.hermes/.env:535` 真配了 `KN_SKILL_EMBEDDING_TOP_K=30`。说明该参数**确实在被调优使用**，本次热更新改造并非纸面收益。测试已改为断言不变量（正整数）而非具体数值，避免单测与部署环境耦合。

**残留（已知、可接受）**：`env_loader._read_env_file()` 的全局缓存无锁，多线程下可能重复读文件，各线程各自构建 dict 后赋值，无数据损坏风险，未加锁。

### 7.4 新增部署通道
`flywheel-orchestrator` 原先无任何 manifest/deploy 项目，运行副本 `/root/.hermes/scripts/flywheel-orchestrator` 为真实目录且长期滞留旧代码。本次新建：
- `deploy/manifests/flywheel-orchestrator.manifest`
- `deploy/projects/flywheel-orchestrator.sh`（专属子目录、`FIRST_DEPLOY_CLEANUP=false`、无 `PROJECT_SVC`）

严格遵守"部署唯一入口 deploy.sh、禁止直接操作 /root/.hermes/"纪律。

### 7.5 回滚
各项目备份时间戳：`cron-wrappers/20260810-183836` 等，`knowledge-navigation/20260810-192438`（P2-3 注释）与 `knowledge-navigation/20260810-193610`（H2/H3 改造）。
```
./deploy/deploy.sh rollback <project> <timestamp>
```

---

## 8. 最终闭环声明（2026-08-10 19:40）

本报告 §3 必须纠正项（5）+ §4 关联瑕疵（4）**已全部修复、部署并复验，无遗留待决项**。

> ⚠️ 注意：§5「修复优先级建议」与 §6 末尾的结论为**核实当时**的快照，此后已按该优先级全部执行完毕。以 §7 为准。

**最终状态**：仓库 ↔ WSL 运行环境（`/root/.hermes`）全量 `diff` 一致，唯一差异为 `circuit_breaker.json` 运行时状态文件（本就不应同步）。`hermes-gateway.service` `active`，knowledge-navigation 全量测试 **242 passed**。
