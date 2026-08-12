# SPEC: 统一 LLM 模型配置

## 目标

将分散在各脚本、配置文件中的 LLM 模型名统一到一个地方管理，同时保持现有行为不变。

## 现状分析

### 已配置（.env）

```bash
LLM_MODEL=sensenova-6.8-flash-lite          # 通用默认
KT_LLM_MODEL=s-deepseek-v4-flash            # 知识树专用
KN_SKILL_MATCHER_MODEL=sensenova-6.8-flash-lite  # 技能匹配专用
RECALL_EVAL_MODEL=sensenova-6.8-flash-lite  # 召回评估专用
```

### 已配置（systemd env）

```bash
KN_ROUTER_MODEL=sensenova-6.8-flash-lite    # 知识导航 Router
```

### 硬编码默认值（需改为读 env）

| 脚本/模块 | 当前默认值 | 应读 env var |
|-----------|-----------|--------------|
| memory-cleanup | `s-deepseek-v4-flash` | `MEMORY_CLEANUP_LLM_MODEL` |
| knowledge-tree-builder | `s-deepseek-v4-flash` | `KT_LLM_MODEL`（已有） |
| clustering-analysis-v3 | `s-deepseek-v4-flash` | `CLUSTERING_LLM_MODEL` |
| self-evolving | `s-deepseek-v4-flash` | `SELF_EVOLVING_LLM_MODEL` |
| dream-synth | `s-deepseek-v4-flash` | `DREAM_SYNTH_LLM_MODEL` |

### Hermes config.yaml（保持不变）

```yaml
auxiliary.kanban_decomposer.model: sensenova-6.8-flash-lite
auxiliary.background_review.model: sensenova-6.8-flash-lite
model.default: s-deepseek-v4-flash  # 主模型不变
```

## 设计方案

### 1. 统一配置源：`/root/.hermes/.env`

所有模型配置集中在 `.env`，格式：

```bash
# ===== LLM 模型配置 =====
# 主模型（Hermes 对话）
LLM_MODEL_MAIN=s-deepseek-v4-flash

# 轻量模型（高频调用，Router/SkillMatcher/Background等）
LLM_MODEL_LIGHT=sensenova-6.8-flash-lite

# 各子系统覆盖（可选，不设置则继承上面两个）
MEMORY_CLEANUP_LLM_MODEL=              # 空=继承 LLM_MODEL_LIGHT
KT_LLM_MODEL=s-deepseek-v4-flash       # 知识树需要高质量
CLUSTERING_LLM_MODEL=                  # 空=继承 LLM_MODEL_LIGHT
SELF_EVOLVING_LLM_MODEL=               # 空=继承 LLM_MODEL_LIGHT
DREAM_SYNTH_LLM_MODEL=                 # 空=继承 LLM_MODEL_LIGHT
KN_ROUTER_MODEL=sensenova-6.8-flash-lite
KN_SKILL_MATCHER_MODEL=sensenova-6.8-flash-lite
RECALL_EVAL_MODEL=sensenova-6.8-flash-lite
```

**继承规则**：子脚本先查自己的 env var，找不到则查 `LLM_MODEL_LIGHT`，再找不到则查 `LLM_MODEL_MAIN`。

### 2. systemd 服务环境变量

`hermes-gateway.service` 的 Environment 中已包含：
- `KN_ROUTER_MODEL=sensenova-6.8-flash-lite` ✅
- `KN_ROUTER_API_KEY=...` ✅

无需修改。

### 3. cron wrapper 脚本修改

每个 cron wrapper 在调用子脚本前，确保导出正确的 env var：

```bash
# knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh
export KT_LLM_MODEL="${KT_LLM_MODEL:-$LLM_MODEL_MAIN}"

# memory-cleanup/daily_dryrun.sh  
export MEMORY_CLEANUP_LLM_MODEL="${MEMORY_CLEANUP_LLM_MODEL:-$LLM_MODEL_LIGHT}"
```

### 4. Python 脚本修改

每个子脚本的 config.py 中，将硬编码默认值改为读 env：

```python
# Before
llm_model: str = "s-deepseek-v4-flash"

# After  
llm_model: str = os.environ.get("MEMORY_CLEANUP_LLM_MODEL", os.environ.get("LLM_MODEL_LIGHT", "s-deepseek-v4-flash"))
```

## 实施步骤

### Step 1: 更新 `.env`（一次性）

在 `/root/.hermes/.env` 中添加统一配置段。

### Step 2: 修改 Python config.py（5个文件）

| 文件 | env var | 当前默认 |
|------|---------|----------|
| `memory-cleanup/src/memory_cleanup/config.py` | `MEMORY_CLEANUP_LLM_MODEL` | s-deepseek-v4-flash |
| `clustering-analysis-v3/src/clustering_analysis/config.py` | `CLUSTERING_LLM_MODEL` | s-deepseek-v4-flash |
| `self-evolving/src/self_evolving/operators/*.py` (3个) | `SELF_EVOLVING_LLM_MODEL` | s-deepseek-v4-flash |
| `dream-synth/scripts/dream-daily.py` | `DREAM_SYNTH_LLM_MODEL` | s-deepseek-v4-flash |

修改方式：在 `from_env()` 或 config class init 中增加 env var 读取。

### Step 3: 修改 cron wrapper（3个文件）

| 文件 | 添加 export |
|------|-------------|
| `memory-cleanup/daily_dryrun.sh` | `export MEMORY_CLEANUP_LLM_MODEL="${MEMORY_CLEANUP_LLM_MODEL:-$LLM_MODEL_LIGHT}"` |
| `knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh` | `export KT_LLM_MODEL="${KT_LLM_MODEL:-$LLM_MODEL_MAIN}"` |
| `clustering-analysis-v3/run.sh`（如有） | `export CLUSTERING_LLM_MODEL="${CLUSTERING_LLM_MODEL:-$LLM_MODEL_LIGHT}"` |

### Step 4: 验证

```bash
# 检查 .env 加载正常
grep LLM_MODEL /root/.hermes/.env

# 检查各脚本能读到正确模型名
cd /root/.hermes/scripts/memory-cleanup && python3 -c "from src.memory_cleanup.config import AppConfig; print(AppConfig().llm_model)"
cd /root/.hermes/scripts/knowledge-tree-builder && python3 -c "from src.knowledge_tree_builder.config import AppConfig; print(AppConfig().llm_model)"
```

## 回滚策略

每个改动都是添加 env var 读取，不影响未设置 env var 时的行为（fallback 到原默认值）。如需回滚，删除 `.env` 中新增行即可。

## 不变的部分

- Hermes Gateway config.yaml（主模型 s-deepseek-v4-flash）
- LiteLLM/Bifrost 路由配置
- KN_ROUTER_MODEL systemd env（已正确设置）
- embedding/reranker 模型配置（仍用 SiliconFlow）
