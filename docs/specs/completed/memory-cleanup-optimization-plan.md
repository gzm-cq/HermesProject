# M-P1-2 实施计划：merge/compress 质量增强

## 背景

基于设计评审结论，**prefilter.py（遗忘曲线预过滤）被否决**——MEMORY.md 是纯平铺记事本（§ 分隔、无元数据、无时间戳），无法提供 `mentioned_at` / `created_at` 字段。

优化方向改为：**增强现有 Phase 1 的 merge 和 compress 质量**，而非新增独立模块。

## 预期收益

| 指标 | 当前基线 | 优化后预期 |
|------|---------|-----------|
| merge 组数 | 3-5 组 | 6-10 组 |
| compress 质量过滤率 | 0% | ~5-10%（过滤低质量输出） |
| 总体 LLM 调用量 | 基线 | -15~20% |

## 修改清单

所有改动集中在 3 个源码文件 + 1 个测试文件。

### 文件 1: `core/prompts.py`

**改动：** 重写 MEMORY 分支的 merge/compress 指令语义

- **merge 指令**：从"多条同主题碎片，合并为一条精简版"→"提取其共同抽象模式/通用版本，而非简单拼接原文"。增加正反示例（错误合并 vs 正确合并）。
- **compress 指令**：在"压缩到 60 字以内"后追加约束——必须保留具体数值/端口/URL/版本号；必须保持可读性；禁止将结论类条目压缩到只剩关键词片段。
- **USER 分支**：merge 指令追加"提取共同偏好/模式的通用版本"说明。

### 文件 2: `core/classifier.py`

在文件末尾新增两个校验函数，**不修改**现有 `classify_all()` 的并行分批逻辑：

- **`validate_merge_quality(entries, merge_list)`** — 过滤掉简单拼接型低质量 merge：
  - 合并文本长度 ＞ 原文总长度 × 80% → 疑似拼接，过滤
  - 多篇均有日期但合并后仍含日期 → 抽象不足，过滤
  - 关键词重叠率 ＜ 30% → 跑题，过滤

- **`validate_compress_quality(entries, compress_list)`** — 过滤遗漏关键事实的 compress：
  - 压缩版 ＜ 10 字符 → 过度压缩，过滤
  - 原文/压缩版 压缩比 ＞ 10:1 → 信息丢失，过滤
  - 遗漏原文中的 IP/URL/端口/路径等实体 → 过滤
  - 中文关键词重叠率 ＜ 30% → 过滤

**调用位置**：在 `classify_all()` 的 return 之前插入校验调用，对 cli.py 完全透明。

### 文件 3: `core/__init__.py`

导出 `validate_merge_quality` 和 `validate_compress_quality`。

### 文件 4: `tests/test_classifier.py`

在现有测试类后追加 2 个测试类：

- **`TestValidateMergeQuality`** — 有效抽象合并应通过 / 简单拼接应过滤 / 含日期应过滤 / 空列表
- **`TestValidateCompressQuality`** — 保留实体应通过 / 过短应过滤 / 遗漏实体应过滤 / 空列表
- **`TestValidateMergeEdgeCases`** — 单条 merge / 纯英文条目 / 跑题 merge

## 不修改的文件

| 文件 | 理由 |
|------|------|
| `cli.py` | 流水线编排不变，校验在 classify_all 内部 |
| `reporter.py` | 报告格式不变 |
| `config.py` / `default.yaml` | 校验阈值硬编码，先 dry-run 验证再决定是否提取为配置 |
| `adapters/` | LLM 调用/文件存储/SessionDB 都不变 |

## 验证方案

### 步骤 1：语法 + 类型检查
```bash
cd D:\HermesProject\scripts\memory-cleanup
python -m py_compile src/memory_cleanup/core/prompts.py
python -m py_compile src/memory_cleanup/core/classifier.py
python -m py_compile src/memory_cleanup/core/__init__.py
```

### 步骤 2：回归测试
```bash
python -m pytest tests/ -v --tb=short
```
确认全部现有测试通过，新增测试覆盖 100%。

### 步骤 3：Prompt 变更验证
确认新 prompt 包含：`"抽象"`/`"通用"`、`"错误合并"`/`"正确合并"`、端口/URL/数值保真度约束。

### 步骤 4：merge/compress 校验验证
使用 `sample_entries` fixture 模拟高质量/低质量的 merge/compress 输出，确认校验函数正确过滤。
