#!/usr/bin/env python3
"""
test_deploy_pycache.py — 验证 deploy/lib/common.sh 的 __pycache__ 清理逻辑。

不直接执行 common.sh（它是 bash），而是用 Python 复刻其清理算法，
对真实文件名做匹配断言，确保命名规则（连字符→下划线）正确。
"""
import os
import subprocess

# ── 复刻 common.sh 中的 stem 计算逻辑 ──────────────────────────────
def pyc_stem(rel_path: str) -> str:
    """health-check-all.py → health_check_all"""
    base = os.path.basename(rel_path)
    stem = base[:-3] if base.endswith(".py") else base
    return stem.replace("-", "_")


def should_clean(pyc_name: str, stem: str) -> bool:
    """判断某个 .pyc 是否应被清理（对应本次部署的 .py）。"""
    if not pyc_name.startswith(f"{stem}.cpython-"):
        return False
    # opt/unoptimized/debug variants are not the plain bytecode cache we target
    if ".opt-1." in pyc_name or ".opt-2." in pyc_name:
        return False
    if "-unoptimized-" in pyc_name or "-debug-" in pyc_name:
        return False
    return True


# ════════════════════════════════════════
# Unit tests: naming rule + matching logic
# ════════════════════════════════════════

def test_stem_hyphen_to_underscore():
    assert pyc_stem("health-check-all.py") == "health_check_all"
    assert pyc_stem("health-check-run.py") == "health_check_run"
    assert pyc_stem("plain.py") == "plain"


def test_matches_corresponding_pyc():
    assert should_clean("health_check_all.cpython-311.pyc", "health_check_all")
    assert should_clean("health_check_run.cpython-312.pyc", "health_check_run")


def test_does_not_match_unrelated_pyc():
    # unrelated project's cache must survive (shared __pycache__ dir)
    assert not should_clean("other_proj.cpython-311.pyc", "health_check_all")
    # same prefix but different module must not match either
    assert not should_clean("health_check_run.cpython-311.pyc", "health_check_all")


def test_excludes_variant_pyc():
    # optimized / unoptimized / debug variants are not plain caches we target,
    # but they'd still be stale after a deploy. We intentionally only clear the
    # plain cpython-XY.pyc form; these variants are rare and regenerated.
    assert not should_clean("mod.cpython-311.opt-1.pyc", "mod")
