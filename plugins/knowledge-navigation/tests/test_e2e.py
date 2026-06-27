"""端到端测试：Router 门控 + 语义标签化输出。

覆盖两大特性的全链路集成：
1. LLM Router → pre_llm_call() 的三路 mask 决策
2. 分来源 XML 标签输出格式验证

注意：turn_gate 的 skip_pre_llm_call 在 Router 之前拦截，
所以测试消息必须 > 10 字符且不以操作型前缀开头。
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from knowledge_navigation.config import CONFIG
from knowledge_navigation.core import hooks as nav_hooks
from knowledge_navigation.core.hooks import pre_llm_call


# ========== 共享测试数据 ==========

_TRACE_WITH_SCORES = {
    "reranked": [
        {"node_id": "hs-aaa", "rerank_score": 0.95},
        {"node_id": "hs-bbb", "rerank_score": 0.85},
        {"node_id": "hs-ccc", "rerank_score": 0.70},
    ]
}

# 包含特殊字符 node_id 的 trace，供 XSS 测试使用
_TRACE_WITH_XSS = {
    "reranked": [
        {"node_id": "hs-<xxx>", "rerank_score": 0.90},
    ]
}

_KT_RESULTS = [
    {"id": 101, "text": "知识树节点一", "name": "KT概念1", "score": 0.8},
    {"id": 102, "text": "知识树节点二", "name": "KT概念2", "score": 0.6},
]


def _mock_recall(results: list | None = None, trace: dict | None = None) -> dict | None:
    """构建 Hindsight recall 返回值。"""
    if results is None:
        return None
    return {"results": results, "trace": trace or _TRACE_WITH_SCORES}


# ========== 测试消息常量 ==========
# 必须 > 10 字符且不以操作型前缀开头，才能通过 turn_gate 的 skip_pre_llm_call
_GENERIC = "请问什么是K8s的架构体系"       # 12 chars, 含"什么是"→generic
_TASK_MSG = "帮我查一下 litellm 的配置"      # task: 含 litellm
_PERSONAL = "as we discussed earlier"       # personal: 含 as we discussed


# ========== 夹具 ==========


@pytest.fixture(autouse=True)
def _reset_globals() -> None:
    """每个测试前重置模块级全局状态。"""
    import knowledge_navigation.core.circuit_breaker as cb
    cb._circuit_failures = 0
    cb._circuit_open_until = 0.0
    cb._circuit_failure_types.clear()
    nav_hooks._injected_ids.clear()
    nav_hooks._hit_counter = nav_hooks._HitCounter()
    nav_hooks._task_tracker = nav_hooks._TaskTracker()
    nav_hooks._compaction = nav_hooks._CompactionTracker()
    nav_hooks._eval_queries = None


# ========== 场景 1: 意图门控 ==========


class TestRouterGating:
    """LLM Router 三路 mask 门控验证。"""

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall")
    @patch("knowledge_navigation.core.hooks._do_skill_match")
    def test_router_only_skill_skips_hs_kt(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """Router {h:0, kt:0, s:1} → HS+KT 不被调用，只跑 skill matcher。"""
        sk.return_value = ""
        with patch.object(nav_hooks, "_router_route", return_value={"h": False, "kt": False, "s": True}):
            pre_llm_call("s1", _TASK_MSG, platform="cli")
        hs.assert_not_called()
        kt.assert_not_called()
        sk.assert_called_once()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall")
    @patch("knowledge_navigation.core.hooks._do_skill_match")
    def test_router_only_skill_returns_only_skill(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """Router {h:0, kt:0, s:1} + skill 匹配 → 返回 skill + system_state。"""
        sk.return_value = "<auto_loaded_skills>\nskill-a\n</auto_loaded_skills>"
        with patch.object(nav_hooks, "_router_route", return_value={"h": False, "kt": False, "s": True}):
            result = pre_llm_call("s2", _TASK_MSG, platform="cli")
        assert result is not None
        assert "<auto_loaded_skills>" in result
        assert "<recalled_memory>" not in result
        assert "<knowledge" not in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall")
    @patch("knowledge_navigation.core.hooks._do_skill_match")
    def test_router_all_off_returns_none(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """Router 全关闭 + 无 skill → None。"""
        sk.return_value = ""
        with patch.object(nav_hooks, "_router_route", return_value={"h": False, "kt": False, "s": False}):
            assert pre_llm_call("s3", _TASK_MSG, platform="cli") is None
        hs.assert_not_called()
        kt.assert_not_called()
        sk.assert_not_called()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall")
    @patch("knowledge_navigation.core.hooks._do_skill_match")
    def test_router_full_on_triggers_all(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """Router 全开 → HS+KT+Skill 全部触发。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "m"}])
        kt.return_value = []
        sk.return_value = ""
        with patch.object(nav_hooks, "_router_route", return_value={"h": True, "kt": True, "s": True}):
            result = pre_llm_call("s4", _TASK_MSG, platform="cli")
        assert result is not None
        hs.assert_called_once()
        kt.assert_called_once()
        sk.assert_called_once()

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall")
    @patch("knowledge_navigation.core.hooks._do_skill_match")
    def test_router_only_h_triggers_recall(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """Router {h:1, kt:0, s:0} → 只触发 Hindsight recall。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "m"}])
        kt.return_value = []
        sk.return_value = ""
        with patch.object(nav_hooks, "_router_route", return_value={"h": True, "kt": False, "s": False}):
            result = pre_llm_call("s5", _TASK_MSG, platform="cli")
        assert result is not None
        hs.assert_called_once()
        kt.assert_not_called()
        sk.assert_not_called()


# ========== 场景 2: XML 输出格式 ==========


class TestXmlOutput:
    """标签化输出结构验证。"""

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_opens_with_user_query(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """输出以 <user_query> 开头。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "m"}])
        result = pre_llm_call("s10", _TASK_MSG, platform="cli")
        assert result is not None
        assert result.strip().startswith("<user_query>")
        assert _TASK_MSG in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_recalled_memory_block(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """HS 块含 count/score_avg/memory 子元素。"""
        hs.return_value = _mock_recall(results=[
            {"id": "hs-aaa", "text": "记忆一"},
            {"id": "hs-bbb", "text": "记忆二"},
        ])
        result = pre_llm_call("s11", _TASK_MSG, platform="cli")
        assert result is not None
        assert '<recalled_memory source="hindsight"' in result
        assert 'count="2"' in result
        assert 'score_avg="' in result
        assert '<memory source="hindsight" node_id="hs-aaa">' in result
        assert '<memory source="hindsight" node_id="hs-bbb">' in result
        assert "</recalled_memory>" in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_knowledge_block(self, sk: MagicMock, hs: MagicMock) -> None:
        """KT 结果被 <knowledge> 包裹，独立于 HS 块。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "HS 记忆"}])
        with patch.object(nav_hooks, "HAS_KNOWLEDGE_TREE", True):
            with patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=_KT_RESULTS):
                result = pre_llm_call("s12", _TASK_MSG, platform="cli")
        assert result is not None
        assert '<knowledge source="knowledge_tree"' in result
        assert '<memory source="knowledge_tree" node_id="101">' in result
        assert "</knowledge>" in result
        # HS 块在 KT 块之前
        assert result.index("<recalled_memory") < result.index("<knowledge")

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_system_state_block(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """system_state 含 pwd/time，是最后一个标签块。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "m"}])
        result = pre_llm_call("s13", _TASK_MSG, platform="cli")
        assert result is not None
        assert "<system_state>" in result
        assert "pwd:" in result
        assert "time:" in result
        assert "</system_state>" in result
        # 不含 skill 时 system_state 是最后一个块
        last_tag_close = max(result.rfind(f"</{t}>") for t in ["user_query", "recalled_memory", "system_state"])
        assert result.rfind("</system_state>") == last_tag_close

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match")
    def test_block_order(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """多个来源共存时严格顺序：user_query → recalled_memory → system_state → auto_loaded_skills。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "m"}])
        sk.return_value = "<auto_loaded_skills>\nskill\n</auto_loaded_skills>"
        result = pre_llm_call("s15", _TASK_MSG, platform="cli")
        assert result is not None
        tags = ["<user_query>", "<recalled_memory source=", "<system_state>", "<auto_loaded_skills>"]
        positions = [result.find(t) for t in tags]
        assert all(p >= 0 for p in positions), f"缺失标签: {[t for t, p in zip(tags, positions) if p < 0]}"
        for i in range(len(positions) - 1):
            assert positions[i] < positions[i + 1], f"{tags[i]} 在 {tags[i+1]} 之后"

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_tags_balanced(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """标签成对闭合，开闭数一致。"""
        hs.return_value = _mock_recall(results=[
            {"id": "hs-aaa", "text": "a"},
            {"id": "hs-bbb", "text": "b"},
        ])
        result = pre_llm_call("s16", _TASK_MSG, platform="cli")
        assert result is not None
        for tag in ["user_query", "recalled_memory", "system_state"]:
            opens = result.count(f"<{tag}>") + result.count(f'<{tag} ')
            closes = result.count(f"</{tag}>")
            assert opens == closes, f"{tag}: 开({opens}) ≠ 闭({closes})"


# ========== 场景 3: 边界 ==========


class TestEdgeCases:

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_only_kt_no_hs_block(self, sk: MagicMock, hs: MagicMock) -> None:
        """HS 空 → 无 <recalled_memory>，仅有 <knowledge>。"""
        hs.return_value = _mock_recall(results=[])
        with patch.object(nav_hooks, "HAS_KNOWLEDGE_TREE", True):
            with patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=_KT_RESULTS):
                result = pre_llm_call("s30", _TASK_MSG, platform="cli")
        assert result is not None
        assert "<recalled_memory>" not in result
        assert '<knowledge source="knowledge_tree"' in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_only_hs_no_kt_block(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """KT 空 → 无 <knowledge>。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "m"}])
        result = pre_llm_call("s31", _TASK_MSG, platform="cli")
        assert result is not None
        assert '<recalled_memory source="hindsight"' in result
        assert "<knowledge>" not in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match", return_value="")
    def test_xss_escaped(self, sk: MagicMock, kt: MagicMock, hs: MagicMock) -> None:
        """特殊字符被 html.escape。node_id 和 text 都在 trace 中有对应记录才能通过分数过滤。"""
        hs.return_value = _mock_recall(
            results=[{"id": 'hs-<xxx>', "text": 'Memory with "quotes" & <brackets>'}],
            trace=_TRACE_WITH_XSS,
        )
        result = pre_llm_call("s32", _TASK_MSG, platform="cli")
        assert result is not None
        # 原始尖括号和 & 不应出现（必转义）
        assert '<brackets>' not in result
        # HTML 转义存在：< → &lt;, > → &gt;, & → &amp;
        assert "&amp;" in result
        # node_id 中的尖括号被转义（quote=True 转义引号+尖括号）
        assert "hs-&lt;xxx&gt;" in result
        # text 中的双引号不转义（quote=False），这是 html.escape 的正确行为

    def test_router_mask_applied_correctly(self) -> None:
        """Router mask 正确应用到 recall 路径。"""
        with patch.object(nav_hooks, "_router_route", return_value={"h": True, "kt": False, "s": False}):
            with patch.object(nav_hooks, "_do_hindsight_recall") as mock_hs:
                mock_hs.return_value = _mock_recall(
                    results=[{"id": "hs-aaa", "text": "memory a"}],
                )
                with patch.object(nav_hooks, "_do_skill_match", return_value=""):
                    result = pre_llm_call("s-router", _TASK_MSG, platform="cli")
        assert result is not None
        assert "recalled_memory" in result
        assert "<knowledge" not in result
        assert "auto_loaded_skills" not in result

    @patch("knowledge_navigation.core.hooks._do_hindsight_recall")
    @patch("knowledge_navigation.core.hooks._do_kt_recall", return_value=[])
    @patch("knowledge_navigation.core.hooks._do_skill_match")
    def test_skills_appended_after_system_state(
        self, sk: MagicMock, kt: MagicMock, hs: MagicMock
    ) -> None:
        """<auto_loaded_skills> 在 <system_state> 之后（追加逻辑）。"""
        hs.return_value = _mock_recall(results=[{"id": "hs-aaa", "text": "m"}])
        sk.return_value = "<auto_loaded_skills>\nskill\n</auto_loaded_skills>"
        result = pre_llm_call("s33", _TASK_MSG, platform="cli")
        assert result is not None
        assert result.find("<auto_loaded_skills>") > result.find("</system_state>")