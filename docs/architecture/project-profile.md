# HermesProject 项目基础信息

> 完整准确的项目基础信息，用于实现阶段参考，一次性获取所有依赖和环境配置。

---

## 1. 项目概述

**项目名称**：HermesProject
**项目位置**：
- 源码（Windows）：`D:\HermesProject\`
- WSL 路径：`/mnt/d/HermesProject/`
- 部署目标（WSL）：`/root/.hermes/`

**核心定位**：
Hermes Agent 的插件和脚本项目仓库，为 Hermes 提供增强能力：
- 知识导航插件：对接 Hindsight + 知识树，提升召回质量
- 自进化框架：SE-Agent 三层进化
- SkillOpt：技能文档自动优化
- 各类运维/数据处理脚本

**依赖外部服务**：
- Hermes Agent：核心平台，运行插件和脚本
- Hindsight RAG：独立深度记忆服务（共享 PostgreSQL）
- Axiom Wiki：独立结构化知识库（MCP 服务）
- LiteLLM：统一模型网关
- Dify：业务前端 RAG 平台（独立 PostgreSQL）
- Windows-MCP：Windows 宿主机 MCP 控制服务

---

## 2. 组件版本信息

| 组件 | 版本 | 部署位置 | 端口 |
|------|------|----------|------|
| Hermes Agent Gateway | latest (git HEAD) | `/root/.hermes/` | 8642 |
| Hindsight Daemon | latest | `/usr/local/bin/` | 9177 |
| Axiom Wiki MCP SSE | latest | `/root/.hermes/scripts/` | 4143 |
| LiteLLM | 1.83.14 | `/root/.local/share/litellm/` | 4142 |
| shared-postgres (Hindsight + LiteLLM) | 15 | Docker | 5434 |
| dify-postgres | 15 | Docker | 5432 |
| Dify | 0.10.0 | Docker | 80/443/5001/5003 |
| Weaviate (Dify) | v1.19.0 | Docker | 8080 |
| Redis (Dify) | 7.4.2 | Docker | 6379 |
| Moon Bridge | latest | `/root/moon-bridge/` | 38440 |
| OpenClaw MCP | latest |  | 8765 |
| Windows-MCP | 3.4.2 | Windows 宿主机 | 8000 |

---

## 3. 模型路由配置

**当前生效路由**（LiteLLM 网关统一调度）：

| 用途 | 模型 | 提供商 | 认证 | 备注 |
|------|------|--------|------|------|
| 主对话模型 | sensenova-6.7-flash-lite | SenseNova | SN_API_KEY / SN_BASE_URL | 日常对话、推理 |
| Hindsight LLM | s-deepseek-v4-flash | DeepSeek via SiliconFlow | SILICONFLOW_API_KEY | 生成、聚合 |
| Embedding | BAAI/bge-m3 | SiliconFlow | SILICONFLOW_API_KEY | 512 token 限制 |
| Reranker | rrf（当前） / BAAI/bge-reranker-v2-m3 | SiliconFlow | SILICONFLOW_API_KEY | 当前因 400 问题切为 rrf，备用 Qwen3-Reranker-0.6B |

**环境变量关键配置**：
```
# LiteLLM 主密钥
LITELLM_MASTER_KEY=sk-lit-xxx-2026

# Hindsight API 密钥
HINDSIGHT_API_LLM_API_KEY=sk-lit-xxx-2026

# 商汤 SenseNova
SN_API_KEY=sn-xxx
SN_BASE_URL=https://api.sensenova.cn/v1

# SiliconFlow（Embedding/Reranker/DeepSeek）
SILICONFLOW_API_KEY=sf-xxx
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1

# Hindsight 配置
HINDSIGHT_IDLE_TIMEOUT=0  # 禁用空闲超时，防止假卡死
```

---

## 4. 数据库分工

| 数据库 | 端口 | 用途 | 所有者 |
|--------|------|------|--------|
| shared-postgres | 5434 | Hindsight 记忆单元、LiteLLM 模型配置、未来调度器/部署系统状态表 | shared |
| dify-postgres | 5432 | Dify 业务数据 | Dify 独立 |

**shared-postgres 核心表**：
- `memory_units`：Hindsight 记忆单元
- `memory_links`：因果链接
- `unit_entities`：实体映射
- `knowledge_point`：知识树知识点
- `litellm_model`：LiteLLM 模型配置
- `litellm_spend`：Token 消费记录

---

## 5. 开发工作流

**必须严格遵守**：

```
1. 修改源码 → 在 D:\HermesProject\ 对应项目目录
2. 自测 → 在 WSL 测试基本功能
3. 提交代码 → Git 提交到仓库
4. 用户 review → 等待用户确认可部署
5. 执行部署 → cd /mnt/d/HermesProject && ./deploy/deploy.sh deploy <project> --yes
6. 验证 → 检查部署结果和服务状态
```

**关键规则**：
- ❌ 禁止直接修改 `/root/.hermes/` 下的运行时代码，会被下次部署覆盖
- ✅ 所有修改必须在 `D:\HermesProject\` 源码，通过 deploy 同步
- ✅ 小修改可直接部署，大修改需要先 review 再部署
- ✅ 部署前自动备份，支持一键回滚：`./deploy/deploy.sh rollback <project>`

---

## 6. 环境依赖清单

**必须预先安装的依赖**：

| 依赖 | 版本要求 | 安装位置 | 用途 |
|------|----------|----------|------|
| Python | 3.11+ | WSL 原生 | 运行脚本和插件 |
| Git | latest | WSL 原生 | 版本管理 |
| Docker | latest | WSL 原生 | 运行 Dify/PostgreSQL |
| Node.js | 18+ | WSL 原生 | MCP 服务器 |
| lark-cli | v1.0.31 | npm | 飞书操作（发送消息/文件） |
| pgvector | 0.4.2 | pip | PostgreSQL 向量扩展 |
| APScheduler | latest | pip | 定时调度（当前分散，未来统一） |
| click/typer | latest | pip | CLI 开发 |
| pyyaml | latest | pip | YAML 配置解析 |

---

## 7. 关键端口映射

| 端口 | 服务 | 协议 | 访问方式 |
|------|------|------|----------|
| 80 | Dify Nginx | TCP | WSL 外部可访问 |
| 443 | Dify Nginx | TCP | WSL 外部可访问 |
| 8000 | Windows-MCP | TCP SSE | WSL → Windows 宿主机 |
| 8642 | Hermes Gateway | HTTP | 本地 |
| 4142 | LiteLLM | HTTP | 本地 |
| 4143 | Axiom Wiki MCP | HTTP SSE | 本地 |
| 5432 | Dify PostgreSQL | TCP | Docker 内部 |
| 5434 | shared-postgreSQL | TCP | WSL 外部可访问 |
| 9177 | Hindsight API | HTTP | 本地 |
| 38440 | Moon Bridge | HTTP | 本地 |
| 8765 | OpenClaw MCP | HTTP | 本地 |
| 5001 | Dify API | TCP | Docker |
| 5003 | Dify Plugin Daemon | TCP | Docker |

---

## 8. 主要项目清单

### 插件项目（需要重启 hermes-gateway）

| 项目 | 作用 | 部署路径 |
|------|------|----------|
| knowledge-navigation | 知识导航：Hindsight + 知识树双路召回 + RRF 融合 + 熔断降级 | `/root/.hermes/plugins/knowledge-navigation/` |
| knowledge-tree-plugin | 知识树：对话中自动提取知识点入库 | `/root/.hermes/plugins/knowledge-tree-plugin/` |

### 脚本项目（无需重启服务）

| 项目 | 作用 | 部署路径 |
|------|------|----------|
| self-evolving | SE-Agent 三层自进化框架 | `/root/.hermes/scripts/self-evolving/` |
| knowledge-tree-builder | 知识树构建：批量从文档提取知识点 | `/root/.hermes/scripts/knowledge-tree-builder/` |
| clustering-analysis-v3 | 因果链聚类分析 Hindsight 记忆 | `/root/.hermes/scripts/clustering-analysis-v3/` |
| daily-learn | 每日在线学习定时任务 | `/root/.hermes/scripts/daily-learn/` |
| memory-cleanup | Hindsight 记忆去重/清理 | `/root/.hermes/scripts/memory-cleanup/` |
| ai-report-system | AI 调研报告生成 | `/root/.hermes/scripts/ai-report-system/` |
| drawio-generator | 架构图自动生成 | `/root/.hermes/scripts/drawio-generator/` |
| skillopt-runner | SkillOpt 技能优化运行器 | `/root/.hermes/skillopt-runner/` |
| skillopt-sleep | SkillOpt 睡眠优化引擎 | `/root/.hermes/skillopt-sleep/` |
| system-health-check | 系统健康巡检 | `/root/.hermes/scripts/system-health-check/` |

---

## 9. 定时任务配置（当前实际）

**调度系统**：使用 Hermes 内置 cron（`hermes cron` / `cronjob` 工具），不是系统 `crontab`，也不是独立统一调度框架。当前统一的是 cron wrapper 调用模式。

**系统 crontab 状态**：`crontab -l` 当前为空，这是正常状态。不要再把同一批任务写入系统 crontab，避免和 Hermes cron 重复触发。

### 白天执行任务（匹配用户晚上关机习惯）

| 任务 | Hermes cron 名称 | 时间（北京时间） | 脚本 | 说明 |
|------|------------------|------------------|------|------|
| 系统健康巡检 | `system-health-check` | 工作日 09:00 | `health-check-cron.sh` | 已接入 `cron_common.sh` |
| 聚类分析 | `聚类分析每周跑` | 周一 10:00 | `clustering-analysis-v3/scripts/clustering-analysis-cron.sh` | 新外层 thin wrapper，已接入 `cron_common.sh`，内部调用原完整 wrapper |
| 知识树 consolidate | `知识树维护每日` | 周一 10:30 | `knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | 已接入 `cron_common.sh`；名称保留旧名，但实际已改为每周一 |
| 知识导航基线 | `知识导航评估基线` | 周一 11:00 | `knowledge-navigation-baseline.sh` | scripts 根入口已接入 `cron_common.sh`，调用插件内基线采集 |
| 记忆清理 | `memory-cleanup-daily` | 每日 13:00 | `memory-cleanup/daily_dryrun.sh` | 已接入 `cron_common.sh` |
| 每日在线学习 | `每日在线学习` | 工作日 14:00 | `daily-learn/daily_learn.sh` | 已接入 `cron_common.sh` |
| SkillOpt 增量优化 | `skillopt-nightly-run` | 每日 15:00 | `skillopt-runner/skillopt-nightly-run.sh` | `/root/.hermes/scripts/` 下的项目子目录入口已接入 `cron_common.sh`，workdir 指向 `/root/.hermes/skillopt-runner` |

### 其他保留任务

| 任务 | 时间 | 说明 |
|------|------|------|
| 每周深度研究-知识树学习 | 周日 20:00 | LLM 驱动任务，保留 |
| 知识树 k_vector 每周兜底维护 | 周六 14:30 | 低频兜底维护，保留 |
| 论文投稿提醒-改投 | 2026-08-06 09:00 一次性 | 提醒任务，保留 |

**consolidation-monitor 说明**：当前运行环境和源码仓库中不存在 `consolidation-monitor.sh`，所以没有添加监控 cron，避免臆造不存在的脚本。现有知识树维护由 `knowledge-tree-consolidate.sh` 每周一 10:30 执行。

---

## 10. 文档索引

| 文档 | 路径 | 说明 |
|------|------|------|
| 调度框架蓝图与当前 wrapper 标准化 | `./cron-scheduler-design.md` | 完整 hermes-scheduler 为条件触发型蓝图；当前只落地 cron wrapper 标准化 |
| 部署系统重构设计 | `./deploy-system-redesign.md` | hermes-deploy 重构设计 |
| 8 个飞轮蓝图 | `./flywheel-blueprint.md` | 可补充飞轮架构和实施优先级 |
| 本文件 | `./project-profile.md` | 项目基础信息 |

---

## 最后更新

- 更新时间：`2026-06-19`
- 更新内容：初始创建，补齐全部基础信息；澄清当前只统一 cron wrapper 调用模式，未实施完整统一调度框架
- 维护者：Hermes Agent
