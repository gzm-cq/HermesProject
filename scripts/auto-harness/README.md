# auto-harness

> 数据飞轮增强方案（`docs/融合计划/20260822-数据飞轮增强执行方案.md` §3.1）的 **skill 回归验证 + 失败模式挖掘** 工具（P1-1，自实现，不拷贝上游）。
>
> 用于自进化闭环：skill 被自动优化后验证"改动未破坏可加载性"，并从 skillopt-sleep 的失败轨迹中挖掘可复现的失败模式。

## 组成

| 文件 | 说明 |
|------|------|
| `regression_tester.py` | `RegressionTester` — skill 修改后的分层回归验证 |
| `weakness_miner.py` | `WeaknessMiner` — 从 skillopt-sleep staging 报告聚类失败模式 |

## RegressionTester（回归验证）

skill 修改的"回归"本质是 SKILL.md 格式/可加载性验证，故实现为分层验证：

- **L1 轻量检查**（默认开）：frontmatter 完整性 + YAML 可解析 + 内容差异
- **L2 Docker 沙箱**（可选开）：项目有 `tests/` 且 Docker 可用时，容器内跑 pytest

```python
from regression_tester import RegressionTester

tester = RegressionTester()
result = tester.test(
    skill_path="/root/.hermes/skills/devops/memory-weeder/SKILL.md",
    old_content="# old",
    new_content="# new",
)
# result.passed → bool; result.failed_checks → list[str]
```

## WeaknessMiner（失败模式挖掘）

数据源：`/root/.hermes/skillopt-runner/.skillopt-sleep/staging/*/report.json`（含 `gate_action` / `rejected_edits[]`，是真实的"skill 优化被拒"失败轨迹）。

```bash
# 默认离线启发式（零外部依赖）
python weakness_miner.py --staging-dir /root/.hermes/skillopt-runner/.skillopt-sleep/staging

# 启用 LLM 增强挖掘
python weakness_miner.py --staging-dir ... --use-llm-miner

# JSON 输出（供 skillopt_sleep.mine() 消费）
python weakness_miner.py --staging-dir ... --json
```

`run()` 组装 SessionDigest 喂给 `skillopt_sleep.mine()`，产出 TaskRecord 列表，接入 skillopt-sleep 的自优化闭环。

## 测试

```bash
cd scripts/auto-harness
python -m pytest tests/
```

> 本目录为轻量工具集合，无独立包配置/部署清单，直接以脚本方式调用。
