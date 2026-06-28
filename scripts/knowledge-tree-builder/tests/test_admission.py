"""测试 admission 模块"""

from knowledge_tree_builder.core.admission import filter_knowledge_points, _is_vague


class TestFilterKnowledgePoints:
    """测试知识准入规则过滤"""

    def test_too_short_dropped(self) -> None:
        """规则1：太短的丢弃"""
        result = filter_knowledge_points(["短"], min_length=10)
        assert len(result) == 0

    def test_vague_header_dropped(self) -> None:
        """规则2：以"本文"开头的丢弃"""
        result = filter_knowledge_points(
            ["本文讨论了SE-Agent的进化机制", "这是一个具体的技术知识点"]
        )
        assert len(result) == 1
        assert result[0] == "这是一个具体的技术知识点"

    def test_vague_verb_dropped(self) -> None:
        """规则3：包含模糊概括动词的丢弃"""
        result = filter_knowledge_points(["文章介绍了HDBSCAN聚类算法的原理"])
        assert len(result) == 0

    def test_valid_point_kept(self) -> None:
        """合法的知识点应该保留"""
        result = filter_knowledge_points(
            ["SE-Agent通过Revision算子分析失败轨迹来修改代码实现"]
        )
        assert len(result) == 1


class TestIsVague:
    """测试模糊判断逻辑"""

    def test_contains_number_specific(self) -> None:
        """包含数字 => 具体"""
        assert not _is_vague("三个进化算子分别负责不同的功能")

    def test_contains_technical_term_specific(self) -> None:
        """包含技术术语 => 具体"""
        assert not _is_vague("HDBSCAN算法通过密度可达性聚类")

    def test_abstract_pattern_vague(self) -> None:
        """抽象句式 => 笼统"""
        assert _is_vague("该方法是对传统聚类方法的一种改进")
