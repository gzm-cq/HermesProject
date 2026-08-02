"""shape_library.py 单元测试 — 形状索引 + 模糊搜索"""

import pytest

from drawio_generator.shape_library import (
    SHAPE_LIBRARY,
    CATEGORIES,
    list_shapes,
    get_shape,
    search_shape,
    shape_to_drawio_style,
    shape_svg_supported,
    summary,
)


# ===== list_shapes =====

class TestListShapes:
    """测试 list_shapes 函数"""

    def test_default_returns_nonempty_dict(self):
        result = list_shapes()
        assert isinstance(result, dict)
        assert len(result) > 0
        # 默认应返回全部形状
        assert len(result) == len(SHAPE_LIBRARY)

    def test_default_returns_deepcopy(self):
        result = list_shapes()
        # 修改返回值不应污染全局
        first_key = next(iter(result))
        original_name = SHAPE_LIBRARY[first_key]["name"]
        result[first_key]["name"] = "__MODIFIED__"
        result[first_key]["keywords"].append("__HACK__")
        assert SHAPE_LIBRARY[first_key]["name"] == original_name
        assert "__HACK__" not in SHAPE_LIBRARY[first_key]["keywords"]

    def test_filter_by_category(self):
        result = list_shapes(category="basic")
        assert len(result) > 0
        for sid, info in result.items():
            assert info["category"] == "basic"

    def test_filter_by_category_data(self):
        result = list_shapes(category="data")
        assert len(result) > 0
        for sid, info in result.items():
            assert info["category"] == "data"

    def test_filter_by_unknown_category_returns_empty(self):
        result = list_shapes(category="not_a_real_category")
        assert result == {}

    def test_only_svg_filter(self):
        result = list_shapes(only_svg=True)
        assert len(result) > 0
        for sid, info in result.items():
            assert info["svg_render"] is True

    def test_only_svg_excludes_non_svg(self):
        all_shapes = list_shapes()
        svg_shapes = list_shapes(only_svg=True)
        # only_svg 结果应少于全部
        assert len(svg_shapes) <= len(all_shapes)
        # 存在 svg_render=False 的形状时，svg 结果应严格更少
        has_non_svg = any(not v["svg_render"] for v in SHAPE_LIBRARY.values())
        if has_non_svg:
            assert len(svg_shapes) < len(all_shapes)

    def test_category_and_only_svg_combined(self):
        result = list_shapes(category="flowchart", only_svg=True)
        for sid, info in result.items():
            assert info["category"] == "flowchart"
            assert info["svg_render"] is True


# ===== get_shape =====

class TestGetShape:
    """测试 get_shape 函数"""

    def test_known_rect(self):
        info = get_shape("rect")
        assert info is not None
        assert info["shape"] == "rect"
        assert info["category"] == "basic"
        assert "drawio" in info
        assert "keywords" in info

    def test_known_cylinder(self):
        info = get_shape("cylinder")
        assert info is not None
        assert info["name"] == "数据库"
        assert info["category"] == "data"

    def test_unknown_returns_none(self):
        assert get_shape("totally_unknown_shape_xyz") is None

    def test_returns_deepcopy(self):
        info = get_shape("rect")
        assert info is not None
        original_name = SHAPE_LIBRARY["rect"]["name"]
        original_kw_len = len(SHAPE_LIBRARY["rect"]["keywords"])
        info["name"] = "__MODIFIED__"
        info["keywords"].append("__HACK__")
        # 全局不应被修改
        assert SHAPE_LIBRARY["rect"]["name"] == original_name
        assert len(SHAPE_LIBRARY["rect"]["keywords"]) == original_kw_len
        # 再次取值应为原始内容
        info2 = get_shape("rect")
        assert info2["name"] == original_name
        assert "__HACK__" not in info2["keywords"]

    def test_returned_dict_has_all_fields(self):
        info = get_shape("rect")
        for field in ("shape", "name", "keywords", "category", "drawio", "svg_render"):
            assert field in info, f"missing field: {field}"


# ===== search_shape =====

class TestSearchShape:
    """测试 search_shape 函数"""

    def test_returns_list_of_tuples(self):
        result = search_shape("rect")
        assert isinstance(result, list)
        if result:
            first = result[0]
            assert isinstance(first, tuple)
            assert len(first) == 3  # (sid, info, score)

    def test_chinese_search_database_matches_cylinder(self):
        result = search_shape("数据库")
        assert len(result) > 0
        matched_ids = [item[0] for item in result]
        assert "cylinder" in matched_ids

    def test_english_search_box_matches_rect(self):
        result = search_shape("box")
        assert len(result) > 0
        matched_ids = [item[0] for item in result]
        assert "rect" in matched_ids

    def test_exact_shape_id_match(self):
        result = search_shape("rect")
        assert len(result) > 0
        # 精确匹配 shape_id 应排在最前
        assert result[0][0] == "rect"

    def test_limit_parameter(self):
        full = search_shape("node")
        limited = search_shape("node", limit=2)
        assert len(limited) <= 2
        if len(full) > 2:
            assert len(limited) == 2

    def test_limit_one_returns_at_most_one(self):
        result = search_shape("rect", limit=1)
        assert len(result) <= 1

    def test_empty_query_returns_empty(self):
        assert search_shape("") == []

    def test_no_result_returns_empty(self):
        # 全 z 字符串：所有 shape 的 name/keywords/sid 均不含 z，相似度极低
        result = search_shape("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
        assert result == []

    def test_result_info_is_dict(self):
        result = search_shape("rect")
        if result:
            _, info, score = result[0]
            assert isinstance(info, dict)
            assert isinstance(score, float)


# ===== shape_to_drawio_style =====

class TestShapeToDrawioStyle:
    """测试 shape_to_drawio_style 函数"""

    def test_known_rect(self):
        style = shape_to_drawio_style("rect")
        assert style == SHAPE_LIBRARY["rect"]["drawio"]

    def test_known_cylinder(self):
        style = shape_to_drawio_style("cylinder")
        assert "cylinder" in style
        assert "html=1" in style

    def test_unknown_returns_fallback(self):
        style = shape_to_drawio_style("totally_unknown_shape_xyz")
        # fallback 到 rect 默认 style
        assert isinstance(style, str)
        assert "html=1" in style

    def test_returns_nonempty_string(self):
        for sid in ("rect", "cylinder", "cloud", "unknown_x"):
            assert shape_to_drawio_style(sid) != ""


# ===== shape_svg_supported =====

class TestShapeSvgSupported:
    """测试 shape_svg_supported 函数"""

    def test_known_svg_supported_rect(self):
        assert shape_svg_supported("rect") is True

    def test_known_svg_supported_cylinder(self):
        assert shape_svg_supported("cylinder") is True

    def test_known_non_svg_terminator(self):
        # terminator 在 SHAPE_LIBRARY 中 svg_render=False
        assert shape_svg_supported("terminator") is False

    def test_unknown_returns_false(self):
        assert shape_svg_supported("totally_unknown_shape_xyz") is False


# ===== summary =====

class TestSummary:
    """测试 summary 函数"""

    def test_returns_dict(self):
        s = summary()
        assert isinstance(s, dict)

    def test_has_reasonable_fields(self):
        s = summary()
        for field in ("total", "svg_supported", "drawio_fallback", "categories", "per_category"):
            assert field in s, f"missing field: {field}"

    def test_total_matches_library(self):
        s = summary()
        assert s["total"] == len(SHAPE_LIBRARY)

    def test_svg_counts_consistent(self):
        s = summary()
        assert s["svg_supported"] + s["drawio_fallback"] == s["total"]

    def test_categories_match_global(self):
        s = summary()
        assert s["categories"] == CATEGORIES

    def test_per_category_sums_to_total(self):
        s = summary()
        assert sum(s["per_category"].values()) == s["total"]
