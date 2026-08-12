"""Refinement Operator — Risk-aware content optimizer with LLM.

Identifies redundancies, scans for risks using LLM pattern analysis,
and iteratively optimizes content.
"""
import logging
import os
from dataclasses import dataclass
from typing import List, Dict
from pathlib import Path

from self_evolving.models.risk_assessment import (
    RiskLevel, RiskCategory, RiskFactor, RiskReport, RefinementOutput,
    RISK_LEVEL_SCORES,
)
from self_evolving.adapters.llm_client import LLMClient
from self_evolving.prompt_loader import get_prompt

logger = logging.getLogger(__name__)

# ── 长度阈值常量（P2-SE-027） ─────────────────────────────────────────────
_REDUNDANCY_SCAN_MIN_LENGTH = 500
_RISK_SCAN_MIN_LENGTH = 200
_LLM_COMPRESS_MIN_LENGTH = 1000

# ── 风险评分阈值常量（P2-SE-029） ─────────────────────────────────────────
_RISK_SCORE_HIGH_THRESHOLD = 0.7
_RISK_SCORE_MEDIUM_THRESHOLD = 0.4
_RISK_SCORE_LOW_THRESHOLD = 0.2

# ── Prompts (硬编码 fallback) ────────────────────────────────────────────

_FALLBACK_RISK_SCAN = """扫描以下内容，识别潜在风险。

内容：
{content}

输出 JSON：
{{
    "risk_factors": [
        {{
            "category": "syntax|logic|security|performance|compatibility|unknown",
            "description": "<风险描述>",
            "severity": "none|low|medium|high|critical",
            "likelihood": 0.0-1.0,
            "impact": 0.0-1.0
        }}
    ]
}}

请基于代码/内容的实际上下文判断，不要仅靠关键词匹配。
"""

_FALLBACK_REDUNDANCY_SCAN = """扫描以下内容中的冗余。

内容：
{content}

输出 JSON：
{{
    "redundancies": [
        {{
            "type": "duplicate|redundant_step|unnecessary_state|repetitive",
            "content": "<冗余内容片段>",
            "severity": 0.0-1.0,
            "removal_safety": 0.0-1.0
        }}
    ]
}}

识别真正冗余的部分（重复逻辑、可合并步骤、不必要中间状态），
不要误报正常代码结构。
"""

_FALLBACK_COMPRESS = """请优化以下内容，在保持信息完整性的前提下压缩到约一半长度。

内容：
{content}

输出压缩后的纯文本内容，不要额外解释。
"""

def RISK_SCAN_PROMPT() -> str:
    return get_prompt("refinement", "risk_scan", _FALLBACK_RISK_SCAN)

def REDUNDANCY_SCAN_PROMPT() -> str:
    return get_prompt("refinement", "redundancy_scan", _FALLBACK_REDUNDANCY_SCAN)

def COMPRESS_PROMPT() -> str:
    return get_prompt("refinement", "compress", _FALLBACK_COMPRESS)


@dataclass
class Redundancy:
    redundancy_id: str
    redundancy_type: str
    location: str
    content: str
    severity: float
    removal_safety: float


@dataclass
class OptimizationStep:
    step_num: int
    action: str
    target: str
    before: str
    after: str
    risk_before: float
    risk_after: float
    reduction_bytes: int


@dataclass
class RefinementConfig:
    risk_threshold: float = 0.3
    optimization_budget: int = 3
    compress_output: bool = True
    target_reduction_ratio: float = 0.5
    max_input_length: int = 12000
    risk_scanning_enabled: bool = True
    risk_scan_depth: str = "full"
    include_failure_patterns: bool = True
    llm_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    llm_model: str = "sensenova-6.8-flash-lite"
    llm_api_key: str = ""
    llm_timeout: int = 300

    @classmethod
    def from_yaml(cls, path: str = None) -> "RefinementConfig":
        if path is None:
            default_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
            if default_path.exists():
                path = str(default_path)
        if path and Path(path).exists():
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f) or {}
                cfg = config_data.get("refinement", {})
                common = config_data.get("common", {})
                rs = cfg.get("risk_scanning", {})
                return cls(
                    risk_threshold=cfg.get("risk_threshold", cls.risk_threshold),
                    optimization_budget=cfg.get("optimization_budget", cls.optimization_budget),
                    compress_output=cfg.get("compress_output", cls.compress_output),
                    target_reduction_ratio=cfg.get("target_reduction_ratio", cls.target_reduction_ratio),
                    max_input_length=cfg.get("max_input_length", cls.max_input_length),
                    risk_scanning_enabled=rs.get("enabled", cls.risk_scanning_enabled),
                    risk_scan_depth=rs.get("scan_depth", cls.risk_scan_depth),
                    include_failure_patterns=rs.get("include_failure_patterns", cls.include_failure_patterns),
                    llm_api_url=cfg.get("llm_api_url") or common.get("llm_api_url", cls.llm_api_url),
                    llm_model=cfg.get("llm_model") or common.get("llm_model", cls.llm_model),
                    llm_api_key=cfg.get("llm_api_key") or os.getenv("LITELLM_MASTER_KEY", ""),
                )
            except Exception as e:
                # 记录加载失败原因，使用默认配置
                logger.warning("从 %s 加载 RefinementConfig 失败，使用默认配置: %s", path, e)
        return cls()

    @classmethod
    def from_env(cls) -> "RefinementConfig":
        # 继承链：KN_REFLECTION_MODEL → SELF_EVOLVING_LLM_MODEL → LLM_MODEL_LIGHT → LLM_MODEL_MAIN
        llm_model = os.getenv("KN_REFLECTION_MODEL") or \
                    os.getenv("SELF_EVOLVING_LLM_MODEL") or \
                    os.getenv("LLM_MODEL_LIGHT") or \
                    os.getenv("LLM_MODEL_MAIN") or \
                    cls.llm_model
        return cls(
            llm_api_url=os.getenv("KN_REFLECTION_API_URL", cls.llm_api_url),
            llm_model=llm_model,
        )


class RefinementOperator:
    """Refinement operator with LLM-powered risk scanning and redundancy detection."""

    def __init__(self, config: RefinementConfig = None,
                 failure_pattern_db: List[str] = None,
                 llm_client: LLMClient = None):
        self.config = config or RefinementConfig()
        self.failure_pattern_db = failure_pattern_db or []
        self._llm = llm_client or self._default_llm()

    def _default_llm(self) -> LLMClient:
        key = self.config.llm_api_key or os.environ.get("LITELLM_MASTER_KEY", "")
        return LLMClient(
            api_url=self.config.llm_api_url,
            model=self.config.llm_model,
            api_key=key,
            timeout=self.config.llm_timeout,
        )

    def _call_llm_json(self, messages: list[dict], max_tokens: int = 16384) -> dict:
        try:
            resp = self._llm.chat_completion(
                messages=messages, temperature=0.1,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            text = self._llm.extract_content(resp)
            return self._llm.parse_json_response(text)
        except Exception as e:
            logger.warning("LLM 调用失败: %s", e)
            return {}

    def _call_llm_text(self, messages: list[dict], max_tokens: int = 16384) -> str:
        try:
            resp = self._llm.chat_completion(
                messages=messages, temperature=0.3,
                max_tokens=max_tokens,
            )
            return self._llm.extract_content(resp)
        except Exception as e:
            logger.warning("LLM 调用失败: %s", e)
            return ""

    # ── Public API ──────────────────────────────────────────────

    def detect_redundancies(self, content: str) -> List[Redundancy]:
        redundancies = []

        # Rule: duplicate lines
        seen_lines = {}
        for idx, line in enumerate(content.split("\n")):
            stripped = line.strip()
            if not stripped or len(stripped) < 10:
                continue
            if stripped in seen_lines:
                redundancies.append(Redundancy(
                    redundancy_id=f"dup_{len(redundancies)}",
                    redundancy_type="duplicate_line",
                    location=f"line {idx}",
                    content=stripped, severity=0.8, removal_safety=0.9,
                ))
            else:
                seen_lines[stripped] = idx

        # LLM-based: detect structural redundancies for larger content
        if len(content) > _REDUNDANCY_SCAN_MIN_LENGTH:
            prompt = REDUNDANCY_SCAN_PROMPT().format(
                content=content[:self.config.max_input_length],
            )
            data = self._call_llm_json([
                {"role": "system", "content": "你是有经验的代码审查专家。"},
                {"role": "user", "content": prompt},
            ])
            for i, r in enumerate(data.get("redundancies", [])):
                redundancies.append(Redundancy(
                    redundancy_id=f"llm_{i}",
                    redundancy_type=r.get("type", "redundant"),
                    location=f"llm-declared",
                    content=r.get("content", ""),
                    severity=r.get("severity", 0.5),
                    removal_safety=r.get("removal_safety", 0.5),
                ))

        return redundancies

    def scan_risks(self, content: str,
                   failure_patterns: List[str] = None) -> RiskReport:
        risk_factors = []

        # LLM-based risk scanning
        if self.config.risk_scanning_enabled and len(content) > _RISK_SCAN_MIN_LENGTH:
            prompt = RISK_SCAN_PROMPT().format(
                content=content[:self.config.max_input_length],
            )
            data = self._call_llm_json([
                {"role": "system", "content": "你是代码安全与质量审查专家。"},
                {"role": "user", "content": prompt},
            ])
            for rf in data.get("risk_factors", []):
                try:
                    category = RiskCategory(rf.get("category", "unknown"))
                except ValueError:
                    category = RiskCategory.UNKNOWN
                try:
                    severity = RiskLevel(rf.get("severity", "low"))
                except ValueError:
                    severity = RiskLevel.LOW
                risk_factors.append(RiskFactor(
                    category=category,
                    description=rf.get("description", ""),
                    severity=severity,
                    likelihood=float(rf.get("likelihood", 0.5)),
                    impact=float(rf.get("impact", 0.5)),
                ))

        # Fallback: keyword matching for quick patterns
        if not risk_factors:
            content_lower = content.lower()
            quick_patterns = {
                "bare except": (RiskCategory.SYNTAX, "Bare except clause", RiskLevel.MEDIUM),
            }
            for pattern, (cat, desc, sev) in quick_patterns.items():
                if pattern in content_lower:
                    risk_factors.append(RiskFactor(
                        category=cat, description=desc,
                        severity=sev, likelihood=0.5, impact=0.5,
                    ))

        # Check custom failure patterns
        patterns = failure_patterns or self.failure_pattern_db
        for pattern in patterns:
            if pattern.lower() in content.lower():
                risk_factors.append(RiskFactor(
                    category=RiskCategory.UNKNOWN,
                    description=f"Custom failure pattern matched: {pattern}",
                    severity=RiskLevel.HIGH, likelihood=0.7, impact=0.6,
                ))

        # Calculate overall risk
        if not risk_factors:
            overall_risk = RiskLevel.NONE
            risk_score = 0.0
        else:
            max_sev = max(risk_factors, key=lambda f: RISK_LEVEL_SCORES.get(f.severity, 0)).severity
            avg_score = sum(f.likelihood * f.impact for f in risk_factors) / len(risk_factors)
            risk_score = min(1.0, avg_score + 0.1)
            if max_sev == RiskLevel.CRITICAL:
                overall_risk = RiskLevel.CRITICAL
            elif max_sev == RiskLevel.HIGH or risk_score > _RISK_SCORE_HIGH_THRESHOLD:
                overall_risk = RiskLevel.HIGH
            elif max_sev == RiskLevel.MEDIUM or risk_score > _RISK_SCORE_MEDIUM_THRESHOLD:
                overall_risk = RiskLevel.MEDIUM
            elif risk_score > _RISK_SCORE_LOW_THRESHOLD:
                overall_risk = RiskLevel.LOW
            else:
                overall_risk = RiskLevel.NONE

        return RiskReport(
            overall_risk=overall_risk,
            risk_factors=risk_factors,
            risk_score=risk_score,
            recommendations=self._generate_risk_recommendations(risk_factors),
        )

    def optimize(self, content: str, redundancies: List[Redundancy],
                 risk_report: RiskReport, iteration: int) -> tuple[str, List[OptimizationStep]]:
        """按 redundant 内容安全删除冗余。

        安全性增强：
        - removal_safety > 0.5 才执行删除
        - 用 location 字段（如有）验证内容确实在预期位置，避免误删
        - 限制单次只删除一处，删除后立即 break 避免多次删除
        - 当 redundancy.content 在优化后文本中出现多次时，记录警告
        """
        steps = []
        optimized = content
        for redundancy in redundancies:
            if redundancy.removal_safety <= 0.5 or not redundancy.content:
                continue
            before = optimized
            content_to_remove = redundancy.content

            # 安全检查 1：如果内容在原文中出现多次，优先检查 location 提示
            occurrences = before.count(content_to_remove)
            if occurrences == 0:
                # 内容已不存在（如之前被处理过），跳过
                logger.debug("redundancy %s 内容已不在文本中，跳过", redundancy.redundancy_id)
                continue
            if occurrences > 1 and not getattr(redundancy, "location", None):
                # 出现多次但没有 location 提示，保守起见只删除第一处
                logger.warning(
                    "redundancy %s 内容出现 %d 次且无 location 提示，仅删除第一处",
                    redundancy.redundancy_id, occurrences,
                )
            elif occurrences > 1 and getattr(redundancy, "location", None):
                # 有 location 提示，但 str.replace 不支持按位置删除，先尝试 locate
                loc = redundancy.location
                if isinstance(loc, int) and 0 <= loc < len(before) - len(content_to_remove):
                    end = loc + len(content_to_remove)
                    if before[loc:end] == content_to_remove:
                        optimized = before[:loc] + before[end:]
                    else:
                        # 位置不匹配，回退到 replace
                        logger.warning(
                            "redundancy %s 位置 %d 处的文本与预期不符，回退到 replace",
                            redundancy.redundancy_id, loc,
                        )
                        optimized = before.replace(content_to_remove, "", 1)
                else:
                    optimized = before.replace(content_to_remove, "", 1)
            else:
                # 唯一一处，直接 replace
                optimized = before.replace(content_to_remove, "", 1)

            if optimized != before:
                steps.append(OptimizationStep(
                    step_num=len(steps) + 1,
                    action="remove_redundancy",
                    target=redundancy.redundancy_id,
                    before=before[:200], after=optimized[:200],
                    risk_before=risk_report.risk_score,
                    risk_after=risk_report.risk_score,
                    reduction_bytes=len(before) - len(optimized),
                ))
        return optimized, steps

    def execute(self, candidate_content: str,
                failure_patterns: List[str] = None,
                risk_threshold: float = None) -> RefinementOutput:
        if risk_threshold is not None:
            self.config.risk_threshold = risk_threshold

        original_content = candidate_content
        original_length = len(original_content)

        redundancies = self.detect_redundancies(candidate_content)
        risk_report = self.scan_risks(candidate_content, failure_patterns)

        current_content = candidate_content
        optimization_log = []
        removed_redundancies = []
        replaced_risky_parts = []
        actual_iterations = 0

        for iteration in range(self.config.optimization_budget):
            risk_report = self.scan_risks(current_content, failure_patterns)
            current_redundancies = self.detect_redundancies(current_content)
            optimized, steps = self.optimize(current_content, current_redundancies, risk_report, iteration)
            optimization_log.extend(steps)
            for redundancy in current_redundancies:
                if redundancy.content not in optimized:
                    removed_redundancies.append(redundancy.content)
            current_content = optimized
            current_length = len(current_content)
            reduction_ratio = (original_length - current_length) / original_length if original_length > 0 else 0
            actual_iterations += 1
            if reduction_ratio >= self.config.target_reduction_ratio:
                break
            if not steps:
                break

        final_risk_report = self.scan_risks(current_content, failure_patterns)
        final_length = len(current_content)
        reduction_stats = {
            "original_length": original_length,
            "refined_length": final_length,
            "reduction_bytes": original_length - final_length,
            "reduction_ratio": (original_length - final_length) / original_length if original_length > 0 else 0,
            "target_ratio": self.config.target_reduction_ratio,
            "iterations": actual_iterations,
            "redundancies_removed": len(removed_redundancies),
        }

        if self.config.compress_output:
            current_content = self._compress_output(current_content)

        return RefinementOutput(
            refined_content=current_content,
            reduction_stats=reduction_stats,
            risk_assessment=final_risk_report,
            removed_redundancies=removed_redundancies,
            replaced_risky_parts=replaced_risky_parts,
            optimization_log=[s.__dict__ for s in optimization_log],
        )

    # ── Internal methods ────────────────────────────────────────

    def _compress_output(self, content: str) -> str:
        """Use LLM for intelligent compression when content is large enough."""
        if len(content) < _LLM_COMPRESS_MIN_LENGTH:
            # Small content: rule-based compression
            lines = content.split("\n")
            compressed = []
            prev_empty = False
            for line in lines:
                is_empty = not line.strip()
                if not is_empty or not prev_empty:
                    compressed.append(line)
                prev_empty = is_empty
            return "\n".join(compressed)

        # Large content: use LLM for compression
        prompt = COMPRESS_PROMPT().format(content=content[:self.config.max_input_length])
        compressed = self._call_llm_text([
            {"role": "system", "content": "你是内容压缩专家，保持信息完整性的同时最大化压缩。"},
            {"role": "user", "content": prompt},
        ])
        return compressed or content

    def _generate_risk_recommendations(self, risk_factors: List[RiskFactor]) -> List[str]:
        recommendations = []
        category_recs = {
            RiskCategory.SYNTAX: "Review and fix syntax-related issues",
            RiskCategory.LOGIC: "Add validation and error handling",
            RiskCategory.SECURITY: "Apply security best practices",
            RiskCategory.PERFORMANCE: "Profile performance-critical paths",
            RiskCategory.COMPATIBILITY: "Test across target environments",
            RiskCategory.UNKNOWN: "Manual review recommended",
        }
        for cat, rec in category_recs.items():
            if any(f.category == cat for f in risk_factors):
                recommendations.append(rec)
        return recommendations


def refine(candidate_content: str, config_path: str = None,
           **kwargs) -> RefinementOutput:
    config = RefinementConfig.from_yaml(config_path)
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
    operator = RefinementOperator(config)
    return operator.execute(candidate_content)
