# cognee-setup

> Cognee 知识图谱 MCP 接入组件（数据飞轮增强方案 §2.2，P0-2）。
>
> 为 Hermes 提供 Cognee 知识图谱能力：`cognee-mcp` 以 stdio 方式注册进 `/root/.hermes/config.yaml` 的 `mcp_servers` 段（只改配置、不破"不动核心源码"约束）。

## 组件分工（2026-08-23 修订：Python 包路线）

- **neo4j**：Docker 官方镜像（`docker.1ms.run/library/neo4j:5.23-community`），本机已验证可拉取
- **cognee-mcp**：PyPI 包（`cognee-mcp 0.5.5`），`pip install --target=/root/.hermes/cognee-mcp-pkg` 安装，由 Hermes 核心以 stdio 拉起

> ⚠️ 原 Docker 镜像路线已废弃：cognee-mcp 官方仅发布 ghcr.io 镜像，本机及国内镜像源均不可达。

## 用法

```bash
bash scripts/cognee-setup/start-cognee.sh up        # 启动 neo4j + 校验 cognee-mcp
bash scripts/cognee-setup/start-cognee.sh down      # 停止 neo4j
bash scripts/cognee-setup/start-cognee.sh check     # 仅校验 cognee-mcp 命令可用
bash scripts/cognee-setup/start-cognee.sh register  # 打印 config.yaml mcp_servers 注册段
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `start-cognee.sh` | neo4j 启动/停止 + cognee-mcp 安装校验 + 注册段输出 |
| `cognee-mcp-run.sh` | cognee-mcp stdio 启动包装（设置 PYTHONPATH 指向 cognee-mcp-pkg） |
| `docker-compose.yml` | neo4j 5.23-community 服务（含 apoc 插件、healthcheck） |

## 安装

```bash
pip3 install --target=/root/.hermes/cognee-mcp-pkg \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  cognee-mcp
```

## 注册

将 `start-cognee.sh register` 输出的段追加到 `/root/.hermes/config.yaml` 的 `mcp_servers`（dict 格式，key=服务名）：
`cognee` → `command: /root/.hermes/scripts/cognee-setup/cognee-mcp-run.sh`。

> 本目录为接入配置工具，无独立部署清单，部署随 `flywheel-scripts` 或手工同步。
