"""SkillRouter 语义召回后端（自实现，非拷贝上游源码）。

依据 `docs/融合计划/20260822-数据飞轮增强执行方案.md` 2.1 节：
把 skill 召回从通用 embedding（远端 API / bge-m3）升级为 SkillRouter 专用
bi-encoder 召回 + cross-encoder 重排。

模型（HuggingFace，apache-2.0，经 hf-mirror 下载）：
- pipizhao/SkillRouter-Embedding-0.6B  ←  Qwen3-Embedding-0.6B 微调
- pipizhao/SkillRouter-Reranker-0.6B   ←  Qwen3-Reranker-0.6B 微调

设计要点：
- 懒加载：首次调用才加载模型（避免拖慢 gateway 启动）。
- 线程安全：模块级单例 + 锁。
- 失败即降级：任何加载/推理异常都抛出，由 skill_matcher 回退到原 API 后端。
- 默认不启用：skill_matcher 仅在 KN_SKILL_EMBEDDING_BACKEND=skillrouter 时调用本模块。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── 路径配置（可被环境变量覆盖）──
DEFAULT_EMBEDDING_DIR = os.getenv(
    "KN_SKILLROUTER_EMBEDDING_DIR",
    "/root/.hermes/models/skillrouter/embedding",
)
DEFAULT_RERANKER_DIR = os.getenv(
    "KN_SKILLROUTER_RERANKER_DIR",
    "/root/.hermes/models/skillrouter/reranker",
)

# Qwen3-Embedding 检索指令（query 端使用，passage 端留空）
_EMBED_INSTRUCTION = "Retrieve relevant passages that answer the query."

# Qwen3-Reranker 任务指令
_RERANK_TASK = (
    "Given a user query about an agent task, retrieve the most relevant "
    "skill whose description can help accomplish the task."
)

# ── 懒加载单例 ──
_lock = threading.Lock()
_emb_model = None
_emb_tokenizer = None
_rerank_model = None
_rerank_tokenizer = None
_yes_token_id = None


def _load_embedding() -> None:
    """加载 bi-encoder（Qwen3-Embedding 微调版）。"""
    global _emb_model, _emb_tokenizer
    from transformers import AutoModel, AutoTokenizer  # 懒导入，避免污染无 transformers 的环境

    emb_dir = os.getenv("KN_SKILLROUTER_EMBEDDING_DIR", DEFAULT_EMBEDDING_DIR)
    if not os.path.isdir(emb_dir):
        raise FileNotFoundError(f"SkillRouter embedding 模型目录不存在: {emb_dir}")
    _emb_tokenizer = AutoTokenizer.from_pretrained(emb_dir)
    _emb_model = AutoModel.from_pretrained(
        emb_dir,
        dtype="auto",
        low_cpu_mem_usage=True,
    )
    _emb_model.eval()


def _load_reranker() -> None:
    """加载 cross-encoder（Qwen3-Reranker 微调版）。"""
    global _rerank_model, _rerank_tokenizer, _yes_token_id
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rk_dir = os.getenv("KN_SKILLROUTER_RERANKER_DIR", DEFAULT_RERANKER_DIR)
    if not os.path.isdir(rk_dir):
        raise FileNotFoundError(f"SkillRouter reranker 模型目录不存在: {rk_dir}")
    _rerank_tokenizer = AutoTokenizer.from_pretrained(rk_dir)
    _rerank_model = AutoModelForCausalLM.from_pretrained(
        rk_dir,
        dtype="auto",
        low_cpu_mem_usage=True,
    )
    _rerank_model.eval()
    _yes_token_id = _rerank_tokenizer.convert_tokens_to_ids("yes")


def is_available() -> bool:
    """环境是否具备 SkillRouter 后端（模型目录 + transformers 均存在）。"""
    emb_dir = os.getenv("KN_SKILLROUTER_EMBEDDING_DIR", DEFAULT_EMBEDDING_DIR)
    rk_dir = os.getenv("KN_SKILLROUTER_RERANKER_DIR", DEFAULT_RERANKER_DIR)
    if not (os.path.isdir(emb_dir) and os.path.isdir(rk_dir)):
        return False
    try:
        import transformers  # noqa: F401
    except Exception:
        return False
    return True


def embed_texts(texts: List[str], is_query: bool = False, batch_size: int = 16, max_length: int = 512) -> np.ndarray:
    """批量生成 embedding（float32，L2 归一化）。

    Args:
        texts: 文本列表
        is_query: True 时（仅对 query）附加检索指令前缀
        batch_size: 分批大小，避免一次编码过多文本导致 padding 内存爆炸
        max_length: 截断长度。skill 描述一般 <800 chars（≈200 tokens），
            默认 512 足够，避免 padding 到 8192 导致大批次极慢

    Returns:
        shape=(N, dim) 的归一化向量
    """
    if not texts:
        return np.zeros((0, 1), dtype=np.float32)

    with _lock:
        if _emb_model is None:
            _load_embedding()

    import torch

    results: List[np.ndarray] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        batch = []
        for t in chunk:
            if is_query:
                batch.append(f"Instruct: {_EMBED_INSTRUCTION}\nQuery: {t}")
            else:
                batch.append(t)

        enc = _emb_tokenizer(
            batch,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        with torch.no_grad():
            out = _emb_model(**enc)
        # Qwen3-Embedding：取最后 token 隐藏态作为句向量，再 L2 归一化
        emb = out.last_hidden_state[:, -1, :].float()
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        results.append(emb.numpy().astype(np.float32))
    return np.concatenate(results, axis=0)


def embed_skills_cached(
    skill_texts: List[str],
    cache_path: str = "/root/.hermes/models/skillrouter/skill_embeddings.npz",
) -> np.ndarray:
    """编码 skill 描述并缓存到磁盘（增量更新，skill 变化只重编码变化条目）。

    skill 增删改场景：
    - 新增 skill → 该条文本不在缓存 → 只编码新的 1 条
    - 修改 skill → 该条文本变化 → 只重编码变化的条目
    - 删除 skill → 缓存残留无害（下次清理），不阻塞
    全量命中时零编码，秒级返回。
    """
    import os

    # 读取现有缓存（text → emb 映射）
    cached: dict[str, np.ndarray] = {}
    if os.path.exists(cache_path):
        try:
            data = np.load(cache_path, allow_pickle=True)
            emb = data["emb"].astype(np.float32)
            for t, e in zip(list(data["texts"]), emb):
                cached[str(t)] = e
            logger.info("SkillRouter: 读取缓存 %d 条 %s", len(cached), cache_path)
        except Exception as e:
            logger.warning("SkillRouter: 缓存读取失败，重建: %s", e)
            cached = {}

    # 找出需要新编码的条目（不在缓存中的）
    to_encode = [t for t in skill_texts if t not in cached]
    if to_encode:
        logger.info("SkillRouter: 增量编码 %d/%d 条（缓存命中 %d）", len(to_encode), len(skill_texts), len(skill_texts) - len(to_encode))
        new_emb = embed_texts(to_encode, is_query=False)
        for t, e in zip(to_encode, new_emb):
            cached[t] = e

    # 按传入顺序组装结果（保证返回顺序与 skill_texts 一致）
    result = np.stack([cached[t] for t in skill_texts]) if skill_texts else np.zeros((0, 1024), dtype=np.float32)

    # 写回（保留缓存中所有条目，即使 skill 已删除也无害）
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        all_texts = list(cached.keys())
        all_emb = np.stack(list(cached.values())) if all_texts else np.zeros((0, 1024), dtype=np.float32)
        np.savez(cache_path, emb=all_emb, texts=np.array(all_texts, dtype=object))
    except Exception as e:
        logger.warning("SkillRouter: 缓存写入失败（不影响功能）: %s", e)
    return result


def rerank(query: str, passages: List[str], batch_size: int = 16) -> List[float]:
    """对 (query, passage) 对批量打分，返回与 passages 对齐的分数列表。

    分数取 reranker 在末尾位置对 "yes" token 的 logit（越高越相关）。
    批量推理以压低 CPU 上逐条因果解码的延迟。
    """
    if not passages:
        return []

    with _lock:
        if _rerank_model is None:
            _load_reranker()

    import torch

    prompts = [
        f"<Instruct>: {_RERANK_TASK}\n<Query>: {query}\n<Document>: {p}"
        for p in passages
    ]
    scores: List[float] = []
    for i in range(0, len(prompts), batch_size):
        chunk = prompts[i : i + batch_size]
        enc = _rerank_tokenizer(
            chunk,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=8192,
        )
        with torch.no_grad():
            logits = _rerank_model(**enc).logits[:, -1, :]
        for row in logits:
            scores.append(float(row[_yes_token_id].item()))
    return scores
