# Hermes-Kit 配置归拢实施计划

> 目标：把散落的配置归拢到 `~/.hermes-kit/config.yaml`，实现 install/upgrade/config 三命令模式

## 受影响的文件清单

| 序号 | 文件 | 当前状态 | 需要做的修改 |
|:----:|------|----------|-------------|
| 1 | `install.sh` | CRON_JOBS 硬编码在脚本里 | 改为从 `config/default.yaml` 的 `cron:` 段读取 |
| 2 | `upgrade.sh` | 不存在 | 新建：部署 + 合并配置 + 重建 cron |
| 3 | `templates/.env.append` | 包含非密钥配置项 | 把非密钥项移入 default.yaml |
| 4 | `config/default.yaml` | 已加 cron 段 ✅ | 再接收从 .env.append 移入的配置项 |
| 5 | `README.md` | 只有 install/uninstall | 增加 upgrade 命令说明 |
| 6 | `scripts/kit-status.sh` | 不存在（SPEC 提及） | 新建：组件状态检查 |
| 7 | `scripts/kit-verify.sh` | 不存在（SPEC 提及） | 新建：安装验证 |
| 8 | `manifests/kit.manifest` | 不存在（SPEC 提及） | 新建：文件清单 |
| 9 | `SPEC.md` | 已更新 ✅ | 无需再改 |

## 执行顺序

```
Phase 1: 改 install.sh（cron 从 config 读取）         → 建 todo
Phase 2: 建 upgrade.sh                                  → 建 todo
Phase 3: 归拢 .env.append → default.yaml                → 建 todo
Phase 4: 更新 README.md                                  → 建 todo
Phase 5: 新建 scripts/kit-status.sh / verify.sh         → 建 todo
Phase 6: 新建 manifests/kit.manifest                     → 建 todo
Phase 7: 验证：全量检查                                    → 逐个核对
```