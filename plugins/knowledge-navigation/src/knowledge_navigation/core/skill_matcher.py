"""LLM skill 检索器：理解意图 → 扩展关键词 → 匹配 skill 列表。

在 pre_llm_call 中根据用户消息选择相关 skill 并注入完整正文。

单层架构：
  LLM 语义理解 + 关键词扩展 + 匹配（无 embedding 层）
  LLM 找到就注入，找不到就不注入，不强行填充。"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SKILLS_HOME = Path.home() / ".hermes" / "skills"
_TOP_K = 3
_MAX_SKILLS = 500

_LLM_TIMEOUT = 15

# ── 模块级缓存 ──
_skill_index: list[dict[str, Any]] | None = None
"""[{name, description, path, category}, ...]"""


# ====================================================================
# Frontmatter 解析
# ====================================================================

def _parse_frontmatter(text: str) -> dict[str, str]:
    """从 SKILL.md 中提取 name / description。"""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---\n", 3)
    if end == -1:
        return meta
    body = text[3:end]
    import re as _re
    for line in body.strip().split("\n"):
        m = _re.match(r"^(name|description):\s*(.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def strip_frontmatter(text: str) -> str:
    """去除 SKILL.md 开头的 YAML frontmatter，返回正文。

    与 _parse_frontmatter 共用同一套分隔逻辑，避免 hooks/skill_matcher
    两处重复实现 frontmatter 解析。
    """
    if not text.startswith("---"):
        return text
    end = text.find("\n---\n", 3)
    if end == -1:
        return text
    return text[end + 5:].lstrip("\n")


# ====================================================================
# 索引构建（首次调用时懒加载）
# ====================================================================

def ensure_index() -> bool:
    """构建 skill 索引。

    只执行一次。首次调用扫描 ~345 个 SKILL.md：
    - 提取 name/description/path/category → _skill_index
    后续调用直接返回缓存。
    """
    global _skill_index

    if _skill_index is not None:
        return len(_skill_index) > 0

    if not SKILLS_HOME.exists():
        logger.warning("Skill index: skills dir not found: %s", SKILLS_HOME)
        _skill_index = []
        return False

    t0 = time.time()
    skill_files = list(SKILLS_HOME.rglob("SKILL.md"))
    if len(skill_files) > _MAX_SKILLS:
        skill_files = skill_files[:_MAX_SKILLS]

    index: list[dict[str, Any]] = []
    n_skipped = 0

    for fp in skill_files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            n_skipped += 1
            continue

        meta = _parse_frontmatter(text)
        name = meta.get("name", "")
        desc = meta.get("description", "")
        if not name or not desc:
            n_skipped += 1
            continue

        category = fp.parent.parent.name if fp.parent.parent != SKILLS_HOME else ""

        index.append({
            "name": name,
            "description": desc,
            "path": str(fp),
            "category": category,
        })

    _skill_index = index

    elapsed = (time.time() - t0) * 1000
    logger.info(
        "Skill index built: %d indexed, %d skipped in %.0fms",
        len(index), n_skipped, elapsed,
    )
    return len(index) > 0


# ====================================================================
# LLM 匹配（独占）
# ====================================================================

def _build_skill_prompt(index: list[dict[str, Any]]) -> str:
    """将 skill 索引格式化为 LLM 可读的列表。

    按名称字母序排列。每行 name + 描述（截断到 120 字），过滤归档。
    ~345 个 skill，chars ~30k，tokens ~8k。
    """
    lines: list[str] = []
    for s in sorted(index, key=lambda x: x["name"]):
        if s.get("category") == ".archive":
            continue
        desc = s.get("description", "")
        desc_trunc = (desc[:120] + "...") if len(desc) > 120 else desc
        lines.append(f"- {s['name']}: {desc_trunc}")
    return "\n".join(lines)


def _llm_match(query: str, top_k: int = _TOP_K) -> list[dict[str, str]]:
    """LLM 语义选择（唯一匹配层）。先分析意图+扩展关键词，再匹配技能列表。"""
    if not _skill_index:
        return []
    skill_text = _build_skill_prompt(_skill_index)
    prompt = (
        "你是一个技能选择器。你的任务是根据用户问题，从给定的技能列表中选出 1-3 个最可能解决问题的技能，并以 JSON 数组形式返回技能名称。\n\n"
        "请严格遵循以下规则：\n\n"
        "## 分析流程\n"
        "1. **意图解析**：阅读用户问题，提炼出核心任务、领域、工具或痛点，形成 3-5 个关键概念。\n"
        "2. **概念扩展（必须补齐中、英文）**：针对每个关键概念，生成 1-2 组同义词、近义词、上位词、下位词或强关联词汇，每组必须同时包含中文和英文表达。\n"
        "3. **技能匹配**：\n"
        "   - 优先按技能名称（name）直接匹配——name 是有意义的关键词标识符\n"
        "   - 如果 name 无直接匹配，再用扩展出的中英文词表逐一对照技能描述（description）中的核心术语\n"
        "   - 只选明确能解决或显著辅助用户问题的技能\n"
        "   - 宁可少选不可错选——没有足够相关的技能时返回 []\n\n"
        "## 输出要求\n"
        "- 仅输出一个合法的 JSON 数组，元素为技能名称字符串，不要用 ``` 包裹或附带任何其他文字\n"
        "- 技能名称必须与可用技能列表中的名称完全一致（含大小写、连字符、符号）\n"
        "- 数量控制在 1-3 个；如果无相关技能，返回 []\n"
        "- 每次对同一问题返回一致的结果\n\n"
        "## 可用技能列表\n"
        + skill_text + "\n\n"
        "## 用户问题\n"
        + query + "\n\n"
        "## 你的输出（仅 JSON）\n"
    )

    for attempt in range(2):
        try:
            import httpx
            api_key = os.environ.get("LITELLM_MASTER_KEY", "")
            resp = httpx.post(
                "http://127.0.0.1:4142/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": "s-deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.1,
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
                timeout=_LLM_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            raw = (body["choices"][0]["message"]["content"] or "").strip()

            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            names = json.loads(raw)
            if not isinstance(names, list):
                logger.debug("Skill match LLM: non-list: %s", raw)
                return []

            info_map = {s["name"]: {"description": s["description"], "path": s["path"]} for s in _skill_index}
            results: list[dict[str, str]] = []
            for name in names[:top_k]:
                if name in info_map:
                    results.append({
                        "name": name,
                        "description": info_map[name]["description"],
                        "path": info_map[name]["path"],
                        "score": "0.500",
                    })
            return results

        except Exception as e:
            if attempt == 0:
                logger.debug("Skill match LLM 重试 (attempt=0): %s", e)
                continue
            logger.debug("Skill match LLM error: %s", e)
            return []


# ====================================================================
# 入口
# ====================================================================

def match_skills(query: str, top_k: int = _TOP_K) -> list[dict[str, str]]:
    """LLM 语义匹配（单层）。去掉 embedding 层——LLM 找到就注入，找不到就不注入。

    Args:
        query: 用户消息
        top_k: 最多返回数量

    Returns:
        [{name, description, score, path}, ...]
        调用方可用 path + strip_frontmatter 读 SKILL.md 正文。
    """
    if not ensure_index():
        return []

    # 空查询 / 纯空白 → 无匹配
    if not query or not query.strip():
        return []

    t0 = time.time()
    results = _llm_match(query, top_k)
    if results:
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "Skill match (LLM): %s (%.0fms)",
            [r["name"] for r in results],
            elapsed,
        )
        return results

    logger.debug("Skill match: empty (LLM returned nothing)")
    return []