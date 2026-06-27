# SkillOpt 集成方案 — 不修改 Hermes 源码适配

## 摘要

**问题：** SkillOpt-Sleep 接入 Hermes，不修改 Hermes 核心源码，仅优化用户/agent 创建的业务 skill，复用 curator 已有使用统计（use/view/patch），排除 Lark/SenseNova/gstack/管道类 skill。

**结论：** 外挂式方案可行，无需改 Hermes 源码，只是在 curator 跑过之后作为后置步骤运行。

---

## 1. Hermes 已有元数据机制

### 1.1 源文件位置：
- 统计：`/root/.hermes/skills/.usage.json`
- 状态：`/root/.hermes/skills/.curator_state`
- 备份：`/root/.hermes/skills/.curator_backups/`
- curator 源码：`/root/.hermes/hermes-agent/agent/curator.py`
- 使用统计源码：`/root/.hermes/hermes-agent/tools/skill_usage.py`

### 1.2 已有字段可直接复用：

| 字段 | 含义 | 使用 |
|------|------|------|
| `created_by` | `agent` 表示 agent 创建，`None` 表示手工本地 | 筛选 |
| `use_count`/`view_count`/`patch_count` | 使用次数统计 | 优先优化高 activity skill |
| `last_used_at`/`last_viewed_at`/`last_patched_at` | 最新活动时间戳 | 筛选近期活跃 skill |
| `state` | `active`/`stale`/`archived` | 只优化 active |
| `pinned` | pinned 技能排除删除 | 我们只优化 unpinned |
| `archived_at` | 归档时间 | 归档排除 |

### 1.3 标识规则（已有不变）：

- `bundled_manifest` 中的 skills → 排除
- `hub.lock` 安装的 skills → 排除
- 明确 `created_by: "agent"` → 候选
- 不在 bundled/hub，且 allowlist 明确 → 候选（手工业务 skill）
- pinned 永远排除 → 用户不希望优化 pinned 技能
- 非 active 状态 → 排除

---

## 2. 排除规则（复用 curator 判断）

**核心原则：信任 curator 的生命周期管理**
- curator 标记 `pinned: true` → 排除（用户不希望动 pinned 技能）
- curator 标记 `state != active` → 排除（curator 已经归档/标记 stale，不用优化）
- 硬排除第三方技能集合（用户明确不动）：
  - `^lark/`
  - `^sensenova/`
  - `^gstack/`

**不再写死排除标准分类**：分类下也可能有用户/agent 创建的业务技能，curator 已经维护了 active 状态，自然会留下来需要优化的。

---

## 3. SkillOpt-Sleep 接入 Hermes

### 3.1 架构分层

SkillOpt-Sleep 核心不变，新增三个 adapter：

| 组件 | 职责 |
|------|------|
| `HermesSessionHarvester` | 读 `/root/.hermes/sessions/session_*.json` → 转 `SessionDigest` |
| `HermesSkillSelector` | 读 `.usage.json` + allowlist/denylist → 输出 candidate skill |
| `HermesSkillAdopter` | proposed 修正 → 调用 `hermes skill_manage patch/edit` → 正式写入 + bump patch count + 备份 |

### 3.2 反馈检测扩展

SkillOpt-Sleep 原有英文反馈词表，增加中文词表：

- **负反馈**：不对、错了、还不对、还是错、没改好、改不对、还是不对、不正确、还是不行、无法、不行、错误、bug、缺陷、失败
- **正反馈**：可以、好了、对了、正确、通过、行了、改好了、修好了、搞定、没问题、可用

---

## 4. 自动修改安全保障

### 4.1 不直接写入 SKILL.md

必须通过 Hermes 官方 `skill_manage` 工具写入：

```python
# 调用 CLI：
python -m hermes skill_manage action=patch name=... old_string=... new_string=...
```

优势：
- Hermes 会做 frontmatter 校验
- Hermes 会做 atomic write
- Hermes 会做 security scan → 失败自动 rollback
- Hermes 自动 bump `patch_count`/`last_patched_at` 在 `.usage.json`
- Hermes 清除 skill prompt cache

### 4.2 额外备份

skillopt-runner 会在 `skillopt-runner/backups/` 再写一份 `.bak`，双重备份。

---

## 5. 触发机制

不改 Hermes curator，做外部 cron/watchdog：

```
cron → 每日白天运行一次
  读取 .curator_state 中 last_run_at
  如果 last_run_at 在最近 run 之后 → 触发 skillopt-runner
  else 退出
```

这样保证 skillopt 总是在 curator 跑完之后跑，利用 curator 已经做的生命周期整理。

---

## 6. 候选 skill 选择逻辑

```python
def filter_eligible(usage, allowlist, denylist_patterns):
    for name, rec in usage.items():
        if rec['pinned']: continue
        if rec['state'] != 'active': continue
        if any(pattern in name for pattern in denylist): continue
        if name in allowlist: yield (name, rec); continue
        if rec['created_by'] == 'agent': yield (name, rec); continue
        # local manual business skill: require activity >= 1
        activity = use_count + view_count + patch_count
        if activity >= 1: yield (name, rec)
```

---

## 7. 边界

| 约束 | 满足？ |
|------|--------|
| 不修改 Hermes 源码 | ✔️ 全部运行在 `/root/.hermes/skillopt-runner/` 外部，不碰 hermes-agent 源码 |
| 排除 Lark/SenseNova/gstack | ✔️ 硬 denylist 保留这三个 |
| 复用 curator 生命周期+使用统计 | ✔️ 直接读 `.usage.json`，复用 curator `pinned`/`state` 判断 |
| curator 生命周期不变 | ✔️ skillopt 不碰 curator 状态，只读 |
| 自动修改走 Hermes 工具入口 | ✔️ 通过 `hermes skill_manage` 调用，符合安全机制 |

---

## 8. 下一步

1. **第一阶段**：安装 skillopt_sleep 从 GitHub main 分支，配置 allowlist，dry-run 跑验证
2. **第二阶段**：打开自动修改，跑 nightly 循环
3. **第三阶段**：接入 cron，curator 跑完自动触发

文件已落地：

- 启动脚本：`/root/.hermes/skillopt-runner/skillopt_runner.py`
- 配置模板：`/root/.hermes/skillopt-runner/config.yaml`
- 允许列模板：`/root/.hermes/skillopt-runner/allowlist.yaml`

