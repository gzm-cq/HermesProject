"""向量嵌入计算 — API 调用、批量处理"""

import json
import re
import time
from typing import Any

import numpy as np

# Optional HTTP deps
try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


def call_llm_for_entity(
    texts: list[str],
    *,
    retries: int = 3,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> str:
    """调用 LLM 提取实体（带重试）"""
    if requests is None:
        return "提取失败（requests 未安装）"
    prompt = (
        "从以下记忆中提取核心实体（主题/概念/技术）：\n"
        + "\n---\n".join(texts)
        + "\n\n只返回实体名，不要其他内容。"
    )
    for attempt in range(retries):
        try:
            resp = requests.post(
                api_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=(10, 120),
            )
            resp.raise_for_status()
            content = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            return content if content else "未提取到"
        except Exception:
            if attempt < retries - 1:
                time.sleep(2**attempt)
    return "提取失败"


def _parse_llm_json_response(
    content: str,
) -> dict | None:
    """3 层解析兜底：直接解析 → markdown code block → raw JSON 对象"""
    if not content:
        return None

    # 第 1 层：直接解析
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass

    # 第 2 层：从 markdown 代码块中提取
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # 第 3 层：从任意位置提取顶层 JSON 对象
    m = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", content)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def call_llm_for_entity_with_causal(
    texts: list[str],
    *,
    retries: int = 3,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> tuple[str, list[dict]]:
    """
    调用 LLM 提取实体名 + 因果对（搭便车模式）。

    在同一个 LLM 调用中同时获取实体名和因果对，
    避免额外的一次 LLM 调用。

    Returns:
        (entity_name, causal_pairs)
        causal_pairs: [{"cause_idx": int, "effect_idx": int, "reason": str}, ...]
    """
    if requests is None:
        return "提取失败（requests 未安装）", []

    # 构造带序号列表的 prompt
    numbered_lines = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
    prompt = (
        "你是一个运维知识库的实体提取和因果关系分析专家。\n\n"
        "任务 1：为以下一组相关记忆提取一个聚合实体名（10-20字）。\n"
        "任务 2：分析这组记忆之间是否存在因果关系。\n\n"
        "输出格式（JSON，不要其他内容）：\n"
        '{\n'
        '  "entity_name": "...",\n'
        '  "causal_pairs": [\n'
        '    {"cause_idx": 0, "effect_idx": 2, "reason": "描述了服务器过载导致系统崩溃"},\n'
        '    ...\n'
        '  ]\n'
        '}\n\n'
        "注意：\n"
        "- cause_idx/effect_idx 是记忆列表中的序号（从0开始）\n"
        "- 只有确定存在因果关系时才输出，不要无中生有\n"
        "- 因果关系包括显式（X导致Y）和隐式（磁盘满→写入失败）\n\n"
        "记忆列表：\n"
        f"{numbered_lines}"
    )

    for attempt in range(retries):
        try:
            resp = requests.post(
                api_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=(10, 120),
            )
            resp.raise_for_status()
            content = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                continue

            parsed = _parse_llm_json_response(content)
            if not parsed:
                # JSON 解析失败，将原始内容作为实体名返回
                return content[:80], []

            entity_name = parsed.get("entity_name", "") or content[:80]
            causal_pairs = parsed.get("causal_pairs", [])
            if not isinstance(causal_pairs, list):
                causal_pairs = []

            # 校验每个因果对的完整性
            valid_pairs: list[dict] = []
            for pair in causal_pairs:
                if not isinstance(pair, dict):
                    continue
                cause_idx = pair.get("cause_idx")
                effect_idx = pair.get("effect_idx")
                if not isinstance(cause_idx, int) or not isinstance(effect_idx, int):
                    continue
                if cause_idx < 0 or effect_idx < 0:
                    continue
                if cause_idx >= len(texts) or effect_idx >= len(texts):
                    continue
                if cause_idx == effect_idx:
                    continue
                valid_pairs.append({
                    "cause_idx": cause_idx,
                    "effect_idx": effect_idx,
                    "reason": str(pair.get("reason", "")),
                })

            return entity_name if entity_name else "未提取到", valid_pairs

        except Exception:
            if attempt < retries - 1:
                time.sleep(2**attempt)

    return "提取失败", []


def batch_embed(
    texts: list[str],
    base_url: str,
    model: str,
    api_key: str,
    batch_size: int = 20,
    max_chars: int = 8000,
) -> list[list[float]] | None:
    """批量获取文本 embedding（Hindsight embedding endpoint）.

    SiliconFlow BAAI/bge-m3 对超长 input 会返回 400/code=20015。
    聚类 Phase 4 只需要更新检索向量，不需要完整超长全文，因此在请求前
    截断到保守上限，避免单条超长文本导致整个 batch 返回 None。
    """
    if requests is None or not api_key:
        return None
    base_url = base_url.rstrip("/")
    all_embeddings: list[list[float]] = []
    truncated_count = 0
    safe_texts: list[str] = []
    for text in texts:
        if max_chars > 0 and len(text) > max_chars:
            truncated_count += 1
            safe_texts.append(text[:max_chars])
        else:
            safe_texts.append(text)
    if truncated_count:
        print(f"   ⚠️  embedding 输入截断: {truncated_count}/{len(texts)} 条超过 {max_chars} 字符")
    for batch_start in range(0, len(safe_texts), batch_size):
        batch = safe_texts[batch_start : batch_start + batch_size]
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": model, "input": batch},
                    timeout=(10, 60),
                )
                resp.raise_for_status()
                data = resp.json()
                all_embeddings.extend(item["embedding"] for item in data["data"])
                break
            except Exception:
                if attempt < 2:
                    time.sleep(2**attempt)
                else:
                    missing = len(safe_texts) - len(all_embeddings)
                    print(f"   ⚠️  batch_embed 部分失败: {missing}/{len(safe_texts)} 条未获取到 embedding")
                    return all_embeddings
    if len(all_embeddings) != len(safe_texts):
        print(f"   ⚠️  batch_embed 数量不匹配: 期望 {len(safe_texts)}, 实际 {len(all_embeddings)}")
    return all_embeddings
