# Setup Guide — HermesProject

> 从零搭建 HermesProject 开发/运行环境的完整指南。
> 适用场景：新机器部署、环境重构、新增开发者入职。

---

## 目录

1. [前置条件](#1-前置条件)
2. [常驻服务安装](#2-常驻服务安装)
3. [Hermes Gateway 与插件](#3-hermes-gateway-与插件)
4. [全局环境变量](#4-全局环境变量)
5. [部署子项目](#5-部署子项目)
6. [注册 Cron 任务](#6-注册-cron-任务)
7. [验证清单](#7-验证清单)
8. [常见问题](#8-常见问题)

---

## 1. 前置条件

### 1.1 硬件与 OS

| 项目 | 要求 | 说明 |
|------|------|------|
| **操作系统** | Windows 11 + WSL2 (Ubuntu 22.04) | 生产环境运行在 WSL2 内 |
| **CPU** | x86_64, 4 核+ | Hermes Gateway 单进程，多核主要给并行 cron 和 LLM 调用 |
| **内存** | ≥ 8 GB（推荐 16 GB） | 11 GB 实测可运行全栈（PG + LiteLLM + Hindsight + Gateway + cron） |
| **磁盘** | ≥ 100 GB 可用 | ~874 GB 全量部署含模型缓存、PG 数据、Hermes 会话日志 |
| **GPU** | 可选（MX550 2GB 已验证） | 当前仅 CUDA 加速 ONNX 推理（Headroom），不参与 LLM 推理 |

### 1.2 Network

| 限制 | 解决方案 |
|------|---------|
| `raw.githubusercontent.com` 不可达 | 使用 Python `requests` 流式下载（`stream=True`） |
| HuggingFace 下载慢/不可达 | 设 `export HF_ENDPOINT=https://hf-mirror.com` |
| WSL2 DNS 大响应丢失 | 配置 `systemd-resolved`（`127.0.0.53`），设 `generateResolvConf=false` |
| Python pip 国内源 | 使用 `pypi.tuna.tsinghua.edu.cn` 镜像 |

### 1.3 软件依赖

| 软件 | 版本要求 | 用途 |
|------|---------|------|
| Python | ≥ 3.10 | 所有子项目运行环境 |
| PostgreSQL | ≥ 14 | Hindsight / 知识树 / 聚类 数据库 |
| pgvector 扩展 | ≥ 0.4.2 | 向量存储与检索 |
| Docker | 24+ | PG 容器（shared-postgres）、Dify 容器 |
| systemd | — | Hermes Gateway / Hindsight Daemon 服务管理 |
| Hermes Agent | 最新（CLI 可用） | Agent 运行时（含 Gateway、插件系统、cron 调度） |

---

## 2. 常驻服务安装

HermesProject 依赖 6 个常驻服务，请按顺序安装。

### 2.1 PostgreSQL（shared-postgres）

Hindsight / 知识树 / SAG 共用同一 PG 实例（端口 5434）。Dify 独立 PG（端口 5432）。

```bash
# 启动 shared-postgres（带 pgvector）
docker run -d --name shared-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5434:5432 \
  pgvector/pgvector:pg17

# 创建数据库
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -c \
  "CREATE DATABASE hindsight WITH ENCODING 'UTF8';"
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -c \
  "CREATE DATABASE knowledge_tree WITH ENCODING 'UTF8';"
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -c \
  "CREATE DATABASE sag_lite WITH ENCODING 'UTF8';"

# 安装 pgvector 扩展
for db in hindsight knowledge_tree sag_lite; do
  PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -d $db -c \
    "CREATE EXTENSION IF NOT EXISTS vector;"
done
```

> Dify 的 PG（5432）由 Dify 的 `docker-compose.yml` 管理，不在此列。

### 2.2 LiteLLM 网关（端口 4142）

所有 LLM 调用统一通过 LiteLLM 路由：

```bash
# 安装
pip install 'litellm[proxy]'

# 配置 ~/.env 添加 API Key
echo 'LITELLM_MASTER_KEY="sk-your-master-key"' >> ~/.hermes/.env
echo 'OPENAI_API_KEY="sk-your-openai-key-style"' >> ~/.hermes/.env

# 启动（推荐 systemd 管理）
cat > /etc/systemd/system/litellm-gateway.service << 'EOF'
[Unit]
Description=LiteLLM Gateway
After=network.target

[Service]
EnvironmentFile=/root/.hermes/.env
ExecStart=/root/.hermes/hermes-agent/venv/bin/litellm --port 4142 --config /root/.hermes/config.yaml
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable --now litellm-gateway.service
```

验证：`curl http://127.0.0.1:4142/health/liveliness`

### 2.3 Hindsight Daemon（端口 9177）

Agent 的记忆存储与检索服务：

```bash
# 克隆并配置
git clone https://github.com/nousresearch/hermes-hindsight.git /root/.hindsight
cp /root/.hindsight/daemon.env.example /root/.hindsight/daemon.env
# 编辑 daemon.env：设置 DB_URL、LLM_API_KEY、EMBEDDING_API_KEY

# systemd 服务
cat > /etc/systemd/system/hindsight-daemon.service << 'EOF'
[Unit]
Description=Hindsight Memory Daemon
After=network.target

[Service]
EnvironmentFile=/root/.hindsight/daemon.env
ExecStart=/root/.hindsight/venv/bin/python -m hindsight.server
Restart=always
User=root
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable --now hindsight-daemon.service
```

验证：`curl http://127.0.0.1:9177/health`

### 2.4 Hermes Gateway

```bash
# 安装 Hermes Agent（含 Gateway）
pip install hermes-agent

# 初始化配置
hermes setup
# 将在 /root/.hermes/ 下生成 config.yaml + .env

# systemd 服务
cat > /etc/systemd/system/hermes-gateway.service << 'EOF'
[Unit]
Description=Hermes AI Agent Gateway
After=litellm-gateway.service hindsight-daemon.service
Requires=litellm-gateway.service hindsight-daemon.service

[Service]
ExecStart=/root/.hermes/hermes-agent/venv/bin/hermes gateway
Restart=always
User=root
KillMode=process

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable --now hermes-gateway.service
```

### 2.5 可选服务

| 服务 | 端口 | 用途 | 是否必需 |
|------|------|------|---------|
| Axiom Wiki MCP | 4143 | 结构化知识库 | 是（知识导航需要） |
| Moon Bridge | 38440 | Codex++ 代理 → DeepSeek | 否（仅开发需要） |
| Dify | 5001 | 低代码 Workflow 编排 | 否（试验性质） |
| SAG MCP | — | SQL-RAG 检索系统 | 否（试验性质） |
| OpenClaw MCP | 8765 | Windows 远程控制 | 否 |

---

## 3. Hermes Gateway 与插件

### 3.1 配置 gateway.yaml

```yaml
# /root/.hermes/config.yaml 关键配置
gateway:
  port: 8080
  plugins:
    - knowledge-navigation   # pre_llm_call: LLM Router 三路注入
    - knowledge-tree-plugin  # post_llm_call: 增量学习
  providers:
    - name: custom
      model: s-deepseek-v4-flash
      api_base: http://127.0.0.1:4142/v1
      api_key: ...
```

### 3.2 安装插件

插件源码在本仓库的 `plugins/` 下，通过 `deploy.sh` 部署：

```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh deploy knowledge-navigation --yes     # 自动重启 gateway
./deploy/deploy.sh deploy knowledge-tree-plugin --yes     # 自动重启 gateway
```

### 3.3 注册 MCP 服务器

在 `config.yaml` 的 `mcp_servers` 段添加：

```yaml
mcp_servers:
  postgres:
    type: postgres
    url: postgresql://postgres:postgres@127.0.0.1:5434/hindsight
  filesystem:
    type: filesystem
    directories:
      - /root/.hermes
  axiom-wiki:
    type: command
    command: node /root/.hermes/scripts/axiom-wiki-mcp-sse.js
  codegraph:
    type: command
    command: /root/.local/bin/codegraph-mcp
  openclaw:
    url: http://127.0.0.1:8765
  windows-mcp:
    url: http://127.0.0.1:8000/sse
```

---

## 4. 全局环境变量

### 4.1 核心凭据

写入 `/root/.hermes/.env`：

```bash
# === LLM 路由 ===
LITELLM_MASTER_KEY=sk-litellm-master-key

# === Embedding / Rerank ===
SILICONFLOW_API_KEY=sf-your-key
HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY=$SILICONFLOW_API_KEY
HINDSIGHT_API_RERANKER_PROVIDER=rrf          # 或 siliconflow

# === 数据库 ===
KT_DB_URL=postgresql://postgres:postgres@127.0.0.1:5434/knowledge_tree
CLUSTERING_DB_URL=postgresql://postgres:postgres@127.0.0.1:5434/hindsight

# === Sensenova（图像生成等）===
SN_API_KEY=sn-your-key
SN_BASE_URL=https://api.sensenova.cn/v1
SN_CHAT_MODEL=sensenova-u1-fast

# === 飞书告警 ===
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_HOME_CHANNEL=oc_xxx
```

### 4.2 知识导航插件（knowledge-navigation）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KN_HINDSIGHT_URL` | `http://localhost:9177/v1/...` | Hindsight API |
| `KN_TIMEOUT_SECONDS` | `25` | 召回超时 |
| `KN_MIN_SCORE` | `0.6` | rerank 最低分数 |
| `KN_MAX_RESULTS` | `3` | 最多注入条数 |
| `KN_CB_THRESHOLD` | `3` | 熔断器阈值 |
| `KN_CB_COOLDOWN` | `120` | 熔断冷却时间（s） |
| `KN_ROUTER_MODEL` | `sensenova-6.8-flash-lite` | Router 模型 |
| `KN_ROUTER_API_URL` | `http://127.0.0.1:4142/v1` | Router LLM 端点 |

完整列表见 `plugins/knowledge-navigation/src/knowledge_navigation/config.py`。

### 4.3 知识树插件（knowledge-tree-plugin）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KT_PLUGIN_SRC` | — | 插件源码路径（开发模式） |
| `KT_DB_URL` | — | 知识树 PG 连接串（必需） |

### 4.4 聚类分析（clustering-analysis-v3）

见 `scripts/clustering-analysis-v3/config/default.yaml`。必需环境变量：

| 变量 | 说明 |
|------|------|
| `CLUSTERING_DB_URL` | PG 连接串（`hindsight` 库） |
| `LITELLM_MASTER_KEY` | LLM 路由认证 |
| `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY` | Embedding API Key |

### 4.5 AI 报告系统

所有变量以 `AI_REPORT_` 为前缀，完整列表见 `scripts/ai-report-system/README.md`。

---

## 5. 部署子项目

从本仓库（`/mnt/d/HermesProject`）部署到运行环境（`/root/.hermes/`）：

```bash
# 查看所有可部署项目
./deploy/deploy.sh list

# 预览变更（不动文件）
./deploy/deploy.sh plan <project>

# 一键部署（加 --yes 跳过确认）
./deploy/deploy.sh deploy <project> --yes
```

### 部署顺序（推荐）

| 步骤 | 项目 | 说明 |
|------|------|------|
| 1 | `cron-common` | 公共 cron 工具库（flock/日志/通知） |
| 2 | `knowledge-navigation` | 必装插件，自动重启 gateway |
| 3 | `knowledge-tree-plugin` | 必装插件，自动重启 gateway |
| 4 | `memory-cleanup` | 每日记忆清理 |
| 5 | `clustering-analysis-v3` | 每日聚类分析 |
| 6 | `daily-learn` | 每日在线学习 |
| 7 | `knowledge-tree-builder` | 知识树管线 |
| 8 | `drawio-generator` | 矢量图生成 |
| 9 | `skillopt-runner` | 技能增量优化 |
| 10 | `system-health-check` | 系统巡检 |
| 11 | `ai-report-system` | AI 报告生成 |
| 12 | `self-evolving` | 自进化研究 |
| 13 | `cron-wrappers` | cron 包装脚本 |
| 14 | `skillopt-sleep` | 技能优化引擎 |

> 部署系统支持回滚：`./deploy/deploy.sh rollback <project>`
> 回滚机制：先按 `.deployed-files` 删除本次部署的所有文件，再按 `.backed-up-files` 还原备份。

---

## 6. 注册 Cron 任务

Hermes 内置 cron 调度器。注册方式：

```bash
# 创建 cron job
cronjob action=create \
  name="memory-cleanup-daily" \
  schedule="0 13 * * *" \
  script="memory-cleanup/daily_dryrun.sh" \
  workdir="/root/.hermes/scripts/memory-cleanup" \
  no_agent=true

# 查看已注册任务
cronjob action=list
```

### 排程总表（10 个活跃任务）

| 时间 | 任务 | 脚本 |
|------|------|------|
| 工作日 08:00 | 系统健康巡检 | `health-check-cron.sh` |
| 工作日 09:00 | 每日在线学习 | `daily-learn/daily_learn.sh` |
| 周一 10:00 | 聚类分析 | `clustering-analysis-cron.sh` |
| 周一 11:00 | 知识树维护 | `knowledge-tree-consolidate.sh` |
| 周一 12:00 | 知识导航基线 | `knowledge-navigation-baseline.sh` |
| 每日 13:00 | 记忆清理干跑 | `memory-cleanup/daily_dryrun.sh` |
| 周六 09:00 | k_vector 兜底 | `knowledge-tree-kvector-maintenance.sh` |
| 周日 09:00 | 深度研究 | agent 驱动 |
| 每日 15:00 | SkillOpt 优化 | `skillopt-nightly-run.sh` |
| 一次性 | 论文投稿提醒 | agent 驱动 |

完整配置见 `scripts/cron-wrappers/cron-jobs-config.md`。

---

## 7. 验证清单

### 7.1 服务健康检查

```bash
# 1. LiteLLM 网关
curl http://127.0.0.1:4142/health/liveliness

# 2. Hindsight Daemon
curl http://127.0.0.1:9177/health

# 3. Hermes Gateway
systemctl is-active hermes-gateway.service

# 4. PostgreSQL
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -c "\l"

# 5. 一键巡检
/root/.hermes/scripts/system-health-check/health-check-all.py
```

### 7.2 插件加载验证

```bash
# 查看 Gateway 日志确认插件已注册
journalctl -u hermes-gateway.service --since "5 min ago" | grep -i "knowledge-navigation\|knowledge-tree"

# 插件 trace 日志
tail -f /root/.hermes/plugins/knowledge-navigation/trace.log | grep router_mask
```

### 7.3 测试运行

```bash
# 每个子项目都有测试套
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3
PYTHONPATH=src python3 -m pytest tests -v

cd /mnt/d/HermesProject/plugins/knowledge-navigation
pip install -e . && pytest

cd /mnt/d/HermesProject/scripts/memory-cleanup
PYTHONPATH=src python3 -m pytest tests -v
```

### 7.4 端到端验证

```bash
# 发送测试消息给 Hermes Gateway
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"message": "测试消息", "session_id": "setup-test"}'

# 查看 trace 确认 pre_llm_call 正确注入
grep "router_mask\|recall_empty\|inject" /root/.hermes/plugins/knowledge-navigation/trace.log
```

---

## 8. 常见问题

### 8.1 LiteLLM 启动卡住

**现象**：`systemctl start litellm-gateway` 长时间无响应。
**原因**：WSL2 DNS 代理不支持 TCP DNS → Python 库 DNS 查询阻塞。
**解决**：

```bash
# 使用 systemd-resolved 替代 WSL 自动 DNS
sudo sed -i 's/^nameserver.*/nameserver 127.0.0.53/' /etc/resolv.conf
sudo resolvectl dns eth0 119.29.29.29 151.202.1.2
# 关闭 WSL 自动 resolv.conf 生成（/etc/wsl.conf）
echo -e "[network]\ngenerateResolvConf=false" | sudo tee -a /etc/wsl.conf
```

### 8.2 PostgreSQL 连接失败

```bash
# 检查容器运行状态
docker ps --filter "name=shared-postgres" --format "{{.Names}}: {{.Status}}"

# 检查端口
ss -tlnp | grep 5434

# 测试连接
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -c "SELECT 1;"
```

### 8.3 Gateway 无法加载插件

```bash
# 确认插件目录存在且 plugin.yaml 有效
ls -la /root/.hermes/plugins/knowledge-navigation/plugin.yaml

# 清除 __pycache__
find /root/.hermes/plugins/knowledge-navigation -name __pycache__ -exec rm -rf {} + 2>/dev/null

# 重启 Gateway
systemctl restart hermes-gateway.service
journalctl -u hermes-gateway.service -n 50 --no-pager
```

### 8.4 部署 manifest 展开为空

```bash
# 检查 glob 模式是否匹配源目录
cd /mnt/d/HermesProject
ls scripts/memory-cleanup/src/memory_cleanup/*.py  # 确认文件存在
cat deploy/manifests/memory-cleanup.manifest        # 确认 glob 模式正确
./deploy/deploy.sh plan memory-cleanup              # 预览
```

### 8.5 Disk 空间不足

```bash
# 检查使用率
df -h /

# 清理 PG 旧数据
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -d hindsight \
  -c "DELETE FROM sessions WHERE created_at < NOW() - INTERVAL '90 days';"

# 清理 cron 输出
find /root/.hermes/cron/output -name "*.log" -mtime +30 -delete

# 清理 Hermes session 文件
hermes session cleanup --older-than 30d
```

---

> 最后更新: 2026-06-28
> 有问题请查看各子项目 README 或 `docs/` 目录下的架构文档。