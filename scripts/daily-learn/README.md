# daily-learn

每日在线学习脚本。在工作日 09:00 自动从 GitHub Trending 和 ArXiv 采集 AI/CS 技术文章与论文，经 LLM 提取知识点后写入知识树。

## 功能描述

- **ArXiv 论文收集** — 自动拉取 cs.AI 和 cs.LG 分类的最新论文（最多 5 篇），提取标题、摘要、作者信息，写入临时 markdown 文件
- **GitHub Trending 收集** — 搜索 AI/LLM 主题热门仓库（按 stars 排序，最多 3 个），记录名称、描述、星级和链接
- **知识树入库** — 将收集到的 markdown 文件输入 `knowledge-tree-builder.cli`，经 LLM 提取知识要点后写入知识树数据库
- **失败重试与通知** — 每个步骤独立跟踪状态，执行完成后通过飞书发送汇总通知

## 管线流程

```
采集 ──┬→ ArXiv API (cs.AI, cs.LG) → markdown
       │
       └→ GitHub API (topic:ai+llm) → markdown
                │
                ▼
         LLM 提取知识点
                │
                ▼
         knowledge-tree-builder.cli
            (--merged 去重合并)
                │
                ▼
         知识树数据库 (PostgreSQL)
```

整个管线为三段式：
1. **采集** — 调用外部 API 获取原始数据，写入临时目录
2. **提取** — knowledge-tree-builder 的 LLM 管线提取结构化知识点
3. **入库** — 去重后写入知识树数据库（通过 `--merged` 标志启用合并模式）

## 配置与环境变量

脚本读取 `/root/.hermes/.env`（不硬编码密钥），要求以下环境变量：

### 必需

| 变量 | 用途 |
|------|------|
| `KT_DB_URL` | 知识树 PostgreSQL 数据库连接串 |
| `LITELLM_MASTER_KEY` | LiteLLM 网关认证密钥 |

### 可选

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `GITHUB_TOKEN` | 无 | GitHub API 令牌（提升搜索速率限额） |
| `FEISHU_CHAT_ID` | 无 | 飞书通知目标群 chat_id（未配置则跳过飞书通知） |
| `FEISHU_WEBHOOK_URL` | 无 | 飞书 Webhook 地址（lark-cli 不可用时的降级通道） |
| `CRON_LOG_DIR` | `/root/.hermes/logs/cron` | 日志输出目录 |
| `CRON_LOCK_DIR` | `/tmp/hermes-cron-locks` | flock 锁文件目录 |
| `CRON_RETRY_MAX` | 2 | 失败步骤的最大重试次数 |
| `CRON_RETRY_DELAY` | 30 | 首次重试前等待秒数（后续指数退避） |
| `CRON_STATE_DIR` | `/root/.hermes/lib/cron-state` | 状态文件目录 |

不要在脚本中硬编码数据库连接串或密钥。

## Cron 排期

| 项目 | 值 |
|------|------|
| 调度表达式 | `0 9 * * 1-5` |
| 执行频率 | 工作日（周一至周五）09:00 |
| 脚本路径 | `daily-learn/daily_learn.sh`（相对 `/root/.hermes/scripts/`） |
| 工作目录 | `/root/.hermes/scripts/daily-learn` |
| 类型 | `no_agent`（纯脚本执行，不消耗 LLM token） |

## 部署方式

```bash
# 从源码部署/更新到 WSL 运行环境
./deploy/deploy.sh deploy daily-learn --yes

# 预览待部署文件（不动文件系统）
./deploy/deploy.sh plan daily-learn

# 查看部署历史
./deploy/deploy.sh history daily-learn

# 回滚到上一个版本
./deploy/deploy.sh rollback daily-learn
```

部署清单（`manifests/daily-learn.manifest`）覆盖 `*.sh` 和 `README.md`，排除 `__pycache__/`。

## 依赖项

| 依赖 | 用途 | 备注 |
|------|------|------|
| **PostgreSQL**（知识树数据库） | 存储提取后的知识点 | 通过 `KT_DB_URL` 连接，需 `pgvector` 扩展 |
| **LiteLLM 网关** | LLM API 路由 | 本地网关 `127.0.0.1:4142`，为知识提取提供模型调用 |
| **knowledge-tree-builder** | 知识点提取与入库管线 | 部署于 `/root/.hermes/scripts/knowledge-tree-builder/`，含独立 venv |
| **cron_common.sh**（cron-common 项目） | 公共 cron 函数库 | 提供 flock 防重入、彩色日志、飞书通知、状态跟踪 |
| **Python 3** | 执行 API 拉取脚本 | 使用标准库 `urllib` + `xml.etree.ElementTree`，无额外依赖 |
| **网络连通性** | 访问 ArXiv/GitHub API | 需能直连 `export.arxiv.org` 和 `api.github.com` |

## 目录结构

```
scripts/daily-learn/
├── daily_learn.sh         # 主执行脚本
├── README.md              # 本文件
└── weekly-reports/        # 每周深度研究输出目录（由另立的 agent cron 任务写入）
```

## 输出与通知

- 临时采集文件写入 `mktemp -d /tmp/daily-learn-XXXX`，执行完成后自动清理
- 所有日志记录至 `CRON_LOG_DIR/daily-learn-YYYYMMDD.log`
- 执行完毕后通过飞书推送汇总通知（步骤清单 + 耗时）
- 状态文件写入 `CRON_STATE_DIR/daily-learn.json`，供下游检测任务消费