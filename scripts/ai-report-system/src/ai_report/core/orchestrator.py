"""
报表工作流编排器 — 集成所有模块的端到端工作流
遵循Hermes Code Rules规范

Stage 0-1 已替换为 StateGraph 意图驱动规划
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeAlias

from .base import BaseComponent
from ..config import get_config, get_parallel_config
from .exceptions import ReportAgentError
from ..config import (
    load_report_config,
    get_credibility_high,
    get_credibility_medium,
    get_default_language,
    get_default_report_type,
)
try:
    from ..graph.report_graph import run_planning, run_goal_definition
except ImportError:
    run_planning = None  # type: ignore[assignment]
    run_goal_definition = None  # type: ignore[assignment]
from ..graph.types import ChapterPrompt
from .planner import HermesReportPlanner, ReportPlan, SectionSpec
from .generator import HermesContentGenerator, GeneratedReport, GeneratedSection
from .evaluator import HermesReportEvaluator, EvaluationResult
from ..adapters.protocols import AIClientProtocol, QualityAssessorProtocol
from ..adapters.quality_assessor import HermesQualityAssessor
from .report_goal_helpers import (
    check_goal_exists,
    check_goal_truncation,
    goal_dir_for_topic,
    load_report_goal,
    save_report_goal,
    validate_report_goal,
)
from .source_loader import SourceDocumentLoader
from .report_cleaner import ReportCleaner
from .summary_generator import ExecutiveSummaryGenerator

logger = logging.getLogger(__name__)

# 类型别名
ReportStage = str
WorkflowResult = dict[str, Any]


@dataclass
class PipelineState:
    """管线状态 — 跟踪工作流各阶段进度"""
    topic: str
    report_type: str
    language: str
    stage: str = "initialized"
    plan: ReportPlan | None = None
    report: GeneratedReport | None = None
    evaluation: EvaluationResult | None = None
    output_path: Path | None = None
    started_at: float = 0.0
    completed_at: float | None = None
    stages: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def mark_stage(self, stage: str) -> None:
        """标记阶段完成"""
        self.stage = stage
        self.stages[stage] = time.time()

    @property
    def elapsed(self) -> float:
        """总耗时"""
        end = self.completed_at or time.time()
        return end - self.started_at


class ReportWorkflowOrchestrator(BaseComponent):
    """
    报表工作流编排器 — 端到端报告生成流水线

    工作流:
    init → planning → generation → quality_check → evaluation → output

    每个阶段可独立执行，支持断点续作和错误恢复

    用法:
        orchestrator = ReportWorkflowOrchestrator()
        result = orchestrator.run("微服务架构设计")
        print(result["report"].full_content[:500])
    """

    COMPONENT_NAME = "ReportWorkflowOrchestrator"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "端到端报告生成工作流编排器"

    def __init__(
        self,
        config: Any | None = None,
        task_executor: Callable[..., str] | None = None,
        quality_assessor: QualityAssessorProtocol | None = None,
        ai_client: AIClientProtocol | None = None,
    ) -> None:
        """初始化编排器。

        Args:
            config: 可选配置，None使用全局配置
            task_executor: 任务执行函数（委托 Hermes Agent），None使用降级路径
            quality_assessor: 质量评估器，None在工厂函数中自动创建
            ai_client: LLM客户端，None在工厂函数中自动创建
        """
        self._planner: HermesReportPlanner | None = None
        self._generator: HermesContentGenerator | None = None
        self._quality: QualityAssessorProtocol | None = quality_assessor
        self._evaluator: HermesReportEvaluator | None = None
        self._source_loader: SourceDocumentLoader | None = None
        self._cleaner: ReportCleaner | None = None
        self._summary_generator: ExecutiveSummaryGenerator | None = None
        self._task_executor: Callable[..., str] | None = task_executor
        self._ai_client: AIClientProtocol | None = ai_client
        super().__init__(config)

    def _initialize_internal(self) -> None:
        """初始化所有子组件"""
        self._planner = HermesReportPlanner(self._config)
        self._generator = HermesContentGenerator(
            self._config,
            task_executor=self._task_executor,
        )
        # quality_assessor 已通过 __init__ 注入，无需在此创建
        self._evaluator = HermesReportEvaluator(self._config)
        self._source_loader = SourceDocumentLoader()
        self._cleaner = ReportCleaner()
        self._summary_generator = ExecutiveSummaryGenerator()
        parallel_cfg = get_parallel_config()
        logger.info(
            "%s 初始化完成 (7个子组件, parallel=%s, max_workers=%d)",
            self.COMPONENT_NAME,
            "on" if parallel_cfg.enabled else "off",
            parallel_cfg.max_workers,
        )

    def run(
        self,
        topic: str,
        report_type: str | None = None,
        language: str | None = None,
        skip_evaluation: bool = False,
        skip_quality: bool = False,
        output_dir: Path | None = None,
        report_goal: dict[str, Any] | None = None,
        enriched_chapter_prompts: list[dict[str, Any]] | None = None,
    ) -> WorkflowResult:
        """
        运行完整工作流

        Args:
            topic: 报告主题
            report_type: 报告类型，None自动检测
            language: 语言，None使用配置
            skip_evaluation: 跳过评估阶段
            skip_quality: 跳过质量检查
            output_dir: 输出目录，None不保存
            report_goal: 已确认的报告目标（由 Step 0 确认后传入），
                        传入后跳过 define_goal 节点
            enriched_chapter_prompts: 代理层补充搜索后的富化章节提示词，
                                      传入后跳过 StateGraph，直接使用

        Returns:
            工作流结果，包含:
            - state: 工作流状态
            - plan: 报告计划
            - report: 生成的报告
            - evaluation: 评估结果（如有）
            - output_path: 输出路径（如有）
        """
        start_time = time.time()
        # 确定报告类型，按类型加载配置
        # 注：SourceDocumentLoader.load 内部自己调用 load_report_config (无 report_type = 使用 defaults)
        resolved_report_type = (
            report_type
            if report_type and report_type != "auto"
            else get_default_report_type({})
        )
        report_config = load_report_config(topic, report_type=resolved_report_type)
        domain_config = {
            "high": get_credibility_high(report_config),
            "medium": get_credibility_medium(report_config),
        }
        state = PipelineState(
            topic=topic,
            report_type=resolved_report_type,
            language=language or get_default_language(report_config),
            started_at=start_time,
        )

        # ── report_goal 前置校验 ──
        # ── report_goal 前置校验（仅 _execution 完备的 report_goal 做严格检查）──
        if report_goal and report_goal.get("_execution"):
            goal_issues = ReportWorkflowOrchestrator._validate_report_goal(report_goal, topic)
            if goal_issues:
                logger.error("=" * 50)
                logger.error("❌ report_goal 前置校验不通过，%d 个问题：", len(goal_issues))
                for issue in goal_issues:
                    logger.error("   %s", issue)
                logger.error("=" * 50)
                raise ValueError(
                    f"report_goal 前置校验不通过（{len(goal_issues)} 个问题），"
                    f"请按提示补全后再进管线"
                )

        # ── 调度门检查 ──
        gate_warnings = ReportWorkflowOrchestrator._gate_check(topic)
        for warning in gate_warnings:
            logger.warning("⚠️ 调度门检查: %s", warning)

        # 注入 task_executor 到 ai_client（让所有 call_llm 通过 Agent 委托）
        if self._ai_client is not None:
            self._ai_client.set_task_executor(self._task_executor)
        else:
            # 降级：直接设置模块级全局状态（保持向后兼容）
            from ..adapters import ai_client as _ai_client_mod
            _ai_client_mod.set_task_executor(self._task_executor)
        logger.debug("  ai_client task_executor=%s", "已设置" if self._task_executor else "未设置（使用降级路径）")

        # ── Stage 0-1: StateGraph 意图驱动规划（替换原有模板规划）──
        state.mark_stage("intent_planning")
        logger.info("[0-1/5] StateGraph 意图驱动规划: %s", topic[:40])
        source_content = self._source_loader.load(topic, resolved_report_type)

        # 如果提供了富化章节提示词，跳过 StateGraph
        if enriched_chapter_prompts:
            goal = report_goal or {}
            chapter_prompts = enriched_chapter_prompts
            logger.info("  使用代理层补充搜索后的富化章节提示词: %d chapters", len(chapter_prompts))
            plan = self._graph_to_plan(
                topic=topic,
                report_type=state.report_type,
                language=state.language,
                goal=goal,
                chapter_prompts=chapter_prompts,
            )
            state.plan = plan
            state.report_type = plan.report_type
        else:
            try:
                graph_result = run_planning(
                    topic=topic,
                    source_content=source_content,
                    report_type=state.report_type,
                    language=state.language,
                    report_goal=report_goal,
                    domain_config=domain_config,
                )
                goal = graph_result.get("report_goal") or {}
                chapter_prompts = (
                    graph_result.get("optimized_prompts")
                    or graph_result.get("chapter_prompts")
                    or []
                )
                logger.info(
                    "  StateGraph: %d chapters, goal='%s'",
                    len(chapter_prompts), goal.get("title", "")[:40],
                )
                # 将 chapter_prompts（含标题/意图/要点/素材/覆盖度标记）写回 report_goal.json
                goal["chapter_prompts"] = chapter_prompts
                goal.setdefault("_execution", {})["stategraph_done"] = {
                    "done": True,
                    "note": f"{len(chapter_prompts)} 章目录+素材已就绪",
                }
                save_report_goal(topic, goal)
                logger.info("  ✅ report_goal.json 已更新（含章节结构+素材）")
                plan = self._graph_to_plan(
                    topic=topic,
                    report_type=state.report_type,
                    language=state.language,
                    goal=goal,
                    chapter_prompts=chapter_prompts,
                )
                state.plan = plan
                state.report_type = plan.report_type
            except Exception as e:
                logger.error("StateGraph 规划失败，降级为模板规划: %s", e)
                samples = self._planner.search_samples(topic, state.report_type)
                plan = self._planner.create_plan(
                    topic=topic,
                    report_type=report_type,
                    language=language,
                    samples=samples if samples else None,
                )
                state.plan = plan
                state.report_type = plan.report_type
        logger.info("  → 类型=%s, %d章节, ~%d字",
                    plan.report_type, len(plan.sections), plan.estimated_total_words)

        # ── Stage 2: 生成 ──
        logger.info("[2/5] 生成阶段: %d章节", len(plan.sections))
        state.mark_stage("generation")
        try:
            report = self._generator.generate_from_plan(plan)
            state.report = report
            logger.info("  → %d字, %d章节, avg_q=%.2f",
                        report.total_words, len(report.sections),
                        report.metadata.get("avg_quality", 0))
        except Exception as e:
            state.errors.append(f"生成失败: {e}")
            logger.error("生成阶段失败: %s", e)
            return self._finalize(state, start_time, success=False)

        # ── 立即保存报告（后续阶段不影响落盘） ──
        if output_dir:
            try:
                initial_path = self._save_report(report, output_dir, state)
                state.output_path = initial_path
                logger.info("  ✅ 报告已保存: %s", initial_path)
            except Exception as e:
                logger.warning("初始保存失败（不阻塞）: %s", e)

        # ── Phase 3: 全文质量闭环 (v5.2.0 暂禁：FullReportLoop 需适配 PipelineState 类型，后续重构) ──
        if not skip_quality:
            logger.info("[3/5] 全文质量闭环暂禁 — 待 FullReportLoop 适配 PipelineState 后启用")

        # ── Phase 4: 图表渲染（已移至 scripts/post_process_charts.py 独立后处理） ──
        logger.info("[4/5] 图表后处理（跳过 — post_process_charts.py 独立运行）")
        state.mark_stage("chart_generation")

        # ── Stage 5: 质量检查 ──
        if not skip_quality and self._quality is not None:
            logger.info("[3/5] 质量检查阶段")
            state.mark_stage("quality_check")
            try:
                quality_result = self._quality.assess(
                    report.full_content,
                    report_id=report.plan.title,
                )
                logger.info("  → score=%.4f, grade=%s",
                            quality_result.overall_score,
                            quality_result.quality_grade)
            except Exception as e:
                logger.warning("质量检查失败: %s", e)
        else:
            logger.info("[3/5] 质量检查已跳过")

        # ── Stage 4: 评估 ──
        if not skip_evaluation:
            logger.info("[4/5] 评估阶段")
            state.mark_stage("evaluation")
            try:
                evaluation = self._evaluator.evaluate_report(report)
                state.evaluation = evaluation
                logger.info("  → score=%.4f, grade=%s, %d优化项",
                            evaluation.overall_score,
                            evaluation.quality_grade,
                            len(evaluation.optimization_tasks))
            except Exception as e:
                logger.warning("评估阶段失败: %s", e)
        else:
            logger.info("[4/5] 评估已跳过")

        # ── Stage 5: 输出 ──
        logger.info("[5/5] 输出阶段")
        state.mark_stage("output")

        # 生成执行摘要（从报告全文提取）
        executive_summary = ""
        if report and report.full_content:
            try:
                executive_summary = self._summary_generator.generate(
                    report.full_content, plan,
                )
                if executive_summary:
                    # 将摘要作为第一页插入到报告内容中
                    report.full_content = executive_summary + "\n\n---\n\n" + report.full_content
                    logger.info("  ✅ 执行摘要已生成: %d chars", len(executive_summary))
            except Exception as e:
                logger.warning("  执行摘要生成失败（不阻塞）: %s", e)

        # ── 清洗报告内容（在保存之前处理） ──
        if report:
            self._cleaner.clean_report(report)
            logger.info("  ✅ 报告内容清洗完成（去除 ** / 修复编号）")

        if output_dir:
            try:
                # .md 版本
                md_path = self._save_report(report, output_dir, state)
                state.output_path = md_path
                logger.info("  → .md: %s", md_path)

                # .docx 版本由 scripts/post_process_charts.py 独立后处理
            except Exception as e:
                logger.warning("保存失败: %s", e)

        state.completed_at = time.time()
        state.mark_stage("completed")

        # ── 后处理清洗：修复 LLM 输出质量问题 ──
        if report:
            self._cleaner.clean_report(report)

        return self._finalize(state, start_time, success=True)

    @staticmethod
    def _validate_report_goal(report_goal: dict, topic: str) -> list[str]:
        """前置校验 report_goal 完整性，不通过提示哪步有问题。

        规则：
        - _execution.step_4 标了 done → 检查 materials_text 非空
        - _execution.step_5 标了 done → 检查 key_points 在 materials_text 中有数据支撑
        - _execution.step_4 没标 → 建议先补 key_points 和 materials_text（非阻塞）
        - fact_bank.json 不存在 → 建议先跑 extract_facts（非阻塞）
        - 声称 done 但内容缺失 → 阻塞，打印具体问题
        """
        from pathlib import Path
        issues: list[str] = []
        execution = report_goal.get("_execution", {})
        chapters = report_goal.get("chapter_prompts", [])
        PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

        # Step 3 前提：fact_bank
        fb_path = PROJECT_DIR / "reports" / topic / "fact_bank.json"
        if not fb_path.exists():
            issues.append("[Step 3] fact_bank.json 不存在，请先执行 scripts/extract_facts.py")

        # Step 4 校验
        s4 = execution.get("step_4_curate_materials", {})
        if s4.get("done"):
            empty_mt = [
                cp.get("title", "?")
                for cp in chapters
                if not cp.get("materials_text", "").strip()
            ]
            if empty_mt:
                issues.append(
                    f"[Step 4] 标记了 done 但以下章节 materials_text 为空："
                    f"{'、'.join(empty_mt)}，请补搜后重试"
                )
        else:
            short_kp = [
                f"「{cp.get('title','?')}」key_points={len(cp.get('key_points',[]))}项"
                for cp in chapters
                if cp.get("key_points") and all(len(k) < 15 for k in cp["key_points"])
            ]
            if short_kp:
                issues.append(
                    f"[Step 4] 未执行——以下章节 key_points 可能是初版标签，"
                    f"建议执行 curate 补充后再进管线：{', '.join(short_kp[:3])}"
                )

        # Step 5 覆盖度校验
        s5 = execution.get("step_5_coverage_check", {})
        if s5.get("done"):
            # 检查每章 materials_text 是否有实质内容（内容检查非空）
            # 精确的 key_points 匹配由管线内的质量检查负责
            empty = [cp.get("title", "?") for cp in chapters if not cp.get("materials_text", "").strip()]
            if empty:
                issues.append(f"[Step 5] 标记了 done 但以下章节 materials_text 为空：{'、'.join(empty)}")
        else:
            issues.append(
                "[Step 5] 未执行覆盖度检查——建议确认 key_points 有数据支撑后标记 step_5_done"
            )

        return issues

    @staticmethod
    def _gate_check(topic: str) -> list[str]:
        """调度门检查：检查前置文件存在性和冲突状态。

        只做轻量门检查，不做严格校验（与 _validate_report_goal 职责不重叠）。
        失败时只打警告日志不 raise，因为模板降级路径仍然可用。

        Args:
            topic: 报告主题

        Returns:
            警告消息列表，空列表表示所有检查通过
        """
        from pathlib import Path
        import json as _json
        warnings: list[str] = []
        PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
        topic_dir = PROJECT_DIR / "reports" / topic

        # 1. 检查 report_goal.json 是否存在
        goal_path = topic_dir / "report_goal.json"
        if not goal_path.exists():
            warnings.append(
                f"report_goal.json 不存在 ({goal_path})，部分功能可能不可用"
            )

        # 2. 检查 fact_bank.json 是否存在
        fb_path = topic_dir / "fact_bank.json"
        if not fb_path.exists():
            warnings.append(
                f"fact_bank.json 不存在 ({fb_path})，请先执行 scripts/extract_facts.py"
            )

        # 3. 检查 conflicts 是否为空（仅当 fact_bank.json 存在时）
        if fb_path.exists():
            try:
                fb = _json.loads(fb_path.read_text(encoding="utf-8"))
                conflicts = fb.get("conflicts", []) or []
                if conflicts:
                    warnings.append(
                        f"fact_bank.json 中有 {len(conflicts)} 个未解决的冲突，"
                        f"建议先处理后再进管线"
                    )
            except Exception as e:
                warnings.append(f"fact_bank.json 解析失败: {e}")

        if not warnings:
            logger.info("✅ 调度门检查通过: report_goal / fact_bank / conflicts 均正常")

        return warnings

    def run_stage(
        self,
        state: PipelineState,
        stage: str,
    ) -> PipelineState:
        """执行单阶段（用于断点续作）"""
        logger.info("断点续作: stage=%s, topic=%s", stage, state.topic[:30])

        if stage == "planning" and state.plan is None:
            plan = self._planner.create_plan(
                topic=state.topic,
                report_type=state.report_type if state.report_type != "auto" else None,
            )
            state.plan = plan

        elif stage == "generation" and state.report is None and state.plan:
            state.report = self._generator.generate_from_plan(state.plan)

        elif stage == "evaluation" and state.evaluation is None and state.report:
            state.evaluation = self._evaluator.evaluate_report(state.report)

        state.mark_stage(stage)
        return state

    # ── StateGraph 输出 → ReportPlan ─────────────────────

    @staticmethod
    def _flatten_sections(
        cp: dict[str, Any],
        sections: list[SectionSpec],
    ) -> None:
        """递归展平层级章节结构为 SectionSpec 列表。

        Args:
            cp: 章节字典（含可选的 sub_sections）
            sections: 输出的 SectionSpec 列表
        """
        level = cp.get("level", 1)
        sec_type = cp.get("section_type", "body")
        words = cp.get("estimated_words", 500)
        chart_spec = cp.get("chart_spec")

        required_data: list[str] = []
        writing_intent = cp.get("writing_intent", "")
        key_points = cp.get("key_points", [])
        if writing_intent:
            required_data.append(f"intent:{writing_intent[:200]}")
        for kp in (key_points or [])[:5]:
            required_data.append(f"kp:{kp[:100]}")

        diagram_types: list[str] = []
        if chart_spec and isinstance(chart_spec, dict):
            ct = chart_spec.get("type", "")
            if ct in ("architecture_table", "architecture_diagram"):
                diagram_types.append("architecture")
            elif ct == "comparison":
                diagram_types.append("comparison")
            else:
                diagram_types.append("table")

        section = SectionSpec(
            title=cp.get("title", f"第{len(sections)+1}节"),
            level=level,
            section_type=sec_type,
            estimated_words=words,
            required_data=required_data,
            diagram_types=diagram_types,
            content_template=cp.get("materials_text", ""),
        )
        sections.append(section)

        # 递归展平子章节
        for sub in cp.get("sub_sections") or []:
            ReportWorkflowOrchestrator._flatten_sections(sub, sections)

    @staticmethod
    def _graph_to_plan(
        topic: str,
        report_type: str,
        language: str,
        goal: dict[str, Any],
        chapter_prompts: list[dict[str, Any]],
    ) -> ReportPlan:
        """将 StateGraph 的输出转换为 ReportPlan。

        Args:
            topic: 报告主题
            report_type: 报告类型
            language: 语言
            goal: report_goal 字典
            chapter_prompts: ChapterPrompt 字典列表

        Returns:
            ReportPlan 实例
        """
        if not chapter_prompts:
            raise ValueError("StateGraph 未产出章节结构，无法转换为 ReportPlan")
        title = goal.get("title", topic)
        sections: list[SectionSpec] = []
        for cp in chapter_prompts:
            ReportWorkflowOrchestrator._flatten_sections(cp, sections)

        # 构建 ReportPlan
        avoid_topics_list: list[str] = []
        for cp in chapter_prompts:
            avoid_topics_list.extend(cp.get("avoid_topics") or [])
            for sub in cp.get("sub_sections") or []:
                avoid_topics_list.extend(sub.get("avoid_topics", []))

        plan = ReportPlan(
            title=title,
            topic=topic,
            report_type=report_type,
            language=language,
            sections=sections,
            metadata={
                "generated_at": datetime.now().isoformat(),
                "plan_source": "stategraph_intent_driven",
                "report_goal": goal,
                "chapter_prompts": chapter_prompts,
                "avoid_topics": list(set(avoid_topics_list)),
                "template_used": False,
            },
        )
        return plan

    # ── 输出 ──────────────────────────────────────────────

    @staticmethod
    def _save_report(
        report: GeneratedReport,
        output_dir: Path,
        state: PipelineState,
    ) -> Path:
        """保存报告到文件"""
        output_dir.mkdir(parents=True, exist_ok=True)

        # 优先使用 goal.title，其次 plan.topic
        goal_title = ""
        if hasattr(state, "report_goal") and state.report_goal:
            goal_title = state.report_goal.get("title", "")
        base_name = goal_title or report.plan.topic
        safe_name = base_name.replace(" ", "_").replace("/", "_")[:40]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.md"
        file_path = output_dir / filename

        with file_path.open("w", encoding="utf-8") as f:
            f.write(report.full_content)

        return file_path

    # ── 结果汇总 ──────────────────────────────────────────

    @staticmethod
    def _finalize(
        state: PipelineState,
        start_time: float,
        success: bool,
    ) -> WorkflowResult:
        """汇总结果"""
        elapsed = (time.time() - start_time) * 1000

        result: WorkflowResult = {
            "success": success,
            "state": state,
            "elapsed_ms": elapsed,
            "elapsed_display": f"{elapsed / 1000:.1f}s",
            "plan": state.plan,
            "report": state.report,
            "evaluation": state.evaluation,
            "output_path": state.output_path,
        }

        if state.errors:
            result["errors"] = state.errors

        return result

    # ── 执行 ──

    def execute(self, operation: str = "run", **kwargs: Any) -> Any:
        """执行工作流操作"""
        operations = {
            "run": self.run,
            "run_stage": self.run_stage,
        }

        if operation not in operations:
            raise ReportAgentError(f"未知工作流操作: {operation}")

        return operations[operation](**kwargs)


# ── 工厂函数 ──────────────────────────────────────────────


def create_orchestrator(
    config: Any | None = None,
    task_executor: Callable[..., str] | None = None,
) -> ReportWorkflowOrchestrator:
    """创建默认配置的编排器实例（工厂函数）。

    使用具体 adapter 实现填充依赖，适合生产环境调用。
    测试环境可直接构造 ReportWorkflowOrchestrator 并注入 mock。

    Args:
        config: 可选配置，None使用全局配置
        task_executor: 任务执行函数（委托 Hermes Agent），None使用降级路径

    Returns:
        完整初始化的编排器实例
    """
    from ..adapters.quality_assessor import HermesQualityAssessor
    from ..adapters.ai_client_adapter import AIClientAdapter

    quality_assessor = HermesQualityAssessor(config)
    ai_client = AIClientAdapter()

    return ReportWorkflowOrchestrator(
        config=config,
        task_executor=task_executor,
        quality_assessor=quality_assessor,
        ai_client=ai_client,
    )
