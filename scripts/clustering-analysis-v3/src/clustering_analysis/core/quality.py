"""记忆质量评分模块 — LLM 评分 + 启发式估算 fallback"""

import json
import re
import time
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_QUALITY_DIMS = ("informativeness", "clarity", "completeness", "timeliness")

_LOW_QUALITY_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"^(好的|嗯|哦|啊|是的|不是|可以|不行|好的呢|收到|了解|明白)$",
    r"^(测试|test|TODO|TBD|FIXME)$",
    r"^.{0,5}$",
]]


def _parse_llm_json_response(content: str) -> dict | None:
    """3 层解析兜底：直接解析 → markdown code block → raw JSON 对象"""
    if not content:
        return None

    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass

    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    m = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", content)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _clamp_score(value: float) -> float:
    """将分数限制在 0-1 范围内"""
    return max(0.0, min(1.0, value))


def estimate_quality_keywords(text: str) -> float:
    """快速启发式估算记忆质量（无 LLM 时的 fallback）。

    基于关键词密度、句子长度、数字/专有名词占比等估算。

    Args:
        text: 记忆文本

    Returns:
        0-1 的质量分数
    """
    if not text or not text.strip():
        return 0.0

    text = text.strip()
    text_len = len(text)

    if text_len < 10:
        return 0.1

    scores: list[float] = []

    # 1. 文本长度得分（适度长度最佳）
    if text_len < 20:
        length_score = text_len / 20.0 * 0.6
    elif text_len < 200:
        length_score = 0.6 + (text_len - 20) / 180.0 * 0.3
    elif text_len < 1000:
        length_score = 0.9
    else:
        length_score = 0.9 - min(0.4, (text_len - 1000) / 2000.0)
    scores.append(_clamp_score(length_score))

    # 2. 句子数量与平均句长
    sentences = re.split(r'[。！？.!?\n]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sent_count = len(sentences)

    if sent_count == 0:
        scores.append(0.2)
    else:
        avg_sent_len = text_len / sent_count
        if 15 <= avg_sent_len <= 60:
            sent_score = 0.8
        elif 8 <= avg_sent_len < 15 or 60 < avg_sent_len <= 100:
            sent_score = 0.6
        else:
            sent_score = 0.4

        if sent_count >= 3:
            sent_score += 0.1
        scores.append(_clamp_score(sent_score))

    # 3. 数字/数据占比（事实性指标）
    digit_count = len(re.findall(r'\d+', text))
    digit_density = digit_count / max(1, text_len / 50)
    if digit_density > 3:
        data_score = 0.9
    elif digit_density > 1:
        data_score = 0.7
    elif digit_density > 0:
        data_score = 0.5
    else:
        data_score = 0.3
    scores.append(data_score)

    # 4. 专有名词/技术术语迹象（大写字母、英文术语）
    en_words = re.findall(r'[A-Za-z][A-Za-z0-9_]+', text)
    en_word_count = len(en_words)
    if en_word_count >= 5:
        term_score = 0.8
    elif en_word_count >= 2:
        term_score = 0.6
    elif en_word_count >= 1:
        term_score = 0.4
    else:
        term_score = 0.3
    scores.append(term_score)

    # 5. 避免低质量信号
    for pattern in _LOW_QUALITY_PATTERNS:
        if pattern.match(text):
            return 0.1

    # 加权平均
    weights = [0.3, 0.3, 0.2, 0.2]
    total = sum(s * w for s, w in zip(scores, weights[:len(scores)]))
    return _clamp_score(total)


def score_memory_quality(
    text: str,
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
    retries: int = 3,
    use_llm: bool = True,
) -> dict[str, float]:
    """使用 LLM 对单条记忆进行质量评分。

    评分维度：
    - informativeness: 0-1，信息密度
    - clarity: 0-1，表述清晰度
    - completeness: 0-1，事实完整性
    - timeliness: 0-1，时效性
    - overall: 0-1，综合质量分

    Args:
        text: 记忆文本
        api_url: LLM API 地址
        api_key: LLM API 密钥
        model: LLM 模型名
        retries: 重试次数
        use_llm: 是否使用 LLM（False 时直接用启发式估算）

    Returns:
        各维度分 + 综合分的字典
    """
    default_scores = {dim: 0.5 for dim in _QUALITY_DIMS}
    default_scores["overall"] = 0.5

    if not text or not text.strip():
        return {k: 0.0 for k in default_scores}

    if not use_llm or requests is None:
        heuristic = estimate_quality_keywords(text)
        return {
            "informativeness": heuristic,
            "clarity": heuristic,
            "completeness": heuristic * 0.9,
            "timeliness": heuristic * 0.8,
            "overall": heuristic,
        }

    prompt = (
        "你是一个记忆质量评估专家。请对以下记忆文本进行质量评分，"
        "从 5 个维度给出 0 到 1 之间的分数（保留 2 位小数）。\n\n"
        "评分维度说明：\n"
        "1. informativeness（信息密度）：记忆是否包含实质、有价值的信息内容\n"
        "2. clarity（表述清晰度）：表达是否清晰、有条理、易于理解\n"
        "3. completeness（事实完整性）：信息是否完整，是否缺少关键要素\n"
        "4. timeliness（时效性）：信息是否具有时效性，是否包含过时内容\n"
        "5. overall（综合质量）：整体质量的综合评分\n\n"
        "输出格式（纯 JSON，不要其他内容）：\n"
        "{\n"
        '  "informativeness": 0.75,\n'
        '  "clarity": 0.80,\n'
        '  "completeness": 0.65,\n'
        '  "timeliness": 0.70,\n'
        '  "overall": 0.72\n'
        "}\n\n"
        "记忆文本：\n"
        f"{text}"
    )

    for attempt in range(retries):
        try:
            # 冷配置：确定性质量评分取低温和低 top_p
            _q_body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "top_p": 0.1,
                "max_tokens": 16384,
            }
            resp = requests.post(
                api_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json=_q_body,
                timeout=(10, 60),
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
                continue

            result: dict[str, float] = {}
            for key in default_scores:
                val = parsed.get(key)
                if isinstance(val, (int, float)):
                    result[key] = _clamp_score(float(val))
                else:
                    result[key] = default_scores[key]

            if "overall" not in result or not isinstance(result.get("overall"), (int, float)):
                result["overall"] = sum(result.get(d, 0.5) for d in _QUALITY_DIMS) / len(_QUALITY_DIMS)

            return result

        except Exception:
            if attempt < retries - 1:
                time.sleep(2**attempt)

    heuristic = estimate_quality_keywords(text)
    return {
        "informativeness": heuristic,
        "clarity": heuristic,
        "completeness": heuristic * 0.9,
        "timeliness": heuristic * 0.8,
        "overall": heuristic,
    }


def batch_score_memories(
    memories: list[dict[str, Any]],
    *,
    batch_size: int = 20,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
    retries: int = 3,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    """批量评分记忆质量。

    Args:
        memories: 记忆列表，每项需包含 id 和 text 字段
        batch_size: 批处理大小
        api_url: LLM API 地址
        api_key: LLM API 密钥
        model: LLM 模型名
        retries: 单条重试次数
        use_llm: 是否使用 LLM

    Returns:
        带 quality_score 和 quality_details 字段的记忆列表
    """
    if not memories:
        return []

    results: list[dict[str, Any]] = []

    for i in range(0, len(memories), batch_size):
        batch = memories[i:i + batch_size]

        for mem in batch:
            mem_id = mem.get("id", "")
            text = mem.get("text", "") or ""

            scores = score_memory_quality(
                text,
                api_url=api_url,
                api_key=api_key,
                model=model,
                retries=retries,
                use_llm=use_llm,
            )

            result_mem = dict(mem)
            result_mem["quality_score"] = scores["overall"]
            result_mem["quality_details"] = scores
            results.append(result_mem)

    return results
