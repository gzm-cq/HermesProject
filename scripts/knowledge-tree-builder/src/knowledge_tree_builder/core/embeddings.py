"""向量嵌入计算 — API 调用、批量处理"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

import numpy as np

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


def batch_embed(
    texts: list[str],
    base_url: str = "https://api.siliconflow.cn/v1",
    model: str = "BAAI/bge-m3",
    api_key: str = "",
    batch_size: int = 20,
) -> list[list[float]] | None:
    """批量获取文本的 embedding 向量。

    API 格式兼容 SiliconFlow / OpenAI 的 embeddings 接口。

    Args:
        texts: 待嵌入文本列表
        base_url: API 基础地址
        model: embedding 模型名
        api_key: API 密钥
        batch_size: 每批最大文本数

    Returns:
        list[list[float]]: 每个文本对应的 embedding 向量列表
        None: 全部失败时返回 None
    """
    if requests is None:
        return None

    all_embeddings: list[list[float]] = []
    url = f"{base_url.rstrip('/')}/embeddings"

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_start = len(all_embeddings)
        for attempt in range(3):
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    json={"model": model, "input": batch},
                    timeout=(10, 60),
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                embeddings_raw = data.get("data", [])
                # 按 index 排序保证顺序；数量不一致时视为批失败，避免
                # embedding 与输入文本错位后污染知识树。
                embeddings_raw.sort(key=lambda x: x.get("index", 0))
                batch_embeddings: list[list[float]] = []
                for item in embeddings_raw:
                    emb = item.get("embedding")
                    if emb is not None:
                        batch_embeddings.append(list(emb))
                if len(batch_embeddings) != len(batch):
                    raise ValueError(
                        f"embedding count mismatch: expected {len(batch)}, got {len(batch_embeddings)}"
                    )
                all_embeddings.extend(batch_embeddings)
                break
            except Exception as e:
                logger.warning(
                    "embedding 批次 %d~%d 失败 (attempt %d/3): %s",
                    i, min(i + batch_size, len(texts)), attempt + 1, e,
                )
                if attempt < 2:
                    time.sleep(2**attempt)
                # else: fallthrough — 3 次全部失败，下方补 None 占位
        # 批次全部失败 → 整体返回 None（调用方单层判断即可）
        if len(all_embeddings) == batch_start:
            return None

    return all_embeddings if all_embeddings else None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度"""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """批量计算 embedding 矩阵的余弦相似度矩阵"""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normed = embeddings / (norms + 1e-10)
    return normed @ normed.T
