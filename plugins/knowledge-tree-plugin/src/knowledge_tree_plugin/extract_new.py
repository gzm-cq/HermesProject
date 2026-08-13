"""post_llm_call 知识点提取编排。

每次 LLM 调用后，分析对话（user_message + llm_response）中是否包含新知识点。
对大输入采用动态切分 + 有限并行提取，避免一次大上下文 LLM 调用拖慢 hook。
提取后经准入门控过滤建议/配置类无效内容。
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from knowledge_tree_builder.config import AppConfig

logger = logging.getLogger(__name__)

# 预编译正则
_WHITESPACE_CLEAN = re.compile(r"\s+")

# 信号量：限制并发提取数
_SMALL_INPUT_CHARS = 1500
_MEDIUM_INPUT_CHARS = 4000
_LARGE_INPUT_CHARS = 12000
_MAX_PARALLEL_CHUNKS = 3
_MAX_XL_CHUNKS = 6
_JACCARD_DEDUP_THRESHOLD = 0.85
# user 提问通常较短且主要用于提供上下文，硬上限 800 字符，
# 把剩余预算全部让给 assistant 回复（知识点的主要来源）
_USER_BUDGET_CHARS = 800

# 全局并行提取信号量：限制跨请求的 LLM 并发数，避免高频对话瞬间打爆 API 限流
# 默认允许 6 路并发（覆盖单次 XL 输入 6 chunk），可通过环境变量调整
_GLOBAL_EXTRACT_SEMAPHORE = threading.Semaphore(6)

# 准入门控：建议/配置/命令类非知识条目模式（与 admit.py 同规）
_GUARD_SUGGESTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^建议[：:]?"),
    re.compile(r"^改进建议"),
    re.compile(r"^[Nn]ote[：:]"),
    re.compile(r"^TODO[：:]"),
    re.compile(r"^FIXME[：:]"),
    re.compile(r"^注意[：:]"),
    re.compile(r"^说明[：:]"),
    re.compile(r"^方案"),
]
_GUARD_CONFIG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"localhost:[0-9]+|127\.0\.0\.1:[0-9]+"),
    re.compile(r"^部署命令"),
    re.compile(r"^健康检查命令"),
    re.compile(r"curl -s.*localhost"),
]


@dataclass(frozen=True)
class ExtractStrategy:
    """对话提取策略。"""

    mode: str
    max_points: int
    max_chunks: int = 1
    parallel: bool = False


def _head_tail(text: str, budget: int) -> str:
    """在固定预算内保留文本首尾。"""
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    if budget <= 20:
        return text[:budget]
    head = budget // 2
    tail = budget - head - 6
    return f"{text[:head]}\n...\n{text[-tail:]}"


def _build_dialog_text(
    user_message: str,
    llm_response: str,
    *,
    max_input_length: int,
) -> str:
    """构造用于提取的对话文本。

    user 消息通常较短，最多保留 800 字符；assistant 回复是主要知识来源，
    使用剩余预算并保留首尾，避免旧逻辑 user/assistant 各占一半导致预算浪费。
    """
    total_budget = max(max_input_length, min(len(user_message) + len(llm_response), max_input_length * _MAX_XL_CHUNKS))
    user_budget = min(_USER_BUDGET_CHARS, max(1, total_budget // 4))
    response_budget = max(1, total_budget - user_budget)

    user_part = _head_tail(user_message, user_budget)
    response_part = _head_tail(llm_response, response_budget)
    return f"# 对话记录\n\n## 用户提问\n{user_part}\n\n## 回答\n{response_part}"


def _choose_extract_strategy(char_count: int) -> ExtractStrategy:
    """根据输入规模选择 LLM 提取策略。"""
    if char_count <= _SMALL_INPUT_CHARS:
        return ExtractStrategy(mode="small_single", max_points=3)
    if char_count <= _MEDIUM_INPUT_CHARS:
        return ExtractStrategy(mode="medium_single", max_points=5)
    if char_count <= _LARGE_INPUT_CHARS:
        return ExtractStrategy(mode="large_parallel", max_points=10, max_chunks=_MAX_PARALLEL_CHUNKS, parallel=True)
    return ExtractStrategy(mode="xl_parallel", max_points=15, max_chunks=_MAX_XL_CHUNKS, parallel=True)


def _split_text_chunks(text: str, *, max_chunk_chars: int, max_chunks: int) -> list[str]:
    """按结构优先切分文本，必要时退化为字符切分。"""
    if len(text) <= max_chunk_chars:
        return [text]

    # 优先按 markdown 标题/空行段落切分，保留代码块等连续段落的完整性。
    parts = re.split(r"(?=\n#{1,3}\s+)|\n\n+", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        candidate = f"{current}\n\n{part}" if current else part
        if len(candidate) <= max_chunk_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(part) <= max_chunk_chars:
            current = part
        else:
            for i in range(0, len(part), max_chunk_chars):
                chunks.append(part[i : i + max_chunk_chars])
    if current:
        chunks.append(current)

    if len(chunks) <= max_chunks:
        return chunks

    # XL 输入只取首尾和中间均匀采样块，避免无界并发/调用费。
    if max_chunks <= 1:
        return [chunks[0]]
    step = (len(chunks) - 1) / (max_chunks - 1)
    selected: list[str] = []
    seen: set[int] = set()
    for i in range(max_chunks):
        idx = round(i * step)
        if idx not in seen:
            selected.append(chunks[idx])
            seen.add(idx)
    return selected


def _normalize_text(text: str) -> str:
    """用于去重的文本规范化。"""
    return _WHITESPACE_CLEAN.sub("", text.strip().lower())


def _jaccard(a: str, b: str) -> float:
    """字符 bigram Jaccard，相比整词更适合中英混合短知识点。"""
    na = _normalize_text(a)
    nb = _normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    grams_a = {na[i : i + 2] for i in range(max(1, len(na) - 1))}
    grams_b = {nb[i : i + 2] for i in range(max(1, len(nb) - 1))}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)


def _dedup_extracted_points(points: list[str], *, max_points: int) -> list[str]:
    """合并分块 LLM 结果，做 exact + Jaccard 去重。"""
    result: list[str] = []
    seen_exact: set[str] = set()
    for point in points:
        text = str(point).strip()
        if not text:
            continue
        norm = _normalize_text(text)
        if norm in seen_exact:
            continue
        if any(_jaccard(text, existing) >= _JACCARD_DEDUP_THRESHOLD for existing in result):
            continue
        seen_exact.add(norm)
        result.append(text)
        if len(result) >= max_points:
            break
    return result


def _guard_filter_points(points: list[str]) -> list[str]:
    """准入门控：过滤建议/意见/配置/命令类非知识条目。

    与 admit.py 的 _guard_filter 同一套规则，在提取后立即拦截。
    """
    result: list[str] = []
    for text in points:
        t = text.strip()
        if not t:
            continue
        # 建议/标记类
        if any(p.match(t) for p in _GUARD_SUGGESTION_PATTERNS):
            logger.debug("guard_filter 拦截建议类: %s", t[:50])
            continue
        # 配置/命令类
        if any(p.search(t) for p in _GUARD_CONFIG_PATTERNS):
            logger.debug("guard_filter 拦截配置类: %s", t[:50])
            continue
        result.append(text)
    return result


def _extract_one_chunk(
    chunk_text: str,
    title: str,
    *,
    min_length: int,
    api_url: str,
    api_key: str,
    model: str,
    max_points: int,
    llm_retries: int = 1,
    llm_timeout_seconds: int = 30,
) -> list[str]:
    """执行单块 LLM 知识提取。

    通过全局信号量 _GLOBAL_EXTRACT_SEMAPHORE 限速，
    避免高频对话瞬间打爆 LLM API 限流。
    """
    from knowledge_tree_builder.phase.merged import analyze_and_split

    with _GLOBAL_EXTRACT_SEMAPHORE:
        cfg = AppConfig(
            llm_api_url=api_url,
            llm_api_key=api_key,
            llm_model=model,
            extract_temperature=0.0,
            max_candidates_per_article=max_points,
            article_max_chars=max(len(chunk_text), 1),
            llm_retries=llm_retries,
            llm_request_timeout_seconds=llm_timeout_seconds,
        )
        atomics, _ = analyze_and_split(chunk_text, title, config=cfg)
        return [a["text"] for a in atomics if len(a.get("text", "")) >= min_length]


def extract_from_dialog(
    user_message: str,
    llm_response: str,
    *,
    min_length: int = 10,
    max_input_length: int = 4000,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
    llm_retries: int = 1,
    llm_timeout_seconds: int = 30,
) -> list[str]:
    """从对话中提取新的知识点。

    小输入走单次 LLM；大输入按结构切分后最多 3 路并行提取，最后合并去重。
    """
    dialog_text = _build_dialog_text(
        user_message=user_message,
        llm_response=llm_response,
        max_input_length=max_input_length,
    )
    strategy = _choose_extract_strategy(len(dialog_text))
    chunk_size = max(500, max_input_length)
    chunks = _split_text_chunks(
        dialog_text,
        max_chunk_chars=chunk_size,
        max_chunks=strategy.max_chunks,
    )

    try:
        if not strategy.parallel or len(chunks) == 1:
            texts = _extract_one_chunk(
                chunks[0],
                "对话知识提取",
                min_length=min_length,
                api_url=api_url,
                api_key=api_key,
                model=model,
                max_points=strategy.max_points,
                llm_retries=llm_retries,
                llm_timeout_seconds=llm_timeout_seconds,
            )
        else:
            texts = []
            max_workers = min(_MAX_PARALLEL_CHUNKS, len(chunks))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _extract_one_chunk,
                        f"# 对话知识提取分块\n\n块信息：第 {idx + 1}/{len(chunks)} 块。\n\n{chunk}",
                        f"对话知识提取-块{idx + 1}",
                        min_length=min_length,
                        api_url=api_url,
                        api_key=api_key,
                        model=model,
                        max_points=max(3, strategy.max_points // len(chunks)),
                        llm_retries=llm_retries,
                        llm_timeout_seconds=llm_timeout_seconds,
                    ): idx
                    for idx, chunk in enumerate(chunks)
                }
                ordered: list[tuple[int, list[str]]] = []
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        ordered.append((idx, future.result()))
                    except Exception as e:
                        logger.warning(
                            "dialog chunk extraction failed",
                            extra={"chunk_index": idx, "error": f"{type(e).__name__}: {e}"},
                        )
                for _, chunk_points in sorted(ordered, key=lambda item: item[0]):
                    texts.extend(chunk_points)
    except Exception as e:
        logger.warning(
            "extract_from_dialog failed",
            extra={"error": f"{type(e).__name__}: {e}"},
        )
        return []

    # 准入门控：过滤建议/配置/命令类非知识条目（与 admit.py _guard_filter 同规）
    texts = _guard_filter_points(texts)

    deduped = _dedup_extracted_points(texts, max_points=strategy.max_points)
    if deduped:
        logger.info(
            "dialog knowledge extraction",
            extra={
                "raw_count": len(texts),
                "filtered_count": len(deduped),
                "strategy": strategy.mode,
                "chunks": len(chunks),
            },
        )
    return deduped