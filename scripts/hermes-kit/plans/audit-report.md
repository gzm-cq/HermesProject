# Hermes-Kit v2 产品化改造方案 — 审计报告

> 审计日期: 2026-07-25
> 审计对象: `productization-plan.md` (v1.1, 454 行)
> 审计范围: 方案完整性、风险、影响范围、实施顺序、遗漏专项
> 审计方法: 逐节审查 + 交叉验证 deploy/projects/*.sh + deploy/manifests/*.manifest + 源码 import 路径

---

## 审计结论

### ❌ 有条件不通过（需修复 1 个 P0 + 3 个 P1 后方可执行）

方案整体**方向正确，架构合理**，但存在**1 个 P0 级致命缺陷**（skillopt-sleep import 路径断裂）、**3 个 P1 级遗漏**（cron-wrappers 深层子目录、flywheel-health-report.py 和 backfill-scope.py 未纳入、cron-common.sh 源路径不准确）和 **2 个 P2 级改进建议**。

修复上述问题后，可以按方案执行。

---

## 一、方案完整性审计

### 1.1 目标架构覆盖

| 组件 | 方案中提及 | 实际检查 | 评估 |
|------|:---------:|:---------:|:----:|
| knowledge-navigation | ✅ components/ | 源码存在 | ✅ |
| knowledge-tree-plugin | ✅ components/ | 源码存在 | ✅ |
| knowledge-tree-builder | ✅ components/ | 源码存在 | ✅ |
| clustering-analysis | ✅ components/ | 源为 clustering-analysis-v3 | ✅ 方案已处理命名差异 |
| memory-cleanup | ✅ components/ | 源码存在 | ✅ |
| skillopt-runner | ✅ components/ | 源码存在 | ✅ |
| skillopt-sleep | ✅ components/ 子目录 | 源码存在 | ⚠️ 见 P0 |
| flywheel | ✅ components/ | 需拆分 | ✅ |
| cron | ✅ components/ | 需拆分 | ✅ |
| dream-synth | ✅ components/ | 源码存在 | ✅ |
| self-evolving | ✅ components/ | 源码存在 | ✅ |
| system-health-check | ✅ components/ | 源码存在 | ✅ |
| daily-learn | ✅ components/cron/ | 源码存在 | ✅ |

**完整性结论：** 11 个组件全覆盖，无遗漏。

### 1.2 搬移清单完整性

搬移清单（§5.1 表格）覆盖了所有 11 个组件，与 deploy/projects/*.sh 一一对应（除 skillopt-sleep 将删除独立部署）。**无遗漏组件。**

### 1.3 配置统一方案

**知识导航插件** `config.py` 已实现 `from_kit_config()` 方法（行 207），从 `~/.hermes-kit/config.yaml` 读取 `plugin_config` 段。**验证通过。**

`config/default.yaml` 内容完整，覆盖 recall、clustering、knowledge_tree、memory_cleanup、skill_optimization、system_health、notification、cron、plugin_config 等 9 大配置段。**方案可行。**

**待完成项：** 其他 Python 子项目（knowledge-tree-plugin、clustering-analysis、memory-cleanup 等）的 `import yaml` 读取改造尚未开始，方案承认"知识导航已完，其他逐步"。

### 1.4 部署方式变化

从 `deploy.sh deploy <项目>` 到 `kit deploy [组件]` 的过渡方案合理：
- 向后兼容：`deploy.sh` 路径更新后仍可用
- 软链过渡期保护
- `kit.manifest` 含 `target:` 字段区分 plugins/ scripts/ lib/

**但 kit CLI 尚未实现**（Phase 3），方案中缺少 `kit.manifest` 模板的完整设计。

---

## 二、风险审计

### 2.1 P0: R3 风险缓解不充分 — skillopt-sleep import 路径断裂

**严重程度：P0（致命）**

**问题描述：**
方案 §8.5 和 §7 的 R3 缓解声称：将 skillopt-sleep 作为 `components/skillopt-runner/skillopt-sleep/` 子目录，删除 skillopt-sleep 的独立部署脚本。

但实际代码中，`scripts/skillopt-runner/skillopt_runner.py` 第 28-31 行：

```python
SKILLOPT_HOME = pathlib.Path('/root/.hermes/skillopt-runner')
_SKILLOPT_SLEEP_PATH = str(SKILLOPT_HOME.parent / 'skillopt-sleep')
# 即 /root/.hermes/skillopt-sleep（兄弟目录，不是子目录）
if _SKILLOPT_SLEEP_PATH not in sys.path:
    sys.path.insert(0, _SKILLOPT_SLEEP_PATH)
```

**关键矛盾：** 代码在运行时将 skillopt-sleep 视为 skillopt-runner 的**兄弟目录**（`SKILLOPT_HOME.parent / 'skillopt-sleep'` 即 `/root/.hermes/skillopt-sleep`），而非子目录。方案将其作为子目录搬移后，运行时路径会变成 `/root/.hermes/skillopt-runner/skillopt-sleep`，导致 `sys.path.insert` 找不到包。

**影响：** skillopt-nightly-run 完全失败（SkillOpt 优化停摆）。

**修复建议（二选一）：**

**方案 A（推荐 — 保持兄弟目录关系）：** 保持 skillopt-sleep 和 skillopt-runner 作为独立 deploy 项目，各自部署到 `/root/.hermes/skillopt-sleep/` 和 `/root/.hermes/skillopt-runner/`，但源码都放在 `components/` 下作为同级目录。更新 `skillopt_runner.py` 第 28 行路径为 `SKILLOPT_HOME = pathlib.Path('/root/.hermes/skillopt-runner')`（不变），同时更新 deploy manifest 反映新路径。

**方案 B（合并为子目录 + 代码修改）：** 将 skillopt-sleep 源码放入 `components/skillopt-runner/skillopt-sleep/`，同时修改 `skillopt_runner.py` 第 29 行：
```python
_SKILLOPT_SLEEP_PATH = str(SKILLOPT_HOME / 'skillopt-sleep')
```
（将 `parent` 去掉）

### 2.2 P1: cron-wrappers 拆分遗漏深层子目录

**严重程度：P1（高）**

**问题描述：**
方案 §8.2 的 cron-wrappers 拆分表只列出了**顶层**脚本文件，但实际 `scripts/cron-wrappers/` 目录下还有**3 个嵌套子目录**，其中包含 4 个关键 cron wrapper 脚本：

| 文件 | 方案中是否提及 | 归属 |
|------|:-------------:|:----:|
| `skillopt-runner/skillopt-nightly-run.sh` | ❌ 未提及 | → 应放入 `cron/` 或 `flywheel/` |
| `memory-cleanup/daily_dryrun.sh` | ✅ 在搬移表格中提及 | → 已随 memory-cleanup 搬移 |
| `knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | ❌ 未提及 | → 已随 knowledge-tree-builder 搬移 |
| `knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh` | ❌ 未提及 | → 已随 knowledge-tree-builder 搬移 |
| `clustering-analysis-v3/scripts/clustering-analysis-cron.sh` | ❌ 未提及 | → 已随 clustering-analysis 搬移 |

**影响：** `skillopt-nightly-run.sh` 是独立 cron wrapper，不为任何组件自带。如果不处理，搬移后 skillopt nightly run 会丢失。

**修复建议：**
1. 在 §8.2 的 cron-wrappers 拆分表中明确 `skillopt-runner/skillopt-nightly-run.sh` → 放入 `cron/` 目录
2. 其他 4 个文件虽然随组件搬移，但应在方案中**明确说明**它们已随组件移走，不纳入 cron-wrappers 拆分范围
3. 更新 `cron.manifest` 加入 `skillopt-runner/skillopt-nightly-run.sh`

### 2.3 P1: cron-wrappers 中遗漏特殊文件

**严重程度：P1（高）**

**问题描述：**
`scripts/cron-wrappers/` 目录下还有 2 个非 .sh 的部署文件：

| 文件 | 方案中是否提及 | 说明 |
|------|:-------------:|:----:|
| `flywheel-health-report.py` | ❌ 未提及 | Python 飞轮报告器，被 `flywheel-health-report.sh` 调用 |
| `backfill-scope.py` | ❌ 未提及 | 作用域回填脚本 |
| `cron-boot-detect.service` | ❌ 未提及 | systemd service 单元文件 |
| `cron-jobs-config.md` | ❌ 未提及 | cron 配置锚点文档 |
| `README.md` | ❌ 未提及 | 目录说明 |

**影响：** `flywheel-health-report.py` 是 flywheel 报告的核心依赖，遗漏会导致 flywheel 功能不完整。`cron-boot-detect.service` 可能是一个已部署的 systemd 单元，丢失后启动检测失效。

**修复建议：**
1. `flywheel-health-report.py` → 放入 `flywheel/` 目录的 manifest 中
2. `backfill-scope.py` → 评估是否仍需部署，如需要放入 `cron/` 或 `flywheel/`
3. `cron-boot-detect.service` → 检查是否已部署到 systemd，如需要放入 `cron/` 并添加 systemd 部署说明
4. `cron-jobs-config.md` → 放入 `cron/` 作为参考文档
5. 更新 `flywheel.manifest` 和 `cron.manifest`

### 2.4 P1: cron-common.sh 源路径不准确

**严重程度：P1（高）**

**问题描述：**
方案 §8.1 指出 `cron-common.sh` 的当前 `PROJECT_SRC_REL="scripts"` 太宽泛。检查发现：

- `cron_common.sh` 实际位于 `/mnt/d/HermesProject/scripts/cron_common.sh`（**不在** cron-wrappers 目录下）
- 当前 `cron-common.sh` 的 deploy 脚本 `PROJECT_SRC_REL="scripts"` 会匹配整个 `scripts/` 目录

方案建议将 `PROJECT_SRC_REL` 改为 `scripts/hermes-kit/components/cron`，但 `cron_common.sh` 在搬移前位于 `scripts/` 根目录，**不是** `scripts/cron-wrappers/` 的子文件。

**影响：** 搬移脚本需要特殊处理 `cron_common.sh` 的 `git mv` 路径。

**修复建议：**
1. 搬移时：`git mv scripts/cron_common.sh scripts/hermes-kit/components/cron/cron-common.sh`
2. 更新 `cron-common.sh` deploy 脚本的 `PROJECT_SRC_REL` 为 `scripts/hermes-kit/components/cron`
3. 更新 `cron-common.manifest` 的源路径注释

### 2.5 R1 缓解 — Gateway 搬移保护评估

**评估：通过。** 方案给出了完整的备份 → 部署 → 验证 → 回滚四步流程，含 Gateway 重启检测。具体可行。

### 2.6 R2 缓解 — cron-common 公共库保护评估

**评估：通过。** 部署目标 `/root/.hermes/lib/` 不变，仅源码路径变更。验证步骤完整。

### 2.7 R4 缓解 — manifest 批量更新评估

**评估：通过。** 方案提供了 diff 验证和 `--dry-run` 检查。但需注意方案中 `deploy/projects/*.sh` 的 `PROJECT_SRC_REL` 批量更新表（§8.6）有 1 处不准确（见 P1 2.4 节）。

### 2.8 R5 缓解 — kit CLI 依赖检查评估

**评估：通过。** 方案在安装时验证 PyYAML，argparse 是标准库无需额外安装。但 `kit` CLI 是 Phase 3 才实现，Phase 1+2 期间依赖 PyYAML 的项目需自行处理。

---

## 三、影响范围审计

### 3.1 必须修改的文件

| 文件类别 | 数量 | 修改内容 |
|---------|:----:|---------|
| `deploy/projects/*.sh` | 10 个 | `PROJECT_SRC_REL` 路径更新 |
| `deploy/manifests/*.manifest` | 10 个 | 源路径注释更新，globs 不变 |
| `deploy/projects/skillopt-sleep.sh` | 1 个 | 删除独立部署 |
| `deploy/projects/cron-wrappers.sh` | 1 个 | 删除，替换为 flywheel.sh + cron.sh |
| `deploy/manifests/cron-wrappers.manifest` | 1 个 | 删除，替换为 flywheel.manifest + cron.manifest |
| `deploy/projects/cron-common.sh` | 1 个 | `PROJECT_SRC_REL` 从 `scripts` 改为精确路径 |
| `deploy/projects/daily-learn.sh` | 1 个 | `PROJECT_SRC_REL` 更新 |
| 软链（原路径→新路径） | 11 个 | 过渡期保护 |
| `scripts/hermes-kit/manifests/kit.manifest` | 1 个 | 新建，含 `target:` 字段 |

**合计：约 27 个文件需要修改或创建。**

### 3.2 不受影响的文件

| 类别 | 说明 |
|------|------|
| 运行时数据 | 数据库、logs、backups、state.json 等不移动 |
| MEMORY.md / USER.md | 不受影响 |
| Hermes 配置 | `~/.hermes/config.yaml` 不受影响 |
| `~/.hermes-kit/config.yaml` | 运行时配置，不变 |
| 已注册的 cron job | `~/.hermes/cron/jobs.json` 不变，仅脚本路径变化 |
| 不纳入 kit 的项目 | ai-report-system、drawio-generator、p0-benchmark、recall-eval 不动 |

### 3.3 不纳入 kit 的项目清单

**验证通过。** 4 个排除项目（ai-report-system、drawio-generator、p0-benchmark、recall-eval）在 deploy/projects/ 中均有独立脚本，且与 kit 组件无交叉依赖。

### 3.4 对现有 cron 任务的影响

**关键发现：** cron 脚本的**运行时路径**不会变——所有 cron 脚本仍然部署到 `/root/.hermes/scripts/`，只是**源码路径**变了。但 `cron-jobs-config.md` 中记录的 cron job 信息（第 4 行：`knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh` 等）涉及嵌套子目录路径，搬移后这些路径需要更新。

**实际影响：** 低。cron 任务的 `hermes cron create` 命令使用的是**运行时路径**（`/root/.hermes/scripts/...`），而非源码路径。运行时路径不变，cron 任务不受影响。

---

## 四、实施顺序审计

### 4.1 Phase 1+2 合并执行

**评估：通过。** 合并执行避免中间态，减少部署中断次数。但需要注意：
- 11 个组件一次性搬移 + 配置改造 + 部署路径更新，工作量大（约 2-3 小时）
- 建议在 Phase 1+2 开始前先完成**全量备份**（`cp -r /root/.hermes /tmp/hermes-backup`）

### 4.2 搬移顺序

**评估：基本合理，但需微调。**

方案建议顺序：插件（2 个）→ 独立脚本（7 个）→ cron-wrappers 拆分

**建议微调：**
1. **先搬 cron-common**（第 0 步），因为它是所有 cron 的公共依赖，且 `PROJECT_SRC_REL="scripts"` 过宽
2. **插件放最后**，因为需要 Gateway 重启，放在最后可以减少中断次数
3. cron-wrappers 拆分中，`skillopt-nightly-run.sh` 和 `flywheel-health-report.py` 需要特殊处理

**建议顺序：**
1. cron-common（修改 `PROJECT_SRC_REL` 从 `scripts` 到精确路径）
2. cron-wrappers 拆分（flywheel + cron + 嵌套子目录）
3. 独立脚本（7 个：knowledge-tree-builder、clustering-analysis 等）
4. 插件（2 个：knowledge-navigation、knowledge-tree-plugin）→ 最后重启 Gateway
5. skillopt-runner + skillopt-sleep 处理（统一路径策略）

### 4.3 每个组件一个 git commit

**评估：通过。** 方案建议每个组件一个 git commit，保留 `git mv` 历史，合理。但 11 个组件 + cron-wrappers 拆分 = 12+ 个 commit，需要 2-3 小时。

### 4.4 搬移后验证步骤

**评估：通过。** 验证步骤完整：
1. `deploy/deploy.sh deploy <name> --dry-run` 确认路径正确
2. `deploy/deploy.sh deploy <name> --yes` 部署到运行时
3. `test -f /root/.hermes/<target>/<name>/<key_file>` 确认文件存在
4. 涉及 Gateway 的验证 `systemctl restart hermes-gateway`

**建议补充：** 搬移后验证 cron-common 时，增加 `source /root/.hermes/lib/cron_common.sh && echo 'OK'` 的功能验证，不仅仅是文件存在检查。

---

## 五、遗漏专项检查

### 5.1 遗漏的组件/文件（P1）

已在上文 2.2 和 2.3 中详细说明。

**汇总：**
- `flywheel-health-report.py` — 飞轮报告核心依赖，必须纳入 flywheel/
- `backfill-scope.py` — 作用域回填，需评估是否仍需要
- `cron-boot-detect.service` — systemd 单元，需评估是否已部署
- `skillopt-runner/skillopt-nightly-run.sh` — 独立 cron wrapper，需纳入 cron/
- `cron-jobs-config.md` — cron 配置锚点文档

### 5.2 未考虑到的依赖

- **PyYAML 依赖范围：** 方案 R5 只考虑了 kit CLI 的 PyYAML 依赖。但知识导航 `config.py` 的 `from_kit_config()` 也依赖 `import yaml`，如果 kit-config 不存在，它回退到 `.env`。搬移后所有子项目统一读 kit-config，PyYAML 成为**硬依赖**。方案应在 Phase 1+2 预检中增加 `pip install pyyaml || true`。

- **skillopt-sleep 的独立配置：** skillopt-sleep 有独立的 `config.yaml` 和 `state.json`，方案说 "作为 skillopt-runner 子目录"，但未说明这些运行时配置文件如何处理。

### 5.3 更好的替代方案

- **渐进式搬移 vs 批量搬移：** 方案选择 Phase 1+2 合并（一次性搬移 11 个组件），但建议先做**渐进式搬移试点**：先搬 1-2 个非关键组件（如 `memory-cleanup` 或 `dream-synth`），验证整个流程正确后再批量执行。
- **kit CLI 先做 vs 后做：** 方案将 CLI 放在 Phase 3（最后），但 Phase 1+2 完成后 `kit config get <key>` 已经需要（bash 脚本通过此命令读配置）。建议 Phase 1+2 中先实现 `kit config get` 的轻量版本（一个简单的 shell wrapper），等 Phase 3 再完整实现。

### 5.4 kit.manifest 设计缺口

方案提及 `kit.manifest` 含 `target:` 字段，但现有的 `kit.manifest`（在 `scripts/hermes-kit/manifests/kit.manifest`）是 hermes-kit 自身的文件清单，**不包含**组件部署清单。方案缺少对 `kit.manifest` 最终格式的完整设计。

**建议：** 在方案中补充 `kit.manifest` 的完整格式设计，含 `target:` 字段如何区分 plugins/ scripts/ lib/ 的具体示例。

---

## 六、问题清单汇总

| 优先级 | ID | 问题 | 位置 | 影响 | 修复难度 |
|:------:|:--:|------|:----:|:----:|:--------:|
| 🔴 P0 | P0-1 | skillopt-sleep import 路径断裂（兄弟 vs 子目录） | §7 R3, §8.5 | SkillOpt 夜间运行完全停摆 | 低（改 1 行代码或改方案） |
| 🟡 P1 | P1-1 | cron-wrappers 拆分遗漏 `skillopt-runner/skillopt-nightly-run.sh` | §8.2 拆分表 | skillopt nightly run 丢失 | 低 |
| 🟡 P1 | P1-2 | cron-wrappers 遗漏 `flywheel-health-report.py` 和 `backfill-scope.py` | §8.2 拆分表 | 飞轮报告功能不完整 | 低 |
| 🟡 P1 | P1-3 | cron-common.sh 源路径 `scripts/cron_common.sh` 不在 cron-wrappers 下 | §8.1 | `git mv` 路径错误 | 低 |
| 🟢 P2 | P2-1 | 缺少 `kit.manifest` 完整格式设计 | §4 | 后期返工风险 | 低 |
| 🟢 P2 | P2-2 | 缺少渐进式搬移试点 | §5 | 批量搬移一次失败风险高 | 低 |
| 🟢 P2 | P2-3 | `kit config get` 需要 Phase 1+2 就可用 | §5 | bash 脚本配置读取依赖 | 中 |

---

## 七、整体评估和建议

### 总体评价

Hermes-Kit v2 产品化改造方案**架构设计合理、目标清晰、风险识别充分**。方案从"shell 包装器"到"统合产品"的升级路径正确，搬移+配置统一+CLI 统一的三阶段设计完整。

### 关键建议

1. **修复 P0 后执行：** 必须先解决 skillopt-sleep import 路径问题（方案 A 或 B），否则搬移后 SkillOpt 彻底停摆
2. **补充遗漏文件：** 在 cron-wrappers 拆分和 flywheel manifest 中补充 `flywheel-health-report.py`、`skillopt-nightly-run.sh` 等
3. **先做小规模试点：** 先搬 1 个非关键组件验证流程，再批量执行
4. **提前实现 `kit config get`：** 在 Phase 1+2 中提供轻量版本，供 bash 脚本读取配置
5. **全量备份：** 执行前备份 `/root/.hermes/` 全目录
6. **更新 skillopt-runner import 路径文档：** 无论选择方案 A 还是 B，更新 `skillopt_runner.py` 中的路径注释

### 预计工作量（修正后）

| Phase | 原估计 | 修正后 | 说明 |
|:-----:|:------:|:------:|------|
| 1+2 | 2-3h | 3-4h | 增加遗漏文件处理 + 试点 |
| 3 | 2-3h | 2-3h | 不变 |
| 4 | 2-3h | 1-2h | 减少，因试点了 |
| **合计** | **6-9h** | **6-9h** | 总工作量不变，但 Phase 1+2 扩大的同时 Phase 4 缩小 |

---

## 附录：交叉验证清单

### A. deploy/projects/*.sh — PROJECT_SRC_REL 对照表

| 文件 | 当前值 | 方案新值 | 评估 |
|------|--------|---------|:----:|
| `knowledge-navigation.sh` | `plugins/knowledge-navigation` | `scripts/hermes-kit/components/knowledge-navigation` | ✅ 正确 |
| `knowledge-tree-plugin.sh` | `plugins/knowledge-tree-plugin` | `scripts/hermes-kit/components/knowledge-tree-plugin` | ✅ 正确 |
| `knowledge-tree-builder.sh` | `scripts/knowledge-tree-builder` | `scripts/hermes-kit/components/knowledge-tree-builder` | ✅ 正确 |
| `clustering-analysis-v3.sh` | `scripts/clustering-analysis-v3` | `scripts/hermes-kit/components/clustering-analysis` | ✅ 正确（文件名也需改） |
| `memory-cleanup.sh` | `scripts/memory-cleanup` | `scripts/hermes-kit/components/memory-cleanup` | ✅ 正确 |
| `skillopt-runner.sh` | `scripts/skillopt-runner` | `scripts/hermes-kit/components/skillopt-runner` | ✅ 正确 |
| `skillopt-sleep.sh` | `scripts/skillopt-sleep` | 删除（子目录） | ⚠️ 见 P0-1 |
| `dream-synth.sh` | `scripts/dream-synth` | `scripts/hermes-kit/components/dream-synth` | ✅ 正确 |
| `self-evolving.sh` | `scripts/self-evolving` | `scripts/hermes-kit/components/self-evolving` | ✅ 正确 |
| `system-health-check.sh` | `scripts/system-health-check` | `scripts/hermes-kit/components/system-health-check` | ✅ 正确 |
| `cron-common.sh` | `scripts` | `scripts/hermes-kit/components/cron` | ✅ 正确（但 git mv 路径待确认） |
| `cron-wrappers.sh` | `scripts/cron-wrappers` | 删除→flywheel.sh + cron.sh | ✅ 正确 |
| `daily-learn.sh` | `scripts/cron-wrappers/daily-learn` | `scripts/hermes-kit/components/cron/daily-learn` | ✅ 正确 |

### B. deploy/manifests/*.manifest — 源路径注释对照

| 文件 | 当前源路径 | 方案新源路径 | 评估 |
|------|-----------|-------------|:----:|
| `knowledge-navigation.manifest` | `plugins/knowledge-navigation/` | `scripts/hermes-kit/components/knowledge-navigation/` | ✅ |
| `knowledge-tree-plugin.manifest` | `plugins/knowledge-tree-plugin/` | `scripts/hermes-kit/components/knowledge-tree-plugin/` | ✅ |
| `knowledge-tree-builder.manifest` | `scripts/knowledge-tree-builder/` | `scripts/hermes-kit/components/knowledge-tree-builder/` | ✅ |
| `clustering-analysis-v3.manifest` | `scripts/clustering-analysis-v3/` | `scripts/hermes-kit/components/clustering-analysis/` | ✅（文件名也需改） |
| `memory-cleanup.manifest` | `scripts/memory-cleanup/` | `scripts/hermes-kit/components/memory-cleanup/` | ✅ |
| `skillopt-runner.manifest` | `scripts/skillopt-runner/` | `scripts/hermes-kit/components/skillopt-runner/` | ✅ |
| `skillopt-sleep.manifest` | `scripts/skillopt-sleep/` | 删除 | ⚠️ 见 P0-1 |
| `dream-synth.manifest` | `scripts/dream-synth/` | `scripts/hermes-kit/components/dream-synth/` | ✅ |
| `self-evolving.manifest` | `scripts/self-evolving/` | `scripts/hermes-kit/components/self-evolving/` | ✅ |
| `system-health-check.manifest` | `scripts/system-health-check/` | `scripts/hermes-kit/components/system-health-check/` | ✅ |
| `cron-common.manifest` | `scripts/` | `scripts/hermes-kit/components/cron/` | ✅ |
| `cron-wrappers.manifest` | `scripts/cron-wrappers/` | 删除→flywheel.manifest + cron.manifest | ⚠️ 见 P1-1, P1-2 |
| `daily-learn.manifest` | `scripts/cron-wrappers/daily-learn/` | `scripts/hermes-kit/components/cron/daily-learn/` | ✅ |

### C. 运行时目标路径对比

| 组件 | 当前目标 | 方案目标 | 评估 |
|------|---------|---------|:----:|
| knowledge-navigation | `/root/.hermes/plugins/knowledge-navigation` | 不变 | ✅ |
| knowledge-tree-plugin | `/root/.hermes/plugins/knowledge-tree-plugin` | 不变 | ✅ |
| knowledge-tree-builder | `/root/.hermes/scripts/knowledge-tree-builder` | 不变 | ✅ |
| clustering-analysis | `/root/.hermes/scripts/clustering-analysis-v3` | `/root/.hermes/scripts/clustering-analysis` | ⚠️ 目标路径名也变了 |
| memory-cleanup | `/root/.hermes/scripts/memory-cleanup` | 不变 | ✅ |
| skillopt-runner | `/root/.hermes/skillopt-runner` | 不变 | ✅ |
| skillopt-sleep | `/root/.hermes/skillopt-sleep` | 删除或子目录 | ⚠️ 见 P0-1 |
| cron-wrappers | `/root/.hermes/scripts/` | 不变（共享目录） | ✅ |
| cron-common | `/root/.hermes/lib/` | 不变 | ✅ |
| dream-synth | `/root/.hermes/scripts/dream-synth` | 不变 | ✅ |
| self-evolving | `/root/.hermes/scripts/self-evolving` | 不变 | ✅ |
| system-health-check | `/root/.hermes/scripts/` | 不变 | ✅ |
| daily-learn | `/root/.hermes/scripts/daily-learn` | 不变 | ✅ |

---

*审计报告结束。建议修复 P0 + P1 问题后重新审查方案，再进行实施。*