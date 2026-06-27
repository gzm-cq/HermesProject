"""阶段1: 分析 — 通读全文，五分类知识点提取 + claims_count"""

from __future__ import annotations

import logging
from typing import Any

from knowledge_tree_builder.config import AppConfig
from knowledge_tree_builder.llm.client import call_llm_json
from knowledge_tree_builder.models import (
    AnalysisReport,
    Candidate,
    KNOWLEDGE_TYPE_NAMES,
)

logger = logging.getLogger(__name__)

# ========== Prompt 模板 ==========

_SYSTEM_PROMPT: str = """你是一个知识提取专家。请从文章中提取所有符合以下五类定义的知识点。

## 五类知识点定义

### 1. principle（原理）
描述"X 通过/因为/遵循 Y → Z"的机制性关系。
必须包含因果/推导/机制（因为、所以、通过、使、遵循、基于、依赖于）。
✅ "Q/K 分离使 query 不进 K 空间"
❌ "注意力机制由 Q/K/V 三个向量组成"（这是要点，不是原理）

### 2. formula（公式）
可复现、可引用的形式化表述，含操作(变量)→输出的关系。
必须包含数学符号、等号、算子名（softmax、cosine_similarity 等）、参数变量。
✅ "attention = softmax(Q × K^T / √d)"
❌ "1+1=2"（纯常量，无变量）

### 3. key_point（要点）
陈述"X 是什么/有什么/怎么分/怎么做"的事实。
包含分类、结构、方法步骤、属性描述。通常没有因果动词。
✅ "自进化Agent 分为三大范式：模型中心、环境中心、模型-环境共进化"
❌ "系统包含多个模块"（太泛化）

### 4. conclusion（结论）
在特定条件下，X [比较词] Y 或 X 经过验证的属性。
必须包含条件或上下文，以及比较级/优劣判断。
✅ "HDBSCAN 在非均匀密度数据上优于 DBSCAN"
❌ "性能更好"（没有条件）

### 5. method（方法/流程）
描述可复现的操作步骤、标准流程、方法学、规范。
包含步骤序列、秩序动词（执行、运行、配置）、规范性表达。
✅ "部署流程分三步：备份 → 同步 → 验证"
❌ "按需调整参数"（没有具体步骤）

## 输出要求

对每个候选知识点：
1. 写出知识点文本（text）
2. 标注类型（type），只能是 principle/formula/key_point/conclusion/method 之一
3. 列出包含的独立 claim（每行 claim: xxx），然后给出 claims_count

输出 JSON 格式：
```json
{{
  "analysis": {{
    "content_summary": "一句话概括文章内容",
    "empty_article": false
  }},
  "candidates": [
    {{
      "text": "知识点文本",
      "type": "principle",
      "claim_list": ["claim1", "claim2"],
      "claims_count": 2
    }}
  ]
}}
```

注意：
- 不限条数，但只提取有知识价值的内容
- 每条 text 必须独立可理解
- 忽略：配置参数、版本号、操作记录、文件路径、个人偏好"""

_USER_PROMPT_TEMPLATE: str = """文章标题：{title}

文章内容：
{article_text}"""

# ========== 文章截断 ==========
_MAX_ARTICLE_CHARS: int = 12000  # 与 config.default.yaml 的 article_max_chars 默认值一致


# ========== 主函数 ==========


def analyze_article(
    article_text: str,
    title: str,
    *,
    config: AppConfig,
) -> AnalysisReport:
    """阶段1: 通读全文，提取五分类知识点候选。

    Args:
        article_text: 文章全文
        title: 文章标题
        config: AppConfig 实例

    Returns:
        AnalysisReport: 分析产物（candidates 不超过 K 条）
    """
    # 截断超长文章
    truncated = article_text
    if len(article_text) > _MAX_ARTICLE_CHARS:
        logger.warning(
            "文章超长（%d 字符），截断至 %d 字符",
            len(article_text),
            _MAX_ARTICLE_CHARS,
        )
        truncated = article_text[:_MAX_ARTICLE_CHARS]

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        title=title,
        article_text=truncated,
    )

    response = call_llm_json(
        prompt=user_prompt,
        system_prompt=_SYSTEM_PROMPT,
        temperature=config.extract_temperature,
        api_url=config.llm_api_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
    )

    # call_llm_json 失败返回 {"error": "..."}，不会返回 None
    if "error" in response:
        logger.warning("LLM 分析失败: %s", response["error"])
        return _empty_report(title)

    return _parse_analysis_response(
        response,
        config.max_candidates_per_article,
        title,
    )


# ========== 解析函数 ==========


def _parse_analysis_response(
    response: dict[str, Any],
    max_candidates: int,
    title: str,
) -> AnalysisReport:
    """解析 LLM JSON 为 AnalysisReport。

    容错处理:
    - "error" in response → 空 report
    - 缺少 "analysis" 键 → 补默认
    - "candidates" 不是列表 → 空列表
    - 单条缺少 "text" → 跳过
    - type 不在 KNOWLEDGE_TYPE_NAMES 中 → 跳过
    - claims_count 缺失或 < 1 → 默认 1
    - 超过 max_candidates → 截断前 K 条
    """
    # 解析 analysis
    analysis_raw = response.get("analysis", {})
    if not isinstance(analysis_raw, dict):
        analysis_raw = {}
    analysis = {
        "content_summary": str(analysis_raw.get("content_summary", "")),
        "empty_article": bool(analysis_raw.get("empty_article", False)),
    }

    # 空文章直接返回
    if analysis.get("empty_article"):
        return AnalysisReport(
            article_title=title,
            analysis=analysis,
            candidates=[],
        )

    # 解析 candidates
    candidates_raw = response.get("candidates", [])
    if not isinstance(candidates_raw, list):
        candidates_raw = []

    candidates: list[Candidate] = []
    for raw in candidates_raw:
        validated = _validate_candidate(raw)
        if validated is not None:
            candidates.append(validated)
        if len(candidates) >= max_candidates:
            break

    return AnalysisReport(
        article_title=title,
        analysis=analysis,
        candidates=candidates,
    )


def _validate_candidate(raw: Any) -> Candidate | None:
    """校验单条候选。

    - text 非空且 len >= 10
    - type 在 KNOWLEDGE_TYPE_NAMES 中
    - claims_count 为正整数（缺省/非法 → 默认 1）

    Returns:
        Candidate 或 None
    """
    if not isinstance(raw, dict):
        return None

    text = str(raw.get("text", "")).strip()
    if not text or len(text) < 10:
        return None

    claimed_type = str(raw.get("type", "")).strip()
    if claimed_type not in KNOWLEDGE_TYPE_NAMES:
        return None

    claims_count = raw.get("claims_count", 1)
    if not isinstance(claims_count, int) or claims_count < 1:
        claims_count = 1

    # 解析 claim_list（独立 claim 列表，可审计）
    raw_claims = raw.get("claim_list", [])
    claim_list: list[str] = []
    if isinstance(raw_claims, list):
        for c in raw_claims:
            c_str = str(c).strip()
            if c_str:
                claim_list.append(c_str)

    return Candidate(
        text=text,
        type=claimed_type,
        claims_count=claims_count,
        claim_list=claim_list,
    )


def _empty_report(title: str) -> AnalysisReport:
    """LLM 失败时的空报告。"""
    return AnalysisReport(
        article_title=title,
        analysis={"content_summary": "", "empty_article": False},
        candidates=[],
    )
