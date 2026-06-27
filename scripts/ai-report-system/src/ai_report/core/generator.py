"""
Hermes Content Generator — 报告内容生成器
=========================================
三步方案中的 Step 3（写文章）。

职责：
  接收 ReportPlan（含每章 chapter_prompts[]），逐章写作，拼装为完整报告。

数据流（三步方案）：
  Step 2（定框架）产出了 chapter_prompts[]，其中 materials_text 已由
  supplement_search 注入网络素材。Step 3 直接使用这些素材写作，不再搜索。

搜索相关：
  首次写作不搜索素材（素材已由 Step 2 注入 materials_text）。
  HermesWebSearcher 已移除。质量闭环迭代如需补搜，由外部编排器
  通过 MaterialService（Tavily/DuckDuckGo）完成。
  当前内容生成器专注串行写作与组装，不负责搜索。

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, TypeAlias

import json as _json

from .base import BaseComponent
from .exceptions import ReportAgentError
from ..adapters.ai_client import call_llm
from ..config import get_parallel_config
from .workflow_state import WorkflowState, SECTION_TYPE_BUSINESS
from .dag_utils import derive_dag_layers
from .planner import ReportPlan, SectionSpec

logger = logging.getLogger(__name__)
SectionContent: TypeAlias = str


@dataclass
class GeneratedSection:
    """已生成的章节内容。"""
    spec: SectionSpec
    content: str
    word_count: int = 0
    quality_score: float = 0.0
    generation_attempts: int = 1
    used_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.word_count:
            self.word_count = len(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.spec.title, "content": self.content,
            "word_count": self.word_count, "quality_score": self.quality_score,
            "generation_attempts": self.generation_attempts,
            "used_fallback": self.used_fallback,
        }


@dataclass
class GeneratedReport:
    """生成的完整报告。"""
    plan: ReportPlan
    sections: list[GeneratedSection]
    full_content: str
    total_words: int
    generation_time_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.plan.title, "topic": self.plan.topic,
            "report_type": self.plan.report_type,
            "total_words": self.total_words,
            "sections_count": len(self.sections),
            "generation_time_ms": self.generation_time_ms,
            "sections": [s.to_dict() for s in self.sections],
            "metadata": self.metadata,
        }


class HermesContentGenerator(BaseComponent):
    """报告内容生成器。

    三步方案中 Step 3：
    - 输入：ReportPlan（来自 orch.run()，含富化的 chapter_prompts[]）
    - 输出：GeneratedReport（完整报告全文）
    - 搜索：首次写作不搜索；质量迭代通过 MaterialService（Tavily/DuckDuckGo）补搜

    写作流程：
      Phase 1: 串行写作（按 chapter_prompts 的 writing_intent + materials_text）
      全文闭环: FullReportLoop（一致性检查）
    """
    COMPONENT_NAME = "HermesContentGenerator"
    COMPONENT_VERSION = "3.0.0"
    COMPONENT_DESCRIPTION = "基于DAG并行化的报告内容生成器，支持3层并行章节写入"

    def __init__(
        self,
        config: Any | None = None,
        task_executor: Callable[..., str] | None = None,
    ) -> None:
        """初始化内容生成器。

        Args:
            config: 配置
            task_executor: 任务执行函数（签名同 delegate_task），
                          None=使用默认 call_llm（向后兼容）
        """
        self._max_retries: int = 2
        self._task_executor: Callable[..., str] | None = task_executor
        parallel_cfg = get_parallel_config()
        self._parallel_enabled = parallel_cfg.enabled
        self._chapter_max_workers = parallel_cfg.chapter_max_workers
        super().__init__(config)
    def _initialize_internal(self) -> None:
        logger.info("%s v3 init (无内置搜索器)", self.COMPONENT_NAME)

    # ── 委托式写作（替代直接 call_llm） ─────────────────────

    @staticmethod
    def _parse_result(result: str) -> tuple[str, str]:
        """从 delegate_task 返回结果中解析正文和摘要。

        约定：末尾 ---SUMMARY--- 标记后为本章摘要。
        无标记时 fallback 取前 200 字。

        Returns:
            (content, summary) 元组
        """
        marker = "---SUMMARY---"
        if marker in result:
            parts = result.split(marker, 1)
            return parts[0].strip(), parts[1].strip()[:200]
        text = result.strip()
        return text, text[:200]

    def _build_chapter_context_json(
        self,
        spec: SectionSpec,
        plan: ReportPlan,
        chapter_index: int,
        prev_chapter_summaries: list[str] | None = None,
        sibling_chapters: list[dict[str, str]] | None = None,
    ) -> str:
        """构建逐章委托的结构化 JSON context。

        Args:
            spec: 当前章节规格
            plan: 完整报告计划
            chapter_index: 章节序号（0-based）
            prev_chapter_summaries: 已写章节摘要列表
            sibling_chapters: 同层并行章节的标题+意图（避免内容重叠）

        Returns:
            JSON 字符串
        """
        goal = plan.metadata.get("report_goal", {}) if hasattr(plan, "metadata") else {}
        cp = None
        chapter_prompts = plan.metadata.get("chapter_prompts") if hasattr(plan, "metadata") else None
        if chapter_prompts and chapter_index < len(chapter_prompts):
            cp = chapter_prompts[chapter_index]

        writing_intent = ""
        key_points: list[str] = []
        avoid_topics: list[str] = []
        chart_spec: dict[str, Any] | None = None
        materials_text = spec.content_template or ""

        if cp:
            writing_intent = cp.get("writing_intent", "") or ""
            key_points = cp.get("key_points") or []
            avoid_topics = cp.get("avoid_topics") or []
            chart_spec = cp.get("chart_spec")
            if cp.get("materials_text"):
                materials_text = cp["materials_text"]
        else:
            # 从 required_data 提取
            for item in spec.required_data:
                if item.startswith("intent:"):
                    writing_intent = item[7:]
                elif item.startswith("kp:"):
                    key_points.append(item[3:])

        prev_summary = ""
        if prev_chapter_summaries:
            prev_summary = "\n".join(f"- {s[:200]}" for s in prev_chapter_summaries[-5:])

        instructions = (
            "你是一个专业的报告写作助手。请根据以下信息写出本章完整内容。\n"
            "要求：\n"
            "1. 严格遵循 writing_role 要求的角色、语调和叙述方式\n"
            "2. 必须覆盖 key_points 中的所有要点\n"
            "3. 避免涉及 avoid_topics 中的话题\n"
            "4. 如果指定了 chart_spec，在合适位置插入图表标记\n"
            "5. 确保与上章内容衔接顺畅、不重复\n"
            "6. 输出纯文本 Markdown，不要多余的前缀说明\n"
            "7. 最后用 ---SUMMARY--- 单独一行，其后跟本章摘要（不超过200字）\n"
        )
        # ⚠️ 标记数据覆盖度不足的章节（#8: supplement_needed 标记传递）
        if cp and cp.get("supplement_needed"):
            instructions = (
                "⚠️ 本章数据覆盖度不足，以下内容部分基于有限素材撰写。\n\n"
                + instructions
            )

        context: dict[str, Any] = {
            "_instructions": instructions,
            "task_type": "write_chapter",
            "report": {
                "topic": plan.topic,
                "type": plan.report_type,
                "language": plan.language,
            },
            "report_goal": goal,
            "chapter": {
                "index": chapter_index,
                "title": spec.title,
                "level": spec.level,
                "section_type": spec.section_type or "body",
                "estimated_words": spec.estimated_words,
                "writing_intent": writing_intent,
                "key_points": key_points,
                "avoid_topics": avoid_topics,
                "chart_spec": chart_spec,
            },
            "materials_text": materials_text,
            "prev_chapter_summary": prev_summary,
            "sibling_chapters": sibling_chapters or [],
        }
        return _json.dumps(context, ensure_ascii=False)

    def _write_chapters_parallel(
        self,
        layer_indices: list[int],
        sections: list[SectionSpec],
        state: WorkflowState,
        plan: ReportPlan,
        retries: int,
        all_prev_summaries: list[str],
    ) -> list[GeneratedSection]:
        """并行写入一层的所有章节。

        实现策略：
          - 单章节层：复用现有 _write_chapter() 串行路径
          - 多章节层：ThreadPoolExecutor 并行委托，每章注入同层其他章节信息

        Args:
            layer_indices: 本层章节在 plan.sections 中的索引列表
            sections: 完整的章节规格列表
            state: 工作流状态
            plan: 报告计划
            retries: 最大重试次数
            all_prev_summaries: 前面所有层的摘要列表

        Returns:
            本层生成的 GeneratedSection 列表（按原始顺序）
        """
        chapter_prompts = plan.metadata.get("chapter_prompts") if hasattr(plan, "metadata") else None

        if len(layer_indices) == 1:
            # ── 单章节：串行写入（复用现有路径） ──
            i = layer_indices[0]
            gen = self._write_chapter(
                sections[i], state, plan, retries, i,
                prev_chapter_summaries=all_prev_summaries,
            )
            return [gen]

        # ── 多章节：构建 sibling_chapters 信息 ──
        sibling_info: list[dict[str, str]] = []
        for i in layer_indices:
            info: dict[str, str] = {"title": sections[i].title}
            if chapter_prompts and i < len(chapter_prompts):
                cp = chapter_prompts[i]
                if isinstance(cp, dict):
                    intent = cp.get("writing_intent", "") or ""
                    if intent:
                        info["writing_intent"] = intent[:200]
            sibling_info.append(info)

        # ── 并行生成章节 ──
        if self._parallel_enabled:
            return self._write_chapters_concurrent(
                layer_indices, sections, state, plan, retries,
                all_prev_summaries, sibling_info,
            )

        # ── 降级串行路径 ──
        result: list[GeneratedSection] = []
        for i in layer_indices:
            gen = self._write_chapter(
                sections[i], state, plan, retries, i,
                prev_chapter_summaries=all_prev_summaries,
                sibling_chapters=sibling_info,
            )
            result.append(gen)
        return result

    def _write_chapters_concurrent(
        self,
        layer_indices: list[int],
        sections: list[SectionSpec],
        state: WorkflowState,
        plan: ReportPlan,
        retries: int,
        all_prev_summaries: list[str],
        sibling_info: list[dict[str, str]],
    ) -> list[GeneratedSection]:
        """使用线程池并行写入同一层的多个章节。

        线程安全说明：
          - 每个章节生成是独立的，不共享可变状态
          - state.set_chapter_result() 在各线程内独立调用，
            不同章节写入不同的 key（章节标题），无竞争

        Args:
            layer_indices: 本层章节索引列表
            sections: 完整章节规格列表
            state: 工作流状态
            plan: 报告计划
            retries: 最大重试次数
            all_prev_summaries: 前层摘要
            sibling_info: 同层章节信息

        Returns:
            按原始章节顺序排列的 GeneratedSection 列表
        """
        max_workers = min(self._chapter_max_workers, len(layer_indices))
        # 保存结果到对应位置，保证顺序
        indexed_results: dict[int, GeneratedSection] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(
                    self._write_chapter,
                    sections[i], state, plan, retries, i,
                    prev_chapter_summaries=all_prev_summaries,
                    sibling_chapters=sibling_info,
                ): i
                for i in layer_indices
            }

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    gen = future.result()
                    indexed_results[idx] = gen
                except Exception as e:
                    logger.warning(
                        "  ⚠ 并行章节[%d]失败, 降级串行重试: %s",
                        idx, e,
                    )
                    # 降级为串行重试
                    try:
                        gen = self._write_chapter(
                            sections[idx], state, plan, retries, idx,
                            prev_chapter_summaries=all_prev_summaries,
                            sibling_chapters=sibling_info,
                        )
                        indexed_results[idx] = gen
                    except Exception as retry_e:
                        logger.error(
                            "  ❌ 串行重试章节[%d]也失败: %s",
                            idx, retry_e,
                        )
                        indexed_results[idx] = self._fallback_section(
                            sections[idx], plan, retries, state,
                        )

        # 按原始顺序排列
        return [indexed_results[i] for i in layer_indices if i in indexed_results]

    def generate_from_plan(
        self,
        plan: ReportPlan,
        max_retries: int | None = None,
    ) -> GeneratedReport:
        """执行四阶段报告生成。

        注意：首次写作不搜索网络素材。
        素材来源渠道：
          1. chapter_prompts[i].materials_text ← supplement_search 注入（主渠道）
          2. source_doc 段落 ← curate 节点提取（备选）
          3. 质量迭代时补搜 ← MaterialService（Tavily/DuckDuckGo）
        """
        start_time = time.time()
        if not plan.sections:
            raise ReportAgentError("报告计划中没有章节")

        retries = max_retries or self._max_retries
        state = WorkflowState(topic=plan.topic, report_type=plan.report_type)
        main_context = self._build_main_context(plan)
        report_goal = plan.metadata.get("report_goal") if hasattr(plan, "metadata") else None
        state.init_from_plan(plan.sections, main_context=main_context, report_goal=report_goal)

        # Phase 1: DAG 驱动并行写入（后层依赖前层摘要）
        chapter_prompts = plan.metadata.get("chapter_prompts") if hasattr(plan, "metadata") else None
        dag_layers = derive_dag_layers(plan.sections, chapter_prompts)
        logger.info("[Phase 1] DAG %d 层并行写入 %d 章节",
                     len(dag_layers), len(plan.sections))

        sections: list[GeneratedSection] = []
        all_prev_summaries: list[str] = []

        for layer_idx, layer in enumerate(dag_layers):
            layer_titles = ", ".join(
                f"'{plan.sections[i].title}'" for i in layer
            )
            logger.info("  Layer %d: [%s]", layer_idx, layer_titles)

            layer_sections = self._write_chapters_parallel(
                layer, plan.sections, state, plan, retries,
                all_prev_summaries,
            )
            sections.extend(layer_sections)

            # 收集本层摘要
            for sec in layer_sections:
                if sec and sec.content:
                    summary_short = sec.content[:200].replace("\n", " ")
                    all_prev_summaries.append(
                        f"「{sec.spec.title}」: {summary_short}"
                    )

        # Phase 2: 全文质量闭环（task_executor 模式下跳过）
        if self._task_executor is None:
            state = self._run_full_report_loop(state, plan)
        else:
            logger.info("  [skip] 全文质量闭环（委托模式）")
        sections = self._rebuild_sections_from_state(state, plan)

        report = self._assemble_report(plan, state, sections, start_time)
        return report

    def _write_chapter(
        self, spec: SectionSpec, state: WorkflowState,
        plan: ReportPlan, max_retries: int, chapter_index: int,
        prev_chapter_summaries: list[str] | None = None,
        sibling_chapters: list[dict[str, str]] | None = None,
    ) -> GeneratedSection:
        """串行生成单个章节（委托式）。

        优先路径：通过 _task_executor 委托给 Hermes Agent。
        降级路径：在 delegate_task 不可用时使用传统 call_llm。

        Args:
            spec: 章节规格
            state: 工作流状态
            plan: 报告计划
            max_retries: 最大重试次数
            chapter_index: 章节序号
            prev_chapter_summaries: 前层章节摘要
            sibling_chapters: 同层并行章节信息（用于避免内容重叠）
        """
        chapter_prompts = plan.metadata.get("chapter_prompts") if hasattr(plan, "metadata") else None
        logger.info("  ╰→ 写作 [%d/%d] '%s'", chapter_index + 1, len(plan.sections), spec.title)

        # ── 优先路径：委托 Hermes Agent ──
        if self._task_executor is not None:
            context_json = self._build_chapter_context_json(
                spec, plan, chapter_index,
                prev_chapter_summaries=prev_chapter_summaries,
                sibling_chapters=sibling_chapters,
            )
            goal = f"写报告第{chapter_index + 1}章: {spec.title}"
            try:
                result = self._task_executor(goal=goal, context=context_json)
                content, summary = self._parse_result(result)
                content = self._ensure_header(content, spec)
                state.set_chapter_result(spec.title, content, summary)
                return GeneratedSection(
                    spec=spec, content=content, word_count=len(content),
                    quality_score=0.5, generation_attempts=1,
                )
            except Exception as e:
                logger.warning("delegate_task 失败, 降级到 call_llm: %s", e)

        # ── 降级路径：传统 call_llm ──
        if chapter_prompts and chapter_index < len(chapter_prompts):
            prompt = self._build_intent_driven_prompt(
                spec, plan, chapter_prompts[chapter_index], state,
                prev_summaries=prev_chapter_summaries,
            )
        else:
            prompt = state.get_chapter_prompt(spec.title)
            prompt = self._enhance_chapter_prompt(prompt, spec, state)
        return self._retry_generation(spec, plan, prompt, max_retries, state)

    @staticmethod
    def _build_intent_driven_prompt(
        spec: SectionSpec,
        plan: ReportPlan,
        cp: dict[str, Any],
        state: WorkflowState,
        prev_summaries: list[str] | None = None,
    ) -> str:
        """基于 StateGraph 的章节意图构建写作 prompt。

        Args:
            spec: 章节规格
            plan: 报告计划（含总目标）
            cp: 单章节的 ChapterPrompt 字典
                关键字段：writing_intent / key_points / materials_text
            state: 工作流状态

        materials_text 的来源（按优先级）：
          1. supplement_search 注入的网络素材（主渠道）
          2. curate 节点从源文档提取的段落（备选）
        """
        parts: list[str] = []

        # ⚠️ 标记数据覆盖度不足的章节（#8: supplement_needed 标记传递）
        if cp and cp.get("supplement_needed"):
            parts.append("⚠️ 本章数据覆盖度不足，以下内容部分基于有限素材撰写。")
            parts.append("")

        # 写作角色与整体规范（统一身份定义）
        goal = plan.metadata.get("report_goal", {}) if hasattr(plan, "metadata") else {}
        writing_role = goal.get("writing_role", {}) if isinstance(goal, dict) else {}
        if writing_role:
            parts.append("## 写作角色与整体规范")
            role = writing_role.get("role", "")
            if role:
                parts.append(f"你是一位{role}。")
            expertise = writing_role.get("expertise", [])
            if expertise:
                parts.append(f"擅长领域：{'、'.join(expertise[:5])}。")
            tone = writing_role.get("tone", "")
            if tone:
                parts.append(f"写作语调：{tone}。")
            voice = writing_role.get("voice", "")
            if voice:
                parts.append(f"叙述方式：{voice}。")
            conventions = writing_role.get("output_conventions", "")
            if conventions:
                parts.append(f"输出规范：{conventions}。")
            parts.append("")

        # 报告总目标
        parts.append(f"## 报告总目标")
        parts.append(goal.get("purpose", f"撰写关于「{plan.topic}」的报告"))
        parts.append("")

        # 本章写作意图
        intent = cp.get("writing_intent", "") or ""
        parts.append(f"## 本章写作意图")
        parts.append(intent)
        parts.append("")

        # 必须覆盖的要点
        key_points = cp.get("key_points") or []
        if key_points:
            parts.append(f"## 必须覆盖的要点")
            for i, kp in enumerate(key_points, 1):
                parts.append(f"{i}. {kp}")
            parts.append("")

        # 应避免的内容
        avoid_topics = cp.get("avoid_topics") or []
        if avoid_topics:
            parts.append(f"## 应避免的内容")
            for av in avoid_topics:
                parts.append(f"- {av}")
            parts.append("")

        # 素材（主渠道：supplement_search 注入的网络素材）
        materials = cp.get("materials_text", spec.content_template or "")
        if materials:
            parts.append(f"## 可用素材（选择性参考，不必全部使用）")
            parts.append(materials)
            parts.append("")

        # 已写章节摘要
        if prev_summaries:
            parts.append("## 已写章节摘要（后续章节应避免重复及保持协调）")
            for s in prev_summaries[-5:]:
                parts.append(f"- {s[:200]}")
            parts.append("")

        # 图表数据（单章注入 — 保证文字引用与图表数据一致）
        chart_spec = cp.get("chart_spec")
        if chart_spec and isinstance(chart_spec, dict):
            chart_data = chart_spec.get("data")
            chart_type = chart_spec.get("type", "")
            if chart_data and chart_type:
                parts.append("## 本章图表数据")
                parts.append(f"本章需要配套一张 {chart_type} 类型图表，数据如下：")
                parts.append(f"```json\n{_json.dumps(chart_data, ensure_ascii=False, indent=2)}\n```")
                parts.append("请在章节正文中引用这些具体数值，确保文字描述与图表数据一致。")
                parts.append("")

        # 写作要求
        parts.append(f"## 写作要求")
        parts.append("1. 紧扣本章写作意图，不跑题")
        parts.append("2. 覆盖所有要点")
        parts.append("3. 避免涉及「应避免的内容」")
        parts.append("4. 素材应严格基于源文档和参考材料，不编造数据")
        parts.append("5. 使用 markdown 格式，禁止使用 ```mermaid 代码块（图表由管线单独处理）")
        parts.append("6. 直接输出内容，不要额外解释")
        parts.append("7. **每章至少包含一个结构化元素**：数据表格、分类清单、或对比分析")

        # ⚡ v5.0.1: 总结/汇总/总览章节 — 数值完整性约束
        title = cp.get("title", "")
        if any(kw in title for kw in ("总结", "汇总", "总览", "投资估算汇总")):
            parts.append("")
            parts.append("## ⚠️ 数值完整性要求（总结/汇总章节适用）")
            parts.append("本章包含的投资估算汇总数据必须严格遵守以下规则：")
            parts.append("1. **数字必须与前面各章的明细数据严格一致**，禁止重新计算年均值或推导新数字")
            parts.append("2. **禁止发明新的分类维度**：汇总表的分类结构必须与前面各章的明细表保持一致")
            parts.append("3. **禁止将总价换算为年均价**：原始数据是什么口径，汇总表就用什么口径（如工控网层470-630万是四年总价，汇总表也必须是四年总价，不能写成'约120万/年'）")
            parts.append("4. **所有数字必须能在素材原文中找到出处**，不能随意编造")
            parts.append("5. 如果素材中缺少某个分类的细项数据，**宁缺勿编**——只汇总有源数据的条目")

        return "\n".join(parts)

    @staticmethod
    def _enhance_chapter_prompt(prompt: str, spec: SectionSpec, state: WorkflowState) -> str:
        ctx = state.chapter_contexts.get(spec.title)
        is_business = ctx and ctx.section_type == SECTION_TYPE_BUSINESS
        if is_business:
            prompt = HermesContentGenerator._add_business_context(prompt, spec.title)
        return prompt

    def _retry_generation(
        self, spec: SectionSpec, plan: ReportPlan,
        prompt: str, max_retries: int, state: WorkflowState,
    ) -> GeneratedSection:
        """带重试的 LLM 生成。"""
        system_prompt = None
        if hasattr(plan, "metadata") and plan.metadata:
            goal = plan.metadata.get("report_goal", {})
            wr = goal.get("writing_role", {}) if isinstance(goal, dict) else {}
            if wr:
                parts_sys = []
                role = wr.get("role", "")
                tone = wr.get("tone", "")
                voice = wr.get("voice", "")
                conventions = wr.get("output_conventions", "")
                if role:
                    parts_sys.append(f"你是一位{role}。")
                if tone:
                    parts_sys.append(f"写作语调：{tone}。")
                if voice:
                    parts_sys.append(f"叙述方式：{voice}。")
                if conventions:
                    parts_sys.append(f"输出规范：{conventions}。")
                if parts_sys:
                    system_prompt = "\n".join(parts_sys)

        for attempt in range(1, max_retries + 1):
            try:
                content = call_llm(prompt, max_iterations=1, system_prompt=system_prompt)
                if not content or len(content.strip()) < 20:
                    logger.debug("  ⚠ 内容过短, 重试 %d/%d", attempt, max_retries)
                    continue
                content = self._ensure_header(content, spec)
                quality = self._check_content_quality(content, spec, plan.topic)
                if quality >= 0.4 or attempt >= max_retries:
                    # ── 自检与修订：LLM 审查自己写的内容（一次迭代） ──
                    if quality >= 0.4:
                        revision_prompt = self._build_revision_prompt(content, spec, plan.topic)
                        try:
                            revised = call_llm(revision_prompt, max_iterations=1,
                                               system_prompt=system_prompt, temperature=0.3)
                            if revised and len(revised.strip()) > len(content) * 0.8:
                                revised = self._ensure_header(revised, spec)
                                revised_quality = self._check_content_quality(
                                    revised, spec, plan.topic)
                                if revised_quality >= quality:
                                    content = revised
                                    quality = revised_quality
                                    logger.info("    📝 修订后质量提升: %.2f → %.2f",
                                                quality, revised_quality)
                        except Exception as e:
                            logger.debug("    ⚠ 修订失败（保留原文）: %s", e)

                    summary = WorkflowState._auto_extract_summary(content)
                    state.set_chapter_result(spec.title, content, summary)
                    return GeneratedSection(
                        spec=spec, content=content, word_count=len(content),
                        quality_score=quality, generation_attempts=attempt,
                        used_fallback=attempt >= max_retries and quality < 0.4,
                    )
            except Exception as e:
                logger.warning("  ⚠ LLM失败 (attempt %d/%d): %s", attempt, max_retries, e)
        return self._fallback_section(spec, plan, max_retries, state)

    @staticmethod
    def _build_revision_prompt(
        content: str,
        spec: SectionSpec,
        topic: str,
    ) -> str:
        """构建自检修订 prompt，让 LLM 审查自己写的章节并改进。

        检查维度：
        1. key_points 是否全覆盖
        2. 结论是否有数据/事实支撑（不空泛）
        3. 逻辑链是否完整（不跳跃）
        4. 语言是否简洁专业
        """
        key_points = "\n".join(f"- {kp}" for kp in (spec.required_data or []))
        return (
            f"请审查以下章节内容，必要时改写以提升质量。\n\n"
            f"## 章节标题\n{spec.title}\n\n"
            f"## 必须覆盖的要点\n{key_points or '(无具体要求)'}\n\n"
            f"## 原始内容\n{content}\n\n"
            "## 自检要求\n"
            "请逐项检查以下维度，仅当发现问题时才改写：\n"
            "1. 要点覆盖：是否遗漏了必须覆盖的要点？\n"
            "2. 论据充分：结论是否有具体数据、政策文件或案例支撑？避免空洞陈述。\n"
            "3. 逻辑完整：论证链条是否有跳跃？每步推理是否有依据？\n"
            "4. 数据一致性：文中涉及的数字、指标是否前后一致？\n\n"
            "## 输出要求\n"
            "- 如果不需要修改，准确输出原始内容（一字不改）\n"
            "- 如果需要修改，输出完整修订后的章节内容（不要只输出修改部分）\n"
            "- 保持原文的标题层级和 markdown 格式\n"
        )

    @staticmethod
    def _ensure_header(content: str, spec: SectionSpec) -> str:
        """确保内容有正确的标题层级，清理 LLM 输出的多余 # 标题。"""
        import re as _re
        first_line = content.split("\n")[0].strip()
        expected_prefix = "#" * min(spec.level, 6) + " " + spec.title
        if first_line == expected_prefix:
            return content
        lines = content.split("\n")
        while lines and lines[0].strip().startswith("#"):
            lines.pop(0)
        cleaned = "\n".join(lines).strip()
        prefix = "#" * min(spec.level, 6)
        return f"{prefix} {spec.title}\n\n{cleaned}"

    @staticmethod
    def _fallback_section(
        spec: SectionSpec, plan: ReportPlan,
        max_retries: int, state: WorkflowState,
    ) -> GeneratedSection:
        goal = plan.metadata.get("report_goal") if hasattr(plan, "metadata") else None
        fallback = HermesContentGenerator._generate_fallback_content(spec, plan.topic, goal=goal)
        state.set_chapter_result(spec.title, fallback, "(降级)")
        return GeneratedSection(spec=spec, content=fallback, quality_score=0.3,
                                generation_attempts=max_retries, used_fallback=True)

    def _run_full_report_loop(self, state: WorkflowState, plan: ReportPlan) -> WorkflowState:
        """全文质量闭环（通过一致性检查优化章节衔接）。

        注意：当前不携带搜索器，搜索由外部编排器负责。
        """
        from ..adapters.full_report_loop import FullReportLoop
        return FullReportLoop().run(state, plan.topic)

    @staticmethod
    def _rebuild_sections_from_state(state: WorkflowState, plan: ReportPlan) -> list[GeneratedSection]:
        sections: list[GeneratedSection] = []
        for spec in plan.sections:
            ctx = state.chapter_contexts.get(spec.title)
            content = ctx.generated_content if ctx else ""
            if content:
                sections.append(GeneratedSection(
                    spec=spec, content=content, word_count=len(content),
                    quality_score=0.5, generation_attempts=1,
                ))
        return sections

    @staticmethod
    def _build_main_context(plan: ReportPlan) -> str:
        type_names = {"tech": "技术报告", "market": "市场分析报告",
                      "product": "产品方案", "research": "研究报告"}
        type_name = type_names.get(plan.report_type, "报告")
        return (
            f"撰写一份关于「{plan.topic}」的{type_name}，语言: {plan.language}。"
            f"目标读者: 具备相关背景的专业人士。全文需保持一致的叙事线和专业口吻。"
        )

    @staticmethod
    def _add_business_context(prompt: str, title: str) -> str:
        note = (
            "\n\n## 业务写作要求\n本章属于业务分析章节。请从业务价值和商业影响的角度撰写。"
            "遵循 copywriting 原则：清晰胜于巧妙，具体胜于模糊，主动语态。"
        )
        return prompt + note

    @staticmethod
    def _check_content_quality(content: str, spec: SectionSpec, topic: str) -> float:
        if not content or len(content) < 30:
            return 0.0
        import re as _re
        score = 0.0
        target = max(spec.estimated_words, 100)
        score += min(len(content) / target, 1.0) * 0.3
        has_headings = bool(_re.search(r'^#{1,6}\s', content, _re.MULTILINE))
        has_lists = bool(_re.search(r'^[-*\d+\.]\s', content, _re.MULTILINE))
        has_breaks = content.count("\n\n") > 1
        struct = (0.15 if has_headings else 0) + (0.1 if has_lists else 0) + (0.05 if has_breaks else 0)
        score += struct
        indicators = ["是当前领域内的重要课题", "随着技术的发展和业务的推进",
                      "以下从不同维度进行详细分析", "合理的设计和充分的测试"]
        bad = sum(1 for i in indicators if i in content)
        score += max(0, 1.0 - bad * 0.2) * 0.2
        if _re.search(r'\d+[%％倍]|\d+\.\d+', content):
            score += 0.2
        return min(score, 1.0)

    @staticmethod
    def _generate_fallback_content(spec: SectionSpec, topic: str, goal: dict | None = None) -> str:
        level = "#" * spec.level
        goal_context = ""
        if goal:
            purpose = goal.get("purpose", "")[:100]
            role = goal.get("writing_role", {}).get("role", "")
            if purpose:
                goal_context = f"，围绕「{purpose}」"
            if role:
                goal_context += f"，以{role}的视角"
        return f"{level} {spec.title}\n\n本节讨论 {topic} 中与「{spec.title}」相关内容{goal_context}。\n\n（此章节由系统自动生成，建议补充具体内容。）"

    @staticmethod
    def _assemble_report(
        plan: ReportPlan, state: WorkflowState,
        sections: list[GeneratedSection], start_time: float,
    ) -> GeneratedReport:
        content_parts: list[str] = []
        content_parts.append(f"# {plan.title}\n")
        content_parts.append(f"> **报告类型**: {plan.report_type}  **语言**: {plan.language}  **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        content_parts.append("---\n")
        for sec in sections:
            content_parts.append(sec.content)
        full_content = "\n".join(content_parts)
        total_words = sum(s.word_count for s in sections)
        avg_q = sum(s.quality_score for s in sections) / len(sections) if sections else 0.0
        report = GeneratedReport(
            plan=plan, sections=sections, full_content=full_content,
            total_words=total_words, generation_time_ms=(time.time() - start_time) * 1000,
            metadata={"generated_at": datetime.now().isoformat(),
                      "sections_generated": len(sections),
                      "phases": "serial_writing+full_report_loop",
                      "avg_quality": avg_q},
        )
        elapsed = (time.time() - start_time) * 1000
        logger.info("报告生成完成: '%s' (%d章, %d字, %.0fms, q=%.2f)",
                    plan.title, len(sections), total_words, elapsed, avg_q)
        return report

    def execute(self, operation: str = "generate_from_plan", **kwargs: Any) -> Any:
        ops = {"generate_from_plan": self.generate_from_plan}
        if operation not in ops:
            raise ReportAgentError(f"未知操作: {operation}")
        return ops[operation](**kwargs)
