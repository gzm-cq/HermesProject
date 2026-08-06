# SPEC: max_tokens 统一提升到 ≥2048（适配 sensenova-6.7-flash-lite fallback）

## 背景
sensenova-6.7-flash-lite 在 Bifrost fallback 链路中（第1 fallback），该模型要求 max_tokens≥512 才能返回完整 JSON，否则 finish_reason=length。安全起见统一提升到 ≥2048。

## 改动文件列表（全部在 /mnt/d/HermesProject/）

### P0 — Router LLM 调用（fallback 必挂）
1. `plugins/knowledge-navigation/src/knowledge_navigation/core/router.py:261`
   - `"max_tokens": 512` → `"max_tokens": 2048`
   - 注释更新：`# Round3: 512→2048 适配 sensenova-6.7-flash-lite fallback（需≥2048返回完整JSON）`

### P1 — self-evolving operators
2. `scripts/self-evolving/src/self_evolving/operators/recombination.py:183`
   - `max_tokens=512` → `max_tokens=2048`
   - 加注释：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output`
3. `scripts/self-evolving/src/self_evolving/operators/revision.py:229`
   - `max_tokens: int = 1024` → `max_tokens: int = 2048`
   - 加注释：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output`

### P2 — llm_client defaults（json_mode=True，必须≥2048）
4. `scripts/memory-cleanup/src/memory_cleanup/adapters/llm_client.py:49`
   - `max_tokens: int = 3000` → **保留**（已≥2048）
5. `scripts/memory-cleanup/src/memory_cleanup/adapters/llm_client.py:225`
   - `max_tokens=800` → `max_tokens=2048`
   - 加注释：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output`
6. `scripts/recall-eval/src/recall_eval/adapters/llm_client.py:66`
   - `max_tokens: int = 1000` → `max_tokens: int = 2048`
   - 加注释：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output`
7. `scripts/recall-eval/src/recall_eval/adapters/llm_client.py:170,214,259`
   - `max_tokens=1000` → `max_tokens=2048`（3处调用点）
   - 加注释：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output`
8. `scripts/self-evolving/src/self_evolving/adapters/llm_client.py:40`
   - `max_tokens: int = 1024` → `max_tokens: int = 2048`
   - 加注释：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output`

### P3 — dream-synth（非关键路径，但统一）
9. `scripts/dream-synth/scripts/dream-daily.py:107`
   - `max_tokens=1024` → `max_tokens=2048`
   - 加注释：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output`

### P4 — skillopt-sleep backend（多处，统一默认值）
10. `scripts/skillopt-sleep/skillopt_sleep/backend.py:348,359,591,739,915,1015,1117,864`
    - `_call(..., max_tokens: int = 1024)` → `_call(..., max_tokens: int = 2048)`（所有函数签名默认值）
    - `_cached_call(..., max_tokens: int = 1024)` → `_cached_call(..., max_tokens: int = 2048)`
    - `_cached_call(..., max_tokens=512)` → `_cached_call(..., max_tokens=2048)`（391行、407行）
    - `_cached_call(..., max_tokens=1024)` → `_cached_call(..., max_tokens=2048)`（432行）
    - `_call(p, max_tokens=2048)`（553行）→ **保留**（已≥2048）
    - `_call(..., max_tokens=600)`（slow_update.py:134）→ `_call(..., max_tokens=2048)`
    - `_call(..., max_tokens=800)`（llm_miner.py:121）→ `_call(..., max_tokens=2048)`
    - `_call(..., max_tokens=1024)`（rollout.py:142）→ `_call(..., max_tokens=2048)`

### P5 — Hermes config.yaml（正确配置方向，Bug #11443修复后生效）
11. `/root/.hermes/config.yaml` — model section 下添加 `max_tokens: 2048`

## ⚠️ 重要约束
- **所有改动必须加中文注释**说明原因：`# min 2048 for sensenova-6.7-flash-lite fallback JSON output (sensenova-6.7-flash-lite需≥512返回完整JSON，安全值≥2048)`
- **不改**已经 ≥2048 的值（如 memory-cleanup llm_client.py:49 的默认值3000、knowledge-tree-builder的3072/4096等）
- **不改** skillopt-sleep backend.py:553（已为2048）和 knowledge-tree-builder/namer.py:57（max_tokens=32是命名任务，输出极短，不需要改）

## 验收标准
```bash
# 语法检查所有改动文件
python3 -m py_compile <每个改动文件>

# git diff --stat 确认改动范围正确
cd /mnt/d/HermesProject && git diff --stat

# grep 验证没有 <2048 的硬编码残留（排除 namer.py:32、已≥2048的值、budget.py的token计数逻辑）
grep -rn "max_tokens" /mnt/d/HermesProject/ --include="*.py" | grep -v "__pycache__" | grep -v "node_modules" | grep -v "test" | grep -v ".git" | grep -E "=\s*(5[0-9]{2}|[6-9][0-9]{3}|[1][0-9]{3})" | grep -v "minimax_backend\|qwen_backend\|budget\|config\|train\|trainer\|__init__\|model/qwen_backend\|model/minimax_backend\|skillopt/config\|skillopt/engine/trainer\|skillopt/model/__init__"

# deploy knowledge-navigation plugin（router.py改动需要部署）
cd /mnt/d/HermesProject && ./deploy/deploy.sh deploy knowledge-navigation --yes

# Hermes config.yaml改动后重启 Gateway
systemctl restart hermes-gateway
```
