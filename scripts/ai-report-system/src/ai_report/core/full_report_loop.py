"""
全文质量闭环 — FullReportLoop
===============================
流程：评估 → 标记 → 并行修复（带衔接锚点）→ 一致性检查 → 再评估
循环条件：最多 5 次 或 评分 ≥ 0.8 或 评分停滞

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import logging

from .quality_loop import QualityLoop
from .workflow_state import WorkflowState

logger = logging.getLogger(__name__)


class FullReportLoop:
    """全文质量闭环。

    流程：
    1. 评估全报告各章节质量
    2. 标记不合格章节
    3. 并行修复（带衔接锚点）
    4. 一致性检查
    5. 重新评估，循环

    终止条件（任一满足即停）：
    - 平均分 ≥ PASS_THRESHOLD (0.8)
    - 达到 MAX_ITERATIONS (5) 次
    - 连续两次评分不涨
    """

    MAX_ITERATIONS: int = 5
    PASS_THRESHOLD: float = 0.8
    CHAPTER_THRESHOLD: float = 0.6

    def __init__(
        self,
        max_iterations: int = MAX_ITERATIONS,
        pass_threshold: float = PASS_THRESHOLD,
    ) -> None:
        """初始化全文质量闭环。

        Args:
            max_iterations: 最大循环次数
            pass_threshold: 通过阈值
        """
        self._max_iterations = max_iterations
        self._pass_threshold = pass_threshold
        self._scores_history: list[float] = []

    # ── 公开接口 ──────────────────────────────────────────

    def run(
        self,
        state: WorkflowState,
        topic: str,
    ) -> WorkflowState:
        """执行全文质量闭环。

        Args:
            state: WorkflowState（含所有章节内容）
            topic: 报告主题

        Returns:
            优化后的 WorkflowState
        """
        if not state._chapter_order:
            logger.warning("FullReportLoop: no chapters to evaluate")
            return state

        total = len(state._chapter_order)

        for iteration in range(1, self._max_iterations + 1):
            scores = self._evaluate_all(state)
            avg_score = sum(scores.values()) / len(scores) if scores else 0.0
            self._scores_history.append(avg_score)

            logger.info(
                "全文闭环 [%d/%d]: avg_score=%.2f (threshold=%.2f)",
                iteration, self._max_iterations, avg_score, self._pass_threshold,
            )

            if avg_score >= self._pass_threshold:
                logger.info("  ✅ 质量达标: %.2f >= %.2f", avg_score, self._pass_threshold)
                break

            if self._score_stagnated():
                break

            failed = [t for t, s in scores.items() if s < self.CHAPTER_THRESHOLD]
            logger.info("  🔧 %d/%d 章节需修正: %s", len(failed), total, failed[:5])

            self._parallel_fix(state, failed, topic)
            self._consistency_check(state)
            self._cross_chapter_fact_check(state)

        return state

    def _score_stagnated(self) -> bool:
        """检查评分是否停滞。"""
        if len(self._scores_history) < 2:
            return False
        prev = self._scores_history[-2]
        curr = self._scores_history[-1]
        if curr <= prev:
            logger.info("  ⏹ 质量不再提升 (%.2f → %.2f)，提前终止", prev, curr)
            return True
        return False

    # ── 评估 ──────────────────────────────────────────────

    @staticmethod
    def _evaluate_all(state: WorkflowState) -> dict[str, float]:
        """评估所有章节的质量分。

        Args:
            state: WorkflowState

        Returns:
            {章节标题: 质量分} 字典
        """
        scores: dict[str, float] = {}
        for title in state._chapter_order:
            ctx = state.chapter_contexts.get(title)
            if ctx and ctx.generated_content:
                scores[title] = QualityLoop._estimate_quality(
                    ctx.generated_content, ctx.estimated_words,
                )
            else:
                scores[title] = 0.0
        return scores

    # ── 衔接锚点 ──────────────────────────────────────────

    @staticmethod
    def _build_anchor(state: WorkflowState, title: str) -> str:
        """构建单章的衔接锚点。

        锚点 = 上一章末尾200字 + 下一章开头200字（均来自原始版本）

        Args:
            state: WorkflowState
            title: 当前章节标题

        Returns:
            锚点文本
        """
        chapters = state._chapter_order
        try:
            idx = chapters.index(title)
        except ValueError:
            return ""

        prev_tail = ""
        if idx > 0:
            prev_ctx = state.chapter_contexts.get(chapters[idx - 1])
            if prev_ctx and prev_ctx.generated_content:
                prev_tail = prev_ctx.generated_content[-200:]

        next_head = ""
        if idx < len(chapters) - 1:
            next_ctx = state.chapter_contexts.get(chapters[idx + 1])
            if next_ctx and next_ctx.generated_content:
                next_head = next_ctx.generated_content[:200]

        parts: list[str] = []
        if prev_tail:
            parts.append(f"上一章末尾: {prev_tail}")
        if next_head:
            parts.append(f"下一章开头: {next_head}")
        return "\n\n".join(parts)

    # ── 并行修复 ──────────────────────────────────────────

    def _parallel_fix(
        self,
        state: WorkflowState,
        failed_titles: list[str],
        topic: str,
    ) -> None:
        """并行修复所有不合格章节。

        Args:
            state: WorkflowState
            failed_titles: 不合格章节标题列表
            topic: 报告主题
        """
        loop = QualityLoop(threshold=self.CHAPTER_THRESHOLD)

        for title in failed_titles:
            ctx = state.chapter_contexts.get(title)
            if ctx is None or not ctx.generated_content:
                continue

            score_before = QualityLoop._estimate_quality(
                ctx.generated_content, ctx.estimated_words,
            )

            # 计算锚点：该章节在报告中的上下文位置
            anchor = QualityLoop._build_anchor(state, title)

            result = loop.run_chapter(state, title, anchor_hint=anchor)
            if result is not None:
                logger.debug(
                    "  ✅ '%s' 全文闭环修正: %.2f → %.2f",
                    title, score_before, result.score_after,
                )

    # ── 一致性检查 ────────────────────────────────────────

    @staticmethod
    def _consistency_check(state: WorkflowState) -> list[str]:
        """检查全文逻辑一致性。

        Args:
            state: WorkflowState

        Returns:
            发现的问题列表
        """
        issues: list[str] = []
        chapters = state._chapter_order

        for i in range(len(chapters) - 1):
            curr_ctx = state.chapter_contexts.get(chapters[i])
            next_ctx = state.chapter_contexts.get(chapters[i + 1])
            if not curr_ctx or not next_ctx:
                continue

            curr_content = curr_ctx.generated_content or ""
            next_content = next_ctx.generated_content or ""

            overlap = _text_overlap(curr_content[-100:], next_content[:100])
            if overlap > 0.6:
                issue = (
                    f"章节衔接可能重复: 「{chapters[i]}」→「{chapters[i + 1]}」"
                    f" 相似度 {overlap:.0%}"
                )
                issues.append(issue)
                logger.warning("一致性: %s", issue)

        if not issues:
            logger.debug("一致性检查通过: %d 章节", len(chapters))

        return issues

    @staticmethod
    def _cross_chapter_fact_check(state: WorkflowState) -> list[str]:
        """交叉核查相邻章节中的事实一致性——数据/政策/指标前后是否矛盾。

        v5.0.1 扩展：追加全量跨章数值一致性审计（正则提取，不依赖LLM）

        Returns:
            发现的事实冲突列表
        """
        from ..adapters.ai_client import call_llm as _call

        chapters = state._chapter_order
        issues: list[str] = []

        # ── Phase 1: LLM检查相邻章节（已有逻辑） ──
        for i in range(len(chapters) - 1):
            curr_ctx = state.chapter_contexts.get(chapters[i])
            next_ctx = state.chapter_contexts.get(chapters[i + 1])
            if not curr_ctx or not next_ctx:
                continue

            curr_content = (curr_ctx.generated_content or "")[:800]
            next_content = (next_ctx.generated_content or "")[:800]
            if not curr_content or not next_content:
                continue

            prompt = (
                f"请对比以下两段报告内容，找出所有事实不一致之处。\n\n"
                f"## 章节 A: 「{chapters[i]}」\n{curr_content}\n\n"
                f"## 章节 B: 「{chapters[i + 1]}」\n{next_content}\n\n"
                "## 检查要求\n"
                "关注以下类型的不一致：\n"
                "1. 同一数据在不同章节数值不同（如投资额、时间节点、指标百分比）\n"
                "2. 同一政策文件在不同章节引用内容矛盾\n"
                "3. 同一概念/术语在不同章节定义不同\n"
                "4. 时间线逻辑矛盾（如A章说2026年启动，B章说2027年启动）\n\n"
                "## 输出格式\n"
                "- 无问题则输出：无\n"
                "- 有问题则每行一个：问题类型 | 章节A的说法 | 章节B的说法\n"
            )
            try:
                result = _call(prompt, max_iterations=1, temperature=0.1)
                if result and "无" not in result.strip()[:5]:
                    issues.append(f"「{chapters[i]}」↔「{chapters[i + 1]}」:\n{result.strip()}")
                    logger.warning("事实不一致: %s", result.strip()[:120])
            except Exception:
                pass

        # ── Phase 2: 正则提取全量跨章数值一致性审计 ──
        numeric_issues = FullReportLoop._cross_chapter_numeric_audit(state)
        issues.extend(numeric_issues)

        if not issues:
            logger.info("  事实一致性检查通过: 相邻章节无数据冲突")
        return issues

    @staticmethod
    def _cross_chapter_numeric_audit(state: WorkflowState) -> list[str]:
        """全量跨章数值一致性审计。

        用正则从所有章节提取金额数据，按网络层（互联网/工控网/内网/汇总/总）
        分组，跨章比对数值一致性。

        Returns:
            发现的数据冲突列表
        """
        import re
        chapters = state._chapter_order
        if len(chapters) < 2:
            return []

        # 收集每个章节的数值信息
        chapter_values: dict[str, list[dict]] = {}

        for title in chapters:
            ctx = state.chapter_contexts.get(title)
            if not ctx or not ctx.generated_content:
                continue
            content = ctx.generated_content

            # 提取金额：X万元, X亿元, X万/年, X亿/年, 约X万元, 约X亿元
            values = []
            # 模式: [约]X.XX[万/亿][元][/年]
            for m in re.finditer(
                r'(约\s*)?'
                r'(\d+(?:[.,]\d+)?)\s*'
                r'(万|亿)\s*'
                r'元?\s*'
                r'(?:/\s*年)?',
                content,
            ):
                prefix = m.group(1) or ""
                num_str = m.group(2).replace(",", "")
                unit = m.group(3)
                num = float(num_str)
                # 统一单位为"万元"
                if unit == "亿":
                    num *= 10000

                values.append({
                    "raw": m.group(0).strip(),
                    "num_wan": num,
                    "text_before": content[max(0, m.start()-30):m.start()],
                })

            # 提取总投资汇总的合计行
            for m in re.finditer(
                r'(?:合计|总计|总[投]资|总投资|年)\s*[:：]?\s*'
                r'(约\s*)?(\d+(?:[.,]\d+)?)\s*(万|亿)\s*元?'
                r'(?:\s*\(.*?\))?',
                content,
            ):
                prefix = m.group(1) or ""
                num_str = m.group(2).replace(",", "")
                unit = m.group(3)
                num = float(num_str)
                if unit == "亿":
                    num *= 10000
                values.append({
                    "raw": m.group(0).strip(),
                    "num_wan": num,
                    "text_before": content[max(0, m.start()-40):m.start()],
                })

            chapter_values[title] = values

        # 按网络层归类比对
        layer_keywords = [
            ("互联网", ["互联网", "互联网层"]),
            ("工控网", ["工控网", "工控网层"]),
            ("内网", ["内网", "内网层"]),
            ("总投资", ["总[投]资", "三网", "汇总", "四年"]),
        ]

        issues: list[str] = []

        for layer_name, keywords in layer_keywords:
            # 收集所有章节中提及该层的数值
            layer_amounts: list[dict] = []
            for title, values in chapter_values.items():
                for v in values:
                    ctx_before = v["text_before"]
                    if any(re.search(kw, ctx_before) for kw in keywords):
                        layer_amounts.append({
                            "title": title,
                            "raw": v["raw"],
                            "num_wan": v["num_wan"],
                        })

            if len(layer_amounts) < 2:
                continue

            # 找出主要数值（排除范围值和极小值）
            main_values = [la for la in layer_amounts
                           if la["num_wan"] > 1]
            if len(main_values) < 2:
                continue

            # 两两比对
            for i in range(len(main_values)):
                for j in range(i + 1, len(main_values)):
                    a, b = main_values[i], main_values[j]
                    if a["num_wan"] == 0 or b["num_wan"] == 0:
                        continue
                    ratio = max(a["num_wan"], b["num_wan"]) / min(a["num_wan"], b["num_wan"])
                    if ratio > 1.5:  # 差异超过50%认为冲突
                        issue = (
                            f"⚠️ 数值不一致: {layer_name} "
                            f"「{a['title']}」说 {a['raw']} (≈{a['num_wan']:.0f}万) vs "
                            f"「{b['title']}」说 {b['raw']} (≈{b['num_wan']:.0f}万) "
                            f"差异{ratio:.1f}倍"
                        )
                        if issue not in issues:
                            issues.append(issue)
                            logger.warning("跨章数值审计: %s", issue)

        if issues:
            logger.info("  跨章数值审计: 发现 %d 个潜在冲突", len(issues))
        else:
            logger.info("  跨章数值审计通过: 各章金额数据一致")

        return issues


def _text_overlap(a: str, b: str) -> float:
    """计算两段文本的字符级重叠率。

    Args:
        a: 文本A
        b: 文本B

    Returns:
        重叠率 (0.0 ~ 1.0)
    """
    if not a or not b:
        return 0.0

    common = 0
    for i in range(min(len(a), len(b))):
        if a[i] == b[i]:
            common += 1
        else:
            break

    return common / max(len(a), len(b), 1)
