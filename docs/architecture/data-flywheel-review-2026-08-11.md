# 数据飞轮（Data Flywheel）全面 Review 报告

> 审查范围：`plugins/knowledge-navigation/`（召回 + Router）+ `scripts/flywheel-health-report/`（健康报告 + mask 级 LLM Judge + Auto-Tuner）
> 审查日期：2026-08-11
> 方法：逐文件读码取证，聚焦「框架完整性 / 逻辑错误 / 优化方向」

---

## 一、框架完整性评估：✅ 完整闭环（评分 9/10）

端到端闭环已**完整打通**，且路径全部对齐到 `/root/.hermes`：

```
每次 LLM 调用前 Router 召回（四路 h/kt/s/sag）
   ↓ 产生 trace.log（UTC 时间戳，含 source 标识）
flywheel-health-report.sh（单 cron 任务，--home $HERMES_HOME）
   ├─ 阶段0 runner.py   ：skill eval 评估 + 登记 KN judge 声明
   ├─ cli.py            ：generate_report → kn_judge 对 30 天窗口做 mask 级 LLM 评分
   │                      → append_daily_summary 写 daily-summary-history.jsonl（含 mask 键）
   └─ 末尾 exec auto-tuner.sh → tuner.py 读 daily-summary → 因果绑定选参 → 改写 /root/.hermes/.env
   ↓ .env 影响 Router 行为
下一轮召回质量变化 → 新 trace.log  ……循环
```

**关键对齐验证（证明不是「写了没人读」）：**
- report 写盘：`parsers.py:57` → `data_flywheel_dir / "daily-summary-history.jsonl"`
- tuner 读取：`config.py:164` `HISTORY_FILE = HERMES_HOME/data/flywheel/daily-summary-history.jsonl`
- `DATA_FLYWHEEL_SUBPATH = data/flywheel`（config.py:18），`home` 默认 `/root/.hermes`（cli.py:26）→ 二者指向**同一文件** ✅
- `.env`：`ENV_FILE = /root/.hermes/.env`（config.py:163），与 Router 运行时同源 ✅

**四路 mask 框架完整：**
| 维度 | 决策 | 执行 | 熔断 | recall_logger source | judge 评分 | tuner 因果绑定 |
|---|---|---|---|---|---|---|
| h (Hindsight) | router.py:492-528 | `_do_hindsight_recall` | `_hindsight_cb` | ✓ | ✓ | ✓ (config.py:191-195) |
| kt (知识树) | ✓ | `_do_kt_recall` | `_kt_cb` | ✓ | ✓ | ✓ |
| s (Skill) | ✓ | `_do_skill_match` | embedding 熔断 | ✓ | ✓ (skill_used_count) | ✓ |
| sag (梦境反思) | ✓ | `_do_sag_recall` | `_sag_cb` | ✓ | ✓ | ✓ (config.py:197-200) |

**熔断框架完整**：三路独立 `CircuitBreaker`（`hindsight/sag/knowledge_tree`，circuit_breaker.py:150-164），共享 `threshold=3 / cooldown=90`（config 驱动），线程安全（实例锁 + 模块级文件锁），状态 `tmp+replace` 原子持久化（:98-101）。**KT 路熔断对称**：`_do_kt_recall` 内部成功调 `kt_circuit_record_success()`（router.py:290）、失败调 `kt_circuit_record_failure`（:300）——此前怀疑「KT 不重置」经核查**不成立**。

**重复 judge 已治理**：`runner.py:8` 注释确认 `knowledge-navigation-baseline` 重复 judge cron 已在 jobs.json 禁用，避免与 report 内建 judge 重复。

**结论：框架完整，无结构性缺口。**

---

## 二、逻辑错误清单（按严重度分级）

### P0（阻断级）：无
此前两个致命 bug 已修复且**本次复核确认修复完整**：
- **30 天窗口被报告日切窗覆盖** → kn_judge.py:340-355 已改为固定 `[now-30d, now]`，与 report 的 CN 日切窗彻底解耦。
- **信任门控读错字典（永远 False → mask 反馈跳过 → 位置游走）** → 两处调用均已正确：
  - `determine_direction` 传 `summary_rec`（tuner.py:1049-1050，trust 读 `summary_rec`）
  - `handle_pending_restart` 的 `metrics_after` 来自 `_extract_metrics_before(today_rec)`（:747），而 `today_rec` 是 daily-summary 全量记录，`FEEDBACK_KEYS`（config.py:212-228）含 `kn_judge_sample_count_*`，故 `metrics_after` **带样本计数** → 信任判定正确（:783 非残留 bug）。

### P1（重要，当前非活跃但为健壮性缺口）
1. **kn_judge 多处 early-return 不写 mask 键且无 fallback**（kn_judge.py:334/338/358-362/366）。
   仅 `judged < min` 路径走 `_kn_judge_fallback`（:407）；其余失败路径（LLM 配置缺失 / import 失败 / trace 缺失 / 全局样本<min）直接 `return {error}`，**当日 daily-summary 无 judge rate 键**。
   - 影响：若 LLM 端点持续不可用，tuner 长期无信号 → 静默停滞。
   - 缓解：30 天窗口每轮重判，单次失败有韧性；但**持续故障会饿死反馈**。
2. **全局 `min_sample`(20) 早退门控 vs mask 级 `mask_min_sample`(12) 不一致**（kn_judge.py:358 vs config.py:237）。
   某天总 recall<20 但单路（如 h=14≥12）达标时，整段被全局门控拦下、无任何 mask 键产出 → mask 级调优被抑制。

### P2（低危，健壮性瑕疵）
3. **时间戳字符串比较脆弱**（kn_judge.py:353-354）：`r["timestamp"]`（config.py:16 产出 `2026-08-11T12:34:56.789012+00:00`，带后缀+微秒）与无后缀的 `floor_iso/until_iso_eff` 做字典序比较。因两侧皆 UTC，实际正确；仅当记录与 `now` 精确到同一秒带后缀时可能误判（几乎不可能）。建议改为 `datetime.fromisoformat` 解析后比较。
4. **信任依赖隐式字段携带**：`handle_pending_restart` 信任判定（:783）依赖 `metrics_after` 隐式携带 `sample_count_*`（已验证成立，但耦合不直观）。建议显式传 `today_rec` 做信任，降低后续维护误改风险。

---

## 三、优化方向（按价值排序）

| # | 方向 | 做什么 | 预期收益 |
|---|---|---|---|
| 1 | **反馈饥饿防御** | 所有 kn_judge early-return 路径统一走 fallback 或回写「上次已知 mask 键」，不让单点故障清空当日 judge 数据 | 消除 LLM 故障导致的 tuner 静默停滞（修 P1-1） |
| 2 | **mask 级早退门控** | kn_judge 的 `min_sample` 早退改为 per-mask 评估：单路达标即产出该路键 | 提升低频样本场景下的 mask 级调参灵敏度（修 P1-2） |
| 3 | **盯紧 SAG 负信号落地** | 下一非冷却日 tuner 应据 `relevant_rate_sag=0.333`（强负）驱动 `KN_SAG_*`（已确认因果绑定正确，config.py:197-200）。建议加 SAG 样本量提升或降 `mask_min_sample_sag` | 把「唯一强负信号」转化为真实参数优化，闭环产生第一个 SAG 侧增益 |
| 4 | **调参决策可观测** | 每次有效调参后，将「参数 / 旧→新 / 依据 mask 键」推一张飞书决策卡（当前飞书仅在熔断打开时告警） | 让优化器行为可追溯、可审计，避免「伪优化器」复发 |
| 5 | **信任判定去耦合** | `handle_pending_restart` 显式接收 `today_rec` 做信任，而非依赖 `metrics_after` 字段隐式携带（修 P2-4） | 降低维护误改风险 |
| 6 | **时间戳稳健化** | kn_judge 窗口过滤改用 `datetime.fromisoformat` 解析比较（修 P2-3） | 消除边界脆弱性 |
| 7 | **仓库卫生** | 清理根目录 `kt_probe*.sh`、`review_*.txt`、`tmp/` 等历史探针脚本（非框架但污染仓库） | 降低误读/误部署风险 |

---

## 四、当前运行状态提示
- `KN_MAX_RESULTS` 处于每日冷却期（上轮已确认 correct 安全机制），今日 tuner 跳过新调优。
- 下一非冷却日定时巡检将基于 `relevant_rate_sag=0.333` 触发 `KN_SAG_*` 因果调参——届时优化器将**首次在 SAG 路产生真实闭环增益**。

## 五、结论
数据飞轮框架**完整、闭环真实打通、无 P0 逻辑错误**。两个历史致命 bug 的修复经验证无残留。剩余为 P1 健壮性缺口（反馈饥饿防御、mask 级门控）与 P2 代码健壮性瑕疵，均非活跃故障，可按上表优先级渐进优化。
