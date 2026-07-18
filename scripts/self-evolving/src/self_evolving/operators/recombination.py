"""Recombination Operator — Cross-trajectory knowledge synthesizer with LLM.

Extracts reusable components, matches them semantically (word-level + LLM hybrid),
and synthesizes an optimal combination.
"""
import logging
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from self_evolving.adapters.llm_client import LLMClient
from self_evolving.prompt_loader import get_prompt

logger = logging.getLogger(__name__)

# ── 质量评分常量（P2-SE-025） ─────────────────────────────────────────────
_QUALITY_BASE_SCORE = 0.4
_QUALITY_STRUCTURED_BONUS = 0.15
_QUALITY_DOC_BONUS = 0.1
_QUALITY_KEYWORD_BONUS_PER = 0.03
_QUALITY_KEYWORD_BONUS_MAX = 0.15
_QUALITY_LENGTH_MIN = 100
_QUALITY_LENGTH_MAX = 5000
_QUALITY_LENGTH_BONUS = 0.1

# ── Prompts (硬编码 fallback，若 prompts.yaml 加载失败使用) ─────────────────

_FALLBACK_CONFLICT_DETECT = """判断以下两个组件是否存在实质性冲突。

组件A（{type_a}）：
{content_a}

组件B（{type_b}）：
{content_b}

输出 JSON：
{{
    "has_conflict": true/false,
    "conflict_severity": 0.0-1.0,
    "is_complementary": true/false,
    "similarity": 0.0-1.0,
    "reason": "<判断理由>"
}}
"""

def CONFLICT_DETECT_PROMPT() -> str:
    """Hot-reloadable prompt accessor（每次调用从 loader 拉最新）。"""
    return get_prompt("recombination", "conflict_detect", _FALLBACK_CONFLICT_DETECT)


@dataclass
class Component:
    """A reusable component extracted from candidate content"""
    component_id: str
    source_index: int
    component_type: str
    content: str
    semantic_embedding: Optional[List[float]] = None
    quality_score: float = 0.5
    is_failure_pattern: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComponentMatch:
    """Match between two components from different candidates"""
    component_a: Component
    component_b: Component
    similarity_score: float
    is_complementary: bool
    is_conflicting: bool
    conflict_severity: float = 0.0


@dataclass
class RecombinationConfig:
    selection_criteria: str = "quality"
    max_components: int = 5
    detect_conflicts: bool = True
    conflict_severity_threshold: float = 0.5
    semantic_similarity_threshold: float = 0.7
    max_input_length: int = 16000
    llm_api_url: str = "http://127.0.0.1:4142/v1/chat/completions"
    llm_model: str = "s-deepseek-v4-flash"
    llm_api_key: str = ""
    llm_timeout: int = 60
    jaccard_threshold_low: float = 0.3   # below this: definitely different
    jaccard_threshold_high: float = 0.7  # above this: definitely similar

    @classmethod
    def from_yaml(cls, path: str = None) -> "RecombinationConfig":
        if path is None:
            default_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
            if default_path.exists():
                path = str(default_path)
        if path and Path(path).exists():
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f) or {}
                cfg = config_data.get("recombination", {})
                common = config_data.get("common", {})
                return cls(
                    selection_criteria=cfg.get("selection_criteria", cls.selection_criteria),
                    max_components=cfg.get("max_components", cls.max_components),
                    detect_conflicts=cfg.get("detect_conflicts", cls.detect_conflicts),
                    conflict_severity_threshold=cfg.get("conflict_severity_threshold", cls.conflict_severity_threshold),
                    semantic_similarity_threshold=cfg.get("semantic_similarity_threshold", cls.semantic_similarity_threshold),
                    max_input_length=cfg.get("max_input_length", cls.max_input_length),
                    llm_api_url=cfg.get("llm_api_url") or common.get("llm_api_url", cls.llm_api_url),
                        llm_model=cfg.get("llm_model") or common.get("llm_model", cls.llm_model),
                        llm_api_key=cfg.get("llm_api_key") or os.getenv("LITELLM_MASTER_KEY", ""),
                )
            except Exception as e:
                # 记录加载失败原因，使用默认配置
                logger.warning("从 %s 加载 RecombinationConfig 失败，使用默认配置: %s", path, e)
        return cls()

    @classmethod
    def from_env(cls) -> "RecombinationConfig":
        return cls(
            llm_api_url=os.getenv("KN_REFLECTION_API_URL", cls.llm_api_url),
            llm_model=os.getenv("KN_REFLECTION_MODEL", cls.llm_model),
        )


@dataclass
class RecombinationOutput:
    recombined_content: str
    component_map: Dict[str, str]
    synergy_score: float
    conflict_log: List[str]
    preserved_components: List[Component]
    replaced_components: List[Component]
    extraction_stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recombined_content": self.recombined_content,
            "component_map": self.component_map,
            "synergy_score": self.synergy_score,
            "conflict_log": self.conflict_log,
            "preserved_components": [
                {"component_id": c.component_id, "source_index": c.source_index,
                 "component_type": c.component_type, "quality_score": c.quality_score,
                 "is_failure_pattern": c.is_failure_pattern}
                for c in self.preserved_components
            ],
            "replaced_components": [
                {"component_id": c.component_id, "source_index": c.source_index,
                 "component_type": c.component_type, "quality_score": c.quality_score}
                for c in self.replaced_components
            ],
            "extraction_stats": self.extraction_stats,
        }


class RecombinationOperator:
    """Recombination operator with LLM-enhanced semantic matching."""

    def __init__(self, config: RecombinationConfig = None,
                 llm_client: LLMClient = None):
        self.config = config or RecombinationConfig()
        self._llm = llm_client or self._default_llm()

    def _default_llm(self) -> LLMClient:
        key = self.config.llm_api_key or os.environ.get("LITELLM_MASTER_KEY", "")
        return LLMClient(
            api_url=self.config.llm_api_url,
            model=self.config.llm_model,
            api_key=key,
            timeout=self.config.llm_timeout,
        )

    def _call_llm_json(self, messages: list[dict]) -> dict:
        try:
            resp = self._llm.chat_completion(
                messages=messages, temperature=0.1,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            text = self._llm.extract_content(resp)
            return self._llm.parse_json_response(text)
        except Exception as e:
            logger.warning("LLM 调用失败: %s", e)
            return {}

    def extract_components(self, candidate_contents: List[str]) -> List[Component]:
        components = []
        for idx, content in enumerate(candidate_contents):
            sections = self._split_into_sections(content)
            for section_idx, section in enumerate(sections):
                component = Component(
                    component_id=f"c_{idx}_{section_idx}",
                    source_index=idx,
                    component_type=self._classify_component(section),
                    content=section,
                    quality_score=self._estimate_quality(section, idx, candidate_contents),
                    is_failure_pattern=self._is_failure_pattern(section),
                    metadata={"source_candidate": idx, "section_index": section_idx},
                )
                components.append(component)
        return components

    def match_components(self, components: List[Component]) -> List[ComponentMatch]:
        matches = []
        by_type: Dict[str, List[Component]] = {}
        for comp in components:
            by_type.setdefault(comp.component_type, []).append(comp)

        for comp_type, type_components in by_type.items():
            for i, comp_a in enumerate(type_components):
                for comp_b in type_components[i+1:]:
                    if comp_a.source_index != comp_b.source_index:
                        similarity = self._calculate_similarity(comp_a, comp_b)
                        is_complementary = self._is_complementary(comp_a, comp_b)
                        # 计算一次 Jaccard 并传入 _is_conflicting，避免重复计算（P2-SE-009）
                        jaccard_sim = self._jaccard_similarity(comp_a.content, comp_b.content)
                        is_conflicting, conflict_data = self._is_conflicting(comp_a, comp_b, jaccard_sim=jaccard_sim)
                        conflict_severity = self._assess_conflict_severity(comp_a, comp_b, is_conflicting, conflict_data)
                        matches.append(ComponentMatch(
                            component_a=comp_a, component_b=comp_b,
                            similarity_score=similarity,
                            is_complementary=is_complementary,
                            is_conflicting=is_conflicting,
                            conflict_severity=conflict_severity,
                        ))
        return matches

    def detect_conflicts(self, matches: List[ComponentMatch]) -> List[str]:
        conflicts = []
        for match in matches:
            if match.is_conflicting and match.conflict_severity >= self.config.conflict_severity_threshold:
                conflicts.append(
                    f"Conflict: {match.component_a.component_id} vs {match.component_b.component_id} "
                    f"(severity: {match.conflict_severity:.2f})"
                )
        return conflicts

    def synthesize(self, components: List[Component], matches: List[ComponentMatch],
                   task_context: str) -> Tuple[str, Dict[str, str], List[Component], List[Component]]:
        preserved, replaced = [], []
        component_map = {}
        content_segments = []

        if self.config.selection_criteria == "quality":
            selected = self._select_by_quality(components, matches)
        elif self.config.selection_criteria == "coverage":
            selected = self._select_by_coverage(components, matches)
        else:
            selected = self._select_by_diversity(components, matches)

        for comp in selected:
            preserved.append(comp)
            content_segments.append(comp.content)
            component_map[comp.component_id] = f"candidate_{comp.source_index}"

        preserved_set = set(id(c) for c in preserved)
        for comp in components:
            if id(comp) not in preserved_set:
                replaced.append(comp)

        recombined_content = self._assemble_content(content_segments, task_context)
        return recombined_content, component_map, preserved, replaced

    def calculate_synergy(self, original_contents: List[str],
                          recombined_content: str) -> float:
        if not original_contents:
            return 0.0
        avg_quality = sum(self._estimate_quality(c, i, original_contents)
                         for i, c in enumerate(original_contents)) / len(original_contents)
        recombined_quality = self._estimate_quality(recombined_content, -1, original_contents)
        if avg_quality > 0:
            synergy = (recombined_quality - avg_quality) / avg_quality
        else:
            synergy = 0.0
        return min(1.0, max(0.0, synergy))

    def execute(self, candidate_contents: List[str], task_context: str,
                selection_criteria: str = None) -> RecombinationOutput:
        if selection_criteria:
            self.config.selection_criteria = selection_criteria
        components = self.extract_components(candidate_contents)
        matches = self.match_components(components)
        conflict_log = []
        if self.config.detect_conflicts:
            conflict_log = self.detect_conflicts(matches)
        recombined_content, component_map, preserved, replaced = self.synthesize(
            components, matches, task_context,
        )
        synergy_score = self.calculate_synergy(candidate_contents, recombined_content)
        extraction_stats = {
            "total_components": len(components),
            "unique_types": len(set(c.component_type for c in components)),
            "matches_found": len(matches),
            "conflicts_detected": len(conflict_log),
            "preserved_count": len(preserved),
            "replaced_count": len(replaced),
            "source_distribution": self._get_source_distribution(components),
        }
        return RecombinationOutput(
            recombined_content=recombined_content,
            component_map=component_map,
            synergy_score=synergy_score,
            conflict_log=conflict_log,
            preserved_components=preserved,
            replaced_components=replaced,
            extraction_stats=extraction_stats,
        )

    # ── Internal methods ────────────────────────────────────────

    def _split_into_sections(self, content: str) -> List[str]:
        sections = [s.strip() for s in content.split("\n\n") if s.strip()]
        return sections if sections else [content]

    def _classify_component(self, section: str) -> str:
        sl = section.lower()
        if "def " in sl or "function" in sl:
            return "function"
        elif "import" in sl or "from " in sl:
            return "import"
        elif "class " in sl:
            return "class"
        elif "if " in sl or "for " in sl or "while " in sl:
            return "control_flow"
        elif "try " in sl or "except" in sl:
            return "error_handling"
        else:
            return "general"

    def _estimate_quality(self, content: str, source_idx: int,
                          all_contents: List[str]) -> float:
        """基于内容特征的质量评估：结构化程度、代码密度、长度合理性。"""
        if not content or len(content) < 10:
            return 0.1
        if len(content) > 10000:
            return 0.3
        score = _QUALITY_BASE_SCORE
        # 结构化特征：含函数/类定义
        if "def " in content or "class " in content:
            score += _QUALITY_STRUCTURED_BONUS
        # 文档特征
        if '"""' in content or "'''" in content or content.strip().startswith("#"):
            score += _QUALITY_DOC_BONUS
        # 代码密度：关键字占比
        code_kw = ["if ", "for ", "return", "import", "while", "try", "with "]
        cl = content.lower()
        kw_count = sum(1 for kw in code_kw if kw in cl)
        score += min(_QUALITY_KEYWORD_BONUS_MAX, kw_count * _QUALITY_KEYWORD_BONUS_PER)
        # 长度合理性：100-5000 字符最佳
        if _QUALITY_LENGTH_MIN <= len(content) <= _QUALITY_LENGTH_MAX:
            score += _QUALITY_LENGTH_BONUS
        return min(1.0, score)

    def _is_failure_pattern(self, section: str) -> bool:
        kw = ["error", "exception", "failed", "bug", "issue", "problem"]
        return any(k in section.lower() for k in kw)

    def _jaccard_similarity(self, a: str, b: str) -> float:
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = len(words_a & words_b)
        union = len(words_a | words_b)
        return intersection / union if union > 0 else 0.0

    def _calculate_similarity(self, comp_a: Component, comp_b: Component) -> float:
        """Hybrid similarity: fast Jaccard for clear cases, LLM for ambiguous."""
        jaccard = self._jaccard_similarity(comp_a.content, comp_b.content)

        # Clear cases: use Jaccard directly
        if jaccard <= self.config.jaccard_threshold_low:
            return 0.0
        if jaccard >= self.config.jaccard_threshold_high:
            return 1.0

        # Ambiguous range: use LLM for deeper judgment
        prompt = CONFLICT_DETECT_PROMPT().format(
            type_a=comp_a.component_type, content_a=comp_a.content[:1000],
            type_b=comp_b.component_type, content_b=comp_b.content[:1000],
        )
        data = self._call_llm_json([
            {"role": "system", "content": "判断两个组件的语义相似度。"},
            {"role": "user", "content": prompt},
        ])
        return data.get("similarity", jaccard)

    def _is_complementary(self, comp_a: Component, comp_b: Component) -> bool:
        return comp_a.component_type != comp_b.component_type

    def _is_conflicting(self, comp_a: Component, comp_b: Component,
                        jaccard_sim: float = 0.0) -> Tuple[bool, dict]:
        if comp_a.component_type != comp_b.component_type:
            return False, {}
        # Word-level fallback：使用传入的 jaccard_sim，避免重复计算（P2-SE-009）
        if jaccard_sim > self.config.jaccard_threshold_low:
            prompt = CONFLICT_DETECT_PROMPT().format(
                type_a=comp_a.component_type, content_a=comp_a.content[:1000],
                type_b=comp_b.component_type, content_b=comp_b.content[:1000],
            )
            data = self._call_llm_json([
                {"role": "system", "content": "判断两个组件是否存在冲突。"},
                {"role": "user", "content": prompt},
            ])
            return data.get("has_conflict", False), data
        return False, {}

    def _assess_conflict_severity(self, comp_a: Component, comp_b: Component,
                                   is_conflicting: bool,
                                   conflict_data: dict = None) -> float:
        if not is_conflicting:
            return 0.0
        # 优先使用 LLM 返回的冲突严重度
        if conflict_data and "conflict_severity" in conflict_data:
            return min(1.0, max(0.0, float(conflict_data["conflict_severity"])))
        # 回退：高 Jaccard = 高冗余（非高冲突），降低系数
        return min(1.0, self._jaccard_similarity(comp_a.content, comp_b.content) * 0.8)

    def _select_by_quality(self, components: List[Component],
                           matches: List[ComponentMatch]) -> List[Component]:
        by_type: Dict[str, List[Component]] = {}
        for comp in components:
            by_type.setdefault(comp.component_type, []).append(comp)
        selected = []
        for type_components in by_type.values():
            sorted_comps = sorted(type_components, key=lambda c: c.quality_score, reverse=True)
            for comp in sorted_comps[:self.config.max_components]:
                if not self._has_high_conflict(comp, matches):
                    selected.append(comp)
        return selected

    def _select_by_coverage(self, components: List[Component],
                            matches: List[ComponentMatch]) -> List[Component]:
        selected = []
        covered_types = set()
        for comp in sorted(components, key=lambda c: c.quality_score, reverse=True):
            if comp.component_type not in covered_types:
                selected.append(comp)
                covered_types.add(comp.component_type)
        return selected

    def _select_by_diversity(self, components: List[Component],
                             matches: List[ComponentMatch]) -> List[Component]:
        selected = []
        sources_used, types_used = set(), set()
        for comp in sorted(components, key=lambda c: c.quality_score, reverse=True):
            if comp.source_index not in sources_used or comp.component_type not in types_used:
                selected.append(comp)
                sources_used.add(comp.source_index)
                types_used.add(comp.component_type)
        return selected

    def _has_high_conflict(self, component: Component,
                           matches: List[ComponentMatch]) -> bool:
        for match in matches:
            if (match.component_a == component or match.component_b == component) and \
               match.is_conflicting and match.conflict_severity >= self.config.conflict_severity_threshold:
                return True
        return False

    def _assemble_content(self, segments: List[str], task_context: str) -> str:
        if not segments:
            return ""
        if len(segments) == 1:
            return segments[0]
        # LLM 智能合成：内容足够多时使用模型融合
        joined = "\n\n".join(segments)
        if len(joined) > 300 and len(segments) >= 3:
            prompt = (f"任务背景：{task_context[:500]}\n\n"
                      f"请将以下 {len(segments)} 个代码片段智能合成为一个连贯的整体，"
                      "去除冗余并保持功能完整。只输出合成后的代码。\n\n" + joined[:6000])
            synthesized = self._llm_extract_text([
                {"role": "system", "content": "你是代码合成专家，智能合并多个片段为一个连贯整体。"},
                {"role": "user", "content": prompt},
            ])
            if synthesized and len(synthesized) > len(joined) * 0.3:
                return synthesized
        return joined

    def _llm_extract_text(self, messages: list[dict]) -> str:
        """LLM 调用返回纯文本（内部辅助）。"""
        try:
            resp = self._llm.chat_completion(messages=messages, temperature=0.2, max_tokens=4096)
            return self._llm.extract_content(resp)
        except Exception:
            return ""

    def _get_source_distribution(self, components: List[Component]) -> Dict[int, int]:
        dist: Dict[int, int] = {}
        for comp in components:
            dist[comp.source_index] = dist.get(comp.source_index, 0) + 1
        return dist


def recombine(candidate_contents: List[str], task_context: str,
              config_path: str = None, **kwargs) -> RecombinationOutput:
    config = RecombinationConfig.from_yaml(config_path)
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
    operator = RecombinationOperator(config)
    return operator.execute(candidate_contents, task_context)
