---
alwaysApply: true
---

# Git 工作流规范 — HermesProject
# ============================================
# 提交信息 + 分支策略 + 推送
# 最后更新：2026-05-27
# ============================================

## [GIT-1] 提交信息格式（强制）

```
<type>(<scope>): <描述>
```

| 类型 | 场景 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(deploy): 新增 skill 文件部署` |
| `fix` | 修复 bug | `fix(manifest): 修复 memory-cleanup 排除规则` |
| `refactor` | 重构 | `refactor(cli): 提取公共 backup 函数` |
| `docs` | 文档 | `docs(readme): 更新架构图` |
| `chore` | 杂项 | `chore: 添加 .gitignore 规则` |
| `style` | 格式 | `style: 统一缩进为 4 空格` |

## [GIT-2] 描述规范

- 中文描述，不超过 72 字符
- 描述做了什么，不描述为什么
- 示例 ✅ `fix(deploy): 修复首次部署防残留未清理旧平铺文件`
- 错误 ❌ `git commit -m "fix bug"`

## [GIT-3] 文件暂存规范

- 提交前检查 `git status`
- 只暂存与本次修改相关的文件
- 不同功能的修改分开提交
- `.gitignore` 修改单独提交

## [GIT-4] 忽略文件策略

- 根 `.gitignore` 控制全局规则
- 每个子项目可在自己目录下加 `.gitignore`（覆盖根规则）
- `__pycache__/`、`*.egg-info/`、`.pytest_cache/` 等必须忽略
- `nul` 等 Windows 保留设备名必须在 `.gitignore` 中

## [GIT-5] 推送注意事项

- 本地提交后需 `git push` 同步到 GitHub
- 公司网络可能需配置 HTTP 代理：
  ```bash
  git config --global http.proxy http://127.0.0.1:<port>
  ```
- push 超时重试即可（commit 已在本地不丢失）
- 禁止 `--force` push 到 main 分支

## [GIT-6] CRLF 处理

- Git 已配置 `core.autocrlf=input`
- Windows 上文件 CRLF 会自动转为 LF
- 不影响功能，git warning 可忽略

## [GIT-7] 分支策略

- 直接使用 `main` 分支（单开发者模式）
- 重大改动前建议先 plan 再实现
- 确保 `main` 始终可部署
