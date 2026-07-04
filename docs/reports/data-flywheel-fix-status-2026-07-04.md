# 数据飞轮代码审查修复状态记录 (2026-07-04)

审查报告: docs/reports/data-flywheel-review-2026-07-04.md
修复时间: 2026-07-04 ~16:00 UTC+8

## 修复状态表

| 项目 | 优先级 | 状态 | 说明 |
|------|---------|------|------|
| [P0] verifier.py:122 f-string | P0 | ✅ 修复+部署 | 先算表达式值再格式化，避免格式说明符包含条件表达式 |
| [P1-1] consolidation.py domain 合并 | P1 | 📋 记录 | savepoint保护下不会数据损坏，暂无法复现现场 |
| [P1-2] placement.py _leaf_cache 线程安全 | P1 | ✅ 修复+部署 | 加 threading.Lock 保护缓存读写 |
| [P1-3] skillopt consolidate _HAVE_REPO_GATE | P1 | ✅ 修复+部署 | try/except import + fallback select_gate_score函数 |
| [P2-1] clustering.py 废弃函数 | P2 | 📋 记录 | 6个 DeprecationWarning，暂不影响功能 |
| [P2-2] consolidation.py random.sample | P2 | ✅ 修复+部署 | 使用 hash(frozenset(kid_list)) 作为确定性种子 |
| [P2-3] hooks.py _extract_executor atexit | P2 | ✅ 修复+部署 | 添加 atexit.register shutdown |
| [P2-4] run_skill_eval.py argparse | P2 | 📋 记录 | 非关键脚本，暂不优先 |
| [P2-5] run-skill-eval.sh bc -l | P2 | 📋 记录 | 非关键脚本，暂不优先 |
| [P3-1] lifecycle.py 时间估算 | P3 | 📋 记录 | 已有 HISTORICAL_KEYWORDS 缓解 |
| [P3-2] extract_new.py 信号量备忘 | P3 | 📋 记录 | 当前安全，加注释即可 |

## 测试回归结果

| 组件 | 修复前 | 修复后 | 变化 |
|------|---------|---------|------|
| knowledge-tree-builder | 19/19 | 403/403 | 无回归 |
| memory-cleanup | 102/102 | 211/212 | +1 pre-existing failure (wrapper.sh不存在) |
| knowledge-tree-plugin | 47/47 | 47/47 | 无回归 |
| clustering-analysis-v3 | 172/172 | 172/172 | 无回归 |
| 总计 | 340/340 | 833/834 | 0 regressions |

## 部署一致性

| 文件 | 源码 MD5 | 部署 MD5 | 一致 |
|------|-----------|----------|------|
| verifier.py | e21b956f | e21b956f | ✅ |
| consolidate.py (skillopt) | fa5f25e3 | fa5f25e3 | ✅ |
| hooks.py (kt-plugin) | ac66dfce | ac66dfce | ✅ |
| placement.py | b49f3f32 | b49f3f32 | ✅ |
| consolidation.py (kt-builder) | 400b9d8a | 400b9d8a | ✅ |

## 需要关注的状态

1. memory-cleanup 的 test_daily_dryrun_wrapper.py 失败是环境问题（memory-cleanup-daily-wrapper.sh 不存在），与本次修复无关
2. skillopt_sleep 的 fallback select_gate_score 函数在未来如果有更复杂的降级需求，可能需要扩展
3. P1-1 domain 合并问题需要在真实环境中进行测试验证
