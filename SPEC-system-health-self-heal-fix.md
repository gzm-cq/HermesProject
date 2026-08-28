# SPEC: system-health-self-heal.sh 修复 — 自愈闭环完整化

## 背景

`scripts/system-health-check/system-health-self-heal.sh` 是系统健康自修复 cron 脚本（每小时，no_agent）。
审查发现修复逻辑存在 6 个缺陷，其中最严重的是**修复后不重新验证恢复**（检测→修复→验证→记录的闭环缺失）。

## 文件清单

| 文件 | 改动 |
|------|------|
| `scripts/system-health-check/system-health-self-heal.sh` | 修复逻辑增强 |

## 修复项

### P0-1: 修复后重新验证恢复（核心闭环）

**现状**: `systemctl restart` 后只检查 `systemctl is-active`（进程存活），不验证服务是否真正健康。
Bifrost 进程活着但 API 挂、SAG 进程活着但 401 等场景会误报「修复成功」。

**方案**: 重启后重新跑一次 `health-check-all.py` 的对应服务检查，对比状态。
- 新增 `verify_service_health <svc>` 函数：重启后重跑 health-check-all.py，读取该服务 status
- 修复成功判定 = 服务 active **且** 健康检查通过
- 若重验仍 fail → `push_manual "recheck_failed:<svc>"`

### P0-2: 启动等待轮询（替代 sleep 3）

**现状**: `sleep 3` 后立即查 is-active，启动慢的服务（gateway ~30s）必然误判。

**方案**: 轮询 `systemctl is-active`，最多 60s，每 3s 一次：
```bash
for i in $(seq 1 20); do
    systemctl is-active --quiet "$unit" && break
    sleep 3
done
```

### P0-3: D6/D7 增加修复动作

**现状**: D5 检测到 sag-mcp-bridge 挂会 restart，但 D6（bifrost_llm）/D7（hindsight_recall）非 ok 时只标记 warn，无修复。

**方案**: D6/D7 检测非 ok 时调用与 Step C 相同的修复逻辑：
- bifrost_llm 非 ok → `systemctl restart bifrost`（带 rate limit + 重验）
- hindsight_recall 非 ok → `systemctl restart hindsight-daemon`（带 rate limit + 重验）

### P1-4: WARN_SERVICES 修复策略

**现状**: Step C 只对 FAILED_SERVICES 修复，WARN_SERVICES 完全跳过。

**决策**: warn 状态（进程活但 API 挂）是否该重启？
- 记录在案但**不自动重启**（warn 可能是瞬时抖动，重启有风险）
- 改为：warn 服务若持续 ≥2 轮（通过 ratelimit 文件记录连续 warn 次数）才触发修复
- 本轮实现：仅记录到 `needs_manual` 中提示「持续 warn」，不自动重启（保守策略）

### P1-5: restart 失败时 SIGKILL fallback

**现状**: `systemctl restart` 失败 → 直接 push_manual。

**方案**: 已知 Gateway 对 SIGTERM 可能不响应（graceful drain 卡住）。加 fallback：
```bash
if ! systemctl restart "$unit" 2>/dev/null; then
    systemctl kill --signal=SIGKILL "$unit" 2>/dev/null
    sleep 3  # systemd Restart=on-failure 自动拉起
fi
```
仅对已知 SIGTERM 卡顿的服务（hermes-gateway）启用，避免误杀其他服务。

### P1-6: 修复后刷新状态（D6/D7 读修复前旧数据）

**现状**: HEALTH_JSON 在 Step A 采集（修复前快照），Step C 修复后 D6/D7 仍读旧数据。

**方案**: 重验函数返回最新状态，D6/D7 优先用重验结果；若无重验则回退到原始快照。
- `verify_service_health` 返回 `(status, models_or_detail)`
- D6/D7 若该服务被修复过，用重验值

## 验收标准

1. `bash -n` 语法通过
2. 全绿时零通知（no news is good news）保持
3. 模拟 fail：注入假 FAILED_SERVICES → 观察 restart 动作 + 重验闭环
4. 模拟 D6 非 ok：确认触发 bifrost restart（带 rate limit）
5. 源码提交 git + 同步运行时 + 实跑验证

## 风险

- 自动 restart 可能影响在线服务（已有 10min rate limit 保护）
- SIGKILL fallback 仅限 hermes-gateway
- 重验会额外跑一次 health-check-all.py（~5s），可接受
