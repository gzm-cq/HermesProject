"""aiicons.py 单元测试 — AI 品牌图标库"""

import pytest

from drawio_generator.aiicons import (
    AIICONS,
    ICON_CATEGORIES,
    list_icons,
    get_icon,
    search_icon,
    summary,
)


# ===== list_icons =====

class TestListIcons:
    """测试 list_icons 函数"""

    def test_default_returns_nonempty_dict(self):
        result = list_icons()
        assert isinstance(result, dict)
        assert len(result) > 0
        assert len(result) == len(AIICONS)

    def test_default_returns_deepcopy(self):
        result = list_icons()
        first_key = next(iter(result))
        original_name = AIICONS[first_key]["name"]
        result[first_key]["name"] = "__MODIFIED__"
        result[first_key]["aliases"].append("__HACK__")
        assert AIICONS[first_key]["name"] == original_name
        assert "__HACK__" not in AIICONS[first_key]["aliases"]

    def test_filter_by_category(self):
        result = list_icons(category="llm")
        assert len(result) > 0
        for key, info in result.items():
            assert info["category"] == "llm"

    def test_filter_by_category_data(self):
        result = list_icons(category="data")
        assert len(result) > 0
        for key, info in result.items():
            assert info["category"] == "data"

    def test_filter_by_unknown_category_returns_empty(self):
        result = list_icons(category="not_a_real_category")
        assert result == {}

    def test_all_categories_filterable(self):
        # 每个类别都应能筛出非空结果
        for cat in ICON_CATEGORIES:
            result = list_icons(category=cat)
            assert len(result) > 0, f"category {cat} should have icons"


# ===== get_icon =====

class TestGetIcon:
    """测试 get_icon 函数"""

    def test_known_openai(self):
        info = get_icon("openai")
        assert info is not None
        assert "OpenAI" in info["name"]
        assert info["category"] == "llm"
        assert "color" in info
        assert "aliases" in info

    def test_known_pytorch(self):
        info = get_icon("pytorch")
        assert info is not None
        assert "PyTorch" in info["name"]
        assert info["category"] == "framework"

    def test_unknown_returns_none(self):
        assert get_icon("totally_unknown_brand_xyz") is None

    def test_returns_deepcopy(self):
        info = get_icon("openai")
        assert info is not None
        original_name = AIICONS["openai"]["name"]
        original_alias_len = len(AIICONS["openai"]["aliases"])
        info["name"] = "__MODIFIED__"
        info["aliases"].append("__HACK__")
        assert AIICONS["openai"]["name"] == original_name
        assert len(AIICONS["openai"]["aliases"]) == original_alias_len
        # 再次取值应为原始内容
        info2 = get_icon("openai")
        assert info2["name"] == original_name
        assert "__HACK__" not in info2["aliases"]

    def test_returned_dict_has_all_fields(self):
        info = get_icon("openai")
        for field in ("name", "aliases", "category", "color", "drawio_image_url", "logo_svg"):
            assert field in info, f"missing field: {field}"


# ===== search_icon =====

class TestSearchIcon:
    """测试 search_icon 函数"""

    def test_returns_list_of_tuples(self):
        result = search_icon("openai")
        assert isinstance(result, list)
        if result:
            first = result[0]
            assert isinstance(first, tuple)
            assert len(first) == 3  # (key, info, score)

    def test_english_search_openai(self):
        result = search_icon("openai")
        assert len(result) > 0
        matched_keys = [item[0] for item in result]
        assert "openai" in matched_keys
        # 精确匹配 key 应排在最前
        assert result[0][0] == "openai"

    def test_english_search_alias(self):
        # gpt 是 openai 的别名
        result = search_icon("gpt")
        assert len(result) > 0
        matched_keys = [item[0] for item in result]
        assert "openai" in matched_keys

    def test_chinese_search_aliyun(self):
        # 阿里云 aliyun 别名含 "阿里"
        result = search_icon("阿里")
        assert len(result) > 0
        matched_keys = [item[0] for item in result]
        assert "aliyun" in matched_keys

    def test_chinese_search_vector_db(self):
        # 向量数据库 milvus 别名含 "向量数据库"
        result = search_icon("向量库")
        assert len(result) > 0
        matched_keys = [item[0] for item in result]
        assert "milvus" in matched_keys

    def test_limit_parameter(self):
        full = search_icon("ai")
        limited = search_icon("ai", limit=2)
        assert len(limited) <= 2
        if len(full) > 2:
            assert len(limited) == 2

    def test_limit_one_returns_at_most_one(self):
        result = search_icon("openai", limit=1)
        assert len(result) <= 1

    def test_empty_query_returns_empty(self):
        assert search_icon("") == []

    def test_no_result_returns_empty(self):
        # 全 z 字符串：token 不命中任何 alias/name/key，相似度极低
        result = search_icon("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
        assert result == []

    def test_result_info_is_dict_with_fields(self):
        result = search_icon("openai")
        if result:
            _, info, score = result[0]
            assert isinstance(info, dict)
            assert isinstance(score, float)
            assert "name" in info


# ===== summary =====

class TestSummary:
    """测试 summary 函数"""

    def test_returns_dict(self):
        s = summary()
        assert isinstance(s, dict)

    def test_has_reasonable_fields(self):
        s = summary()
        for field in ("total", "categories", "per_category"):
            assert field in s, f"missing field: {field}"

    def test_total_matches_library(self):
        s = summary()
        assert s["total"] == len(AIICONS)

    def test_categories_match_global(self):
        s = summary()
        assert s["categories"] == ICON_CATEGORIES

    def test_per_category_sums_to_total(self):
        s = summary()
        assert sum(s["per_category"].values()) == s["total"]

    def test_per_category_keys_match_categories(self):
        s = summary()
        assert set(s["per_category"].keys()) == set(s["categories"])
