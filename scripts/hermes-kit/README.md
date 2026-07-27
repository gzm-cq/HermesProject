# Hermes-Kit

Hermes 知识飞轮增强包 — 四路召回、聚类分析、Skill 优化、飞轮监控、知识树维护、记忆清理、梦境合成、自进化研究，一键安装。

## 快速开始

```bash
# 预览安装（不实际执行）
./install.sh --dry-run

# 正式安装
./install.sh --yes

# 升级（更新代码 + 合并配置 + 重建 cron）
./upgrade.sh --yes

# 查看安装状态
hermes cron list
ls ~/.hermes-kit/

# 卸载（默认 dry-run）
./uninstall.sh
./uninstall.sh --apply      # 真正卸载
```

## 命令说明

| 命令 | 用途 | 说明 |
|------|------|------|
| `install.sh` | 首次安装 | 部署组件 + 创建配置 + 建 cron |
| `upgrade.sh` | 升级 | 更新代码 + 合并配置 + 重建 cron |
| `uninstall.sh` | 卸载 | 代码卸载，数据保留，默认 dry-run |

## 目录结构

```
hermes-kit/
├── SPEC.md                 # 架构方案（v1.2）
├── README.md               # 本文件
├── install.sh              # 一键安装脚本
├── uninstall.sh            # 卸载脚本
├── config/
│   └── default.yaml        # 默认配置模板
└── templates/
    └── .env.append         # 需要追加到 .env 的配置项
```

## 安装内容

**14 个组件**（通过 `deploy/deploy.sh` 部署）：
- cron-common、knowledge-navigation、knowledge-tree-plugin、knowledge-tree-builder
- clustering-analysis-v3、memory-cleanup、skillopt-runner、skillopt-sleep
- system-health-check、daily-learn、dream-synth、self-evolving、cron-wrappers、recall-eval

**14 个常驻 cron**（通过 `hermes cron create`）：
- 系统巡检、飞轮健康报告、每日在线学习、k-vector 维护、每周深度研究
- 聚类分析、知识树维护、知识导航基线、Skill Eval、记忆清理
- Router 巡检、Skill 优化、梦境合成、cron 异常检测

## 配置

安装后配置文件位于 `~/.hermes-kit/config.yaml`。

环境变量通过 `~/.hermes/.env` 注入，所有 kit 相关变量以 `HERMES_KIT_` 前缀命名。

详见 [SPEC.md](./SPEC.md) 第四节。

## 卸载

```bash
# 预览卸载
./uninstall.sh

# 真正卸载（保留数据）
./uninstall.sh --apply

# 保留 cron 只删代码
./uninstall.sh --apply --keep-cron

# 保留配置
./uninstall.sh --apply --keep-config
```

卸载原则：**代码卸载，数据保留**。数据库、日志、备份、Hermes 全局配置均不删除。

## 故障排查

| 问题 | 排查命令 |
|------|---------|
| cron 没跑 | `hermes cron status` / `hermes cron list` |
| 插件没加载 | `hermes plugins list` |
| 安装中断 | 重新运行 `./install.sh --yes`（有状态标记，断点续跑） |
| 部署失败 | 查看 `~/.hermes/backups/<project>/` 回滚 |

## 版本

v1.2 / 2026-07-25
