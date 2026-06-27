"""
Hermes Report Evaluator — AIAgent驱动的报告评估器
8维度智能评分系统
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from .base import BaseComponent
from .exceptions import QualityError
from .generator import GeneratedReport
from ..config import get_parallel_config

logger = logging.getLogger(__name__)

ScoreValue = float
OptimizationTask = dict[str, Any]


@dataclass
class DimensionEval:
    """单维度评估结果"""
    name: str
    display_name: str
    score: ScoreValue
    weight: float
    issues: list[str]
    strengths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "score": round(self.score, 4),
            "weight": self.weight,
            "issues": self.issues[:5],
            "strengths": self.strengths[:3],
        }


@dataclass
class EvaluationResult:
    """报告评估结果"""
    report_title: str
    overall_score: ScoreValue
    quality_grade: str
    dimensions: dict[str, DimensionEval]
    optimization_tasks: list[OptimizationTask]
    suggestions: list[str]
    confidence: ScoreValue
    evaluation_time_ms: float
    ai_assessment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_title": self.report_title,
            "overall_score": round(self.overall_score, 4),
            "quality_grade": self.quality_grade,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "optimization_tasks": self.optimization_tasks[:10],
            "suggestions": self.suggestions[:10],
            "confidence": round(self.confidence, 4),
            "ai_assessment": self.ai_assessment[:300] if self.ai_assessment else "",
        }


class HermesReportEvaluator(BaseComponent):
    """
    报告评估器 — AIAgent驱动的8维度智能评分系统

    维度 (权重):
    - grammar语法: 0.12
    - structure结构: 0.15
    - style风格: 0.10
    - technical技术: 0.18 (最重)
    - readability可读性: 0.15
    - completeness完整性: 0.12
    - actionability可操作性: 0.10
    - originality原创性: 0.08

    功能:
    - LLM驱动的8维度智能评分
    - 质量等级判定
    - 自动生成优化建议
    - 强弱项分析
    """

    COMPONENT_NAME = "HermesReportEvaluator"
    COMPONENT_VERSION = "2.0.0"
    COMPONENT_DESCRIPTION = "AIAgent驱动的8维度报告评估器"

    DIMENSIONS: dict[str, dict[str, Any]] = {
        "grammar": {"name": "语法和拼写", "weight": 0.12},
        "structure": {"name": "结构和组织", "weight": 0.15},
        "style": {"name": "风格和语调", "weight": 0.10},
        "technical": {"name": "技术准确性", "weight": 0.18},
        "readability": {"name": "可读性", "weight": 0.15},
        "completeness": {"name": "完整性和深度", "weight": 0.12},
        "actionability": {"name": "可操作性", "weight": 0.10},
        "originality": {"name": "洞察深度", "weight": 0.08},
    }

    GRADE_THRESHOLDS = [
        ("excellent", 0.85),
        ("good", 0.70),
        ("acceptable", 0.60),
        ("needs_improvement", 0.50),
    ]

    def __init__(self, config: Any | None = None, llm_caller=None) -> None:
        self._llm_caller = llm_caller  # set before super().__init__() triggers _initialize_internal
        parallel_cfg = get_parallel_config()
        self._parallel_enabled = parallel_cfg.enabled
        self._evaluate_max_workers = parallel_cfg.evaluate_max_workers
        super().__init__(config)

    def _initialize_internal(self) -> None:
        if self._llm_caller is None:
            from ..adapters.ai_client import call_llm as _fallback
            self._llm_caller = _fallback
        logger.info("%s 初始化完成, %d维度", self.COMPONENT_NAME, len(self.DIMENSIONS))

    # ── 公开接口 ──────────────────────────────────────────

    def evaluate_report(
        self,
        report: GeneratedReport,
        detailed: bool = True,
    ) -> EvaluationResult:
        """
        评估生成的报告（规则+LLM混合评估）

        规则评分快速稳定，LLM评分提供深度洞察
        当LLM调用失败时自动降级到纯规则评分
        """
        start_time = time.time()

        if not report.full_content or not report.full_content.strip():
            raise QualityError(message="报告内容为空", score=0.0)

        content = report.full_content

        # 1. 规则评分（快速，所有维度）
        dimensions: dict[str, DimensionEval] = {}
        if self._parallel_enabled:
            dimensions = self._evaluate_dimensions_parallel(content, report)
        else:
            for dim_name, dim_config in self.DIMENSIONS.items():
                dim_eval = self._evaluate_dimension(dim_name, content, report)
                dimensions[dim_name] = dim_eval

        # 2. LLM深度评估（尝试，失败不阻塞）
        ai_assessment = ""
        try:
            ai_feedback = self._ai_evaluate(content, report)
            if ai_feedback:
                ai_assessment = ai_feedback
                # 用AI评估修正维度分数
                self._apply_ai_feedback(dimensions, ai_feedback)
        except Exception as e:
            logger.debug("LLM评估失败，使用纯规则评分: %s", e)

        # 3. 计算加权总分
        overall_score = self._calculate_overall(dimensions)

        # 4. 质量等级
        quality_grade = "poor"
        for grade_name, threshold in self.GRADE_THRESHOLDS:
            if overall_score >= threshold:
                quality_grade = grade_name
                break

        # 5. 优化任务
        optimization_tasks = self._generate_tasks(dimensions, content)

        # 6. 汇总建议
        all_suggestions: list[str] = []
        for dim_eval in dimensions.values():
            all_suggestions.extend(dim_eval.issues)

        # 7. 置信度
        total_issues = sum(len(d.issues) for d in dimensions.values())
        total_strengths = sum(len(d.strengths) for d in dimensions.values())
        assessment_depth = min((total_issues + total_strengths) / 16.0, 1.0)
        confidence = 0.5 + assessment_depth * 0.5

        result = EvaluationResult(
            report_title=report.plan.title,
            overall_score=overall_score,
            quality_grade=quality_grade,
            dimensions=dimensions,
            optimization_tasks=optimization_tasks,
            suggestions=self._deduplicate(all_suggestions)[:15],
            confidence=confidence,
            evaluation_time_ms=(time.time() - start_time) * 1000,
            ai_assessment=ai_assessment,
        )

        elapsed = (time.time() - start_time) * 1000
        self._record_performance(start_time, success=True)
        logger.info(
            "评估完成: '%s' score=%.4f grade=%s %.0fms%s",
            report.plan.title[:30], overall_score, quality_grade, elapsed,
            " (AI)" if ai_assessment else "",
        )

        return result

    # ── LLM评估 ───────────────────────────────────────────

    def _ai_evaluate(self, content: str, report: GeneratedReport) -> str:
        """使用LLM对报告进行深度评估"""
        # 从 plan.metadata 读取报告目标
        goal_context = ""
        if hasattr(report, "plan") and hasattr(report.plan, "metadata"):
            goal = report.plan.metadata.get("report_goal") if report.plan.metadata else None
            if goal:
                wr = goal.get("writing_role", {})
                role_parts = []
                role = wr.get("role", "")
                tone = wr.get("tone", "")
                voice = wr.get("voice", "")
                audience = goal.get("target_audience", "")
                if role:
                    role_parts.append(f"写作角色：{role}")
                if tone:
                    role_parts.append(f"写作语调：{tone}")
                if voice:
                    role_parts.append(f"叙述方式：{voice}")
                if audience:
                    role_parts.append(f"目标读者：{audience}")
                if role_parts:
                    goal_context = "\n".join(role_parts) + "\n\n"

        prompt = (
            f"你是专业的报告质量评估专家。请评估以下报告，从8个维度给出评分(0-1)和改进建议。\n\n"
            f"报告主题：{report.plan.title}\n"
            f"报告类型：{report.plan.report_type}\n"
            f"字数：{len(content)} 字符\n\n"
            f"{goal_context}"
            f"报告内容：\n{content[:3000]}\n\n"
            f"请按以下JSON格式输出评估结果，不要加额外解释：\n"
            f"{{\n"
            f'  "scores": {{\n'
            f'    "grammar": 0-1,\n'
            f'    "structure": 0-1,\n'
            f'    "style": 0-1,\n'
            f'    "technical": 0-1,\n'
            f'    "readability": 0-1,\n'
            f'    "completeness": 0-1,\n'
            f'    "actionability": 0-1,\n'
            f'    "originality": 0-1\n'
            f"  }},\n"
            f'  "summary": "一段总体评价(100字内)",\n'
            f'  "top_issues": ["问题1", "问题2", "问题3"]\n'
            f"}}"
        )

        response = self._llm_caller(prompt, max_iterations=1, max_tokens=1500)
        return response.strip()

    @staticmethod
    def _apply_ai_feedback(
        dimensions: dict[str, DimensionEval],
        ai_feedback: str,
    ) -> None:
        """将AI评估结果应用到维度分数"""
        try:
            # 提取JSON
            json_match = re.search(r'\{[^{}]*"scores"[^{}]*\}', ai_feedback, re.DOTALL)
            if not json_match:
                return
            data = json.loads(json_match.group())
            ai_scores = data.get("scores", {})

            for dim_name, ai_score in ai_scores.items():
                if dim_name in dimensions and isinstance(ai_score, (int, float)):
                    dim = dimensions[dim_name]
                    # 混合评分：60%规则 + 40%AI
                    dim.score = dim.score * 0.6 + min(max(ai_score, 0), 1) * 0.4
                    dim.score = min(max(dim.score, 0), 1)

            # 添加AI发现的摘要
            summary = data.get("summary", "")
            if summary and "语法和拼写" not in str(dimensions.get("grammar", "")):
                dim = dimensions.get("grammar")
                if dim:
                    dim.issues.append(f"AI总体评价: {summary[:100]}")

            top_issues = data.get("top_issues", [])
            if top_issues:
                # 将top issues分配到最低分的维度
                sorted_dims = sorted(
                    dimensions.values(), key=lambda d: d.score,
                )
                for i, issue in enumerate(top_issues[:3]):
                    if i < len(sorted_dims):
                        dim = sorted_dims[i]
                        if issue not in dim.issues:
                            dim.issues.append(issue[:80])

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug("AI反馈解析失败: %s", e)

    # ── 维度评估（规则） ──────────────────────────────────

    def _evaluate_dimensions_parallel(
        self,
        content: str,
        report: GeneratedReport,
    ) -> dict[str, DimensionEval]:
        """并行评估多个质量维度。

        线程安全说明：
          - 每个维度的评估是独立的纯函数，不共享可变状态
          - _eval_* 方法均为 @staticmethod，无实例状态依赖

        Args:
            content: 报告全文
            report: 生成的报告对象

        Returns:
            维度名 → DimensionEval 映射
        """
        dimensions: dict[str, DimensionEval] = {}
        dim_names = list(self.DIMENSIONS.keys())
        max_workers = min(self._evaluate_max_workers, len(dim_names))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_dim = {
                executor.submit(
                    self._evaluate_dimension, dim_name, content, report,
                ): dim_name
                for dim_name in dim_names
            }

            for future in as_completed(future_to_dim):
                dim_name = future_to_dim[future]
                try:
                    dim_eval = future.result()
                    dimensions[dim_name] = dim_eval
                except Exception as e:
                    logger.warning(
                        "  ⚠ 并行评估维度 '%s' 失败, 降级串行: %s",
                        dim_name, e,
                    )
                    # 降级为串行评估
                    try:
                        dimensions[dim_name] = self._evaluate_dimension(
                            dim_name, content, report,
                        )
                    except Exception as retry_e:
                        logger.error(
                            "  ❌ 串行评估维度 '%s' 也失败: %s",
                            dim_name, retry_e,
                        )
                        # 使用默认评分
                        dimensions[dim_name] = DimensionEval(
                            name=dim_name,
                            display_name=self.DIMENSIONS.get(dim_name, {}).get("name", dim_name),
                            score=0.7,
                            weight=self.DIMENSIONS.get(dim_name, {}).get("weight", 0.1),
                            issues=["评估失败，使用默认评分"],
                            strengths=[],
                        )

        return dimensions

    def _evaluate_dimension(
        self,
        dim_name: str,
        content: str,
        report: GeneratedReport,
    ) -> DimensionEval:
        """评估单个维度"""
        dispatcher = {
            "grammar": self._eval_grammar,
            "structure": self._eval_structure,
            "style": self._eval_style,
            "technical": self._eval_technical,
            "readability": self._eval_readability,
            "completeness": self._eval_completeness,
            "actionability": self._eval_actionability,
            "originality": self._eval_originality,
        }
        handler = dispatcher.get(dim_name)
        if handler:
            return handler(content, report)
        return DimensionEval(
            name=dim_name,
            display_name=self.DIMENSIONS.get(dim_name, {}).get("name", dim_name),
            score=0.7,
            weight=self.DIMENSIONS.get(dim_name, {}).get("weight", 0.1),
            issues=[],
            strengths=[],
        )

    @staticmethod
    def _eval_grammar(content: str, _report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        sentences = [s for s in re.split(r'[。！？\n.]', content) if len(s.strip()) > 5]
        if len(sentences) >= 5:
            strengths.append(f"内容段落数量充足 ({len(sentences)}句)")
        else:
            issues.append("建议增加内容段落")

        long_sents = [s for s in sentences if len(s) > 150]
        if long_sents:
            issues.append(f"存在 {len(long_sents)} 个超长句（>150字），建议拆分")
        else:
            strengths.append("句子长度适中")

        paras = [p for p in content.split("\n\n") if p.strip()]
        empty_paras = sum(1 for p in paras if len(p.strip()) < 10)
        if empty_paras > 2:
            issues.append(f"存在 {empty_paras} 个空段落")

        passed = max(0, 5 - len(issues))
        score = min(passed / 5.0, 1.0)
        return DimensionEval("grammar", "语法和拼写", score, 0.12, issues, strengths)

    @staticmethod
    def _eval_structure(content: str, _report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        headings = re.findall(r'^(#{1,6})\s+(.+)$', content, re.MULTILINE)
        if headings:
            strengths.append(f"包含 {len(headings)} 级标题结构")
        else:
            issues.append("缺少标题层级结构")

        levels = [len(h[0]) for h in headings]
        if levels:
            if max(levels) - min(levels) > 2 and len(levels) > 3:
                issues.append("标题层级跳跃过大")

        if headings:
            strengths.append(f"按顺序组织了 {len(headings)} 个章节")

        passed = max(0, 4 - len(issues))
        score = min(passed / 4.0, 1.0)
        return DimensionEval("structure", "结构和组织", score, 0.15, issues, strengths)

    @staticmethod
    def _eval_style(content: str, _report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        passive_count = content.count("被") + content.count("受到")
        if passive_count > 10:
            issues.append(f"被动语态使用过于频繁 ({passive_count}次)")

        redundancy_patterns = ["非常", "十分", "极其", "非常地"]
        redundancy_count = sum(content.count(p) for p in redundancy_patterns)
        if redundancy_count > 5:
            issues.append("存在冗余修饰词")
        else:
            strengths.append("语言简洁，冗余修饰少")

        strengths.append("整体语调一致")
        passed = max(0, 3 - len(issues))
        score = min(passed / 3.0, 1.0)
        return DimensionEval("style", "风格和语调", score, 0.10, issues, strengths)

    @staticmethod
    def _eval_technical(content: str, report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        has_data = bool(re.search(r'\d+[%％倍]|\d+\.\d+', content))
        if has_data:
            strengths.append("引用了具体数据支持论点")
        else:
            issues.append("缺乏数据支持，建议补充具体数字")

        tech_terms = ["架构", "系统", "设计", "实现", "算法", "性能", "优化", "模型", "框架"]
        used_terms = [t for t in tech_terms if t in content]
        if used_terms:
            strengths.append(f"使用了技术术语（{len(used_terms)}个）")
        else:
            issues.append("技术术语较少")

        rtype = report.plan.report_type
        if rtype == "tech" and "测试" not in content:
            issues.append("技术报告建议包含测试验证部分")
        elif rtype == "market" and "市场" not in content:
            issues.append("市场报告建议包含市场数据")

        passed = max(0, 4 - len(issues))
        score = min(passed / 4.0, 1.0)
        return DimensionEval("technical", "技术准确性", score, 0.18, issues, strengths)

    @staticmethod
    def _eval_readability(content: str, _report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        lines = content.splitlines()
        long_paras = [l for l in lines if len(l) > 200 and not l.startswith("#")]
        if long_paras:
            issues.append(f"存在 {len(long_paras)} 个过长段落（>200字）")
        else:
            strengths.append("段落长度适中")

        has_lists = bool(re.search(r'^[\\-\\*\\d+\\.]\\s', content, re.MULTILINE))
        has_tables = "|" in content and "---" in content
        if has_lists or has_tables:
            strengths.append("使用了列表或表格等结构元素")
        else:
            issues.append("建议增加列表或表格辅助阅读")

        line_breaks = content.count("\n\n")
        if line_breaks >= 5:
            strengths.append("段落间有空行分隔")
        else:
            issues.append("段落间缺少必要空行")

        passed = max(0, 4 - len(issues))
        score = min(passed / 4.0, 1.0)
        return DimensionEval("readability", "可读性", score, 0.15, issues, strengths)

    @staticmethod
    def _eval_completeness(content: str, report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        target = report.plan.estimated_total_words
        actual = report.total_words
        ratio = actual / max(target, 1)
        if ratio >= 0.8:
            strengths.append(f"字数达标 ({actual}/{target})")
        else:
            issues.append(f"字数不足 ({actual}/{target})")

        if report.sections:
            strengths.append(f"章节完整 ({len(report.sections)}个)")
        else:
            issues.append("缺少章节")

        has_conclusion = any(
            "结论" in s.spec.title or "总结" in s.spec.title or s.spec.section_type == "conclusion"
            for s in report.sections
        )
        if has_conclusion:
            strengths.append("包含结论部分")
        else:
            issues.append("缺少结论部分")

        passed = max(0, 4 - len(issues))
        score = min(passed / 4.0, 1.0)
        return DimensionEval("completeness", "完整性和深度", score, 0.12, issues, strengths)

    @staticmethod
    def _eval_actionability(content: str, _report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        has_action_words = any(
            word in content
            for word in ["建议", "推荐", "步骤", "方案", "下一步", "should", "recommend"]
        )
        if has_action_words:
            strengths.append("包含可操作建议")
        else:
            issues.append("缺少具体建议或下一步行动")

        has_numbered = bool(re.search(r'^\d+[\\.\\、]', content, re.MULTILINE))
        if has_numbered:
            strengths.append("建议采用结构化编号")

        passed = max(0, 3 - len(issues))
        score = min(passed / 3.0, 1.0)
        return DimensionEval("actionability", "可操作性", score, 0.10, issues, strengths)

    @staticmethod
    def _eval_originality(content: str, _report: GeneratedReport) -> DimensionEval:
        issues: list[str] = []
        strengths: list[str] = []
        template_indicators = [
            "是当前领域内的重要课题",
            "随着技术的发展和业务的推进",
            "以下从不同维度进行详细分析",
            "合理的设计和充分的测试",
        ]
        template_hits = sum(1 for ind in template_indicators if ind in content)
        if template_hits > 0:
            issues.append(f"使用了 {template_hits} 处模板化表述")

        if len(content) > 1000:
            strengths.append("内容充实，有深入分析的潜力")
        else:
            issues.append("内容较短，建议深入挖掘")

        passed = max(0, 3 - len(issues))
        score = min(passed / 3.0, 1.0)
        return DimensionEval("originality", "洞察深度", score, 0.08, issues, strengths)

    # ── 总分计算 ──────────────────────────────────────────

    @staticmethod
    def _calculate_overall(dimensions: dict[str, DimensionEval]) -> float:
        total_weight = 0.0
        weighted = 0.0
        for dim in dimensions.values():
            weighted += dim.score * dim.weight
            total_weight += dim.weight
        return weighted / total_weight if total_weight > 0 else 0.0

    # ── 优化任务生成 ─────────────────────────────────────

    @staticmethod
    def _generate_tasks(
        dimensions: dict[str, DimensionEval],
        content: str,
    ) -> list[OptimizationTask]:
        tasks: list[OptimizationTask] = []
        for dim_name, dim_eval in dimensions.items():
            if dim_eval.score < 0.7 and dim_eval.issues:
                tasks.append({
                    "dimension": dim_name,
                    "priority": "high" if dim_eval.score < 0.5 else "medium",
                    "current_score": round(dim_eval.score, 2),
                    "issues": dim_eval.issues[:3],
                    "suggested_action": dim_eval.issues[0] if dim_eval.issues else "改进此维度",
                })
        tasks.sort(key=lambda t: t["current_score"])
        return tasks

    def execute(self, operation: str = "evaluate_report", **kwargs: Any) -> Any:
        """执行评估操作"""
        operations = {
            "evaluate_report": self.evaluate_report,
        }
        if operation not in operations:
            raise QualityError(message=f"未知评估操作: {operation}", score=0.0)
        return operations[operation](**kwargs)

    @staticmethod
    def _deduplicate(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            key = item.strip().lower()[:30]
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
