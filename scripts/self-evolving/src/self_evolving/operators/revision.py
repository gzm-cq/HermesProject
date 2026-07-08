"""Revision Operator — Failure-driven strategy generator with LLM integration.

Performs multi-level reflection on failed trajectories to diagnose root causes
and generate alternative solutions.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path

from self_evolving.models.failure_diagnosis import (
    FailureType, FailureSignal, DiagnosisResult,
    AlternativeSolution, RevisionOutput, FAILURE_TYPE_DESCRIPTIONS,
)
from self_evolving.models.trajectory import Trajectory
from self_evolving.adapters.llm_client import LLMClient
from self_evolving.prompt_loader import get_prompt

logger = logging.getLogger(__name__)

# ── Prompts (硬编码 fallback，若 prompts.yaml 加载失败使用) ─────────────────

_FALLBACK_AUTO_DETECT = """分析以下失败内容，判断最可能的错误类型。

失败内容：
{failed_content}

任务上下文：
{context}

请从以下类型中选择一个，输出 JSON：
{{
    "failure_type": "<类型>",
    "reason": "<选择原因>"
}}

类型定义：
- invalid_tool_call: 工具调用格式/名称错误
- argument_mismatch: 参数类型/格式不匹配
- state_mismatch: 状态与预期不一致
- recovery_failure: 错误恢复机制失败
- missing_tool_call: 缺少必要的工具调用
- response_mismatch: 输出不符合预期
- unknown: 无法确定
"""

_FALLBACK_REFLECT_DIRECT = """分析以下失败内容，识别直接原因。

失败内容：
{failed_content}

任务上下文：
{context}

失败类型：{failure_type}

输出 JSON：
{{
    "direct_cause": "<一句话描述直接原因>",
    "evidence": ["<证据1>", "<证据2>"]
}}
"""

_FALLBACK_REFLECT_ROOT = """基于直接原因，深入分析根本原因。

失败内容：
{failed_content}

任务上下文：
{context}

失败类型：{failure_type}
直接原因：{direct_cause}

输出 JSON：
{{
    "root_cause": "<一句话描述根本原因>",
    "confidence": 0.0-1.0
}}
"""

_FALLBACK_REFLECT_DEEP = """执行第三层深度分析，追溯问题起源。

失败内容：
{failed_content}

任务上下文：
{context}

失败类型：{failure_type}
直接原因：{direct_cause}
根本原因：{root_cause}

输出 JSON：
{{
    "deep_analysis": "<系统性原因分析>",
    "systemic_pattern": "<是否属于系统性问题>"
}}
"""

_FALLBACK_REVISE_CONTENT = """基于诊断结果，生成修正后的内容。

失败内容：
{failed_content}

诊断结果：
- 失败类型：{failure_type}
- 直接原因：{direct_cause}
- 根本原因：{root_cause}

请生成修正后的内容。只输出修正后的纯文本，不要额外解释。
"""

_FALLBACK_FIX_TYPE = """基于失败类型和根因，推荐修复方式。

失败类型：{failure_type}
根本原因：{root_cause}

输出 JSON：
{{"recommended_fix_type": "<修复类型>"}}
"""

# Public accessors — 每次读取时从 loader 拉取最新（支持热更新）
def AUTO_DETECT_PROMPT() -> str:
    return get_prompt("revision", "auto_detect", _FALLBACK_AUTO_DETECT)

def REFLECT_DIRECT_PROMPT() -> str:
    return get_prompt("revision", "reflect_direct", _FALLBACK_REFLECT_DIRECT)

def REFLECT_ROOT_PROMPT() -> str:
    return get_prompt("revision", "reflect_root", _FALLBACK_REFLECT_ROOT)

def REFLECT_DEEP_PROMPT() -> str:
    return get_prompt("revision", "reflect_deep", _FALLBACK_REFLECT_DEEP)

def REVISE_CONTENT_PROMPT() -> str:
    return get_prompt("revision", "revise_content", _FALLBACK_REVISE_CONTENT)

def FIX_TYPE_PROMPT() -> str:
    return get_prompt("revision", "fix_type", _FALLBACK_FIX_TYPE)


@dataclass
class RevisionConfig:
    """Configuration for Revision operator"""
    reflection_depth: int = 2
    generate_alternatives: bool = True
    alternative_count: int = 2
    confidence_threshold: float = 0.6
    max_input_length: int = 8000
    llm_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    llm_model: str = "s-deepseek-v4-flash"
    llm_api_key: str = ""
    llm_timeout: int = 60

    @classmethod
    def from_yaml(cls, path: str = None) -> "RevisionConfig":
        if path is None:
            default_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
            if default_path.exists():
                path = str(default_path)
        if path and Path(path).exists():
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f) or {}
                revision_cfg = config_data.get("revision", {})
                common = config_data.get("common", {})
                llm_url = revision_cfg.get("llm_api_url") or common.get("llm_api_url") or cls.llm_api_url
                llm_model = revision_cfg.get("llm_model") or common.get("llm_model") or cls.llm_model
                return cls(
                    reflection_depth=revision_cfg.get("reflection_depth", cls.reflection_depth),
                    generate_alternatives=revision_cfg.get("generate_alternatives", cls.generate_alternatives),
                    alternative_count=revision_cfg.get("alternative_count", cls.alternative_count),
                    confidence_threshold=revision_cfg.get("confidence_threshold", cls.confidence_threshold),
                    max_input_length=revision_cfg.get("max_input_length", cls.max_input_length),
                    llm_api_url=revision_cfg.get("llm_api_url", llm_url),
                    llm_model=revision_cfg.get("llm_model", llm_model),
                    llm_api_key=revision_cfg.get("llm_api_key", ""),
                    llm_timeout=revision_cfg.get("llm_timeout", cls.llm_timeout),
                )
            except Exception as e:
                # 记录加载失败原因，使用默认配置
                logger.warning("从 %s 加载 RevisionConfig 失败，使用默认配置: %s", path, e)
        return cls()

    @classmethod
    def from_env(cls) -> "RevisionConfig":
        return cls(
            llm_api_url=os.getenv("KN_REFLECTION_API_URL", cls.llm_api_url),
            llm_model=os.getenv("KN_REFLECTION_MODEL", cls.llm_model),
            llm_api_key=os.getenv("KN_REFLECTION_API_KEY") or os.getenv("LITELLM_MASTER_KEY", ""),
            llm_timeout=int(os.getenv("KN_REFLECTION_TIMEOUT", str(cls.llm_timeout))),
            reflection_depth=int(os.getenv("SE_REVISION_DEPTH", str(cls.reflection_depth))),
        )


class RevisionOperator:
    """Revision operator with LLM-powered failure diagnosis."""

    def __init__(self, config: RevisionConfig = None,
                 failure_pattern_db: Dict = None,
                 llm_client: LLMClient = None):
        self.config = config or RevisionConfig()
        self.failure_pattern_db = failure_pattern_db or {}
        self._llm = llm_client or self._default_llm()

    def _default_llm(self) -> LLMClient:
        import os
        key = self.config.llm_api_key or os.environ.get("LITELLM_MASTER_KEY", "")
        return LLMClient(
            api_url=self.config.llm_api_url,
            model=self.config.llm_model,
            api_key=key,
            timeout=self.config.llm_timeout,
        )

    def _call_llm_json(self, messages: list[dict], max_tokens: int = 1024) -> dict:
        """调用 LLM 并解析 JSON 响应"""
        try:
            resp = self._llm.chat_completion(
                messages=messages, temperature=0.3,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            text = self._llm.extract_content(resp)
            return self._llm.parse_json_response(text)
        except Exception as e:
            logger.warning("LLM 调用失败: %s", e)
            return {}

    def _call_llm_text(self, messages: list[dict], max_tokens: int = 2048) -> str:
        """调用 LLM 并返回纯文本"""
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

    def diagnose(self, failed_content: str, context: str,
                 failure_type: Optional[FailureType] = None) -> DiagnosisResult:
        if failure_type is None:
            failure_type = self._auto_detect_failure_type(failed_content, context)

        evidence = self._extract_evidence(failed_content, context, failure_type)
        direct_cause = self._reflect_direct(failed_content, context, failure_type, evidence)
        root_cause = self._reflect_root(failed_content, context, failure_type, direct_cause)
        deep_analysis = None
        if self.config.reflection_depth >= 3:
            deep_analysis = self._reflect_deep(failed_content, context, failure_type, direct_cause, root_cause)

        recommended_fix = self._recommend_fix_type(failure_type, root_cause)
        confidence = self._calculate_diagnosis_confidence(evidence, failure_type)

        return DiagnosisResult(
            failure_type=failure_type,
            confidence=confidence,
            direct_cause=direct_cause,
            root_cause=root_cause,
            deep_analysis=deep_analysis,
            evidence=evidence,
            recommended_fix_type=recommended_fix,
        )

    def generate_alternatives(self, failed_content: str, context: str,
                              diagnosis: DiagnosisResult) -> List[AlternativeSolution]:
        alternatives = []
        direct_fix = AlternativeSolution(
            solution_id="A", solution_type="direct_fix",
            description=f"Direct fix for {diagnosis.failure_type.value}",
            content="", confidence=0.7, risk_level="low",
            pros=["Minimal changes", "Preserves original structure"],
            cons=["May not address root cause", "Could have hidden issues"],
        )
        alternatives.append(direct_fix)

        if self.config.generate_alternatives:
            orthogonal_fix = AlternativeSolution(
                solution_id="B", solution_type="orthogonal_fix",
                description=f"Alternative approach avoiding {diagnosis.root_cause}",
                content="", confidence=0.6, risk_level="medium",
                pros=["Addresses root cause", "Different perspective"],
                cons=["More changes", "May introduce new issues"],
            )
            alternatives.append(orthogonal_fix)

            conservative_fix = AlternativeSolution(
                solution_id="C", solution_type="conservative_fix",
                description="Fall back to known working pattern",
                content="", confidence=0.8, risk_level="low",
                pros=["Proven approach", "Low risk"],
                cons=["May not be optimal", "Could be outdated"],
            )
            alternatives.append(conservative_fix)
        return alternatives

    def execute(self, failed_content: str, context: str,
                failure_type: Optional[str] = None,
                trajectory: Optional[Trajectory] = None) -> RevisionOutput:
        ft = None
        if failure_type:
            try:
                ft = FailureType(failure_type)
            except ValueError:
                ft = FailureType.UNKNOWN

        diagnosis = self.diagnose(failed_content, context, ft)
        alternatives = self.generate_alternatives(failed_content, context, diagnosis)

        revised_content = self._generate_revised_content(
            failed_content, context, diagnosis, alternatives,
        )

        confidence_score = min(
            diagnosis.confidence,
            max(a.confidence for a in alternatives) if alternatives else 0.5,
        )
        return RevisionOutput(
            revised_content=revised_content,
            diagnosis=diagnosis,
            alternatives=alternatives,
            confidence_score=confidence_score,
        )

    # ── LLM-powered internal methods ────────────────────────────

    def _auto_detect_failure_type(self, content: str, context: str) -> FailureType:
        prompt = AUTO_DETECT_PROMPT().format(
            failed_content=content[:self.config.max_input_length],
            context=context[:1000],
        )
        data = self._call_llm_json([
            {"role": "system", "content": "你是失败类型分析专家，输出结构化 JSON。"},
            {"role": "user", "content": prompt},
        ])
        ftype = data.get("failure_type", "unknown")
        try:
            return FailureType(ftype)
        except ValueError:
            return FailureType.UNKNOWN

    def _reflect_direct(self, content: str, context: str,
                        failure_type: FailureType,
                        evidence: List[str]) -> str:
        prompt = REFLECT_DIRECT_PROMPT().format(
            failed_content=content[:self.config.max_input_length],
            context=context[:1000],
            failure_type=failure_type.value,
        )
        data = self._call_llm_json([
            {"role": "system", "content": "你是有经验的 Debug 工程师，输出直接原因分析。"},
            {"role": "user", "content": prompt},
        ])
        return data.get("direct_cause", FAILURE_TYPE_DESCRIPTIONS.get(failure_type, "未知错误"))

    def _reflect_root(self, content: str, context: str,
                      failure_type: FailureType, direct_cause: str) -> str:
        prompt = REFLECT_ROOT_PROMPT().format(
            failed_content=content[:self.config.max_input_length],
            context=context[:1000],
            failure_type=failure_type.value,
            direct_cause=direct_cause[:500],
        )
        data = self._call_llm_json([
            {"role": "system", "content": "你是根因分析专家，输出根本原因。"},
            {"role": "user", "content": prompt},
        ])
        return data.get("root_cause", f"需进一步调查{failure_type.value}")

    def _reflect_deep(self, content: str, context: str,
                      failure_type: FailureType, direct_cause: str,
                      root_cause: str) -> str:
        prompt = REFLECT_DEEP_PROMPT().format(
            failed_content=content[:self.config.max_input_length],
            context=context[:1000],
            failure_type=failure_type.value,
            direct_cause=direct_cause[:500],
            root_cause=root_cause[:500],
        )
        data = self._call_llm_json([
            {"role": "system", "content": "你是系统性思维专家，输出深度分析。"},
            {"role": "user", "content": prompt},
        ])
        return data.get("deep_analysis", "")

    def _generate_revised_content(self, content: str, context: str,
                                   diagnosis: DiagnosisResult,
                                   alternatives: List[AlternativeSolution]) -> str:
        prompt = REVISE_CONTENT_PROMPT().format(
            failed_content=content[:self.config.max_input_length],
            failure_type=diagnosis.failure_type.value,
            direct_cause=diagnosis.direct_cause[:500],
            root_cause=diagnosis.root_cause[:500],
        )
        return self._call_llm_text([
            {"role": "system", "content": "你根据诊断结果修正代码或内容。"},
            {"role": "user", "content": prompt},
        ])

    def _recommend_fix_type(self, failure_type: FailureType, root_cause: str) -> str:
        prompt = FIX_TYPE_PROMPT().format(
            failure_type=failure_type.value,
            root_cause=root_cause[:500],
        )
        data = self._call_llm_json([
            {"role": "system", "content": "输出推荐修复类型。"},
            {"role": "user", "content": prompt},
        ])
        return data.get("recommended_fix_type", "general_debugging")

    # ── Rule-based helpers ──────────────────────────────────────

    def _extract_evidence(self, content: str, context: str,
                          failure_type: FailureType) -> List[str]:
        evidence = []
        evidence.append(f"Failure type: {failure_type.value}")
        evidence.append(f"Context: {context[:200]}...")
        evidence.append(f"Failed content length: {len(content)} chars")
        return evidence

    def _calculate_diagnosis_confidence(self, evidence: List[str],
                                        failure_type: FailureType) -> float:
        base = 0.5 if failure_type != FailureType.UNKNOWN else 0.3
        bonus = min(0.3, len(evidence) * 0.05)
        return min(0.95, base + bonus)


# Convenience function
def revise(failed_content: str, context: str, config_path: str = None,
           **kwargs) -> RevisionOutput:
    config = RevisionConfig.from_yaml(config_path)
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
    operator = RevisionOperator(config)
    return operator.execute(failed_content, context)
