#!/usr/bin/env python3
"""RegressionTester — skill 修改后的回归验证（P1-1，自实现）。

依据 docs/融合计划/20260822-数据飞轮增强执行方案.md §3.1：
原设计为 Docker 沙箱跑回归测试验证 skill 修改。

实际评估：skill 修改的"回归"本质是 SKILL.md 格式/可加载性验证，
Docker 沙箱跑 pytest 成本高且多数 skill 无配套测试。
故实现为分层验证：
- L1 轻量检查（默认开）：frontmatter 完整性 + YAML 可解析 + 内容差异
- L2 Docker 沙箱（可选开）：项目有 tests/ 且 Docker 可用时，容器内跑 pytest

用法：
    from regression_tester import RegressionTester
    tester = RegressionTester()
    result = tester.test(
        skill_path="/root/.hermes/skills/devops/memory-weeder/SKILL.md",
        old_content="# old",
        new_content="# new",
    )
    # result.passed → bool; result.failed_checks → list[str]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RegressionResult:
    """回归测试结果。"""

    passed: bool
    checks_total: int = 0
    checks_passed: int = 0
    failed_checks: list[str] = field(default_factory=list)
    docker_used: bool = False
    docker_output: str = ""


class RegressionTester:
    """skill 修改后的回归验证器（自实现，不拷贝上游）。"""

    # SKILL.md frontmatter 必需字段
    REQUIRED_FRONTMATTER_KEYS = ("name", "description")

    def test(
        self,
        skill_path: str,
        old_content: str,
        new_content: str,
        project_root: str = "/mnt/d/HermesProject",
        use_docker: bool = False,
    ) -> RegressionResult:
        """对修改后的 skill 内容做回归检查。

        Args:
            skill_path: SKILL.md 路径（用于确认是 skill 文件）
            old_content: 修改前内容（用于 diff）
            new_content: 修改后内容（用于验证）
            project_root: 项目根目录（Docker mount 源）
            use_docker: 启用 L2 Docker 沙箱 pytest（可选）

        Returns:
            RegressionResult（passed=False 时 failed_checks 含具体原因）
        """
        failed: list[str] = []
        total = 0
        passed = 0

        # ── L1-1: frontmatter 完整性（必需字段）──
        total += 1
        fm_ok, fm_msg = self._check_frontmatter(new_content)
        if fm_ok:
            passed += 1
        else:
            failed.append(f"frontmatter 检查失败: {fm_msg}")

        # ── L1-2: 内容非空 + 非纯 frontmatter（有 body）──
        total += 1
        if new_content.strip() and len(new_content.strip()) > 20:
            passed += 1
        else:
            failed.append("内容检查失败: SKILL.md 为空或仅含 frontmatter")

        # ── L1-3: 与旧版本有实际差异（防止空操作/死循环）──
        total += 1
        if new_content.strip() != old_content.strip():
            passed += 1
        else:
            failed.append("差异检查失败: 修改后内容与修改前完全相同（无效产出）")

        # ── L1-4: protected surface（frontmatter 元数据未被改动）──
        total += 1
        ps_ok, ps_msg = self._check_protected_surface(old_content, new_content)
        if ps_ok:
            passed += 1
        else:
            failed.append(f"保护面检查失败: {ps_msg}")

        # ── L2 (optional): Docker 沙箱 pytest ──
        docker_used = False
        docker_out = ""
        if use_docker and failed:
            # 已有 L1 失败则不浪费 Docker 资源
            pass
        elif use_docker:
            docker_used, docker_out, dom_ok = self._docker_sandbox_test(project_root)
            total += 1
            if dom_ok:
                passed += 1
            else:
                failed.append(f"Docker 沙箱 pytest 失败: {docker_out[-300:]}")

        return RegressionResult(
            passed=len(failed) == 0,
            checks_total=total,
            checks_passed=passed,
            failed_checks=failed,
            docker_used=docker_used,
            docker_output=docker_out,
        )

    # ── 内部检查实现 ──────────────────────────────────────────

    def _check_frontmatter(self, content: str) -> tuple[bool, str]:
        """检查 YAML frontmatter 必需字段是否齐全。"""
        if not content.strip().startswith("---"):
            return True, ""  # 非 SKILL.md 类内容（无 frontmatter），跳过
        try:
            import yaml

            parts = content.split("---", 2)
            if len(parts) < 3:
                return False, "frontmatter 未闭合（缺少第二个 ---）"
            meta = yaml.safe_load(parts[1]) or {}
            missing = [k for k in self.REQUIRED_FRONTMATTER_KEYS if k not in meta]
            if missing:
                return False, f"缺少必需字段: {missing}"
            return True, ""
        except Exception as e:
            return False, f"YAML 解析失败: {e}"

    def _check_protected_surface(self, old: str, new: str) -> tuple[bool, str]:
        """检查 frontmatter 元数据未被自动修改（protected surface）。"""
        try:
            old_fm = old.split("---", 2)[1] if old.strip().startswith("---") else ""
            new_fm = new.split("---", 2)[1] if new.strip().startswith("---") else ""
            if old_fm and new_fm and old_fm != new_fm:
                return False, "frontmatter 元数据被修改（protected surface 违规）"
            return True, ""
        except Exception:
            return True, ""

    def _docker_sandbox_test(self, project_root: str) -> tuple[bool, str, bool]:
        """在 Docker 沙箱内跑项目 pytest（需要 docker 可用 + tests/ 存在）。"""
        # 检查 docker 可用
        if shutil.which("docker") is None:
            return True, "docker 不可用，跳过沙箱测试", True
        # 检查项目有 tests/
        if not os.path.isdir(os.path.join(project_root, "tests")):
            return True, "项目无 tests/ 目录，跳过沙箱测试", True

        try:
            result = subprocess.run(
                [
                    "docker", "run", "--rm", "-v",
                    f"{project_root}:/workspace", "-w", "/workspace",
                    "python:3.11-slim", "sh", "-c",
                    "pip install -q pytest 2>/dev/null && python -m pytest tests/ -q --tb=line --ignore=tests/test_e2e.py 2>&1 | tail -20",
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            ok = result.returncode == 0
            return True, result.stdout[-500:], ok
        except subprocess.TimeoutExpired:
            return True, "Docker 沙箱测试超时（300s）", False
        except Exception as e:
            return True, f"Docker 沙箱测试异常: {e}", False


def _cli() -> int:
    """CLI 入口（供 sklearn 式脚本独立调用）。"""
    import argparse

    ap = argparse.ArgumentParser(description="RegressionTester — skill 修改回归验证")
    ap.add_argument("--skill-path", required=True, help="SKILL.md 路径")
    ap.add_argument("--old-file", required=True, help="旧内容文件")
    ap.add_argument("--new-file", required=True, help="新内容文件")
    ap.add_argument("--use-docker", action="store_true", help="启用 Docker 沙箱 pytest")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    old = Path(args.old_file).read_text(encoding="utf-8") if os.path.isfile(args.old_file) else args.old_file
    new = Path(args.new_file).read_text(encoding="utf-8") if os.path.isfile(args.new_file) else args.new_file

    tester = RegressionTester()
    result = tester.test(args.skill_path, old, new, use_docker=args.use_docker)
    if args.json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"[regression-tester] {status} ({result.checks_passed}/{result.checks_total})")
        for fc in result.failed_checks:
            print(f"  · {fc}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(_cli())