"""阶段1+2 合并 — 分析+拆解合并为单次 LLM 调用

将 analyze 和 split 合入一次 LLM 调用，直接输出原子知识列表。
跳过拆分阶段的 LLM 串行调用，节省约 80% tokens。

与独立模式的对比:
- 独立模式: analyze → 候选列表 → split → claims_count 校验 → sum 校验 → 原子列表
- 合并模式: LLM 一次输出原子列表（无中间候选，无 claims_count 校验）
"""

from __future__ import annotations

import logging
from typing import Any

from knowledge_tree_builder.config import AppConfig
from knowledge_tree_builder.llm.client import call_llm_json
from knowledge_tree_builder.models import (
    AtomicKnowledge,
    KNOWLEDGE_TYPE_NAMES,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT: str = """你是一个知识提取专家。请从文章中提取所有符合以下五类定义的知识点。
直接输出原子化的知识点（每条 claims_count=1），不需要拆分步骤。

## 五类知识点定义

### 1. principle（原理）
因果/机制关系。包含"通过、使、导致、基于、依赖于"。
✅ "Q/K 分离使 query 不进 K 空间"
❌ "注意力机制由 Q/K/V 三个向量组成"（这是要点）

### 2. formula（公式）
含数学符号、等号、算子名、参数变量。
✅ "attention = softmax(Q × K^T / √d)"
❌ "1+1=2"（纯常量，无变量）

### 3. key_point（要点）
事实、分类、结构描述。通常无因果动词。
✅ "自进化Agent 分为三大范式：模型中心、环境中心、模型-环境共进化"
❌ "系统包含多个模块"（太泛化）

### 4. conclusion（结论）
有条件对比。包含"优于、在...下、提升"等。
✅ "HDBSCAN 在非均匀密度数据上优于 DBSCAN"
❌ "性能更好"（没有条件）

### 5. method（方法/流程）
可复现步骤、规范。包含步骤序列或规范性表达。
✅ "部署流程分三步：备份 → 同步 → 验证"
❌ "按需调整参数"（没有具体步骤）

## 输出要求

1. 每条知识点必须满足自解释性：
   - 不能包含「该模型」「这种方法」「上述算法」等指代代词
   - 不能包含「如上所述」「下文详述」等元引用
   - 不能包含依赖原文才能理解的省略

2. 每条知识点必须 claims_count=1（原子化）

3. 输出 JSON 格式：
```json
{
  "analysis": {
    "content_summary": "一句话概括",
    "empty_article": false
  },
  "atomic_knowledge": [
    {
      "text": "知识点文本",
      "type": "principle",
      "entities": ["实体A", "实体B"]
    },
    {
      "text": "知识点文本",
      "type": "key_point",
      "entities": ["实体C"]
    }
  ]
}
```

4. 每条知识点的 "entities" 字段：从知识点中提取 2-8 个命名实体（名词性关键概念），中英文均可。没有可提取实体时返回空数组 []。

5. 最多输出 15 条。只提取有知识价值的内容。

## 不应提取的内容

以下类型不应作为知识点输出：
- **建议/意见类**：以「建议」「改进建议」「方案」开头的改进提议或设计方案
- **命令/配置类**：部署命令、端口配置、路径、版本号特指的具体配置参数
- **TODO/FIXME/Note 标记**：临时性笔记，非知识事实
- **示例引导**：以「例如」「比如」「如」开头且仅为举例说明的文本

只提取通用性、可复用的知识事实。"""


def analyze_and_split(
    article_text: str,
    title: str,
    *,
    config: AppConfig,
) -> tuple[list[AtomicKnowledge], str]:
    """阶段1+2 合并：一次 LLM 调用，直接输出原子知识列表。

    Args:
        article_text: 文章全文
        title: 文章标题
        config: AppConfig 实例

    Returns:
        (atomic_knowledge_list, content_summary)
    """
    truncated = article_text
    if len(article_text) > config.article_max_chars:
        logger.warning("文章超长（%d 字符），截断至 %d", len(article_text), config.article_max_chars)
        truncated = article_text[:config.article_max_chars]

    user_prompt = f"文章标题：{title}\n\n文章内容：\n{truncated}"

    response = call_llm_json(
        prompt=user_prompt,
        system_prompt=_SYSTEM_PROMPT,
        temperature=config.extract_temperature,
        api_url=config.llm_api_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        retries=config.llm_retries,
        timeout_seconds=config.llm_request_timeout_seconds,
    )

    if "error" in response:
        logger.warning("LLM 合并分析失败: %s", response["error"])
        return [], ""

    # 解析 atomic_knowledge
    raw_atomics = response.get("atomic_knowledge", [])
    analysis = response.get("analysis", {})
    content_summary = str(analysis.get("content_summary", "")) if isinstance(analysis, dict) else ""

    if not isinstance(raw_atomics, list):
        return [], content_summary

    atomics: list[AtomicKnowledge] = []
    for i, raw in enumerate(raw_atomics):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "")).strip()
        ktype = str(raw.get("type", "")).strip()

        if not text or len(text) < 10:
            continue
        if ktype not in KNOWLEDGE_TYPE_NAMES:
            ktype = "key_point"

        entities_raw = raw.get("entities", [])
        if not isinstance(entities_raw, list):
            entities_raw = []
        entities = [str(e) for e in entities_raw if isinstance(e, str) and e.strip()]

        atomics.append(AtomicKnowledge(
            text=text,
            type=ktype,
            claims_count=1,
            source_candidate_index=i,
            source_title=title,
            entities=entities,
        ))

        # K 上限
        if len(atomics) >= config.max_candidates_per_article:
            break

    return atomics, content_summary
