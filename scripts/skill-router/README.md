# skill-router

> SkillRouter 语义召回后端（数据飞轮增强方案 §2.1，自实现，非拷贝上游源码）。
>
> 把 skill 召回从通用 embedding（远端 API / bge-m3）升级为 SkillRouter 专用 **bi-encoder 召回 + cross-encoder 重排**。

## 模型（HuggingFace，apache-2.0，经 hf-mirror 下载）

- `pipizhao/SkillRouter-Embedding-0.6B` ← Qwen3-Embedding-0.6B 微调
- `pipizhao/SkillRouter-Reranker-0.6B` ← Qwen3-Reranker-0.6B 微调

## 设计要点

- **懒加载**：首次调用才加载模型（避免拖慢 gateway 启动）
- **线程安全**：模块级单例 + 锁
- **失败即降级**：任何加载/推理异常都抛出，由 skill_matcher 回退到原 API 后端
- **默认不启用**：skill_matcher 仅在 `KN_SKILL_EMBEDDING_BACKEND=skillrouter` 时调用本模块

## 组成

| 文件 | 说明 |
|------|------|
| `backend.py` | 语义召回后端（embedding + reranker，懒加载单例） |
| `build_cache.py` | 全量 skill embedding 缓存构建脚本（一次性，后台运行） |

## 缓存构建

```bash
python3 scripts/skill-router/build_cache.py
```

构建 `~/.hermes/models/skillrouter/skill_embeddings.npz`（423 条 skill，CPU 约 15-20 分钟）。
增量恢复：每 `ENCODE_BATCH`(32) 条写 checkpoint，中断后重启跳过已编码批次，不会白跑。

## 路径配置（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `KN_SKILLROUTER_EMBEDDING_DIR` | `/root/.hermes/models/skillrouter/embedding` | embedding 模型目录 |
| `KN_SKILLROUTER_RERANKER_DIR` | `/root/.hermes/models/skillrouter/reranker` | reranker 模型目录 |

## 启用

在知识导航配置中设置 `KN_SKILL_EMBEDDING_BACKEND=skillrouter`，skill_matcher 即切换到 SkillRouter 后端（加载失败自动降级原 API）。
