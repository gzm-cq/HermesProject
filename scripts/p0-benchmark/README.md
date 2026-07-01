# P0 Benchmark 测试框架

> P0 优化 Benchmark 测试框架 — 验证 P0-1/2/3 的性能提升和准确率。

## 概述

本框架用于验证数据飞轮优化项目中三个 P0 性能优化的实际效果：

| 优化项 | 验证指标 |
|--------|----------|
| **P0-1** Skill Matcher 关键词预筛选 | 延迟 < 1s；Token 节省 ≥ 85%；准确率 ≥ 95% |
| **P0-2** pgvector 去重下推 | 1000 条 10x+；5000 条 50x+；10000 条 100x+ |
| **P0-3** LLM 调用合并 | 调用减少 ≥ 50%；知识点数量/类型分布差异 < 5% |

## 安装

```bash
cd scripts/p0-benchmark
pip install -e .
```

## 使用

```bash
# 运行所有 Benchmark
p0-benchmark all

# 运行单个 Benchmark
p0-benchmark p0-1 --queries 100
p0-benchmark p0-2 --sizes 1000,5000,10000
p0-benchmark p0-3 --articles 50

# 指定配置文件
p0-benchmark all --config config/default.yaml
```

## 验收标准

### P0-1: Skill Matcher 关键词预筛选
- ✅ 延迟从 ~3s 降到 < 1s
- ✅ LLM token 减少 ~85%
- ✅ 与全量 LLM 结果一致性 ≥ 95%

### P0-2: pgvector 去重下推
- ✅ 1000 条知识库时，去重速度提升 10x+
- ✅ 5000 条知识库时，去重速度提升 50x+
- ✅ 10000 条知识库时，去重速度提升 100x+
- ✅ 去重结果与内存扫描一致性 100%（精确去重）

### P0-3: LLM 调用合并
- ✅ LLM 调用次数减少 ~50%（从 2N 到 N）
- ✅ 建树质量（知识点数量）差异 < 5%
- ✅ 知识点类型分布差异 < 5%

## 目录结构

```
src/p0_benchmark/
├── __init__.py
├── __main__.py
├── cli.py              # CLI 入口
├── config.py           # 配置管理
└── core/
    ├── __init__.py
    ├── skill_benchmark.py   # P0-1: Skill Matcher Benchmark
    ├── dedup_benchmark.py  # P0-2: pgvector 去重 Benchmark
    └── llm_benchmark.py    # P0-3: LLM 合并调用 Benchmark
```

## 输出

Benchmark 结果保存到 `reports/p0-benchmark-report-{timestamp}.json`，包含：
- 各优化项的详细测试结果
- 验收标准通过情况
- 性能指标（延迟、加速比等）
