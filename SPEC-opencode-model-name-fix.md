# SPEC: 修复 opencode.jsonc 模型名配置错误

## 问题描述
2026-08-12 三模型评估中发现 `opencode.jsonc` 中 sensenova 模型名配置错误:
- 配置名: `bifrost/sensenova/sensenova-6.7-flash-lite` (不存在于 Bifrost)
- 实际名: `bifrost/sensenova/sensenova-6.8-flash-lite` (Bifrost 实际注册)

影响范围:
- `small_model` 配置项: 指向 6.7, 导致轻量级任务无法使用 sensible 模型
- `explore agent` 模型: 指向 6.7, 探索类 agent 失效
- `provider.bifrost.models` 列表: 注册名 6.7 但实际不存在

## 改动文件
- `/mnt/d/HermesProject/.opencode/opencode.jsonc`

## 改动清单 (P0)
1. `model` — 不动(正确, 指向 s-deepseek-v4-flash)
2. `small_model` — `sensenova-6.7-flash-lite` → `sensenova-6.8-flash-lite`
3. `agent.explore.model` — `sensenova-6.7-flash-lite` → `sensenova-6.8-flash-lite`
4. `provider.bifrost.models` — 键名 `sensenova/sensenova-6.7-flash-lite` → `sensenova/sensenova-6.8-flash-lite`

## 验收标准
1. `opencode models bifrost` 正确列出 7 个模型,含 `sensenova-6.8-flash-lite`
2. `opencode run --model bifrost/sensenova/sensenova-6.8-flash-lite` 能正常调用
3. JSONC 语法验证通过 (strip 注释后 json.loads 成功)
4. 新增 `sensenova-6.8-flash-lite` 配置后, 不应影响其他模型

## 回滚方案
```bash
cd /mnt/d/HermesProject
git checkout -- .opencode/opencode.jsonc
```