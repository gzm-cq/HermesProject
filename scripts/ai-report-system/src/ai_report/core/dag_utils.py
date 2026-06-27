"""
DAG 层推导工具 — 为并行章节生成提供依赖关系分析
============================================

职责：
  根据章节的 section_type 自动推导 3 层 DAG 结构。
  Layer 0: intro 类型（无依赖，可立即并行）
  Layer 1: body/analysis 类型（依赖 Layer 0）
  Layer 2: conclusion/appendix 类型（依赖全部前层）

设计原则：
  - 不硬编码章节标题，仅使用 section_type 元数据
  - 章节 ≤3 或无有效 section_type 时退化为单层
  - 返回的索引列表保持原始章节顺序
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── 类型别名 ─────────────────────────────────────────────


# ── DAG 推导 ─────────────────────────────────────────────

def derive_dag_layers(
    sections: list,
    chapter_prompts: list[dict] | None = None,
) -> list[list[int]]:
    """从章节规格推导 DAG 分层。

    章节分类规则（基于 section_type 字段）：
      Layer 0 (intro): 概述、背景、引言 — 无依赖
      Layer 1 (body/analysis): 方案、架构、分析 — 依赖 Layer 0
      Layer 2 (conclusion/appendix): 结论、建议、附录 — 依赖全部前层

    降级规则：
      - 总章节 ≤ 3 → 单层（退化为纯并行，不降串行）
      - 无 intro → Layer 0 为空（body 章节自动前移）
      - 无 conclusion → Layer 2 为空
      - section_type 为未知值 → 视为 body（不阻塞流程）

    Args:
        sections: SectionSpec 列表（必须含 section_type 属性）
        chapter_prompts: 可选，从 StateGraph 获得的章节提示词列表

    Returns:
        分层索引列表。如 [[0, 2], [1, 3, 5], [4]]
        每层内索引顺序保持原始章节顺序。
        空层会被跳过（不再入返回列表）。
    """
    n = len(sections)
    if n == 0:
        return []

    # 从 sections 提取 section_type
    section_types = _extract_section_types(sections, chapter_prompts, n)

    # 分层
    layer0: list[int] = []
    layer1: list[int] = []
    layer2: list[int] = []

    for i, stype in enumerate(section_types):
        stype_lower = stype.strip().lower() if stype else ""

        if stype_lower in ("intro", "introduction", "background", "overview"):
            layer0.append(i)
        elif stype_lower in ("conclusion", "summary", "appendix", "recommendation",
                             "conclusions", "总结", "结论", "附录"):
            layer2.append(i)
        else:
            # body, analysis, 或未知类型 → 视为 body
            layer1.append(i)

    # 降级：总章节 ≤ 3 或无 intro 类 → 单层
    if n <= 3 or (not layer0 and not layer2):
        logger.info("  DAG 降级: 章节数=%d, 无 intro/conclusion → 单层", n)
        return [list(range(n))]

    # 组装结果（跳过空层）
    layers: list[list[int]] = []
    if layer0:
        layers.append(layer0)
    if layer1:
        layers.append(layer1)
    if layer2:
        layers.append(layer2)

    # 验证：所有章节索引都被覆盖
    assigned = set(layer0) | set(layer1) | set(layer2)
    if len(assigned) != n:
        unassigned = [i for i in range(n) if i not in assigned]
        logger.warning("DAG 未覆盖所有章节: %s, 强制附加", unassigned)
        if layers:
            layers[-1].extend(unassigned)
        else:
            layers.append(unassigned)

    _log_layers(layers, sections)
    return layers


def _extract_section_types(
    sections: list,
    chapter_prompts: list[dict] | None,
    n: int,
) -> list[str]:
    """提取每个章节的 section_type。

    优先级：
      1. chapter_prompts[i].section_type（StateGraph 生成）
      2. sections[i].section_type（ planner 设定）
      3. 回退: "body"
    """
    section_types: list[str] = []

    for i in range(n):
        stype: str | None = None

        # 优先从 chapter_prompts 取
        if chapter_prompts and i < len(chapter_prompts):
            cp = chapter_prompts[i]
            if isinstance(cp, dict):
                stype = cp.get("section_type")

        # 再从 sections 取
        if not stype and hasattr(sections[i], "section_type"):
            stype = getattr(sections[i], "section_type", None)

        if not stype:
            stype = "body"

        section_types.append(stype)

    return section_types


def _log_layers(layers: list[list[int]], sections: list) -> None:
    """记录 DAG 分层信息到日志。"""
    if not layers:
        logger.info("  DAG: 空层（无章节）")
        return

    layer_desc = []
    for li, layer in enumerate(layers):
        titles = []
        for idx in layer:
            if idx < len(sections):
                title = getattr(sections[idx], "title", f"ch-{idx}")
            else:
                title = f"ch-{idx}"
            titles.append(f"'{title}'")
        layer_desc.append(f"[{', '.join(titles)}]")

    logger.info("  DAG %d 层: %s", len(layers), " → ".join(layer_desc))
