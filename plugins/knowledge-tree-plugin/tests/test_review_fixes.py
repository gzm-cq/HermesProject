"""Review 修复项的回归测试。

覆盖：
- P0: knowledge-tree-builder 路径定位器（环境变量 / 向上自定位 / 失败降级）
- P0: extract_new._USER_BUDGET_CHARS 未定义导致的 NameError
- P1: 多跳三路 SQL 已下沉到 adapter
- P2: attention_filter 单候选边界（softmax 恒为 1.0）
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest


# ---------- P0: kt_builder_path 定位器 ----------

class TestKtBuilderPath:
    def test_locates_builder_in_dev_repo(self):
        from knowledge_tree_plugin.kt_builder_path import locate_kt_builder_src

        src = locate_kt_builder_src()
        assert src is not None, "开发态应能向上自定位到 knowledge-tree-builder/src"
        assert (src / "knowledge_tree_builder" / "__init__.py").is_file()

    def test_env_var_takes_priority(self, monkeypatch, tmp_path):
        from knowledge_tree_plugin.kt_builder_path import locate_kt_builder_src

        fake = tmp_path / "src"
        (fake / "knowledge_tree_builder").mkdir(parents=True)
        (fake / "knowledge_tree_builder" / "__init__.py").write_text("")
        monkeypatch.setenv("KT_BUILDER_SRC", str(fake))

        assert locate_kt_builder_src() == fake

    def test_invalid_env_var_falls_back(self, monkeypatch, tmp_path):
        """环境变量指向无效目录时应降级到自定位，而不是直接失败。"""
        from knowledge_tree_plugin.kt_builder_path import locate_kt_builder_src

        monkeypatch.setenv("KT_BUILDER_SRC", str(tmp_path / "nonexistent"))
        src = locate_kt_builder_src()
        assert src is not None
        assert (src / "knowledge_tree_builder" / "__init__.py").is_file()

    def test_ensure_is_idempotent(self):
        from knowledge_tree_plugin.kt_builder_path import ensure_kt_builder_on_path

        ensure_kt_builder_on_path()
        before = len(sys.path)
        ensure_kt_builder_on_path()
        assert len(sys.path) == before, "重复调用不应重复注入 sys.path"

    def test_strict_raises_when_missing(self, monkeypatch):
        import knowledge_tree_plugin.kt_builder_path as m

        monkeypatch.setattr(m, "locate_kt_builder_src", lambda: None)
        with pytest.raises(ImportError, match="knowledge-tree-builder"):
            m.ensure_kt_builder_on_path(strict=True)

    def test_non_strict_returns_none_when_missing(self, monkeypatch):
        import knowledge_tree_plugin.kt_builder_path as m

        monkeypatch.setattr(m, "locate_kt_builder_src", lambda: None)
        assert m.ensure_kt_builder_on_path() is None


# ---------- P0: _USER_BUDGET_CHARS ----------

class TestUserBudgetChars:
    def test_constant_is_defined(self):
        from knowledge_tree_plugin import extract_new

        assert isinstance(extract_new._USER_BUDGET_CHARS, int)
        assert extract_new._USER_BUDGET_CHARS > 0

    def test_long_user_text_does_not_raise(self):
        """修复前此路径会抛 NameError，导致整条抽取链路崩溃。"""
        from knowledge_tree_plugin import extract_new

        budget = extract_new._USER_BUDGET_CHARS
        out = extract_new._build_dialog_text(
            "问" * (budget * 3),
            "答" * 2000,
            max_input_length=4000,
        )

        assert isinstance(out, str) and out
        assert "## 用户提问" in out and "## 回答" in out

    def test_user_part_is_capped_by_budget(self):
        """user 片段不应超过 _USER_BUDGET_CHARS 上限。"""
        from knowledge_tree_plugin import extract_new

        budget = extract_new._USER_BUDGET_CHARS
        out = extract_new._build_dialog_text(
            "问" * (budget * 5),
            "答" * 100,
            max_input_length=4000,
        )
        user_part = out.split("## 用户提问\n", 1)[1].split("\n\n## 回答", 1)[0]
        assert len(user_part) <= budget


# ---------- P1: 多跳 SQL 已下沉 adapter ----------

class TestMultiHopInAdapter:
    def test_adapter_exposes_multi_hop_methods(self):
        from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter

        for name in (
            "has_entity_links",
            "multi_hop_by_subject",
            "multi_hop_by_entity",
            "multi_hop_by_edge",
        ):
            assert callable(getattr(PluginDatabaseAdapter, name, None)), name

    def test_public_api_has_no_raw_sql_helpers(self):
        """三路裸 SQL 私有函数应已从 public_api 移除。"""
        from knowledge_tree_plugin import public_api

        for name in ("_strategy_subject", "_strategy_entity", "_strategy_edge"):
            assert not hasattr(public_api, name), f"{name} 应已迁出 public_api"

    def test_public_api_source_has_no_business_sql(self):
        """public_api 不应再出现业务裸 SQL。

        允许保留的例外：连接保活探针 ``SELECT 1``。
        文档字符串中提及表名不算 SQL。
        """
        src = Path(public_api_file()).read_text(encoding="utf-8")
        # 业务表访问必须走 adapter
        assert "FROM knowledge_tree" not in src
        assert "FROM kt_entity_links" not in src
        assert "JOIN " not in src
        # 除保活探针外不应有其他 execute
        executes = [
            ln.strip()
            for ln in src.splitlines()
            if "cursor.execute" in ln
        ]
        assert executes == ['adapter.cursor.execute("SELECT 1")'], executes

    def test_empty_seeds_short_circuit(self):
        from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter

        a = object.__new__(PluginDatabaseAdapter)
        a._inner = MagicMock()

        assert a.multi_hop_by_subject([], 10) == []
        assert a.multi_hop_by_entity([], 10) == []
        assert a.multi_hop_by_edge([], 10) == []
        a._inner.cursor.execute.assert_not_called()

    def test_entity_route_returns_empty_without_entities(self):
        from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter

        a = object.__new__(PluginDatabaseAdapter)
        a._inner = MagicMock()
        a._inner.cursor.fetchall.return_value = []

        assert a.multi_hop_by_entity([1, 2], 10) == []

    def test_edge_route_falls_back_to_vector_bridge(self):
        from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter

        a = object.__new__(PluginDatabaseAdapter)
        cur = MagicMock()
        a._inner = MagicMock()
        a._inner.cursor = cur
        # 第一次（预建边）为空，第二次（向量桥接）有结果
        cur.fetchall.side_effect = [[], [(7, "n", "t", 0.91)]]

        out = a.multi_hop_by_edge([1], 5)
        assert out == [{"id": 7, "name": "n", "text": "t", "score": 0.91}]
        assert cur.execute.call_count == 2

    def test_edge_route_is_bidirectional(self):
        """Route C 修复（2026-08-30）：边单向存储，跨域边全在反向。

        断言 SQL 同时含 fwd（from_seed）与 rev（to_seed）双向展开，
        且参数按 fwd/rev 各一组 seed 传 4 个。
        """
        from knowledge_tree_plugin.adapters.database import PluginDatabaseAdapter

        a = object.__new__(PluginDatabaseAdapter)
        cur = MagicMock()
        a._inner = MagicMock()
        a._inner.cursor = cur
        cur.fetchall.side_effect = [[(7, "n", "t", 2), (8, "m", "u", 1)], []]

        out = a.multi_hop_by_edge([1, 2], 5)
        sql = cur.execute.call_args_list[0][0][0]
        params = cur.execute.call_args_list[0][0][1]

        # 双向：fwd + rev 两个 CTE 均存在
        assert "fwd" in sql and "rev" in sql
        assert "to_node_id = ANY" in sql or "to_node_id=ANY" in sql.replace(" ", "")
        assert "UNION ALL" in sql
        # 参数：fwd seed, fwd 排除, rev seed, rev 排除 → 4 个
        assert params == ([1, 2], [1, 2], [1, 2], [1, 2], 5)
        # 得分 = min(1.0, cc/3.0)，cc=2 → 0.667
        assert out[0]["score"] == pytest.approx(2 / 3)


def public_api_file() -> str:
    from knowledge_tree_plugin import public_api

    return public_api.__file__


# ---------- P2: attention_filter 单候选边界 ----------

class TestAttentionFilterSingleCandidate:
    @staticmethod
    def _node(node_id: int, vec):
        return {"id": node_id, "name": f"n{node_id}", "k_vector": vec}

    def test_single_irrelevant_candidate_is_rejected(self):
        """单候选时 softmax 恒为 1.0，必须靠原始相似度守卫拦截不相关结果。"""
        from knowledge_tree_plugin.recall import attention_filter

        q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        opposite = np.array([-1.0, 0.0, 0.0], dtype=np.float32)

        out = attention_filter(q, [self._node(1, opposite.tolist())], cold_start=False)
        assert out == [], "完全反向的唯一候选不应被召回"

    def test_single_relevant_candidate_is_kept(self):
        from knowledge_tree_plugin.recall import attention_filter

        q = np.array([1.0, 0.0, 0.0], dtype=np.float32)

        out = attention_filter(q, [self._node(1, q.tolist())], cold_start=False)
        assert len(out) == 1
        assert out[0]["id"] == 1

    def test_empty_candidates(self):
        from knowledge_tree_plugin.recall import attention_filter

        q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        assert attention_filter(q, [], cold_start=False) == []
