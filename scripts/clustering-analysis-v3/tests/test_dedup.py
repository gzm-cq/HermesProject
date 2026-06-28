"""记忆去重模块测试"""

from datetime import datetime, timedelta

import pytest

from clustering_analysis.core.dedup import (
    HAS_DATASKETCH,
    _bigrams,
    _jaccard,
    dedup_memories,
    jaccard_dedup,
    minhash_dedup,
)


class TestBigrams:
    """测试字符 bigram 生成"""

    def test_normal_text(self) -> None:
        result = _bigrams("hello world")
        assert isinstance(result, set)
        assert "he" in result
        assert "el" in result
        assert "ld" in result

    def test_single_char(self) -> None:
        result = _bigrams("a")
        assert result == {"a"}

    def test_empty_string(self) -> None:
        result = _bigrams("")
        assert result == set()

    def test_chinese_text(self) -> None:
        result = _bigrams("测试文本")
        assert "测试" in result
        assert "试文" in result
        assert "文本" in result


class TestJaccard:
    """测试 Jaccard 相似度计算"""

    def test_identical_sets(self) -> None:
        set_a = {"a", "b", "c"}
        set_b = {"a", "b", "c"}
        assert _jaccard(set_a, set_b) == 1.0

    def test_no_overlap(self) -> None:
        set_a = {"a", "b"}
        set_b = {"c", "d"}
        assert _jaccard(set_a, set_b) == 0.0

    def test_partial_overlap(self) -> None:
        set_a = {"a", "b", "c"}
        set_b = {"b", "c", "d"}
        assert _jaccard(set_a, set_b) == 0.5

    def test_empty_sets(self) -> None:
        assert _jaccard(set(), set()) == 0.0
        assert _jaccard({"a"}, set()) == 0.0
        assert _jaccard(set(), {"a"}) == 0.0


class TestJaccardDedup:
    """测试 Jaccard 去重"""

    def test_no_duplicates(self) -> None:
        memories = [
            {"id": 1, "text": "完全不同的内容一", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "完全不同的内容二", "created_at": datetime(2024, 1, 2)},
            {"id": 3, "text": "完全不同的内容三", "created_at": datetime(2024, 1, 3)},
        ]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 3
        assert removed == 0

    def test_exact_duplicate(self) -> None:
        memories = [
            {"id": 1, "text": "这是一段测试文本内容", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "这是一段测试文本内容", "created_at": datetime(2024, 1, 2)},
        ]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 1
        assert removed == 1
        assert result[0]["id"] == 1

    def test_keeps_earliest(self) -> None:
        memories = [
            {"id": 2, "text": "这是一段测试文本内容", "created_at": datetime(2024, 1, 2)},
            {"id": 1, "text": "这是一段测试文本内容", "created_at": datetime(2024, 1, 1)},
        ]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_high_similarity(self) -> None:
        memories = [
            {"id": 1, "text": "这是一段测试文本内容用于去重检测", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "这是一段测试文本内容用于去重检测！", "created_at": datetime(2024, 1, 2)},
        ]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 1
        assert removed == 1

    def test_low_similarity_not_removed(self) -> None:
        memories = [
            {"id": 1, "text": "这是第一段完全不同的文本内容", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "那是第二段毫无关系的文字描述", "created_at": datetime(2024, 1, 2)},
        ]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 2
        assert removed == 0

    def test_empty_input(self) -> None:
        result, removed = jaccard_dedup([], threshold=0.85)
        assert result == []
        assert removed == 0

    def test_single_input(self) -> None:
        memories = [{"id": 1, "text": "单条记忆", "created_at": datetime(2024, 1, 1)}]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 1
        assert removed == 0

    def test_multiple_duplicates(self) -> None:
        base_text = "这是一段用于测试多重复制的基础文本内容"
        memories = [
            {"id": i, "text": base_text, "created_at": datetime(2024, 1, i)}
            for i in range(1, 6)
        ]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 1
        assert removed == 4
        assert result[0]["id"] == 1

    def test_none_created_at(self) -> None:
        memories = [
            {"id": 1, "text": "相同文本内容", "created_at": None},
            {"id": 2, "text": "相同文本内容", "created_at": datetime(2024, 1, 1)},
        ]
        result, removed = jaccard_dedup(memories, threshold=0.85)
        assert len(result) == 1
        assert removed == 1


@pytest.mark.skipif(not HAS_DATASKETCH, reason="datasketch not installed")
class TestMinHashDedup:
    """测试 MinHash 去重"""

    def test_no_duplicates(self) -> None:
        memories = [
            {"id": 1, "text": "完全不同的内容一", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "完全不同的内容二", "created_at": datetime(2024, 1, 2)},
            {"id": 3, "text": "完全不同的内容三", "created_at": datetime(2024, 1, 3)},
        ]
        result, removed = minhash_dedup(memories, threshold=0.85)
        assert len(result) == 3
        assert removed == 0

    def test_exact_duplicate(self) -> None:
        memories = [
            {"id": 1, "text": "这是一段测试文本内容用于去重检测", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "这是一段测试文本内容用于去重检测", "created_at": datetime(2024, 1, 2)},
        ]
        result, removed = minhash_dedup(memories, threshold=0.85, num_perm=256)
        assert len(result) == 1
        assert removed == 1
        assert result[0]["id"] == 1

    def test_keeps_earliest(self) -> None:
        memories = [
            {"id": 2, "text": "这是一段测试文本内容用于去重检测", "created_at": datetime(2024, 1, 2)},
            {"id": 1, "text": "这是一段测试文本内容用于去重检测", "created_at": datetime(2024, 1, 1)},
        ]
        result, removed = minhash_dedup(memories, threshold=0.85, num_perm=256)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_empty_input(self) -> None:
        result, removed = minhash_dedup([], threshold=0.85)
        assert result == []
        assert removed == 0

    def test_single_input(self) -> None:
        memories = [{"id": 1, "text": "单条记忆", "created_at": datetime(2024, 1, 1)}]
        result, removed = minhash_dedup(memories, threshold=0.85)
        assert len(result) == 1
        assert removed == 0

    def test_multiple_duplicates(self) -> None:
        base_text = "这是一段用于测试多重复制的基础文本内容，足够长以确保 MinHash 精度"
        memories = [
            {"id": i, "text": base_text, "created_at": datetime(2024, 1, i)}
            for i in range(1, 6)
        ]
        result, removed = minhash_dedup(memories, threshold=0.85, num_perm=256)
        assert len(result) == 1
        assert removed == 4
        assert result[0]["id"] == 1


class TestDedupMemoriesUnified:
    """测试统一去重入口"""

    def test_returns_method_name(self) -> None:
        memories = [
            {"id": 1, "text": "测试文本一", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "测试文本二", "created_at": datetime(2024, 1, 2)},
        ]
        result, removed, method = dedup_memories(memories, use_minhash=False)
        assert method == "jaccard"
        assert len(result) == 2
        assert removed == 0

    def test_minhash_flag_disabled(self) -> None:
        memories = [
            {"id": 1, "text": "测试文本", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "测试文本", "created_at": datetime(2024, 1, 2)},
        ]
        result, removed, method = dedup_memories(memories, use_minhash=False, threshold=0.85)
        assert method == "jaccard"
        assert len(result) == 1
        assert removed == 1

    def test_minhash_flag_enabled(self) -> None:
        memories = [
            {"id": 1, "text": "测试文本内容用于去重", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "测试文本内容用于去重", "created_at": datetime(2024, 1, 2)},
        ]
        result, removed, method = dedup_memories(memories, use_minhash=True, threshold=0.85)
        if HAS_DATASKETCH:
            assert method == "minhash"
        else:
            assert method == "jaccard(fallback)"
        assert len(result) == 1
        assert removed == 1


@pytest.mark.skipif(not HAS_DATASKETCH, reason="datasketch not installed")
class TestMinHashJaccardConsistency:
    """测试 MinHash 与 Jaccard 结果的一致性（近似）"""

    def test_exact_duplicates_consistent(self) -> None:
        base_text = "这是一段完全相同的文本内容，用于测试 MinHash 和 Jaccard 的一致性"
        memories = [
            {"id": i, "text": base_text, "created_at": datetime(2024, 1, i)}
            for i in range(1, 11)
        ]

        j_result, j_removed = jaccard_dedup(memories, threshold=0.9)
        m_result, m_removed = minhash_dedup(memories, threshold=0.9, num_perm=256)

        assert j_removed == 9
        assert m_removed == 9
        assert len(j_result) == 1
        assert len(m_result) == 1

    def test_high_similarity_consistent(self) -> None:
        text1 = "这是一段用于测试相似度的基础文本内容，包含足够多的信息以确保稳定性"
        text2 = "这是一段用于测试相似度的基础文本内容，包含足够多的信息以确保稳定性。"
        memories = [
            {"id": 1, "text": text1, "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": text2, "created_at": datetime(2024, 1, 2)},
        ]

        j_result, j_removed = jaccard_dedup(memories, threshold=0.8)
        m_result, m_removed = minhash_dedup(memories, threshold=0.8, num_perm=256)

        assert j_removed == 1
        assert m_removed == 1

    def test_different_texts_consistent(self) -> None:
        memories = [
            {"id": 1, "text": "完全不同的第一段文本内容，讲述服务器性能优化", "created_at": datetime(2024, 1, 1)},
            {"id": 2, "text": "毫无关系的第二段文字描述，关于数据库索引设计", "created_at": datetime(2024, 1, 2)},
        ]

        j_result, j_removed = jaccard_dedup(memories, threshold=0.85)
        m_result, m_removed = minhash_dedup(memories, threshold=0.85, num_perm=256)

        assert j_removed == 0
        assert m_removed == 0


class TestDedupConfig:
    """测试去重配置项"""

    def test_default_config_values(self) -> None:
        from clustering_analysis.config import AppConfig

        cfg = AppConfig()
        assert cfg.dedup_use_minhash is True
        assert cfg.dedup_minhash_threshold == 0.85
        assert cfg.dedup_minhash_num_perm == 128

    def test_from_dict_overrides(self) -> None:
        from clustering_analysis.config import AppConfig

        cfg = AppConfig.from_dict({
            "dedup_use_minhash": False,
            "dedup_minhash_threshold": 0.9,
            "dedup_minhash_num_perm": 256,
        })
        assert cfg.dedup_use_minhash is False
        assert cfg.dedup_minhash_threshold == 0.9
        assert cfg.dedup_minhash_num_perm == 256

    def test_env_override_bool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clustering_analysis.config import load_config

        monkeypatch.setenv("CLUSTERING_DEDUP_USE_MINHASH", "false")
        config = load_config("nonexistent.yaml")
        assert config["dedup_use_minhash"] is False

    def test_env_override_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clustering_analysis.config import load_config

        monkeypatch.setenv("CLUSTERING_DEDUP_MINHASH_NUM_PERM", "256")
        config = load_config("nonexistent.yaml")
        assert config["dedup_minhash_num_perm"] == 256

    def test_env_override_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clustering_analysis.config import load_config

        monkeypatch.setenv("CLUSTERING_DEDUP_MINHASH_THRESHOLD", "0.9")
        config = load_config("nonexistent.yaml")
        assert config["dedup_minhash_threshold"] == 0.9
