# 部署系统（一键 / 文件级 / 项目级）

> 统一入口 `deploy.sh` 分发至各项目脚本，各项目脚本加载共享函数库执行部署/回滚/计划预览/历史查看/清理操作。

## 架构

```
deploy/
├── deploy.sh                    # 轻量分发器 —— 仅处理 list 和路由
├── lib/
│   └── common.sh                # 共享函数库 —— 全部部署/回滚/清理逻辑
├── projects/
│   ├── ai-report-system.sh      # AI 报告生成系统配置
│   ├── clustering-analysis-v3.sh# 聚类分析配置
│   ├── cron-common.sh           # cron 公共库配置
│   ├── cron-wrappers.sh         # cron wrapper 集合配置
│   ├── daily-learn.sh           # 每日在线学习配置
│   ├── drawio-generator.sh      # Draw.io 生成器配置
│   ├── knowledge-navigation.sh  # 知识导航插件配置
│   ├── knowledge-tree-builder.sh# 知识树构建器配置
│   ├── knowledge-tree-plugin.sh # 知识树在线插件配置
│   ├── memory-cleanup.sh        # 记忆清理配置
│   ├── self-evolving.sh         # 自我进化研究配置
│   ├── skillopt-runner.sh       # SkillOpt Runner 配置
│   ├── skillopt-sleep.sh        # SkillOpt Sleep 配置
│   └── system-health-check.sh   # 系统健康巡检配置
└── manifests/                   # 部署清单（glob 模式，各项目独立）
    ├── ai-report-system.manifest
    ├── clustering-analysis-v3.manifest
    ├── cron-common.manifest
    ├── cron-wrappers.manifest
    ├── daily-learn.manifest
    ├── drawio-generator.manifest
    ├── knowledge-navigation.manifest
    ├── knowledge-tree-builder.manifest
    ├── knowledge-tree-plugin.manifest
    ├── memory-cleanup.manifest
    ├── self-evolving.manifest
    ├── skillopt-runner.manifest
    ├── skillopt-sleep.manifest
    └── system-health-check.manifest
```

## 项目映射

| 项目 | 源 | 目标 | 技能部署 | 重启服务 |
|------|------|------|---------|----------|
| `ai-report-system` | `scripts/ai-report-system/` | `/root/.hermes/scripts/ai-report-system/` | `skills/` → `/root/.hermes/skills/` | — |
| `clustering-analysis-v3` | `scripts/clustering-analysis-v3/` | `/root/.hermes/scripts/clustering-analysis-v3/` | `skills/` → `/root/.hermes/skills/` | — |
| `cron-common` | — | — | — | — |
| `cron-wrappers` | `scripts/cron-wrappers/` | `/root/.hermes/scripts/cron-wrappers/` | — | — |
| `daily-learn` | `scripts/daily-learn/` | `/root/.hermes/scripts/daily-learn/` | — | — |
| `drawio-generator` | `scripts/drawio-generator/` | `/root/.hermes/scripts/drawio-generator/` | `skills/` → `/root/.hermes/skills/` | — |
| `knowledge-navigation` | `plugins/knowledge-navigation/` | `/root/.hermes/plugins/knowledge-navigation/` | `skills/` → `/root/.hermes/skills/` | `hermes-gateway.service` |
| `knowledge-tree-builder` | `scripts/knowledge-tree-builder/` | `/root/.hermes/scripts/knowledge-tree-builder/` | `skills/` → `/root/.hermes/skills/` | — |
| `knowledge-tree-plugin` | `plugins/knowledge-tree-plugin/` | `/root/.hermes/plugins/knowledge-tree-plugin/` | — | `hermes-gateway.service` |
| `memory-cleanup` | `scripts/memory-cleanup/` | `/root/.hermes/scripts/memory-cleanup/` | `skills/` → `/root/.hermes/skills/` | — |
| `self-evolving` | `scripts/self-evolving/` | `/root/.hermes/scripts/self-evolving/` | `skills/` → `/root/.hermes/skills/` | — |
| `skillopt-runner` | `scripts/skillopt-runner/` | `/root/.hermes/scripts/skillopt-runner/` | — | — |
| `skillopt-sleep` | `scripts/skillopt-sleep/` | `/root/.hermes/scripts/skillopt-sleep/` | — | — |
| `system-health-check` | `scripts/system-health-check/` | `/root/.hermes/scripts/system-health-check/` | — | — |

## 子命令

```bash
# 1. 列出可部署项目
./deploy/deploy.sh list

# 2. 预览将部署的文件（不动文件系统）
./deploy/deploy.sh plan ai-report-system

# 3. 一键部署（默认会要求 yes 确认；加 --yes 自动确认）
./deploy/deploy.sh deploy ai-report-system
./deploy/deploy.sh deploy knowledge-navigation --yes

# 4. 查看历史
./deploy/deploy.sh history ai-report-system

# 5. 回滚（默认回到最近一次；也可指定时间戳）
./deploy/deploy.sh rollback ai-report-system
./deploy/deploy.sh rollback ai-report-system 20260526-103000

# 6. 项目脚本也可直接调用（无需经过分发器）
./deploy/projects/ai-report-system.sh plan
./deploy/projects/memory-cleanup.sh deploy --yes
```

## 设计原则

1. **项目维度** —— 每次只处理一个逻辑项目，互不影响。
2. **项目配置解耦** —— 每个项目有独立脚本定义源目录/目标路径/服务/旧文件清理规则/Skill 部署。
3. **共享逻辑库** —— 全部部署/回滚/清理逻辑集中在 `lib/common.sh`，减少重复。
4. **文件级清单** —— `manifests/<project>.manifest` 通过 glob 描述包含/排除模式，运行时展开为具体文件，逐个备份/拷贝/记录，**不做整目录复制**。
5. **影响范围显式声明** —— manifest 头注释中标明源、目标、是否重启服务。
6. **文件级备份** —— 每个被覆盖的目标文件单独 `cp -p` 到 `/root/.hermes/backups/<project>/<ts>/<相对路径>`。
7. **防残留** —— 部署时记录 `.deployed-files`；下次部署前对比上次清单，删除本次未覆盖的旧文件，确保生产环境无遗留。
8. **回滚原子保证** —— 回滚时先按 `.deployed-files` 删除本次写入的所有文件，再按 `.backed-up-files` 还原备份；确保还原后环境与部署前一致。

## 备份目录结构

```
/root/.hermes/backups/<project>/
├── 20260526-103000/                    # 时间戳目录
│   ├── src/ai_report/cli.py            # 备份的旧文件（原 target 路径下的）
│   ├── skills/devops/memory-md-cleanup/SKILL.md  # 备份的旧 Skill 文件
│   ├── ...
│   ├── .deployed-files                 # 本次部署写入的所有目标文件绝对路径
│   ├── .backed-up-files                # 本次备份的目标文件绝对路径
│   └── .meta                           # project / ts / source / target / restart_service / file_count
├── 20260526-150000/
│   └── ...
└── latest -> 20260526-150000           # 符号链接，指向最近一次
```

## 清单语法

`deploy/manifests/<project>.manifest` 中：

- `# 起始`：注释
- `path/**/*.py`：包含模式（glob，相对项目源根，bash globstar）
- `!**/__pycache__/**`：排除模式（以 `!` 开头）
- 排除模式后置匹配：先收集所有包含模式命中的文件，再按排除模式过滤

修改清单后：先 `plan` 预览，再 `deploy`。

## 故障排查

- **服务启动失败** → `sudo systemctl status hermes-gateway.service`，必要时立即 `rollback`
- **清单展开为空** → 检查 manifest glob 是否与源目录结构匹配
- **备份目录残缺** → 上次部署可能被中断；可手动 `rm -rf /root/.hermes/backups/<project>/<ts>` 删除半成品后重试
- **项目脚本错误** → 项目配置在 `deploy/projects/<project>.sh` 中，共享逻辑在 `deploy/lib/common.sh` 中
- **Skill 部署异常** → 检查 `deploy/projects/<project>.sh` 中 `SKILLS_SRC` 路径是否指向正确的技能源码目录
- **Skill 回滚失败** → 确保备份目录中有 `skills/` 子目录；若缺失，Skill 文件不会参与回滚
