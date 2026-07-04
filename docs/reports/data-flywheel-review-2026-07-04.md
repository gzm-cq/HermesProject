# 数据飞轮全面代码审查报告 (2026-07-04)

审查范围：5个核心组件，约40个文件，5000+行源码
审查技能：requesting-code-review / graph-assisted-code-review / coding-standards / audit-methodology
审查方法：全文件逐行阅读 + 安全扫描 + 部署一致性校验 + 340个测试回归 + 上次P1 bug验证

## P0 — 会崩的（1项）

### [P0] verifier.py:122 — f-string 嵌套条件表达式导致运行时崩溃
- 文件: scripts/memory-cleanup/src/memory_cleanup/core/verifier.py
- 代码: result["note"] = f"... char={char_overlap:.2f if total_kw < 3 else kw_overlap:.2f} ..."
- 问题: Python f-string 的格式说明符 :.2f 被解析为覆盖整个三元表达式，导致 ValueError
- 验证: 已实际执行确认 — AST解析通过但运行时必崩
- 影响: Phase 2 验证中当 has_real_fix=False 且 corrected 非空时触发，验证器异常退出
- 测试盲区: 102个测试全通过但未覆盖此路径

## P1 — 逻辑/性能问题（3项）

### [P1-1] consolidation.py:498-513 — domain 合并时 subject 子节点搬迁
- 问题: subject 子节点搬迁和删除在同一 savepoint 内，FK 约束可能导致合并失败
- 影响: 中 — savepoint 保护下不会数据损坏，但合并成功率降低

### [P1-2] placement.py:25-303 — _leaf_cache 模块级全局变量线程不安全
- 问题: _leaf_cache 和 _leaf_cache_at 无锁保护，_extract_executor(max_workers=2) 并发调用时可能竞争
- 影响: 低概率 — 可能读到 None 导致重复 DB 查询或缓存不一致

### [P1-3] skillopt_sleep/consolidate.py:24 — _HAVE_REPO_GATE 硬编码 True 导致死代码
- 问题: _HAVE_REPO_GATE = True 硬编码，else 分支永远不会执行
- 影响: gate 模块导入失败时整个 consolidate 函数会崩溃而非降级

## P2 — 代码维护性（5项）

- [P2-1] clustering.py — 3个废弃函数仍被测试调用，6个 DeprecationWarning
- [P2-2] consolidation.py:761 — random.sample 无 seed，结果不可复现
- [P2-3] hooks.py (kt-plugin):56 — _extract_executor 无 atexit 清理
- [P2-4] run_skill_eval.py — 手动 sys.argv 参数解析脆弱
- [P2-5] run-skill-eval.sh — shell 浮点比较使用 bc -l 可能不一致

## P3 — 代码风格（2项）

- [P3-1] lifecycle.py — 时间估算启发式可能误判历史日期（已有缓解）
- [P3-2] extract_new.py — 信号量与线程池并发控制重叠（当前安全）

## 上次审查 P1 bug 修复验证

1. consolidate 连接泄漏 → ✅ 已修复 (complex.py:265-266 try/finally)
2. hindsight_list retain 失败未记录 → ✅ 已修复 (memory_store.py)
3. TORCH_AVAILABLE 死代码 → ✅ 确认为误报 (GPU 路径使用)

## 部署一致性

5个核心文件 MD5 完全一致（源码 ↔ 部署）✅

## 安全扫描

硬编码密钥 0项 ✅ | SQL注入 0项 ✅ | Shell注入 0项 ✅ | eval/exec 0项 ✅

## 测试回归

| 组件 | 测试数 | 通过 | 警告 |
|------|--------|------|------|
| knowledge-tree-builder | 19 | 19 | 0 |
| memory-cleanup | 102 | 102 | 0 |
| knowledge-tree-plugin | 47 | 47 | 0 |
| clustering-analysis-v3 | 172 | 172 | 6 deprecation |
| **总计** | **340** | **340** | **0 regressions** |
