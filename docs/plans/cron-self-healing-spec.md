# SPEC: Cron 自愈框架 — 三层 Detection → Auto-heal → Human 闭环

**项目**: cron-common + cron-wrappers
**版本**: v2
**状态**: 草稿

## 0. 核心逻辑

```
          Layer 1 (Detection)                 Layer 2 (Auto-heal)           Layer 3 (Human)
     ┌──────────────────────────┐     ┌──────────────────────────┐    ┌──────────────────────┐
     │ 两种触发：                │     │ 三层自处理（按优先级）：   │    │ 人工兜底条件：         │
     │                          │     │                          │    │                      │
     │ ① 开机检测               │ →   │ ① Inline 重试             │ →  │ Layer 2 重试耗尽      │
     │    (systemd oneshot)     │     │    (exponential backoff)  │    │  且                  │
     │ ② 周期检测               │     │ ② Scheduler catch-up      │    │ Layer 1 持续检测到失败 │
     │    (每 30min cron job)   │     │    (gateway 启动后的 tick) │    │                      │
     │                          │     │ ③ 下个 scheduled tick     │    │ 人工收到 8 字段证据链  │
     │ 输出：谁错过了/谁失败了   │     │    (自然重试)              │    │                      │
     │ 谁重试用尽进入人工        │     │                          │    │ 修复 → 调用 repair 脚本│
     └──────────────────────────┘     └──────────────────────────┘    └──────────────────────┘
```

## 1. 验收标准

| # | 标准 | 测量方式 |
|---|------|---------|
| AC1 | 机器关机>2h 后重启，Layer 1 检测到并在飞书发出追赶报告 | 手动关机→重启→检查飞书 |
| AC2 | job 执行失败时自动重试 N 次+exponential backoff，log 可见 | 让脚本第一遍 exit 1，监控 log |
| AC3 | 重试耗尽后 Layer 1 持续在周期检测中标记该 job 为"需人工介入" | 飞书收到含 8 字段证据链的兜底待处理通知 |
| AC4 | 修复根因后通过一条命令触发追赶重跑 | `bash cron-catchup-repair.sh memory-cleanup` |
| AC5 | 追赶执行的 job 在 log 和飞书中标记为 `⚡[catchup]`，与正常可区分 | 查看 log 和飞书消息 |
| AC6 | Layer 1 报告中能区分"错过了 N 个 tick" vs "执行失败" | 报告中有 skipped_tick 和 failed 两类条目 |

## 2. 改动顺序（从下往上：先有自愈能力，再做检测，再做人工入口）

```
Phase 1: Layer 2 自处理能力建设        ← 先 build
  ├── cron_common.sh 增强
  │   ├── cron_run_step_retry（exponential backoff 重试）
  │   ├── cron_init 的 catchup 模式标记
  │   └── cron_finish 写入状态文件
  └── 部署到运行时 + 验证

Phase 2: Layer 1 检测能力建设          ← 再 detect
  ├── cron-boot-detect.sh（开机检测，systemd oneshot）
  ├── cron-periodic-detect.sh（周期检测，每 30min cron job）
  └── 状态文件格式对齐 + 飞书通知

Phase 3: Layer 3 人工兜底入口建设       ← 最后 provide
  ├── cron-catchup-repair.sh（修复后追赶脚本）
  └── 联调测试三层链路
```

## 3. 代码变更

### 3.1 Phase 1 — Layer 2 自处理能力（cron-common 项目）

**文件**: `/mnt/d/HermesProject/scripts/cron_common.sh`

#### 3.1.1 cron_init 新增 catchup 模式标记

```bash
# 签名
cron_init "job-name" ["normal"|"catchup"]

# 默认 normal。传 "catchup" 时：
# - 所有 log 前缀从 [cron] 改为 ⚡[catchup]
# - cron_finish 的飞书标题从 [job-name] 改为 ⚡[job-name] (catchup)
# - 追加到 _STEP_RESULTS 时前缀带 ⚡
```

#### 3.1.2 失败重试函数 cron_run_step_retry

```bash
# 环境变量（可选）
CRON_RETRY_MAX=${CRON_RETRY_MAX:-2}          # 最大重试次数
CRON_RETRY_DELAY=${CRON_RETRY_DELAY:-30}      # 初始退避秒数

# 用法
cron_run_step_retry "步骤名" command args...
```

行为：
- 首次失败 → 等待 `CRON_RETRY_DELAY` 秒 → 重试
- 再次失败 → 等待 `CRON_RETRY_DELAY * 2` 秒 → 重试
- 重试耗尽 → 记 `❌ 步骤名 (after N retries)`，`OVERALL_STATUS=fail`
- 中间某次成功 → 记 `⚠️ 步骤名 (retry N/N 后恢复)`，`OVERALL_STATUS=partial`
- 每次重试 log: `[retry] 步骤名: 重试 1/2 在 30s 后...`

#### 3.1.3 状态文件写入（cron_finish 退出前）

```bash
# 写 /root/.hermes/lib/cron-state/<job-name>.json
# 格式：
{
  "job_name": "memory-cleanup",
  "status": "success|fail|partial",
  "cron_mode": "normal|catchup",
  "run_at": "2026-06-25T08:00:00+08:00",
  "elapsed_seconds": 120,
  "retries_used": 0,
  "overall_retries_exhausted": false,     # true = 重试耗尽，需人工介入
  "steps": [
    {"name": "步骤1", "status": "ok"},
    {"name": "步骤2", "status": "fail", "retries": 2, "retries_exhausted": true}
  ],
  "last_error": "HTTP 400 - invalid api key"   # 从 last_step 的 stderr 提取
}
```

此文件是 Layer 1 和 Layer 3 的数据来源。

#### 3.1.4 状态文件目录初始化

`cron_init` 中新增：
```bash
mkdir -p /root/.hermes/lib/cron-state
```

### 3.2 Phase 2 — Layer 1 检测能力（cron-wrappers 项目）

#### 3.2.1 cron-boot-detect.sh（开机检测）

**新建**: `/mnt/d/HermesProject/scripts/cron-wrappers/cron-boot-detect.sh`

**触发**: systemd oneshot `After=hermes-gateway.service`，延迟 30s

**逻辑**:
1. 读 `~/.hermes/cron/jobs.json`，遍历所有 enabled job
2. 对每个 job 计算：
   - `last_run_at` 距离现在的时间
   - 从排程推算理论上应该触发几次（cron 表达式用 croniter 推算，interval 直接除法）
   - 当前 `last_status` 是否为 error
3. 读 `~/.hermes/lib/cron-state/*.json`，检查是否有 `retries_exhausted=true`
4. 输出三分类报告：

```
⚡ 系统恢复报告 — 2026-06-25 08:30
关机窗口: 6/24 20:00 → 6/25 08:30 (12.5h)

【补跑成功】— Hermes scheduler 已自动处理
  ✅ memory-cleanup     补跑于 08:01 (missed 6/24 13:00)
  ✅ system-health-check 补跑于 08:01 (missed 6/25 08:00)

【错过未补跑】— fast-forward，下次正常排程
  ⚪ skillopt-nightly    上次 6/24 15:05, 下次 6/25 15:00 (未来)
  ⚪ daily-learn         上次 6/19, 下次 6/26 09:00 (missed 4 ticks)

【失败重试耗尽 — 需人工介入】
  ❌ memory-cleanup      6/23 14:07 error (已重试 2 次耗尽)
     ├ 失败原因: HTTP 400 - invalid api key
     ├ 首次失败: 6/23
     ├ Log: /root/.hermes/logs/cron/memory-cleanup-20260623.log
     └ 建议: 检查 SILICONFLOW_API_KEY 是否过期
```

#### 3.2.2 cron-periodic-detect.sh（周期检测）

**新建**: `/mnt/d/HermesProject/scripts/cron-wrappers/cron-periodic-detect.sh`

**触发**: Hermes cron job，每 30min 跑一次（`*:00` 和 `*:30`）

**逻辑**: 与 boot-detect 基本一致，但：
- 不检测"停机错过"（那是 boot 专属场景）
- 只检测 `last_status=error` 且 `retries_exhausted=true` 的 job
- 对**持续失败**的 job，每次检测到都发飞书告警（但去重：同一 job 同一失败原因 1h 内不重复告警）
- 如果**上次检测到的 error 已恢复**（last_status 变回 ok），发一条恢复通知：「✅ memory-cleanup 已恢复正常执行」

**为什么需要周期检测**：
- boot-detect 只在开机后触发一次
- 如果某个 job 在工作时间跑失败了，Layer 2 重试也耗尽了，需要 Layer 1 尽快发现并通知人工，不需要等到下一次开机

#### 3.2.3 飞书通知模板

所有 Layer 1 通知使用统一的飞书消息格式：

```
标题: [Cron 自愈] <状态emoji> <job名称> — <摘要>

正文:
━━━━━━━━━━━━━━━━━━━━
  状态: ❌ 失败重试耗尽
  Job: memory-cleanup
  时间: 6/25 13:00
  原因: HTTP 400 - invalid api key
  重试: 2 次已耗尽 (30s → 60s backoff)
  持续: 6/23 首次失败，至今未恢复
  Log: /root/.hermes/logs/cron/memory-cleanup-20260625.log
  Next: 6/26 13:00 (系统自动再试)
  建议: 检查 SILICONFLOW_API_KEY
━━━━━━━━━━━━━━━━━━━━
```

### 3.3 Phase 3 — Layer 3 人工兜底入口（cron-wrappers 项目）

#### 3.3.1 cron-catchup-repair.sh

**新建**: `/mnt/d/HermesProject/scripts/cron-wrappers/cron-catchup-repair.sh`

**职责**: 人工修复根因后，通过此脚本触发一次追赶执行。

```bash
# 用法
bash /root/.hermes/scripts/cron-catchup-repair.sh                    # 重跑所有 last_status=error 的 job
bash /root/.hermes/scripts/cron-catchup-repair.sh --job memory-cleanup  # 只重跑指定 job
bash /root/.hermes/scripts/cron-catchup-repair.sh --job memory-cleanup --force  # 强制重跑即使状态是 ok
```

**逻辑**:
1. 读 `~/.hermes/cron/jobs.json` 找到目标 job 的 job_id
2. 调用 `hermes cron run <job_id>` 触发执行
   - 如果 job 是 no_agent → 直接执行 script 路径（给 cron_run_step_retry 传 CRON_MODE=catchup）
   - 等待执行完成
3. 读最新的 `~/.hermes/lib/cron-state/<job>.json` 检查结果
4. 如果成功了 → 更新状态文件标记 `repair_success=true`，推送飞书恢复通知
5. 如果又失败了 → 推送"修复后仍失败"告警（升级告警级别）

**数据来源**：
- `~/.hermes/cron/jobs.json` — 获取 job_id、script、workdir
- `~/.hermes/lib/cron-state/<job>.json` — 获取上次失败时的 retries_exhausted、last_error

## 4. Layer 1 检测的两种触发时机会冲突吗？

不会。它们覆盖不同时间窗口：

| 触发 | 时机 | 检测范围 | 何时工作 |
|------|------|---------|---------|
| Boot-detect | 每次 gateway 启动后 30s | 停机错过 + 失败 | 刚开机 |
| Periodic-detect | 每 30min | 仅运行时失败（不含停机错过） | 正常运行中 |

两者独立不冲突：
- Boot-detect 只跑一次就退出
- Periodic-detect 只在停机错过场景什么都不做，等 30 分钟后自动开始周期检测

如果开机后 5 秒就触发了一次 Periodic-detect，它和 Boot-detect 的检测范围不重叠（periodic 不报停机错过），所以报告不会重复。

## 5. 状态文件完整数据流

```
step_a: 正常执行 → cron_finish → 写 state.json {status:success, retries_exhausted:false}
  ↑ Layer 1 period-detect 读到后标记为正常，跳过

step_b: 失败+重试恢复 → cron_finish → 写 state.json {status:partial, retries_used:1}
  ↑ Layer 1 读到后标记为"已自恢复"，不触发人工

step_c: 失败+重试耗尽 → cron_finish → 写 state.json {status:fail, retries_exhausted:true}
  ↓
Layer 1 period-detect 读到 → 发现 retries_exhausted=true + last_status=error
  ↓
推送飞书【兜底待处理】（8字段证据链）
  ↓
Layer 3 人工：检查证据链 → 修复根因 → 执行 cron-catchup-repair.sh
  ↓
修复重跑成功 → state.json 自动变为 {status:success, retries_exhausted:false}
  ↓
Layer 1 next period-detect 读到 → 推送恢复通知
```

## 6. 部署清单

### cron-common 项目（变更 `cron_common.sh`）

| 变更 | 文件 | 类型 |
|------|------|------|
| cron_init 新增 catchup 参数 | `cron_common.sh` | 修改 |
| 新增 cron_run_step_retry 函数 | `cron_common.sh` | 新增 |
| cron_finish 新增状态文件写入 | `cron_common.sh` | 修改 |
| cron_init 新增 state 目录创建 | `cron_common.sh` | 修改 |

### cron-wrappers 项目（新增 3 个脚本）

| 文件 | 用途 | manifest |
|------|------|----------|
| `cron-boot-detect.sh` | 开机检测 | 新增 |
| `cron-periodic-detect.sh` | 周期检测 | 新增 |
| `cron-catchup-repair.sh` | 修复触发追赶 | 新增 |

### systemd service（手动安装）

| 文件 | 位置 |
|------|------|
| `cron-boot-detect.service` | `/etc/systemd/system/cron-boot-detect.service` |

### Hermes cron job（新增）

| job | 排程 | script |
|-----|------|--------|
| cron-periodic-detect | `*/30 * * * *` | `cron-periodic-detect.sh` |

## 7. 回滚

```bash
# 回滚 cron_common.sh
cd /mnt/d/HermesProject && git revert HEAD -- scripts/cron_common.sh
./deploy/deploy.sh deploy cron-common

# 删除新增脚本
rm /root/.hermes/scripts/cron-boot-detect.sh
rm /root/.hermes/scripts/cron-periodic-detect.sh
rm /root/.hermes/scripts/cron-catchup-repair.sh
./deploy/deploy.sh deploy cron-wrappers
cronjob action=remove job_id=cron-periodic-detect

# 卸载 systemd
sudo systemctl disable cron-boot-detect.service
sudo rm /etc/systemd/system/cron-boot-detect.service
sudo systemctl daemon-reload
```

## 8. 不做

- ❌ 不做 PG 持久化（状态文件 JSON 已够用）
- ❌ Layer 1 不做追赶执行（追赶是 Layer 2 的事 — Hermes scheduler 已处理）
- ❌ 不做 agent-mode cron job 的追赶（一次性任务不受影响）
- ❌ 不做 web 面板
- ❌ Periodic-detect 不做停机错过检测（那是 boot-detect 的职责）
