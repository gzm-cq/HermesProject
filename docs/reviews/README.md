# Reviews 目录说明

> 本目录是 2026-06-15 记忆/知识系统审查的过程归档。不要把单个中间文件里的 FAIL 当作当前状态。

## 阅读顺序

1. `spec-memory-knowledge-review-execution-2026-06-15.md` — 当轮审查执行规范。
2. `A-plugin-review.md`、`B-builder-clustering-review.md`、`C-runtime-memory-review.md`、`D-e2e-regression-review.md` — 四条线只读审查结果，记录修复前问题。
3. `00-review-summary.md` — 汇总修复前 FAIL 状态和 P0/P1 风险。
4. `01-fix-plan.md` — 从审查结果转出的历史修复计划。
5. `02-verification-log.md` — 修复过程验证日志，包含过程中的失败输出。
6. `03-post-fix-audit-2026-06-15.md` — **最终修复后审计，当前阅读时优先以此文件为准**。

## 状态规则

- `00/01/02` 和 `A/B/C/D` 是历史过程文件，保留失败证据，不代表当前状态。
- 当前状态应优先查：实际源码、deploy manifest、运行时验证、`03-post-fix-audit-2026-06-15.md`。
- 如果后续新增一轮 review，请新建日期文件，不要覆盖本轮历史记录。
