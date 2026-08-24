# SPEC: 系统健康归口自愈 — system-health-self-heal

## 现状问题

开机自检和健康巡检分散在 5 个地方，互相冗余：

| 组件 | 类型 | 频率 | 问题 |
|------|------|------|------|
| cron-boot-detect.service | systemd oneshot | 开机一次 | 与 Hermes 自身补跑竞速，误报 |
| health-check.timer + .service | systemd timer | 开机 3min 后 | 待基础设施就绪后才能跑，但无依赖等待 |
| cron-periodic-detect | Hermes cron, no_agent | 每h | 只检查不修复，报告噪音 |
| kn-router-health-check | Hermes cron, no_agent | 每天14h | 只检查不修复，频率低 |
| flywheel-watchdog | Hermes cron, no_agent | 每5min | 检查频率过高，与 flywheel-health-report 日报冗余 |

所有检测都是 **shell 脚本驱动**，只能报告问题，不能自动修复。

此外 health-check-all.py 遗漏了以下关键检查项：
- smb-mounts.service（/mnt/c /mnt/d CIFS 挂载）
- local-embedding-gpu.service（GPU embedding 服务）
- axiom-wiki-mcp-sse.service（Wiki MCP SSE 端口）
- wsl-keepalive.service（WSL 防回收）
- codegraph bind mount 挂载点
- postgres-mcp / axiom-wiki SSE 端口连通性
- Bifrost LLM 可用性（不只看容器健康，要看 /v1/models 能不能用）
- Hindsight recall 可用性（不只看进程在不在，要看 /health 返回 healthy）

## 方案

### 核心思路

**两件事：**

1. **systemd 层不动** — 各服务并行启动互不阻塞。Gateway 有 `Restart=always` + fallback provider 链，自己会重试。
2. **砍掉 5 个冗余检测 + 补全遗漏项 → 归口为 1 个 agent-driven 巡检 job** — `system-health-self-heal`，每小时跑一次。

### 为什么不需要开机触发

Hermes cron 的 `get_due_jobs` 在 Gateway 启动后第一次 tick 自动检查所有 job。如果 `system-health-self-heal` 是每小时 job，开机后首次 tick 发现它 due（上次关机后 next_run_at 是过去时间），立即补跑一次。**不需要 systemd 帮它触发。**

### 变更清单

#### 删除

| 项目 | 路径 | 操作 |
|------|------|------|
| cron-boot-detect.service | /etc/systemd/system/ | systemctl disable + rm |
| health-check.service | /etc/systemd/system/ | systemctl disable + rm |
| health-check.timer | /etc/systemd/system/ | systemctl disable + rm |
| cron-periodic-detect.sh | /root/.hermes/scripts/ | 从 deploy 移除 + deploy cron-wrappers 同步 |
| kn-router-health-check.sh | /root/.hermes/scripts/ | 从 deploy 移除 + deploy cron-wrappers 同步 |
| flywheel-watchdog 脚本 | /root/.hermes/scripts/flywheel-health-report/scripts/ | 从 deploy 移除 + deploy flywheel-health-report 同步 |
| system-health-check 项目 | /root/.hermes/scripts/health-check-all.py + health-check-run.py | **保留**（agent 调用 health-check-all.py 获取结果） |

#### 创建的 Hermes cron job

| 字段 | 值 |
|------|-----|
| name | system-health-self-heal |
| schedule | 0 * * * *（每小时整点） |
| no_agent | false（agent-driven） |
| deliver | 默认（origin）— 现阶段每次执行都推飞书；后期可一键静默 |
| prompt | 见下方 agent prompt |
| skills | 不需要 |

#### 保留不变

- memory-cleanup-daily
- 知识树维护每日
- 知识树k_vector每周兜底
- 每日在线学习
- 每周深度研究-知识树学习
- skillopt-nightly-run
- self-evolving-nightly
- dream-daily
- flywheel-health-report（每天 8am 日报，保留，但**删除其 watchdog 脚本**）

### Agent Prompt（system-health-self-heal 的 prompt 内容）

```
你是系统自愈 Agent。执行以下步骤，输出 JSON 到 /root/.hermes/logs/cron/system-health-self-heal-latest.json：

## 步骤 1：基础设施健康检查 + 自愈

### 1a. 运行 health-check-all.py 获取基础检查
运行 `python3 /root/.hermes/scripts/health-check-all.py`，输出为 JSON。
它检查以下服务（已有）：
- hermes（进程 + Bifrost API 端口 4142 连通性）
- bifrost（Docker 容器 + 健康状态 + 模型数）
- hindsight（进程 + 健康 endpoint + PG 连接）
- sag（进程 + 健康 endpoint）
- postgres（Docker 容器 + PG 连接）
- dashboard（进程 + 端口 9119）
- mcp（6 个 server 进程存活：axiom-wiki/postgres/codegraph/sag/windows-mcp/cognee）
- memory_files（MEMORY.md / USER.md 状态）
- orphans（stale pid 文件）

### 1b. 补充检查 health-check-all.py 遗漏的项
agent 额外执行以下检查，每项都直接决定系统能否正常使用：

| 检查项 | 检查方式 | 异常时的修复动作 |
|--------|---------|----------------|
| smb-mounts.service 活跃 | systemctl is-active smb-mounts.service | systemctl restart smb-mounts.service |
| /mnt/c 挂载可用 | mountpoint /mnt/c | 同上 |
| /mnt/d 挂载可用 | mountpoint /mnt/d | 同上 |
| codegraph bind mount 挂载 | mountpoint /mnt/d/HermesProject/.codegraph | 同上 |
| local-embedding-gpu.service 活跃 | systemctl is-active local-embedding-gpu.service | systemctl restart local-embedding-gpu.service |
| axiom-wiki-mcp-sse.service 活跃 | systemctl is-active axiom-wiki-mcp-sse | systemctl restart axiom-wiki-mcp-sse |
| wsl-keepalive.service 活跃 | systemctl is-active wsl-keepalive | systemctl restart wsl-keepalive |
| postgres-mcp SSE 端口通 | ss -tlnp | grep -q :4145 | systemctl restart postgres-mcp |
| axiom-wiki SSE 端口通 | ss -tlnp | grep -q :4143 | systemctl restart axiom-wiki-mcp-sse |
| Bifrost LLM 可用（关键） | curl http://127.0.0.1:4142/v1/models 返回200+有模型 | docker restart bifrost |
| Hindsight recall 可用（关键） | curl http://127.0.0.1:9177/health 含 healthy | systemctl restart hindsight-daemon |
| postgres Docker 容器（如 health-check-all 报 fail） | docker ps --filter name=shared-postgres --format '{{.Status}}' | docker restart shared-postgres |

**Rate limit**：同一服务 10 分钟内不重复重启。读 /root/.hermes/lib/cron-state/self-heal-ratelimit.json 判断。

## 步骤 2：Cron job 状态检查 + 补跑
读 /root/.hermes/cron/jobs.json，检查所有 enabled job 的 last_status（排除自身 system-health-self-heal）。

对 last_status="error" 的 job：
1. 先确认步骤 1 的基础设施是否全部 OK。如果基础设施还有异常未修复成功，先记录 job 为"等待基础设施恢复"，跳过补跑（补跑了也会失败）。
2. 基础设施 OK 后，执行 `hermes cron run <job_id>` 补跑一次。
3. 补跑成功 → 标记为 "healed"。
4. 补跑失败 → 标记为 "needs_manual"，在报告中明确列出 job 名、失败原因。

注意：Hermes cron 源码已有 stale error recovery 机制（_job_is_stale_error_recurring），
会在下个 tick 自动重新调度 error 状态的 recurring job。agent 做的是主动补跑加速恢复，
不是替代源码的自愈逻辑。

## 步骤 3：报告输出
输出 JSON 到 /root/.hermes/logs/cron/system-health-self-heal-latest.json，格式：
{
  "timestamp": "ISO 时间",
  "infra": {
    "each_service": "ok/fail/healed",
    "health_check_all": "完整 JSON 输出",
    "extra_checks": {
      "smb_mounts": "ok/fail/healed",
      "mnt_c": "ok/fail",
      "mnt_d": "ok/fail",
      "codegraph_bind_mount": "ok/fail",
      "local_embedding_gpu": "ok/fail/healed",
      "axiom_wiki_mcp_sse": "ok/fail/healed",
      "wsl_keepalive": "ok/fail/healed",
      "postgres_mcp_port": "ok/fail",
      "axiom_wiki_port": "ok/fail",
      "bifrost_llm": "ok/fail",
      "hindsight_recall": "ok/fail"
    }
  },
  "cron_jobs": {
    "each_failed_job": {
      "status": "healed / needs_manual / waiting_infra",
      "error": "失败原因"
    }
  },
  "actions_taken": ["重启了 hindsight", "修复了 bind mount"],
  "needs_manual": true/false,
  "summary": "一句话摘要"
}

## 通知规则
- 现阶段：每次执行都推飞书（包括全正常），便于观察运行情况
- 后期一键静默：将下方"全正常"改为以 [SILENT] 开头即可
- 有修复动作（成功或失败）→ 推飞书报告，标题含修复结果
- 补跑失败的 job → 在飞书通知中明确标记"需人工介入"，列出 job 名和失败原因
- 全正常 → 推飞书简报："✅ system-health-self-heal 正常 — 全部服务 OK，N 个 cron job 正常"
- 有需人工介入项 → 推飞书时标题加 ⚠️ 标记并列出具体项
```

### 验收标准

1. `systemctl disable cron-boot-detect.service health-check.service health-check.timer` 后，开机不再报 Failed to start
2. `hermes cron run system-health-self-heal` 手动触发后，agent 完成全量检查 + 自愈尝试
3. 全正常时推送飞书简报（现阶段不静默，后期可一键改 [SILENT]）
4. 有修复时推送飞书，标题含修复结果
5. 开机后第一次 tick 自动补跑（无需手动触发）
6. 目录 /root/.hermes/logs/cron/system-health-self-heal-latest.json 可读，包含全部 11 项额外检查结果
7. flywheel-health-report 日报仍正常（每天 8am）

### 实施步骤

```
1. 修改 HermesProject 源码
   - 删除 /mnt/d/HermesProject/scripts/cron-wrappers/cron-periodic-detect.sh
   - 删除 /mnt/d/HermesProject/scripts/cron-wrappers/kn-router-health-check.sh
   - 删除 /mnt/d/HermesProject/scripts/flywheel-health-report/scripts/flywheel-watchdog.sh
   - 更新 cron-jobs-config.md：移除已删 job 条目，新增 system-health-self-heal
   - manifest 和 deploy 脚本不需要改（glob 匹配，文件删除后自动不部署）

2. 部署到 /root/.hermes/
   - ./deploy/deploy.sh deploy cron-common --yes
   - ./deploy/deploy.sh deploy cron-wrappers --yes    ← 同步删除目标目录中的旧脚本
   - ./deploy/deploy.sh deploy system-health-check --yes
   - ./deploy/deploy.sh deploy flywheel-health-report --yes  ← 同步删除 watchdog 脚本

3. 删除 systemd 单元
   - systemctl disable --now cron-boot-detect.service
   - systemctl disable --now health-check.service
   - systemctl disable --now health-check.timer
   - rm /etc/systemd/system/cron-boot-detect.service
   - rm /etc/systemd/system/health-check.service
   - rm /etc/systemd/system/health-check.timer
   - systemctl daemon-reload

4. 删除旧 Hermes cron jobs + 创建新 job

4a. 先查要删的 job 的 id:
    hermes cron list | grep -E 'cron-periodic-detect|知识导航 Router|flywheel-watchdog'
    记下每个 job 的 id（如 a14f893aa7c7），然后 `hermes cron remove <id>`
    （注意：hermes cron remove 需要 job_id，不是 name）

4b. 创建新 job（不指定 deliver，默认 origin 自动推飞书）:
    hermes cron create "0 * * * *" --name system-health-self-heal \
      --prompt "$(cat prompt 内容)"

5. 验证
   - hermes cron list | grep system-health-self-heal
   - 手动触发: hermes cron run system-health-self-heal
   - 检查飞书是否收到推送（包括全正常简报）
   - 检查输出: cat /root/.hermes/logs/cron/system-health-self-heal-latest.json
   - 确认旧脚本已从运行时移除
   - 确认 flywheel-health-report 日报仍正常
```