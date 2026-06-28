---
trigger: always_on
alwaysApply: true
---

# 部署规范 — HermesProject
# ============================================
# deploy.sh 使用与维护规范
# 最后更新：2026-05-27
# ============================================

## [DEPLOY-1] 部署入口唯一性

- 所有部署操作必须通过 `deploy/deploy.sh` 执行
- 禁止直接操作 `/root/.hermes/` 目标目录
- 禁止使用旧的 `sync-*.sh` 脚本

## [DEPLOY-2] 常用命令

```bash
# 列出所有项目
./deploy/deploy.sh list

# 预览部署清单（不动文件）
./deploy/deploy.sh plan <project>

# 执行部署
./deploy/deploy.sh deploy <project> [--yes]

# 回滚到最近一次备份
./deploy/deploy.sh rollback <project>

# 回滚到指定时间戳
./deploy/deploy.sh rollback <project> <timestamp>

# 查看部署历史
./deploy/deploy.sh history <project>

# 清理历史备份（默认保留最近5次）
./deploy/deploy.sh cleanup <project> [--keep N]

# 完全卸载项目
./deploy/deploy.sh cleanup <project> --uninstall
```

## [DEPLOY-3] 添加新项目

1. 在 `deploy/manifests/` 创建 `<project>.manifest`
2. 在 `deploy/deploy.sh` 的 `PROJECTS` 数组添加映射行
3. 如果有旧系统平铺文件，在 `LEGACY_CLEANUP` 数组添加
4. 如果有 Skill 文件，在 `SKILLS` 数组添加
5. 运行 `plan` 验证 manifest 展开结果

## [DEPLOY-4] 修改 Manifest

- 清单使用 glob 模式，每行一个（相对项目根）
- `!` 开头为排除模式（如 `!**/__pycache__/**`）
- `#` 开头为注释
- 修改后必须 run `plan` 验证

## [DEPLOY-5] 维护旧系统平铺文件清理

- `LEGACY_CLEANUP` 中列出旧脚本曾散落的平铺文件路径
- 支持 glob 模式匹配（如 `entities_v3_*.json`）
- 只在 `latest` 不存在时（首次部署）生效

## [DEPLOY-6] Skill 文件管理

- 源码放在 `<project>/skills/` 目录
- 在 `SKILLS` 映射中声明 `source_subdir|target_root`
- 部署自动发布到 `/root/.hermes/skills/`
- 回滚自动覆盖

## [DEPLOY-7] 备份与恢复

- 备份目录：`/root/.hermes/backups/<project>/<timestamp>/`
- `.deployed-files`：本次部署写入的所有文件路径
- `.backed-up-files`：本次部署覆盖/删除的文件路径
- 回滚：先删 `.deployed-files` 中的文件，再还原 `.backed-up-files`
- 防残留：两次部署之间自动清理本次未覆盖的旧文件

## [DEPLOY-8] 部署前验证

```bash
# 1. 语法检查
bash -n deploy/deploy.sh

# 2. 清单预览
./deploy/deploy.sh plan <project>

# 3.（可选）确保本地 commit 后再部署
```
