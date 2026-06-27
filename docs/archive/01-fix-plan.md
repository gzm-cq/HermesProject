# 01 Fix Plan — 记忆/知识系统

> **文档状态：历史修复计划 / 已执行后的归档**  
> 本文是从 review 转出的执行计划，不代表当前未完成清单。修复后最终状态见 `03-post-fix-audit-2026-06-15.md`。


> 来源：`00-review-summary.md`
> 目标：把 review 发现转为后续可执行修复任务。
> 注意：本文件是修复计划，不代表已修改业务代码。执行前应逐批确认。

## 执行原则

1. 先关写入安全，再关召回正确性，再关性能/维护性。
2. 所有源码修改走 `/mnt/d/HermesProject`，再通过 `deploy/deploy.sh` 部署。
3. 插件/脚本不是 Hermes 核心源码；当前任务均不需要修改 Hermes 核心。
4. 所有生产写入类动作必须有 dry-run、备份、人工确认。
5. 每批修复后跑对应测试 + import smoke + 必要 DB 只读验证。

---

## Batch 1：写入安全 P0（最高优先级）

### T1. 修正 memory-cleanup-daily-dryrun 行为

| 字段 | 内容 |
|---|---|
| 来源 | C-runtime-memory-review / 00-summary P0-02 |
| 当前状态 | cron 名为 `memory-cleanup-daily-dryrun`，但 wrapper 实际执行 `bash run.sh --vote 1 --apply` |
| 目标状态 | daily cron 只 dry-run，不修改 MEMORY.md/USER.md；apply 只能人工触发 |
| 期望目标 | 每日清理只产生报告，不会自动误删长期记忆/用户偏好 |
| 改动位置 | 部署脚本或 `/root/.hermes/scripts/memory-cleanup` 对应 wrapper；源码侧补同名脚本/manifest |
| 工作量 | 小：0.5 天 |
| 检查方法 | cron list 确认脚本；运行脚本不产生 MEMORY/USER diff |
| 前置依赖 | 无 |

### T2. 拆分 clustering weekly wrapper 的 destructive apply

| 字段 | 内容 |
|---|---|
| 来源 | C-runtime-memory-review / 00-summary P0-03/P0-04 |
| 当前状态 | `cron_wrapper.sh` 串联 quality、long_memory_governance --apply、dedup_minhash --apply、clustering apply、Feishu；最近 120s timeout |
| 目标状态 | weekly 默认只 report-only/dry-run；destructive steps 单独脚本 + 人工确认 + 更长 timeout |
| 期望目标 | 聚类失败不会连带触发删除/写库；cron 超时可定位到具体步骤 |
| 改动位置 | `scripts/clustering-analysis-v3/scripts/cron_wrapper.sh`、cron job 脚本 `clustering-weekly.sh` |
| 工作量 | 中：1 天 |
| 检查方法 | cron dry-run 成功；不出现 `--apply`；日志按 step 输出 |
| 前置依赖 | 无 |

### T3. 建立 apply 型命令安全 gate

| 字段 | 内容 |
|---|---|
| 来源 | Review plan Gate 3 / C 报告 |
| 当前状态 | 多个脚本存在 `--apply`，有的 cron 自动执行 |
| 目标状态 | apply 需要显式 `CONFIRM_APPLY=1` 或交互确认；cron 环境默认无法 apply |
| 期望目标 | 防止自动任务误删/误写生产记忆 |
| 改动位置 | `memory-cleanup/run.sh`、`clustering-analysis-v3/scripts/*.sh/*.py` |
| 工作量 | 中：1 天 |
| 检查方法 | 无确认变量时 apply 退出；dry-run 正常 |
| 前置依赖 | T1/T2 |

---

## Batch 2：知识树可召回 P0

### T4. 在线新增知识点写入自身 k_vector

| 字段 | 内容 |
|---|---|
| 来源 | A-plugin-review / D-e2e-regression-review / 00-summary P0-01 |
| 当前状态 | tree plugin placement 计算 embedding，但新增 knowledge_point 疑似只更新父节点 k_vector，未写自身 k_vector |
| 目标状态 | 每个新增 knowledge_point 插入时写入自身 `knowledge_tree.k_vector` |
| 期望目标 | post_llm_call 学到的新知识下一轮可被 public_api/navigation 召回 |
| 改动位置 | `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/placement.py`、`adapters/database.py`，必要时 builder adapter |
| 工作量 | 中：1 天 |
| 检查方法 | 单测：新增点后 DB row k_vector not null；recall 能命中新点 |
| 前置依赖 | 无 |

### T5. backfill 缺失 knowledge_point/subject k_vector

| 字段 | 内容 |
|---|---|
| 来源 | DB 验证：knowledge_point 5649/2402 with k_vector；subject 54/33 with k_vector |
| 当前状态 | 大量知识点/科目缺 k_vector |
| 目标状态 | 提供安全 backfill dry-run/apply 脚本；先测试库，再生产人工执行 |
| 期望目标 | 科目匹配与局部 attention 不因空向量退化 |
| 改动位置 | `scripts/knowledge-tree-builder/scripts/` 新增 backfill 脚本或修复现有脚本 |
| 工作量 | 中：1 天 |
| 检查方法 | backfill dry-run 统计；apply 后 count(k_vector)=count(*) |
| 前置依赖 | T4 |

### T6. 统一 knowledge_review_queue status

| 字段 | 内容 |
|---|---|
| 来源 | B-builder-clustering-review / DB 查询 `pending=8` |
| 当前状态 | 插入用 `pending`，查询/处理用 `pending_review` |
| 目标状态 | 统一枚举；迁移现有 `pending` 数据 |
| 期望目标 | 待审知识可被 review/consolidation 正常处理 |
| 改动位置 | `scripts/knowledge-tree-builder/src/knowledge_tree_builder/adapters/database.py`、`consolidate/review.py` |
| 工作量 | 小：0.5 天 |
| 检查方法 | 单测覆盖 insert/list；DB status 分布符合预期 |
| 前置依赖 | 无 |

---

## Batch 3：插件链路 P1

### T7. 解耦 Hindsight fail 与 knowledge-tree recall

| 字段 | 内容 |
|---|---|
| 来源 | A-plugin-review P1 / D 报告 |
| 当前状态 | Hindsight 失败/空结果时可能丢弃已可用的 KT recall |
| 目标状态 | Hindsight、KT 两路独立降级；任一路成功即可注入 |
| 期望目标 | Hindsight 短暂异常不导致知识树也失效 |
| 改动位置 | `plugins/knowledge-navigation/src/knowledge_navigation/core/hooks.py` |
| 工作量 | 小：0.5 天 |
| 检查方法 | mock Hindsight fail + KT success，输出含 KT context |
| 前置依赖 | 无 |

### T8. public_api adapter cache 与测试一致

| 字段 | 内容 |
|---|---|
| 来源 | knowledge-tree-plugin test failed 2 |
| 当前状态 | 测试期待 `_adapter_cache/_get_cached_adapter`，源码不存在 |
| 目标状态 | 实现健康检查 cache，或修改测试明确“不缓存”设计；建议实现 cache |
| 期望目标 | 高频 recall 降低 DB 连接开销，测试恢复通过 |
| 改动位置 | `plugins/knowledge-tree-plugin/src/knowledge_tree_plugin/public_api.py`、tests |
| 工作量 | 小：0.5 天 |
| 检查方法 | tree plugin `pytest -q` 全绿 |
| 前置依赖 | 无 |

### T9. 显式处理插件间 turn_gate 依赖

| 字段 | 内容 |
|---|---|
| 来源 | A-plugin-review |
| 当前状态 | tree plugin 直接依赖 `knowledge_navigation.turn_gate`，pyproject 未声明 |
| 目标状态 | 抽 shared turn_gate 或 tree plugin 本地 fallback；依赖声明同步 |
| 期望目标 | 单独加载 tree plugin 不因 navigation 未加载而失败 |
| 改动位置 | `plugins/knowledge-tree-plugin/pyproject.toml`、`hooks.py` |
| 工作量 | 小：0.5 天 |
| 检查方法 | 只设置 tree plugin PYTHONPATH import post_llm_call 成功 |
| 前置依赖 | 无 |

---

## Batch 4：聚类/自进化测试与算法 P1

### T10. 修复 clustering HDBSCAN 依赖/归一化

| 字段 | 内容 |
|---|---|
| 来源 | verification log：HDBSCAN ImportError；B 报告 normalize 风险 |
| 当前状态 | 当前环境 `sklearn.cluster.HDBSCAN` 不可用；聚类前未显式 normalize |
| 目标状态 | 固定 sklearn>=1.3 或使用 hdbscan 包 fallback；输入 L2 normalize |
| 期望目标 | 聚类单测可运行，语义距离更合理 |
| 改动位置 | `scripts/clustering-analysis-v3/pyproject.toml`、`core/clustering.py` |
| 工作量 | 中：1 天 |
| 检查方法 | clustering tests HDBSCAN 相关通过；dry-run 不报 ImportError |
| 前置依赖 | 无 |

### T11. 修复 clustering 因果转换测试/逻辑一致性

| 字段 | 内容 |
|---|---|
| 来源 | verification log：causal pairs 3 个失败 |
| 当前状态 | 期望 weight=0.85，实际 0.7；部分 pair 被过滤为空 |
| 目标状态 | 明确新逻辑是对还是测试旧；同步测试与实现 |
| 期望目标 | 因果链生成规则可验证，避免静默错链/漏链 |
| 改动位置 | `core/clustering.py`、`tests/test_clustering.py` |
| 工作量 | 小：0.5 天 |
| 检查方法 | clustering tests 通过 |
| 前置依赖 | T10 |

### T12. batch_embed 数量校验

| 字段 | 内容 |
|---|---|
| 来源 | B 报告 |
| 当前状态 | 部分失败后 zip 静默少更新 |
| 目标状态 | 返回数量不等于输入时 fail-fast 或重试 |
| 期望目标 | 不出现“部分 embedding 丢失但流程成功”的假成功 |
| 改动位置 | clustering/builder embedding 调用处 |
| 工作量 | 小：0.5 天 |
| 检查方法 | mock 少返回向量，断言报错/重试 |
| 前置依赖 | 无 |

### T13. self-evolving RiskLevel 比较修复

| 字段 | 内容 |
|---|---|
| 来源 | verification log：`TypeError: '>' not supported between RiskLevel` |
| 当前状态 | `max(f.severity for f in risk_factors)` 直接比较 Enum |
| 目标状态 | 使用 severity rank 显式比较 |
| 期望目标 | self-evolving 单测全绿 |
| 改动位置 | `scripts/self-evolving/src/self_evolving/operators/refinement.py` |
| 工作量 | 小：0.5 天 |
| 检查方法 | self-evolving `pytest -q` 全绿 |
| 前置依赖 | 无 |

---

## Batch 5：维护性 P2

### T14. 同步版本、README、依赖声明、env 命名

| 字段 | 内容 |
|---|---|
| 来源 | A 报告 P2 |
| 当前状态 | README 版本、pyproject/plugin.yaml、依赖/env 命名不一致 |
| 目标状态 | 版本统一；依赖清单一致；env 兼容别名文档化 |
| 期望目标 | 减少部署/维护误判 |
| 改动位置 | plugin README、pyproject.toml、plugin.yaml、config docs |
| 工作量 | 小：0.5 天 |
| 检查方法 | grep 版本一致；import smoke |
| 前置依赖 | 主要 P0/P1 完成后 |

---

## 推荐执行顺序

1. T1 → T2 → T3：先阻断自动写入风险。
2. T4 → T5 → T6：保证知识树“写入即可召回”。
3. T7 → T8 → T9：保证插件链路稳定。
4. T10 → T11 → T12 → T13：恢复测试与算法 gate。
5. T14：维护性收尾。

## 每批完成后的验证命令

```bash
# 插件
cd /mnt/d/HermesProject/plugins/knowledge-navigation && PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src pytest -q
cd /mnt/d/HermesProject/plugins/knowledge-tree-plugin && PYTHONPATH=src:/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src pytest -q

# Builder / clustering / memory / self-evolving
cd /mnt/d/HermesProject/scripts/knowledge-tree-builder && PYTHONPATH=src pytest -q
cd /mnt/d/HermesProject/scripts/clustering-analysis-v3 && PYTHONPATH=src pytest -q
cd /mnt/d/HermesProject/scripts/memory-cleanup && PYTHONPATH=src pytest -q
cd /mnt/d/HermesProject/scripts/self-evolving && PYTHONPATH=src pytest -q

# import smoke
PYTHONPATH=/mnt/d/HermesProject/plugins/knowledge-navigation/src:/mnt/d/HermesProject/plugins/knowledge-tree-plugin/src:/mnt/d/HermesProject/scripts/knowledge-tree-builder/src python3 - <<'PY'
import knowledge_navigation, knowledge_tree_plugin
from knowledge_navigation.core.hooks import pre_llm_call
from knowledge_tree_plugin.hooks import post_llm_call
from knowledge_tree_plugin.public_api import recall_from_tree_raw
print('imports ok')
PY
```

## 部署规则

修复并经代码审查后，按项目逐个部署：

```bash
cd /mnt/d/HermesProject
./deploy/deploy.sh plan <project>
./deploy/deploy.sh deploy <project>
./deploy/deploy.sh history <project>
```

插件项目部署后需检查：

```bash
systemctl status hermes-gateway.service --no-pager
```
