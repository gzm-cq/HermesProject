"""
ReportGraph — 意图驱动报告规划 StateGraph
===========================================
使用 LangGraph StateGraph 实现 5 节点规划管线。

节点：
  1. define_goal     — 报告目标定义
  2. search_refs     — 参考搜索 + LLM选URL + web_extract
  3. synthesize      — 章节意图+图表规划合成
  4. curate          — 素材筛选打包
  5. prompt_review   — 提示词自检优化

输出：chapter_prompts[] → 现有流水线

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import json as _json
import logging
import re as _re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# LangGraph
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from ..adapters.ai_client import call_llm
from .material_service import MaterialService
from .types import (
    ChapterPrompt,
    ExtractedArticle,
    MaterialPack,
    ReportGoal,
    SourceRef,
    WebResult,
)

logger = logging.getLogger(__name__)


# ── State 定义 ──────────────────────────────────────────────


class GraphState(TypedDict):
    """StateGraph 的全局状态。"""
    topic: str                                    # 输入：报告主题
    source_content: str                           # 输入：源文档全文
    report_type: str                              # 输入：报告类型 tech/market/...
    language: str                                 # 输入：语言

    report_goal: dict[str, Any] | None            # Node1 输出：报告目标（含 writing_role）
    reference_outlines: list[str] | None          # Node2 输出：同类文章的大纲参考
    chapter_prompts: list[dict[str, Any]] | None  # Node3 输出：章节 prompt 列表
    materials: dict[str, Any] | None              # Node4 输出：{chapter_key: MaterialPack}
    optimized_prompts: list[dict[str, Any]] | None # Node5 输出：优化后的 prompt

    domain_config: dict[str, list[str]] | None    # 外部注入：域配置 {high: [...], medium: [...]}
    raw_materials: list[dict[str, Any]] | None     # 统一素材池：全文文章列表


class _GraphStateInput(TypedDict):
    """用户输入字段（不含中间产物）。"""
    topic: str
    source_content: str
    report_type: str
    language: str
    report_goal: dict[str, Any] | None
    reference_outlines: list[str] | None
    domain_config: dict[str, list[str]] | None


# ── 节点实现 ────────────────────────────────────────────────

# Node 提升至模块级别，满足 StateGraph.add_node 签名要求


def _build_define_goal_prompt(topic: str, source: str) -> str:
    """构建 define_goal 的 LLM prompt。"""
    source_preview = source[:4000] if source else "(无源文档)"
    return (
        f"你是一个报告规划专家。请根据以下信息，定义一份报告的目标，并提取写作角色。\n\n"
        f"报告主题：{topic}\n\n"
        f"源文档内容摘要：\n{source_preview}\n\n"
        f"请输出以下 JSON（不要多余文字，所有字段必须输出完整内容，禁止使用 ... 或省略号代替后续内容）：\n"
        f"{{\n"
        f'  "title": "报告标题（完整、明确）",\n'
        f'  "purpose": "报告目的／解决的问题（完整描述，不要缩写）",\n'
        f'  "target_audience": "目标读者（3-4 个读者角色类别，如：决策层、管理层、执行层）",\n'
        f'  "overall_strategy": "写作方法论和决策框架，**不是内容目录**！定义以什么角度切入、排除什么、决策原则（如：每项建议必须有数据支撑，否则标注为推断）",\n'
        f'  "writing_role": {{\n'
        f'    "role": "最适合撰写这份报告的专业角色（如：行业战略分析师、技术架构师、产业研究员等）",\n'
        f'    "expertise": ["擅长的领域1", "擅长的领域2", "擅长的领域3", "擅长的领域4"],\n'
        f'    "tone": "写作语调（如：专业、数据驱动、可执行、学术严谨等）",\n'
        f'    "voice": "人称和叙述方式（1-2 句定义视角和语调，如：以单位内部战略规划负责人视角，从政策、技术、业务等多角度客观论述）",\n'
        f'    "output_conventions": "输出规范约定（如：每节以结论句开头、对比数据用表格呈现、避免口号式表达等；推断内容须附带推断依据说明，如：(推断, 基于XX数据)）"\n'
        f"  }}\n"
        f"}}\n\n"
        f"⚠️ 禁止使用 ... 或任何形式的省略号。每个字段必须输出完整的、有意义的描述。\n"
    )


def _parse_goal_response(response: str, default_title: str) -> dict[str, Any]:
    """解析 define_goal 的 LLM 响应，含 fallback 逻辑。"""
    goal: dict[str, Any] = {
        "title": default_title, "purpose": "", "target_audience": "",
        "overall_strategy": "", "writing_role": {},
    }
    try:
        parsed = _json.loads(response.strip())
        if isinstance(parsed, dict):
            for k in ("title", "purpose", "target_audience", "overall_strategy"):
                if k in parsed and isinstance(parsed[k], str):
                    goal[k] = parsed[k]
            if "writing_role" in parsed and isinstance(parsed["writing_role"], dict):
                goal["writing_role"] = parsed["writing_role"]
        return goal
    except _json.JSONDecodeError:
        pass

    # Fallback: extract first JSON object
    import re as _re
    match = _re.search(r'\{[^{}]*\}', response, _re.DOTALL)
    if match:
        try:
            parsed = _json.loads(match.group())
            if isinstance(parsed, dict):
                for k in ("title", "purpose", "target_audience", "overall_strategy"):
                    if k in parsed and isinstance(parsed[k], str):
                        goal[k] = parsed[k]
                if "writing_role" in parsed and isinstance(parsed["writing_role"], dict):
                    goal["writing_role"] = parsed["writing_role"]
        except _json.JSONDecodeError:
            pass
    return goal


def define_goal(state: GraphState) -> dict[str, Any]:
    """Node 1: LLM 解析源文档 → 报告目标定义 + 写作角色提取。

    输出 {title, purpose, target_audience, overall_strategy, writing_role}
    """
    topic = state["topic"]
    prompt = _build_define_goal_prompt(topic, state["source_content"])
    response = call_llm(prompt, max_tokens=1200, temperature=0.3)
    goal = _parse_goal_response(response, topic)

    role_name = goal.get("writing_role", {}).get("role", "N/A")
    logger.info(
        "[Node 1] define_goal: title='%s' role='%s'",
        goal["title"], role_name,
    )
    return {"report_goal": goal}


def _optimize_goal(
    goal: dict[str, Any],
    source_content: str = "",
) -> dict[str, Any]:
    """LLM 优化已提取的 report_goal，修复截断、补充薄弱字段、确保角色一致性。

    Args:
        goal: define_goal 的原始输出
        source_content: 源文档全文，用于上下文参考

    Returns:
        优化后的 report_goal
    """
    goal_json = _json.dumps(goal, ensure_ascii=False, indent=2)
    source = source_content[:5000] if source_content else "(无源文档)"

    prompt = (
        f"你是一个报告规划质量优化专家。请审核以下 report_goal，并优化。\n\n"
        f"## 源文档参考\n{source}\n\n"
        f"## 当前 report_goal\n{goal_json}\n\n"
        f"### 检查清单\n"
        f"1. 所有字段必须完整、有意义的描述？禁止使用 ... 或省略号\n"
        f"2. purpose 是否准确反映了源文档要解决的核心问题？\n"
        f"3. target_audience 是否精简到 3-4 个角色类别，而非逐一枚举部门？\n"
        f"4. overall_strategy 是**写作方法论和决策框架**，不是内容摘要（不应该写'报告覆盖X到Y'这类内容目录）\n"
        f"5. writing_role.role 是否与源文档内容匹配？\n"
        f"6. writing_role.voice 是否 1-2 句简洁完整，不缩写？\n"
        f"7. writing_role.output_conventions 是否实用具体？\n"
        f"8. 每个字段的长度是否与功能匹配？纲领性目标不宜过长\n\n"
        f"请输出优化后的 JSON，仅输出 JSON，不要多余文字。\n"
        f"{{\n"
        f'  "title": "报告标题",\n'
        f'  "purpose": "完整描述",\n'
        f'  "target_audience": "具体读者",\n'
        f'  "overall_strategy": "完整策略",\n'
        f'  "writing_role": {{\n'
        f'    "role": "专业角色",\n'
        f'    "expertise": ["领域1", "领域2", "领域3", "领域4"],\n'
        f'    "tone": "写作语调",\n'
        f'    "voice": "完整叙述方式描述",\n'
        f'    "output_conventions": "具体输出规范"\n'
        f"  }}\n"
        f"}}\n\n"
        f"⚠️ 禁止使用 ... 或省略号，每个字段必须完整输出。"
    )
    response = call_llm(prompt, max_tokens=2000, temperature=0.3)
    parsed: dict[str, Any] | None = None
    # 三层解析（与 synthesize 相同策略）
    try:
        parsed = _json.loads(response.strip())
    except _json.JSONDecodeError:
        pass
    if not isinstance(parsed, dict):
        m = _re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', response)
        if m:
            try:
                parsed = _json.loads(m.group(1).strip())
            except _json.JSONDecodeError:
                pass
    if not isinstance(parsed, dict):
        m = _re.search(r'\{[\s\S]*\}', response)
        if m:
            try:
                parsed = _json.loads(m.group())
            except _json.JSONDecodeError:
                pass
    if isinstance(parsed, dict):
        # 合并优化结果：只替换原 goal 中存在的字段
        for k in ("title", "purpose", "target_audience", "overall_strategy"):
            if k in parsed and isinstance(parsed[k], str) and parsed[k].strip():
                goal[k] = parsed[k]
        if "writing_role" in parsed and isinstance(parsed["writing_role"], dict):
            wr = parsed["writing_role"]
            for k in ("role", "expertise", "tone", "voice", "output_conventions"):
                if k in wr and wr[k]:
                    goal["writing_role"][k] = wr[k]
        logger.info("[optimize_goal] 优化完成")
    else:
        logger.warning("[optimize_goal] JSON 解析失败（已试 3 种策略），使用原始 goal")
    return goal


def search_refs(state: GraphState) -> dict[str, Any]:
    """Node 2: 从统一素材池提取参考大纲。

    不再做网络搜索。pre_search 已将文章 + toc_lines 写入
    materials/all_articles.json，直接从中提取目录结构作为参考。

    统一素材池中的全文内容作为 raw_materials 传给 synthesize。
    """
    topic = state["topic"]
    logger.info("[Node 2] search_refs: topic=%s", topic[:40])

    material_service = MaterialService(
        domain_config=state.get("domain_config"),
    )

    # 统一素材池（pre_search 已填充的文章 + toc_lines）
    pool_articles = material_service.load_all_materials()
    if not pool_articles:
        logger.info("[Node 2] search_refs: 统一池为空，仅依赖源文档结构")
        return {"reference_outlines": [], "raw_materials": []}

    logger.info("[Node 2] search_refs: 统一池 %d 篇文章", len(pool_articles))

    # 从 toc_lines 提取参考大纲
    outlines: list[str] = []
    for art in pool_articles:
        toc_lines = art.get("toc_lines") or []
        if toc_lines:
            title = art.get("title", "") or art.get("url", "")[:50]
            outline = f"## 参考: {title}\n" + "\n".join(toc_lines[:20])
            outlines.append(outline)
            logger.info("  toc_lines from: %s (%d sections)", title, len(toc_lines))

    if outlines:
        logger.info("[Node 2] search_refs: %d reference outlines from pool", len(outlines))
    else:
        logger.info("[Node 2] search_refs: 池中文章无目录结构，仅依赖源文档结构")

    # 全文加入 raw_materials（供 synthesize 使用）
    result: dict[str, Any] = {"reference_outlines": outlines}
    result["raw_materials"] = pool_articles
    logger.info("[Node 2] search_refs: %d articles as raw_materials", len(pool_articles))
    return result


def _build_synthesize_prompt(state: GraphState) -> str:
    """构建 synthesize 的 LLM prompt。"""
    goal = state.get("report_goal") or {}
    source = state["source_content"]
    refs = state.get("reference_outlines") or []

    goal_text = _json.dumps(goal, ensure_ascii=False)
    # 源文档预览：显示更多内容，并标注文件来源
    source_preview = source[:8000] if source else "(无源文档)"
    # 如果有多文件，显示完整的文件列表
    source_files = ""
    if source and "📄" in source:
        file_names = [line.strip("📄 ") for line in source.split("\n") if line.startswith("📄")]
        source_files = "\n".join(f"- {fn}" for fn in file_names)
        source_files = f"\n## 素材文件列表\n本次共提供 {len(file_names)} 个素材文件：\n{source_files}\n"
    refs_text = "\n".join(refs[:3]) if refs else "(无参考资料)"

    # 统一素材池：全文内容
    raw = state.get("raw_materials") or []
    raw_text = ""
    if raw:
        raw_parts: list[str] = []
        for i, art in enumerate(raw[:3]):
            raw_parts.append(
                f"--- 参考文献 {i+1}: {art.get('title','')} "
                f"[{art.get('credibility','')}] ---\n"
                f"{art.get('content','')[:2000]}\n"
            )
        raw_text = "## 参考文献全文\n" + "\n".join(raw_parts)

    return (
        f"你是一个报告规划专家。请为以下报告生成一个完整的大纲。\n\n"
        f"## 报告目标\n{goal_text}\n\n"
        f"## 源文档内容（素材全文，含文件来源标记）\n{source_preview}\n\n"
        f"{source_files}"
        f"## 同类文章大纲参考\n{refs_text}\n\n"
        f"{raw_text}\n\n"
        f"## 规划要求\n"
        f"请根据「报告目标」中的评估框架来规划章节结构，而不是照搬源文档的章节组织方式。\n"
        f"源文档的内容作为素材使用，但章节结构应由报告目标中的 overall_strategy 字段定义的评估维度驱动——\n"
        f"章节应按报告目标的评估维度组织，而不是按源文档的模块划分（如网络层、部门、系统名等）组织。\n"
        f"1. 首先分析 report_goal.overall_strategy（写作方法论和决策框架），以其中的评估维度作为章节设计依据\n"
        f"2. 然后将源文档中的关键数据（预算、时间节点、技术方案）分配到对应评估维度下的章节中\n"
        f"3. 不同文件可能对应报告的不同层次或章节，注意识别文件内容主题\n"
        f"4. 每个核心章节应聚焦于**方案论证/可行性评估/能力分析**，而非写实施步骤\n"
        f"5. 涉及分阶段规划的应有**年度或阶段性里程碑**（逐年分解）\n"
        f"6. 每个主章节应有 2-4 个 H2 子节，子节内容不越界\n"
        f"## 章节标题规则\n"
        f"- 标题**不要**包含\"第X章\"或\"第n章\"这类占位符\n"
        f"- 直接使用实际内容标题，如\"顶层设计与架构布局\"而不是\"第X章 顶层设计与架构布局\"\n"
        f"- 编号规则由系统在最终输出时统一添加，你只需提供纯净的标题文本\n"
        f"- 子节标题用\"一、二、三\"中文编号体系（如\"一、建设内容\"\"二、投资估算\"）\n\n"
        f"输出 JSON 数组，每个元素代表一个章节，支持层级结构：\n"
        f"[{{\n"
        f'  "title": "章节标题",\n'
        f'  "level": 1,                     // H1 主标题\n'
        f'  "section_type": "intro" | "body" | "conclusion" | "appendix",\n'
        f'  "estimated_words": 字数,\n'
        f'  "writing_intent": "本章写作意图",\n'
        f'  "key_points": ["要点1", "要点2"],\n'
        f'  "avoid_topics": [],\n'
        # preferred_source removed — set in Step 5
        f'  "chart_spec": null | {{"type": "architecture_table" | "timeline" | "comparison", "data_fields": ["字段1", "字段2"]}},\n'
        f'  "sub_sections": [              // H2 子章节，可选\n'
        f'    {{"title": "子节1", "level": 2, "writing_intent": "子节的写作意图", "key_points": ["要点1"]}}\n'
        f'  ]\n'
        f"}}]\n\n"
        f"要求：\n"
        f"1. 4-6 个 H1 主章节，每个主章节下 1-4 个 H2 子节\n"
        f"2. H1 之间不重叠、不矛盾\n"
        f"3. H2 从属于 H1，子节内容不越界\n"
        f"4. 每个 H1 和 H2 都要有明确的 writing_intent\n"
        f"5. chart_spec 类型使用规则：\n"
        f"   - architecture_table: 描述系统架构/分层结构时有数据支撑时使用\n"
        f"     data_fields: 该表格需要展示哪些列（如[\"层名\", \"定位\", \"职能\", \"策略\"]），按该章实际需求填写\n"
        f"   - timeline: 有逐年里程碑、实施路径时使用\n"
        f"     data_fields: 该时间线需要展示哪些维度（如[\"年份\", \"互联网里程碑\", \"工控网里程碑\"]），按该章实际需求填写\n"
        f"   - comparison: 有投资估算、预算对比时使用\n"
        f"     data_fields: 该对比图需要展示哪些指标（如[\"费用项\", \"金额\", \"所属网络层\"]），按该章实际需求填写\n"
        f"   - 不要使用 flowchart 类型（系统不支持）\n"
        f"   - 无数据支撑时 \"chart_spec\" 设为 null\n"
        f"   - data_fields 告诉系统该章图表需要什么数据，系统会按字段从源文档提取\n"
        f"6. 写作角色规范：\n"
        f"   - 角色：{goal.get('writing_role', {}).get('role', '专业分析师')}\n"
        f"   - 语调：{goal.get('writing_role', {}).get('tone', '专业')}\n"
        f"   - 叙述方式：{goal.get('writing_role', {}).get('voice', '客观叙述')}\n"
        f"7. 仅输出 JSON，不要多余文字"
    )


def _parse_synthesize_response(response: str) -> list[dict[str, Any]]:
    """解析 synthesize 的 LLM 响应，含多层 fallback 逻辑。"""
    chapter_prompts: list[dict[str, Any]] = []
    try:
        parsed = _json.loads(response.strip())
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "chapters" in parsed:
            return parsed["chapters"]
    except _json.JSONDecodeError:
        pass

    import re as _re
    # 尝试从被markdown代码块包裹的JSON中提取
    json_match = _re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', response)
    if json_match:
        try:
            parsed = _json.loads(json_match.group(1).strip())
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "chapters" in parsed:
                return parsed["chapters"]
        except _json.JSONDecodeError:
            pass
    else:
        # 原有的 [] 提取逻辑
        match = _re.search(r'\[[\s\S]*\]', response)
        if match:
            try:
                parsed = _json.loads(match.group())
                if isinstance(parsed, list):
                    return parsed
            except _json.JSONDecodeError:
                pass

    logger.warning("  Node3 JSON 解析失败，使用降级大纲")
    # 降级：用默认结构
    return [
        {"title": f"第{i+1}章", "level": 2, "section_type": "body",
         "estimated_words": 500, "writing_intent": "", "key_points": [],
         "avoid_topics": [], "chart_spec": None}
        for i in range(5)
    ]



def synthesize(state: GraphState) -> dict[str, Any]:
    """Node 3: 组装写作 prompt（不生成内容）。

    输入：report_goal + source_content + materials
    输出：chapter_prompts[] — 每章有 writing_intent, key_points, avoid

    v5.1.0: 图表相关逻辑已移除，由 scripts/post_process_charts.py 独立后处理。
    v5.0.6: chapter_prompts 由 CLI Step 1-5 预定义（含 materials_text），
    本节点只做格式传递，不做 LLM 内容生成。
    """
    # 检查是否有预定义的 chapter_prompts
    goal = state.get("report_goal") or {}
    predefined = goal.get("chapter_prompts") or []
    has_predefined = (
        isinstance(predefined, list)
        and len(predefined) > 0
        and any(
            cp.get("writing_intent","") or cp.get("key_points",[])
            for cp in predefined
        )
    )

    if has_predefined:
        chapter_prompts = predefined
        logger.info(
            "[Node 3] synthesize: 使用预定义 chapter_prompts (%d 章, 跳过 LLM)",
            len(chapter_prompts),
        )
        # v5.1.0: 图表已移出管线，由 scripts/post_process_charts.py 独立后处理
    else:
        logger.warning("[Node 3] synthesize: chapter_prompts 为空——建议确认目标后先跑 optimize_structure")
        prompt = _build_synthesize_prompt(state)
        response = call_llm(prompt, max_tokens=6000, temperature=0.3)
        chapter_prompts = _parse_synthesize_response(response)
        logger.info(
            "[Node 3] synthesize: %d chapters synthesized (LLM)",
            len(chapter_prompts),
        )

    return {"chapter_prompts": chapter_prompts}


# ── Node 2.5: optimize_structure ────────────────────────────


def _build_optimize_prompt(
    goal: dict[str, Any],
    outlines: list[str],
    prompt_instruction: str = "",
) -> str:
    """构建 optimize_structure 的 LLM prompt。

    Args:
        goal: 报告目标字典
        outlines: 参考文章目录列表
        prompt_instruction: 自定义 prompt 指令。当 overall_strategy 为空时，
                           传 '请根据报告主题和参考目录规划章节结构' 覆盖默认指令。
    """
    refs_text = "\n".join(outlines[:3]) if outlines else "(无参考文章)"
    goal_text = _json.dumps(goal, ensure_ascii=False)

    if prompt_instruction:
        return (
            f"你是一个报告规划专家。{prompt_instruction}\n\n"
            f"## 报告目标\n{goal_text}\n\n"
            f"## 参考文章目录（仅供参考，若与目标不一致则以目标为准）\n{refs_text}\n\n"
            f"## 规划要求\n"
            f"1. 章节结构应由报告主题和参考目录驱动\n"
            f"2. 不要照搬源文档的模块划分组织章节\n"
            f"3. 聚焦方案论证/可行性评估/能力分析，非实施步骤\n"
            f"4. 每章有明确的 writing_intent 和 key_points\n\n"
            f"输出 JSON 数组：[{{\"title\":...}}]，4-6 章，仅输出 JSON。"
        )

    return (
        f"你是一个报告规划专家。请根据以下报告目标的评估框架来规划章节结构。\n\n"
        f"## 报告目标\n{goal_text}\n\n"
        f"## 参考文章目录（仅供参考，若与目标不一致则以目标为准）\n{refs_text}\n\n"
        f"## 规划要求\n"
        f"1. 章节结构应由 report_goal.overall_strategy 定义的评估维度驱动\n"
        f"2. 不要照搬源文档的模块划分组织章节\n"
        f"3. 聚焦方案论证/可行性评估/能力分析，非实施步骤\n"
        f"4. 每章有明确的 writing_intent 和 key_points\n\n"
        f"输出 JSON 数组：[{{\"title\":...}}]，4-6 章，仅输出 JSON。"
    )


def _parse_optimize_response(response: str) -> list[dict[str, Any]]:
    """解析 optimize_structure 的 LLM 响应。"""
    chapter_prompts: list[dict[str, Any]] = []
    try:
        parsed = _json.loads(response.strip())
        if isinstance(parsed, list):
            chapter_prompts = parsed
        elif isinstance(parsed, dict) and "chapters" in parsed:
            chapter_prompts = parsed["chapters"]
    except _json.JSONDecodeError:
        import re as _re
        match = _re.search(r"\[[\s\S]*\]", response)
        if match:
            try:
                parsed = _json.loads(match.group())
                if isinstance(parsed, list):
                    chapter_prompts = parsed
            except _json.JSONDecodeError:
                pass
    if not chapter_prompts:
        logger.warning("[optimize_structure] 解析失败，使用默认大纲")
        chapter_prompts = [
            {"title": "概述", "level": 1, "section_type": "intro",
             "estimated_words": 500, "writing_intent": "项目背景与目标", "key_points": [], "avoid_topics": []},
            {"title": "核心分析", "level": 1, "section_type": "body",
             "estimated_words": 1500, "writing_intent": "各维度论证", "key_points": [], "avoid_topics": []},
            {"title": "总结与建议", "level": 1, "section_type": "conclusion",
             "estimated_words": 500, "writing_intent": "综合结论与决策建议", "key_points": [], "avoid_topics": []},
        ]
    return chapter_prompts


def optimize_structure(state: GraphState) -> dict[str, Any]:
    """Node 2.5: 根据报告目标优化章节结构（替代 skill Step 2b）。"""
    goal = state.get("report_goal") or {}
    predefined = goal.get("chapter_prompts") or []
    has_predefined = (
        isinstance(predefined, list)
        and len(predefined) > 0
        and any(
            cp.get("writing_intent", "") or cp.get("key_points", [])
            for cp in predefined
        )
    )
    if has_predefined:
        logger.info(
            "[Node 2.5] optimize_structure: 使用预定义 (%d 章, 跳过 LLM)",
            len(predefined),
        )
        return {"chapter_prompts": predefined}

    # chapter_prompts 必须在确认目标时写入 report_goal.json。
    # 管线不在此用 LLM 重生成目录——确定性优先。
    raise ValueError(
        "[Node 2.5] optimize_structure: 缺少预定义章节。请确保"
        " report_goal.chapter_prompts 已写入，然后重新运行管线。"
    )


# ── Node 3: synthesize ──────────────────────────────────────


def curate(state: GraphState) -> dict[str, Any]:
    """Node 4: 按章节意图筛选素材，打包为 materials_text。

    v5.0.6: 如果 chapter_prompts 已带 materials_text（Step 5 预配好），跳过。
    否则：优先从 fact_bank.json 读取结构化事实，回退原文段落提取。

    为每章做三件事：
    1. 加载对应缓存（如有）
    2. 按 writing_intent + key_points 匹配结构化事实
    3. 拼成 materials_text（事实清单，非原文段落）
    """
    chapter_prompts = state.get("chapter_prompts") or []
    source = state["source_content"]
    goal = state.get("report_goal") or {}
    topic = state.get("topic", "")
    if not chapter_prompts:
        return {}

    # 如果已带 materials_text（Step 5 预配好），跳过 curate
    if all(cp.get("materials_text", "") for cp in chapter_prompts):
        logger.info(
            "[Node 4] curate: 跳过（%d 章已有 materials_text）",
            len(chapter_prompts),
        )
        return {"chapter_prompts": chapter_prompts}

    # 尝试加载 fact_bank.json
    fact_bank = _load_fact_bank(topic)

    for cp in chapter_prompts:
        intent = cp.get("writing_intent", "")
        key_points = cp.get("key_points", [])
        parts: list[str] = []

        # ── 优先用结构化事实库 ──
        if fact_bank:
            matched = _match_facts_to_chapter(fact_bank, intent, key_points)
            if matched:
                # 按类别分组展示
                by_cat: dict[str, list[str]] = {}
                for f in matched:
                    cat = f.get("category", "其他")
                    if cat not in by_cat:
                        by_cat[cat] = []
                    by_cat[cat].append(f["fact"])

                parts.append("### 关键事实（来自源文档）\n")
                for cat, items in by_cat.items():
                    parts.append(f"**{cat}**")
                    for item in items:
                        parts.append(f"- {item}")
                    parts.append("")

        # ── 回退：旧逻辑切原文段落 ──
        elif source:
            refs = _extract_source_sections_simple(source)
            matched_sections = []
            for ref in refs:
                if any(kp in ref for kp in key_points if kp):
                    matched_sections.append(ref[:1500])
                    if len(matched_sections) >= 3:
                        break
            if not matched_sections and refs:
                matched_sections = [refs[i][:1500] for i in range(min(2, len(refs)))]
            if matched_sections:
                parts.append("### 源文档参考\n")
                parts.extend(matched_sections)

        # ── 写作约束（不变） ──
        parts.append("\n### 写作约束")
        if intent:
            parts.append(f"写作意图: {intent}")
        if key_points:
            parts.append(f"必须覆盖: {'; '.join(key_points)}")
        avoid = cp.get("avoid_topics", [])
        if avoid:
            parts.append(f"避免涉及: {'; '.join(avoid)}")

        cp["materials_text"] = "\n".join(parts)

        # 追加全局写作角色约束
        wr = goal.get("writing_role", {})
        if wr:
            role_notes: list[str] = []
            if wr.get("tone"):
                role_notes.append(f"语气：{wr['tone']}")
            if wr.get("voice"):
                role_notes.append(f"叙述方式：{wr['voice']}")
            if wr.get("output_conventions"):
                role_notes.append(f"输出规范：{wr['output_conventions']}")
            if role_notes:
                cp["materials_text"] += "\n\n### 全局写作要求\n" + "\n".join(role_notes)

        # 检测素材是否充足
        mt = cp.get("materials_text", "")
        source_has_content = len(mt) > 100
        cp["supplement_needed"] = not source_has_content
        if cp["supplement_needed"]:
            logger.debug("  ⚠️ '%s' 素材不足，标记需补充", cp.get("title", ""))
        logger.debug("  curate: '%s' → %d chars", cp.get("title", ""), len(cp.get("materials_text", "")))

    logger.info(
        "[Node 4] curate: %d chapters curated (source=%s)",
        len(chapter_prompts),
        "fact_bank" if fact_bank else "raw_text",
    )
    return {"chapter_prompts": chapter_prompts}


def _load_fact_bank(topic: str) -> list[dict] | None:
    """加载 fact_bank.json，返回有效的事实列表（排除 superseded 状态）。

    查找路径：reports/<topic>/fact_bank.json
    """
    fb_path = Path("reports") / topic / "fact_bank.json"
    if not fb_path.exists():
        logger.info("  fact_bank.json 不存在，使用原文段落提取")
        return None

    try:
        fb = _json.loads(fb_path.read_text(encoding="utf-8"))
        facts = fb.get("facts", [])
        # 只取有效的事实（排除用户已否定的）
        valid = [f for f in facts if f.get("status") != "superseded"]
        if not valid:
            logger.info("  fact_bank.json 无有效事实，使用原文段落提取")
            return None
        logger.info("  fact_bank.json 已加载: %d 有效事实", len(valid))
        return valid
    except Exception as e:
        logger.warning("  fact_bank.json 加载失败: %s", e)
        return None


def _match_facts_to_chapter(
    facts: list[dict],
    writing_intent: str,
    key_points: list[str],
) -> list[dict]:
    """按章节意图和要点匹配事实。

    匹配策略（由宽到精）：
    1. key_points 关键词命中事实
    2. writing_intent 关键词命中事实
    3. 投资金额/时间节点/架构方案等核心类别（无条件保留部分）
    """
    matched: list[dict] = []
    seen: set[str] = set()

    def _add(f: dict) -> None:
        key = f.get("fact", "")[:50]
        if key not in seen:
            matched.append(f)
            seen.add(key)

    # 收集匹配关键词
    keywords = set()
    for kp in key_points:
        for w in kp.split("、"):
            w = w.strip()
            if w and len(w) >= 2:
                keywords.add(w)
    for w in writing_intent.split("，"):
        w = w.strip()
        if w and len(w) >= 3:
            keywords.add(w)

    # 优先：关键词命中
    for f in facts:
        text = f.get("fact", "")
        for kw in keywords:
            if kw in text:
                _add(f)
                break

    # 补充：核心类别（确保关键数据不遗失）
    core_categories = {"投资金额", "时间节点", "架构方案"}
    for f in facts:
        if f.get("category") in core_categories:
            _add(f)

    return matched


def coverage_check(state: GraphState) -> dict[str, Any]:
    """Node 4.5: 检查每章 key_points 覆盖度，不足时搜索补充素材。

    输入：chapter_prompts（含 materials_text）+ fact_bank
    输出：补充后的 chapter_prompts（或标记不足）

    Workflow:
    1. 逐章检查 key_points 是否已在 materials_text / fact_bank 中覆盖
    2. 不足时从缺失 key_points 提取搜索关键词
    3. 调用 MaterialService.prepare 搜索补充（按缺失数量降序，15s超时）
    4. 重检查：仍然不足则标记 supplement_needed
    """
    chapter_prompts = state.get("chapter_prompts") or []
    topic = state.get("topic", "")

    if not chapter_prompts:
        return {}

    logger.info("[Node 4.5] coverage_check: 开始 %d 章覆盖度检查", len(chapter_prompts))

    # ── #6: 读取 coverage_threshold ──
    coverage_threshold = 0.7  # 默认值
    domain_cfg = state.get("domain_config") or {}
    if isinstance(domain_cfg, dict) and "coverage_threshold" in domain_cfg:
        coverage_threshold = float(domain_cfg["coverage_threshold"])
    else:
        try:
            from ..config import load_report_config
            report_type = state.get("report_type", "")
            rc = load_report_config(topic, report_type=report_type)
            rc_threshold = rc.get("coverage_threshold")
            if rc_threshold is not None:
                coverage_threshold = float(rc_threshold)
        except Exception:
            pass
    logger.info("  coverage_check: coverage_threshold=%.2f", coverage_threshold)

    # 加载 fact_bank 辅助判断
    fact_bank = _load_fact_bank(topic)

    material_service = MaterialService(
        domain_config=state.get("domain_config"),
    )

    # ── 第一遍：逐章检查覆盖度 ──
    chapters_needing_search: list[tuple[int, dict, list[str], str]] = []

    for cp in chapter_prompts:
        key_points = cp.get("key_points", [])
        materials_text = cp.get("materials_text", "")
        title = cp.get("title", "未知章节")

        if not key_points:
            logger.debug("  coverage_check: '%s' 无 key_points，跳过", title)
            continue

        # 检查当前 materials_text 中是否已覆盖所有 key_points
        missing_kps: list[str] = []
        for kp in key_points:
            if not kp:
                continue
            if kp in materials_text:
                continue
            # 在 fact_bank 中也检查
            found_in_fb = False
            if fact_bank:
                for fb_entry in fact_bank:
                    text = fb_entry.get("fact", "")
                    if kp in text:
                        found_in_fb = True
                        break
            if not found_in_fb:
                missing_kps.append(kp)

        total_kps = len([kp for kp in key_points if kp])
        covered_kps = total_kps - len(missing_kps)
        coverage_ratio = covered_kps / total_kps if total_kps > 0 else 1.0

        if coverage_ratio >= coverage_threshold:
            logger.debug(
                "  coverage_check: '%s' 覆盖度 %.1f%% >= %.0f%%，无需搜索",
                title, coverage_ratio * 100, coverage_threshold * 100,
            )
            continue

        logger.info(
            "  coverage_check: '%s' 覆盖度 %.1f%%，缺失 %d 个 key_points: %s",
            title, coverage_ratio * 100, len(missing_kps), missing_kps,
        )

        chapters_needing_search.append((len(missing_kps), cp, missing_kps, title))

    # ── #7: 按缺失 key_points 数量降序排列 ──
    chapters_needing_search.sort(key=lambda x: x[0], reverse=True)

    if chapters_needing_search:
        logger.info(
            "  coverage_check: 按缺失数量降序搜索 %d 章（最多缺失 %d 个）",
            len(chapters_needing_search), chapters_needing_search[0][0],
        )

    # ── #7: 第二遍搜索，每章 15s 超时 ──
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

    for missing_count, cp, missing_kps, title in chapters_needing_search:
        # Step 2: 从缺失 key_points 提取搜索关键词
        search_kws: list[str] = []
        for kp in missing_kps:
            kp_stripped = kp.strip()
            if not kp_stripped:
                continue
            # 以 "、" 分隔的分项拆开作为搜索词
            if "、" in kp_stripped:
                for w in kp_stripped.split("、"):
                    w2 = w.strip()
                    if w2 and len(w2) >= 2:
                        search_kws.append(w2)
            else:
                search_kws.append(kp_stripped)

        if not search_kws:
            search_kws = missing_kps

        query = " ".join(search_kws[:5])
        materials_text = cp.get("materials_text", "")

        # Step 3: 搜索补充（15s 超时）
        try:
            with ThreadPoolExecutor() as executor:
                future = executor.submit(
                    material_service.prepare,
                    chapter_key=title,
                    query=query,
                    with_kb=True,
                    max_web=2,
                )
                try:
                    pack = future.result(timeout=15)
                    # #5: 搜索结果与已有 materials_text 去重
                    supplement_text = _format_supplement_from_pack(pack, existing_text=materials_text)
                    if supplement_text:
                        cp["materials_text"] = (
                            (cp.get("materials_text") or "")
                            + "\n\n### coverage_check 补充\n" + supplement_text
                        )
                        logger.info(
                            "  coverage_check: '%s' 已补充素材 (%d extracted, %d web)",
                            title, len(pack.extracted), len(pack.web_results),
                        )
                except FutureTimeoutError:
                    logger.warning(
                        "  coverage_check: '%s' 搜索超时 (15s)，标记为 supplement_needed",
                        title,
                    )
                    cp["supplement_needed"] = True
                    continue  # 超时，跳到下一章
        except Exception as e:
            logger.warning("  coverage_check: '%s' 搜索补充失败: %s", title, e)

        # Step 4: 重检查——仍然不足则标记
        still_missing: list[str] = []
        for kp in missing_kps:
            if kp in cp.get("materials_text", ""):
                continue
            still_missing.append(kp)

        if still_missing:
            cp["supplement_needed"] = True
            logger.warning(
                "  coverage_check: '%s' 仍有 %d 个 key_points 未覆盖: %s",
                title, len(still_missing), still_missing,
            )
        else:
            cp["supplement_needed"] = False

    logger.info("[Node 4.5] coverage_check: 完成")
    return {"chapter_prompts": chapter_prompts}


def _format_supplement_from_pack(pack: Any, existing_text: str = "") -> str:
    """格式化 MaterialPack 为补充素材文本，支持与已有文本去重。

    搜索结果前 50 字在 existing_text 中已存在时跳过（子串匹配）。
    """
    parts: list[str] = []
    for art in pack.extracted[:3]:
        title = getattr(art, "title", "") or getattr(art, "url", "")[:50]
        content = getattr(art, "content", "") or ""
        if content:
            # Dedup: skip if first 50 chars already exist in existing_text
            if existing_text and content[:50] in existing_text:
                continue
            parts.append(f"- {_json.dumps(title, ensure_ascii=False)}: {content[:600]}")
    for wr in pack.web_results[:3]:
        title = getattr(wr, "title", "") or getattr(wr, "url", "")[:50]
        snippet = getattr(wr, "snippet", "") or ""
        if snippet:
            # Dedup: skip if first 50 chars of snippet already exist in existing_text
            if existing_text and snippet[:50] in existing_text:
                continue
            parts.append(f"- {_json.dumps(title, ensure_ascii=False)} (摘要): {snippet[:300]}")
    return "\n".join(parts)


def _build_prompt_review_prompt(
    goal: dict[str, Any],
    chapter_prompts: list[dict[str, Any]],
    source: str,
) -> str:
    """构建 prompt_review 的 LLM prompt。"""
    goal_text = _json.dumps(goal, ensure_ascii=False)
    chapters_text = _json.dumps(
        [{
            "title": cp.get("title"),
            "writing_intent": cp.get("writing_intent"),
            "key_points": cp.get("key_points"),
            "avoid_topics": cp.get("avoid_topics"),
            "chart_spec": cp.get("chart_spec"),
            "section_type": cp.get("section_type"),
            "materials_text": cp.get("materials_text", ""),
        } for cp in chapter_prompts],
        ensure_ascii=False, indent=2,
    )
    source_preview = source[:3000] if source else "(无源文档)"

    return (
        f"你是一个提示词质量审核专家。请审核以下报告大纲，并优化。\n\n"
        f"## 报告目标\n{goal_text}\n\n"
        f"## 源文档摘要\n{source_preview}\n\n"
        f"## 当前大纲\n{chapters_text}\n\n"
        f"### 第0步：必须覆盖要点对账\\n"
        f"先基于「报告目标」、「各章素材(materials_text)」和「源文档摘要」，列出一份「必须覆盖要点清单」（5-10 项），\\n"
        f"然后逐项检查当前大纲是否覆盖了该要点：\\n"
        f"- 如果已覆盖，注明覆盖在哪一章、基于哪个 key_points\\n"
        f"- 如果遗漏，说明应在哪个位置补充、是因为素材中已有对应数据但大纲未列\\n"
        f"注意：请使用各章的 materials_text 来判断该章的关键覆盖范围，\\n"
        f"而不是仅从 key_points 列表来判断——素材中如有数据支撑但 key_points 未列，说明 key_points 有遗漏。\\n\\n"
        f"### 然后检查以下问题：\n"
        f"1. 所有章节是否覆盖了报告目标？有没有明显遗漏？\n"
        f"2. 章节之间是否有重叠或矛盾？\n"
        f"3. 每个章节的 key_points 和 avoid_topics 是否冲突？\n"
        f"4. 角色一致性检查：各章节的 writing_intent 和 key_points\n"
        f"   是否符合报告目标中定义的写作角色和语调？\n"
        f"   角色：{goal.get('writing_role', {}).get('role', 'N/A')}\n"
        f"   语调：{goal.get('writing_role', {}).get('tone', 'N/A')}\n"
        f"5. chart_spec 是否与写作意图匹配？\n"
        f"6. 整体章节顺序是否合理？\n\n"
        f"### 关键约束\n"
        f"**必须保留原始 key_points 和 writing_intent**，这些是用户确认的必须覆盖内容。\n"
        f"- 只能补充遗漏的要点（追加到数组末尾）\n"
        f"- 不能删除或修改已有的 key_points\n"
        f"- 不能替换已有的 writing_intent\n"
        f"- 如果发现遗漏，在对应章节追加新的 key_point，不要改动原内容\n\n"
        f"基于以上分析，输出优化后的 JSON 数组（格式与输入相同），仅输出 JSON，不要多余文字。"
    )


def _parse_prompt_review_response(
    response: str,
    chapter_prompts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """解析 prompt_review 的 LLM 响应，含 fallback 逻辑。"""
    optimized: list[dict[str, Any]] | None = None
    try:
        parsed = _json.loads(response.strip())
        if isinstance(parsed, list):
            optimized = parsed
        elif isinstance(parsed, dict) and "optimized" in parsed:
            optimized = parsed["optimized"]
    except _json.JSONDecodeError:
        import re as _re
        match = _re.search(r'\[[\s\S]*\]', response)
        if match:
            try:
                parsed = _json.loads(match.group())
                if isinstance(parsed, list):
                    optimized = parsed
            except _json.JSONDecodeError:
                pass

    if not optimized:
        logger.info("  prompt_review: LLM 输出解析失败，使用原始大纲")
        return chapter_prompts

    # 保留原始非结构化字段 + v5.0.6 保护 key_points 和 writing_intent
    PROTECTED_KEYS = ("materials_text", "section_type", "key_points", "writing_intent")
    for i, opt in enumerate(optimized):
        if i < len(chapter_prompts):
            for key in PROTECTED_KEYS:
                orig_val = chapter_prompts[i].get(key)
                opt_val = opt.get(key)
                if key in ("key_points",):
                    # key_points: 合并，不覆盖——原要点必须在，新的追加
                    # v5.0.6+: LLM 整键丢弃时还原，非 list 时也还原
                    if isinstance(orig_val, list):
                        if not isinstance(opt_val, list):
                            opt[key] = list(orig_val)
                        else:
                            combined = list(orig_val)
                            for item in opt_val:
                                if item not in combined:
                                    combined.append(item)
                            opt[key] = combined
                elif key == "writing_intent":
                    # writing_intent: 以原始为准，LLM可追加补充说明
                    # v5.0.6+: 去掉 and opt_val 条件——LLM 整键丢弃时也还原
                    if orig_val and orig_val != opt_val:
                        opt[key] = orig_val
                else:
                    # materials_text / section_type: 原逻辑
                    if key in chapter_prompts[i] and key not in opt:
                        opt[key] = chapter_prompts[i][key]
    # 移除 LLM 可能带回的 materials_summary（只存在输入 prompt 中，不写盘）
    for opt in optimized:
        opt.pop("materials_summary", None)
    return optimized


def prompt_review(state: GraphState) -> dict[str, Any]:
    """Node 5: LLM 自检 + 必须覆盖要点对账。

    流程：
    0. 分析源文档 + report_goal → 提取"必须覆盖要点清单"
    1. 逐章对账：当前大纲覆盖了哪些？遗漏了哪些？
    2-6. 原有 5 项检查
    输出优化后的 chapter_prompts。
    """
    goal = state.get("report_goal") or {}
    chapter_prompts = state.get("chapter_prompts") or []
    source = state.get("source_content", "")

    if not chapter_prompts:
        return {}

    prompt = _build_prompt_review_prompt(goal, chapter_prompts, source)
    response = call_llm(prompt, max_tokens=6000, temperature=0.3)
    optimized = _parse_prompt_review_response(response, chapter_prompts)

    logger.info(
        "[Node 5] prompt_review: %d chapters reviewed",
        len(optimized),
    )
    return {"optimized_prompts": optimized}


# ── 工具函数 ──────────────────────────────────────────────



# ── 图表数据注入 (v5.2.0 移除：已移至 scripts/post_process_charts.py 独立后处理) ──





# ── 图表数据注入 (v5.2.0 移除：已移至 scripts/post_process_charts.py 独立后处理) ──


def _extract_source_sections_simple(source: str) -> list[str]:
    """增强提取源文档的主要段落。

    同时按两种模式拆分：
      1. 按 📄 文件标记（多文件合并源）
      2. 按 一/二/三/（ 中文序号（传统格式）

    确保每个 docx 文件的内容作为独立段落被提取。
    """
    sections: list[str] = []

    # ── 模式 1：按 📄 文件标记拆分 ──
    if "📄" in source:
        file_blocks = source.split("📄")
        for block in file_blocks:
            block = block.strip()
            if not block:
                continue
            sections.append(block)
        return sections

    # ── 模式 2：按中文序号拆分（旧格式，单文件） ──
    current: list[str] = []
    for line in source.split("\n"):
        stripped = line.strip()
        if stripped and any(
            stripped.startswith(p) for p in ["一", "二", "三", "四", "五", "（"]
        ):
            if current:
                sections.append("\n".join(current))
                current = []
        if stripped:
            current.append(stripped)
    if current:
        sections.append("\n".join(current))
    return sections


# ── Graph 构建 ──────────────────────────────────────────────


def build_report_graph() -> StateGraph:
    """构建并返回报告规划 StateGraph。"""
    builder = StateGraph(GraphState)

    builder.add_node("define_goal", define_goal)
    builder.add_node("search_refs", search_refs)
    builder.add_node("optimize_structure", optimize_structure)
    builder.add_node("synthesize", synthesize)
    builder.add_node("curate", curate)
    builder.add_node("coverage_check", coverage_check)
    builder.add_node("prompt_review", prompt_review)

    builder.set_entry_point("define_goal")
    builder.add_edge("define_goal", "search_refs")
    builder.add_edge("search_refs", "optimize_structure")
    builder.add_edge("optimize_structure", "synthesize")
    builder.add_edge("synthesize", "curate")
    builder.add_edge("curate", "coverage_check")
    builder.add_edge("coverage_check", "prompt_review")
    builder.add_edge("prompt_review", END)

    return builder.compile()


def build_goal_graph() -> StateGraph:
    """构建仅含 define_goal 的单节点图（供目标确认流程使用）。"""
    builder = StateGraph(GraphState)
    builder.add_node("define_goal", define_goal)
    builder.set_entry_point("define_goal")
    builder.add_edge("define_goal", END)
    return builder.compile()


def run_goal_definition(
    topic: str,
    source_content: str = "",
    report_type: str = "tech",
    language: str = "zh",
    domain_config: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """只运行 Node 1（define_goal），返回 report_goal。

    自动落盘到 reports/<topic>/report_goal.json。
    用于管线中暂停等待用户确认目标+角色后再继续。

    Returns:
        优化后的 report_goal 字典
    """
    app = build_goal_graph()
    result = app.invoke({
        "topic": topic,
        "source_content": source_content,
        "report_type": report_type,
        "language": language,
        "report_goal": None,
        "reference_outlines": [],
        "chapter_prompts": None,
        "materials": None,
        "optimized_prompts": None,
        "domain_config": domain_config,
    })
    raw_goal = result.get("report_goal") or {}
    # LLM 优化：修复截断、补充薄弱字段
    optimized = _optimize_goal(raw_goal, source_content=source_content)

    # ── 自动保存到 reports/<topic>/report_goal.json ──
    import os as _os
    safe_name = topic.replace(" ", "_").replace("/", "_").replace("\\", "_")[:60]
    goal_dir = Path("reports") / safe_name
    goal_dir.mkdir(parents=True, exist_ok=True)
    goal_path = goal_dir / "report_goal.json"
    goal_path.write_text(
        _json.dumps(optimized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("  ✅ goal 已自动保存: %s (%d chars)", goal_path, goal_path.stat().st_size)
    return optimized


def run_planning(
    topic: str,
    source_content: str = "",
    report_type: str = "tech",
    language: str = "zh",
    reference_outlines: list[str] | None = None,
    report_goal: dict[str, Any] | None = None,
    domain_config: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """一键运行 StateGraph 规划管线。

    Args:
        topic: 报告主题
        source_content: 源文档全文
        report_type: 报告类型
        language: 语言
        reference_outlines: 同类文章大纲参考（由 Orchestrator 通过 delegate_task 获取）
        domain_config: 域配置 {high: [...], medium: [...]}，None 使用模块默认值

    Returns:
        {"optimized_prompts": [...], "report_goal": {...}}
    """
    if report_goal:
        # 使用已确认的目标，直接跑 search_refs → ... → prompt_review
        from langgraph.graph import StateGraph as _SG, END as _END
        app = _SG(GraphState)
        app.add_node("search_refs", search_refs)
        app.add_node("optimize_structure", optimize_structure)
        app.add_node("synthesize", synthesize)
        app.add_node("curate", curate)
        app.add_node("coverage_check", coverage_check)
        app.add_node("prompt_review", prompt_review)
        app.set_entry_point("search_refs")
        app.add_edge("search_refs", "optimize_structure")
        app.add_edge("optimize_structure", "synthesize")
        app.add_edge("synthesize", "curate")
        app.add_edge("curate", "coverage_check")
        app.add_edge("coverage_check", "prompt_review")
        app.add_edge("prompt_review", _END)
        stateful_app = app.compile()
    else:
        stateful_app = build_report_graph()

    result = stateful_app.invoke({
        "topic": topic,
        "source_content": source_content,
        "report_type": report_type,
        "language": language,
        "report_goal": report_goal or None,
        "reference_outlines": reference_outlines or [],
        "chapter_prompts": None,
        "materials": None,
        "optimized_prompts": None,
        "domain_config": domain_config,
    })
    return result
