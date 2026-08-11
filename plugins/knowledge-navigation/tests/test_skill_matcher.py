"""skill_matcher 单元测试。

覆盖：
- frontmatter 解析 / 剥离
- 索引懒加载与缓存
- 关键词提取与预筛选
- Embedding 预筛选（降级逻辑）
- LLM 驱动匹配（mock httpx）
- 三级匹配架构（关键词 + Embedding + LLM）
- 配置常量
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from knowledge_navigation.core.skill_matcher import (
    _TOP_K,
    _PRESCREEN_TOP_K,
    _parse_frontmatter,
    strip_frontmatter,
    ensure_index,
    match_skills,
    _extract_keywords,
    _keyword_prescreen,
)


# ====================================================================
# Frontmatter 解析
# ====================================================================


class TestParseFrontmatter:
    def test_valid_yaml(self) -> None:
        meta = _parse_frontmatter("---\nname: foo\ndescription: bar baz\n---\nbody")
        assert meta == {"name": "foo", "description": "bar baz"}

    def test_no_frontmatter(self) -> None:
        assert _parse_frontmatter("# just content") == {}

    def test_open_without_close(self) -> None:
        assert _parse_frontmatter("---\nname: foo\n") == {}

    def test_missing_name(self) -> None:
        meta = _parse_frontmatter("---\ndescription: only desc\n---\nbody")
        assert "description" in meta
        assert "name" not in meta

    def test_extra_fields_ignored(self) -> None:
        meta = _parse_frontmatter("---\nname: x\nversion: 1.0\ndescription: d\n---\nbody")
        assert meta == {"name": "x", "description": "d"}


# ====================================================================
# Frontmatter 剥离
# ====================================================================


class TestStripFrontmatter:
    def test_with_yaml(self) -> None:
        text = "---\nname: foo\ndescription: bar\n---\n\n# Hello\nbody here"
        assert strip_frontmatter(text) == "# Hello\nbody here"

    def test_no_yaml(self) -> None:
        assert strip_frontmatter("# Hello\nbody") == "# Hello\nbody"

    def test_no_close(self) -> None:
        assert strip_frontmatter("---\nname: foo\n") == "---\nname: foo\n"

    def test_empty(self) -> None:
        assert strip_frontmatter("") == ""

    def test_only_frontmatter(self) -> None:
        assert strip_frontmatter("---\nname: x\n---\n") == ""


# ====================================================================
# 索引
# ====================================================================


def _make_mock_skills(root: Path) -> None:
    """在临时目录下创建 mock SKILL.md 文件。"""
    skills = {
        "docker-patterns": "Docker deployment patterns and container orchestration",
        "lark-notify": "飞书消息通知发送",
        "git-workflow": "Git branching and workflow patterns",
    }
    for name, desc in skills.items():
        skill_dir = root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\ncontent for {name}\n",
            encoding="utf-8",
        )


def test_ensure_index_builds(tmp_path: Path) -> None:
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ok = ensure_index()
        assert ok is True
        sm._skill_index = None


def test_ensure_index_caches(tmp_path: Path) -> None:
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ensure_index()
        first_len = len(sm._skill_index)  # type: ignore[union-attr]
        ensure_index()  # 缓存命中
        second_len = len(sm._skill_index)  # type: ignore[union-attr]
        assert first_len == second_len
        sm._skill_index = None


def test_ensure_index_missing_dir() -> None:
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    with patch.object(sm, "SKILLS_HOME", Path("/nonexistent/path/xyz")):
        ok = ensure_index()
        assert ok is False
        sm._skill_index = None


def test_ensure_index_skips_no_frontmatter(tmp_path: Path) -> None:
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    skill_dir = tmp_path / "no-meta"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("just plain content\n", encoding="utf-8")
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ok = ensure_index()
        assert ok is False  # 无有效 skill
        sm._skill_index = None


# ====================================================================
# 索引增量更新 (P2-3)
# ====================================================================


def _set_config_incremental(enabled: bool) -> None:
    """设置 skill_index_incremental 配置。"""
    from knowledge_navigation.config import CONFIG
    CONFIG.skill_index_incremental = enabled


def test_incremental_first_call_full_build(tmp_path: Path) -> None:
    """测试首次调用全量构建索引。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _set_config_incremental(True)
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ok = ensure_index()
        assert ok is True
        assert isinstance(sm._skill_index, dict)
        assert len(sm._skill_index) == 3
        for skill_data in sm._skill_index.values():
            assert "mtime" in skill_data
            assert "name" in skill_data
            assert "description" in skill_data
            assert "path" in skill_data
            assert "category" in skill_data
        sm._skill_index = None


def test_incremental_second_call_no_change(tmp_path: Path) -> None:
    """测试第二次调用无变化时快速返回（不重新加载文件）。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _set_config_incremental(True)
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ensure_index()
        first_index = dict(sm._skill_index)  # 保存第一份索引的拷贝

        with patch.object(sm, "_load_skill_file") as mock_load:
            ensure_index()
            assert mock_load.call_count == 0  # 没有变化，不应调用加载函数

        assert len(sm._skill_index) == len(first_index)
        sm._skill_index = None


def test_incremental_add_new_file(tmp_path: Path) -> None:
    """测试新增文件时增量添加。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _set_config_incremental(True)
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ensure_index()
        initial_count = len(sm._skill_index)

        new_skill_dir = tmp_path / "new-skill"
        new_skill_dir.mkdir()
        (new_skill_dir / "SKILL.md").write_text(
            "---\nname: new-skill\ndescription: a brand new skill\n---\n\ncontent\n",
            encoding="utf-8",
        )
        import time
        time.sleep(0.01)

        ensure_index()
        assert len(sm._skill_index) == initial_count + 1

        new_skill_found = False
        for skill_data in sm._skill_index.values():
            if skill_data["name"] == "new-skill":
                new_skill_found = True
                assert skill_data["description"] == "a brand new skill"
                break
        assert new_skill_found
        sm._skill_index = None


def test_incremental_modify_file(tmp_path: Path) -> None:
    """测试修改文件时增量更新。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _set_config_incremental(True)
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ensure_index()

        docker_skill_path = None
        for path_str, skill_data in sm._skill_index.items():
            if skill_data["name"] == "docker-patterns":
                docker_skill_path = path_str
                break
        assert docker_skill_path is not None
        old_desc = sm._skill_index[docker_skill_path]["description"]

        import os
        import time
        time.sleep(0.01)
        with open(docker_skill_path, "w", encoding="utf-8") as f:
            f.write("---\nname: docker-patterns\ndescription: UPDATED docker description\n---\n\nupdated content\n")

        time.sleep(0.01)
        ensure_index()

        assert sm._skill_index[docker_skill_path]["description"] == "UPDATED docker description"
        assert sm._skill_index[docker_skill_path]["description"] != old_desc
        sm._skill_index = None


def test_incremental_delete_file(tmp_path: Path) -> None:
    """测试删除文件时索引移除。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _set_config_incremental(True)
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ensure_index()
        initial_count = len(sm._skill_index)

        import shutil
        import time
        time.sleep(0.01)
        docker_dir = tmp_path / "docker-patterns"
        shutil.rmtree(docker_dir)

        time.sleep(0.01)
        ensure_index()
        assert len(sm._skill_index) == initial_count - 1

        for skill_data in sm._skill_index.values():
            assert skill_data["name"] != "docker-patterns"
        sm._skill_index = None


def test_incremental_disabled_full_scan_every_time(tmp_path: Path) -> None:
    """测试关闭增量时行为不变（每次都全量扫描，但有缓存）。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _set_config_incremental(False)
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ensure_index()
        first_count = len(sm._skill_index)

        ensure_index()
        second_count = len(sm._skill_index)

        assert first_count == second_count
        sm._skill_index = None

    _set_config_incremental(True)


def test_rebuild_skill_index(tmp_path: Path) -> None:
    """测试 rebuild_skill_index 强制重建索引。"""
    from knowledge_navigation.core.skill_matcher import rebuild_skill_index
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    _set_config_incremental(True)
    _make_mock_skills(tmp_path)
    with patch.object(sm, "SKILLS_HOME", tmp_path):
        ensure_index()
        first_index = dict(sm._skill_index)

        ok = rebuild_skill_index()
        assert ok is True
        assert len(sm._skill_index) == len(first_index)
        assert isinstance(sm._skill_index, dict)
        sm._skill_index = None


# ====================================================================
# LLM 驱动匹配（mock httpx — 函数内 import，需 patch httpx 模块）
# ====================================================================


def _mock_llm_response(content: str) -> MagicMock:
    """构建 mock httpx 响应。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_match_skills_empty_query() -> None:
    assert match_skills("") == []
    assert match_skills("   ") == []


def test_match_skills_no_index() -> None:
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = None
    with patch.object(sm, "SKILLS_HOME", Path("/nonexistent/xyz")):
        assert match_skills("anything") == []
        sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_match_skills_llm_returns_names(mock_ensure: MagicMock) -> None:
    """LLM 返回 skill 名称列表 → match_skills 转为带 path 的 dict。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker stuff", "path": "/skills/docker/SKILL.md", "category": "ops"},
        {"name": "git-workflow", "description": "Git patterns", "path": "/skills/git/SKILL.md", "category": "dev"},
    ]

    with patch("httpx.post", return_value=_mock_llm_response('["docker-patterns", "git-workflow"]')):
        results = match_skills("deploy docker containers")

    assert len(results) == 2
    assert results[0]["name"] == "docker-patterns"
    assert results[0]["path"] == "/skills/docker/SKILL.md"
    assert "score" in results[0]
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_match_skills_llm_returns_empty_list(mock_ensure: MagicMock) -> None:
    """LLM 返回空数组 → 无匹配。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [{"name": "x", "description": "d", "path": "/p", "category": "c"}]

    with patch("httpx.post", return_value=_mock_llm_response("[]")):
        results = match_skills("unrelated query")

    assert results == []
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_match_skills_llm_error_returns_empty(mock_ensure: MagicMock) -> None:
    """LLM 调用异常 → 静默返回空。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [{"name": "x", "description": "d", "path": "/p", "category": "c"}]

    with patch("httpx.post", side_effect=ConnectionError("connection refused")):
        results = match_skills("anything")

    assert results == []
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_match_skills_llm_returns_non_list(mock_ensure: MagicMock) -> None:
    """LLM 返回非数组 → 静默返回空。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [{"name": "x", "description": "d", "path": "/p", "category": "c"}]

    with patch("httpx.post", return_value=_mock_llm_response("no skills match")):
        results = match_skills("anything")

    assert results == []
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_match_skills_llm_code_fenced_json(mock_ensure: MagicMock) -> None:
    """LLM 返回 ```json ... ``` 包裹的 JSON 也能正确解析。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [{"name": "docker", "description": "d", "path": "/p", "category": "c"}]

    with patch("httpx.post", return_value=_mock_llm_response('```json\n["docker"]\n```')):
        results = match_skills("docker")

    assert len(results) == 1
    assert results[0]["name"] == "docker"
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_match_skills_unknown_name_skipped(mock_ensure: MagicMock) -> None:
    """LLM 返回不在索引中的名称 → 跳过。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [{"name": "docker", "description": "d", "path": "/p", "category": "c"}]

    with patch("httpx.post", return_value=_mock_llm_response('["docker", "nonexistent-skill"]')):
        results = match_skills("docker")

    assert len(results) == 1
    assert results[0]["name"] == "docker"
    sm._skill_index = None


# ====================================================================
# 配置常量
# ====================================================================


def test_top_k_default() -> None:
    assert _TOP_K == 3


# ====================================================================
# 关键词提取
# ====================================================================


class TestExtractKeywords:
    def test_english_words(self) -> None:
        kw = _extract_keywords("docker deploy container")
        assert "docker" in kw
        assert "deploy" in kw
        assert "container" in kw

    def test_english_lowercase(self) -> None:
        kw = _extract_keywords("Docker Deploy")
        assert "docker" in kw
        assert "deploy" in kw

    def test_english_stopwords_filtered(self) -> None:
        kw = _extract_keywords("the docker is a tool")
        assert "the" not in kw
        assert "docker" in kw
        assert "tool" in kw

    def test_english_short_words_filtered(self) -> None:
        kw = _extract_keywords("a b cd docker")
        assert "a" not in kw
        assert "b" not in kw
        assert "cd" in kw
        assert "docker" in kw

    def test_chinese_segments(self) -> None:
        kw = _extract_keywords("飞书通知发送")
        assert "飞书" in kw
        assert "书通" in kw
        assert "通知" in kw
        assert "知发" in kw
        assert "发送" in kw

    def test_chinese_full_segment(self) -> None:
        kw = _extract_keywords("飞书通知")
        assert "飞书通知" in kw

    def test_mixed_languages(self) -> None:
        kw = _extract_keywords("docker 部署 容器")
        assert "docker" in kw
        assert "部署" in kw
        assert "容器" in kw

    def test_empty_text(self) -> None:
        assert _extract_keywords("") == set()

    def test_only_stopwords(self) -> None:
        kw = _extract_keywords("the a 是的")
        assert len(kw) == 0


# ====================================================================
# 关键词预筛选
# ====================================================================


def _sample_index() -> list[dict]:
    return [
        {"name": "docker-patterns", "description": "Docker deployment patterns and container orchestration", "path": "/p1", "category": "ops"},
        {"name": "lark-notify", "description": "飞书消息通知发送", "path": "/p2", "category": "integration"},
        {"name": "git-workflow", "description": "Git branching and workflow patterns", "path": "/p3", "category": "dev"},
        {"name": "python-testing", "description": "Python testing with pytest and unittest", "path": "/p4", "category": "dev"},
        {"name": "database-migration", "description": "Database schema migration tools", "path": "/p5", "category": "ops"},
    ]


class TestKeywordPrescreen:
    def test_empty_index(self) -> None:
        assert _keyword_prescreen("docker", []) == []

    def test_name_exact_match(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("docker-patterns", index, top_k=3)
        assert len(results) >= 1
        assert results[0]["name"] == "docker-patterns"
        assert "_score" in results[0]
        assert results[0]["_score"] > 0

    def test_name_keyword_match(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("docker deploy", index, top_k=3)
        assert len(results) >= 1
        assert results[0]["name"] == "docker-patterns"

    def test_chinese_description_match(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("飞书通知", index, top_k=3)
        assert len(results) >= 1
        assert results[0]["name"] == "lark-notify"

    def test_category_match(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("ops", index, top_k=5)
        names = [r["name"] for r in results]
        assert "docker-patterns" in names
        assert "database-migration" in names

    def test_top_k_limit(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("docker", index, top_k=2)
        assert len(results) <= 2

    def test_no_match_returns_empty(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("xyz_not_exist_12345", index, top_k=3)
        assert len(results) == 0

    def test_archive_skipped(self) -> None:
        index = [
            {"name": "old-skill", "description": "archived skill", "path": "/p", "category": ".archive"},
            {"name": "active-skill", "description": "active skill about docker", "path": "/p", "category": "ops"},
        ]
        results = _keyword_prescreen("docker", index, top_k=3)
        names = [r["name"] for r in results]
        assert "old-skill" not in names
        assert "active-skill" in names

    def test_score_order_descending(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("docker deploy container", index, top_k=5)
        scores = [r["_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_query_fallback(self) -> None:
        index = _sample_index()
        results = _keyword_prescreen("   ", index, top_k=3)
        assert len(results) == 3
        # 无关键词时按 name 字母序
        names = [r["name"] for r in results]
        assert names == sorted(names)


# ====================================================================
# 三级匹配架构
# ====================================================================


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_three_stage_match_with_keyword_prescreen(mock_ensure: MagicMock) -> None:
    """启用关键词预筛选时，走三级匹配路径（关键词 → Embedding 可选 → LLM）。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/skills/docker/SKILL.md", "category": "ops"},
        {"name": "git-workflow", "description": "Git branching patterns", "path": "/skills/git/SKILL.md", "category": "dev"},
    ]

    with patch("httpx.post", return_value=_mock_llm_response('["docker-patterns"]')):
        results = match_skills("docker deploy", enable_keyword_prescreen=True)

    assert len(results) == 1
    assert results[0]["name"] == "docker-patterns"
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_single_stage_match_without_prescreen(mock_ensure: MagicMock) -> None:
    """关闭关键词预筛选时，走全量 LLM 匹配路径。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/skills/docker/SKILL.md", "category": "ops"},
    ]

    with patch("httpx.post", return_value=_mock_llm_response('["docker-patterns"]')):
        results = match_skills("docker deploy", enable_keyword_prescreen=False)

    assert len(results) == 1
    assert results[0]["name"] == "docker-patterns"
    sm._skill_index = None


def test_prescreen_top_k_default() -> None:
    assert _PRESCREEN_TOP_K == 30


# ====================================================================
# Fallback 机制（LLM 返回空时回退到 union top-K）
# ====================================================================


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_fallback_when_llm_returns_empty(mock_ensure: MagicMock) -> None:
    """LLM 返回空 → fallback 到 keyword prescreen top-K。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/skills/docker/SKILL.md", "category": "ops"},
        {"name": "git-workflow", "description": "Git branching patterns", "path": "/skills/git/SKILL.md", "category": "dev"},
    ]

    with patch("httpx.post", return_value=_mock_llm_response("[]")):
        results = match_skills("docker deploy")

    # Fallback 应返回 keyword prescreen 的 top-3
    assert len(results) >= 1
    assert results[0]["name"] == "docker-patterns"
    # Fallback 分数应低于 LLM 命中基线 0.5
    assert float(results[0]["score"]) < 0.5
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_fallback_when_llm_error(mock_ensure: MagicMock) -> None:
    """LLM 调用异常 → fallback 到 keyword prescreen top-K。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/skills/docker/SKILL.md", "category": "ops"},
    ]

    with patch("httpx.post", side_effect=ConnectionError("timeout")):
        results = match_skills("docker deploy")

    assert len(results) == 1
    assert results[0]["name"] == "docker-patterns"
    assert float(results[0]["score"]) < 0.5
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_no_fallback_without_prescreen(mock_ensure: MagicMock) -> None:
    """关闭预筛时 LLM 返回空 → 不触发 fallback，直接返回空。"""
    import knowledge_navigation.core.skill_matcher as sm
    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/skills/docker/SKILL.md", "category": "ops"},
    ]

    with patch("httpx.post", return_value=_mock_llm_response("[]")):
        results = match_skills("docker", enable_keyword_prescreen=False)

    assert results == []
    sm._skill_index = None


# ====================================================================
# Embedding + Union 机制
# ====================================================================


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_union_with_embedding_candidates(mock_ensure: MagicMock) -> None:
    """Embedding 预筛补全关键词漏筛的候选 → union 后 LLM 可见更多候选。"""
    import knowledge_navigation.core.skill_matcher as sm

    # 两个 skill：docker 有关键词重叠，db-migration 无关键词重叠
    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/p1", "category": "ops"},
        {"name": "db-migration", "description": "Database schema versioning", "path": "/p2", "category": "ops"},
    ]

    # Mock embedding prescreen 返回 db-migration（关键词漏筛的）
    def mock_emb_prescreen(query, candidates, top_k=20):
        result = []
        for c in candidates:
            if c["name"] == "db-migration":
                c_copy = dict(c)
                c_copy["_emb_score"] = 0.85
                result.append(c_copy)
        return result

    with patch.object(sm, "_embedding_circuit_breaker", return_value=False), \
         patch.object(sm, "_get_embedding_config", return_value=("model", "url", "key", 20)), \
         patch.object(sm, "_embedding_prescreen", side_effect=mock_emb_prescreen), \
         patch("httpx.post", return_value=_mock_llm_response('["db-migration"]')):
        results = match_skills("database version upgrade")

    # LLM 应该能从 union 中选到 db-migration
    assert len(results) == 1
    assert results[0]["name"] == "db-migration"
    sm._skill_index = None


@patch("knowledge_navigation.core.skill_matcher.ensure_index", return_value=True)
def test_embedding_degraded_skips_emb(mock_ensure: MagicMock) -> None:
    """Embedding 熔断/降级时返回无 _emb_score 的候选 → 跳过 embedding，只用 keyword。"""
    import knowledge_navigation.core.skill_matcher as sm

    sm._skill_index = [
        {"name": "docker-patterns", "description": "Docker deployment patterns", "path": "/p1", "category": "ops"},
    ]

    # Mock embedding prescreen 返回降级结果（无 _emb_score）
    def mock_emb_prescreen(query, candidates, top_k=20):
        return [{"name": "docker-patterns", "description": "Docker stuff", "path": "/p1", "category": "ops"}]

    with patch.object(sm, "_embedding_circuit_breaker", return_value=False), \
         patch.object(sm, "_get_embedding_config", return_value=("model", "url", "key", 20)), \
         patch.object(sm, "_embedding_prescreen", side_effect=mock_emb_prescreen), \
         patch("httpx.post", return_value=_mock_llm_response('["docker-patterns"]')):
        results = match_skills("docker deploy")

    assert len(results) == 1
    assert results[0]["name"] == "docker-patterns"
    sm._skill_index = None


# ====================================================================
# 调优参数运行期读取（热更新）与 clamp 保护
# ====================================================================
# 回归防线：这些参数曾在模块导入期（含函数默认参数）一次性求值，
# 导致改 .env 后必须重启 gateway 才生效。若有人改回 `top_k: int = _TOP_K`
# 这类 def 期求值写法，下列测试会立即失败。


def _make_index(n: int) -> list[dict[str, str]]:
    """构造 n 个均能命中关键词 docker 的 skill，用于验证 top_k 截断。"""
    return [
        {"name": f"docker-skill-{i}", "description": "docker deploy container", "category": "ops"}
        for i in range(n)
    ]


class TestTuningParamsRuntimeRead:
    def test_accessor_reflects_env_change(self, monkeypatch) -> None:
        """改 env 后 accessor 立即返回新值，无需重新导入模块。"""
        import knowledge_navigation.core.skill_matcher as sm

        monkeypatch.setenv("KN_SKILL_TOP_K", "7")
        assert sm._get_top_k() == 7
        monkeypatch.setenv("KN_SKILL_TOP_K", "2")
        assert sm._get_top_k() == 2

    def test_default_arg_not_frozen_at_def_time(self, monkeypatch) -> None:
        """不传 top_k 时，函数默认值随 env 变化（而非 def 期固化）。"""
        import knowledge_navigation.core.skill_matcher as sm

        index = _make_index(50)
        for value in (5, 12, 27):
            monkeypatch.setenv("KN_SKILL_PRESCREEN_TOP_K", str(value))
            assert len(sm._keyword_prescreen("docker", index)) == value

    def test_explicit_arg_overrides_env(self, monkeypatch) -> None:
        """显式传参优先级仍高于 env。"""
        import knowledge_navigation.core.skill_matcher as sm

        monkeypatch.setenv("KN_SKILL_PRESCREEN_TOP_K", "27")
        assert len(sm._keyword_prescreen("docker", _make_index(50), top_k=4)) == 4

    def test_invalid_values_fall_back_to_default(self, monkeypatch) -> None:
        """误配 0 / 负数 / 非数字时回退默认值。

        其中 batch_size=0 若不 clamp 会让 range(step=0) 抛 ValueError。
        """
        import knowledge_navigation.core.skill_matcher as sm

        cases = [
            ("KN_SKILL_EMBEDDING_BATCH_SIZE", "0", sm._get_embedding_batch_size, 20),
            ("KN_SKILL_TOP_K", "-3", sm._get_top_k, 3),
            ("KN_SKILL_MAX_SKILLS", "abc", sm._get_max_skills, 500),
            ("KN_SKILL_EMBEDDING_TOP_K", "0", sm._get_embedding_top_k, 20),
            ("KN_SKILL_MATCH_TIMEOUT", "-1", sm._get_llm_timeout, 45),
        ]
        for key, bad_value, accessor, expected in cases:
            monkeypatch.setenv(key, bad_value)
            assert accessor() == expected, f"{key}={bad_value} 未回退到 {expected}"

    def test_snapshot_constants_still_exported(self) -> None:
        """导入期快照保留供向后兼容，且恒为正整数。

        注意：这些快照会被部署环境的 .env 覆盖（例如线上配了
        KN_SKILL_EMBEDDING_TOP_K=30），因此只断言不变量而非具体数值，
        避免单测与部署环境耦合。
        """
        import knowledge_navigation.core.skill_matcher as sm

        for name in ("_TOP_K", "_MAX_SKILLS", "_PRESCREEN_TOP_K",
                     "_EMBEDDING_TOP_K", "_EMBEDDING_BATCH_SIZE", "_LLM_TIMEOUT"):
            value = getattr(sm, name)
            assert isinstance(value, int), f"{name} 应为 int"
            assert value >= 1, f"{name} 应为正整数，实际 {value}"
