"""skill_matcher 单元测试。

覆盖：
- frontmatter 解析 / 剥离
- 索引懒加载与缓存
- LLM 驱动匹配（mock httpx）
- 配置常量
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from knowledge_navigation.core.skill_matcher import (
    _TOP_K,
    _parse_frontmatter,
    strip_frontmatter,
    ensure_index,
    match_skills,
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
