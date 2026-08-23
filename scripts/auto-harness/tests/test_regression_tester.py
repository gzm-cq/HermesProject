"""regression_tester 单元测试（P1-1）。

覆盖 RegressionTester 的 4 项 L1 轻量检查 + 结果结构，不依赖 Docker/外部服务。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保源码可导入
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from regression_tester import RegressionTester  # noqa: E402

OLD_VALID = """---
name: test-skill
description: A test skill
---
# Test Skill
Old content here."""

NEW_VALID = """---
name: test-skill
description: A test skill
---
# Test Skill
New improved content here."""


class TestRegressionTester:
    def setup_method(self) -> None:
        self.tester = RegressionTester()

    def test_valid_change_passes(self) -> None:
        """合法修改（frontmatter 不变，body 变化）→ 4/4 通过。"""
        r = self.tester.test("/fake/skill.md", OLD_VALID, NEW_VALID)
        assert r.passed is True
        assert r.checks_passed == 4
        assert r.checks_total == 4
        assert r.failed_checks == []

    def test_frontmatter_protected_surface(self) -> None:
        """frontmatter 元数据被修改（protected surface 违规）→ 拒绝。"""
        bad = NEW_VALID.replace("name: test-skill", "name: renamed-skill")
        r = self.tester.test("/fake/skill.md", OLD_VALID, bad)
        assert r.passed is False
        assert any("保护面" in fc for fc in r.failed_checks)

    def test_identical_content_rejected(self) -> None:
        """修改前后内容相同（无效产出）→ 拒绝。"""
        r = self.tester.test("/fake/skill.md", OLD_VALID, OLD_VALID)
        assert r.passed is False
        assert any("完全相同" in fc for fc in r.failed_checks)

    def test_empty_new_content_rejected(self) -> None:
        """新内容为空 → 拒绝。"""
        r = self.tester.test("/fake/skill.md", OLD_VALID, "")
        assert r.passed is False
        assert any("为空" in fc for fc in r.failed_checks)

    def test_missing_required_frontmatter(self) -> None:
        """frontmatter 缺必需字段（name/description）→ 拒绝。"""
        bad = """---
name: only-name
---
# Test Skill
Some body content here."""
        r = self.tester.test("/fake/skill.md", OLD_VALID, bad)
        assert r.passed is False
        assert any("缺少必需字段" in fc for fc in r.failed_checks)

    def test_non_frontmatter_content_skips_fm_check(self) -> None:
        """非 SKILL.md 类内容（无 frontmatter）→ frontmatter 检查跳过。"""
        r = self.tester.test("/fake/other.md", "old content for testing purposes", "new content for testing purposes here")
        # 无 frontmatter → L1-1 通过，L1-2/3/4 通过 → 4/4
        assert r.passed is True
        assert r.checks_passed == 4