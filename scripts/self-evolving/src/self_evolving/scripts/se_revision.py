#!/usr/bin/env python3
"""CLI entry point for Revision operator.

Usage:
    python -m self_evolving.scripts.se_revision --help
    se-revision --failed-content "..." --context "..."
    se-revision --failed-content-file failed.py --context "Fix bug in module X"
    se-revision --failed-content "..." --context "..." --failure-type argument_mismatch
    se-revision --failed-content "..." --context "..." --reflection-depth 3
"""
import argparse
import json
import sys
from pathlib import Path

from self_evolving.operators.revision import RevisionOperator, RevisionConfig
from self_evolving.models.failure_diagnosis import FailureType


def main():
    parser = argparse.ArgumentParser(
        description="Revision Operator - Failure-driven strategy generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python -m self_evolving.scripts.se_revision --failed-content "def foo(x): return x + 'hello'" --context "String concatenation with int"
  
  # Specify failure type
  python -m self_evolving.scripts.se_revision --failed-content "..." --context "..." --failure-type argument_mismatch
  
  # Deep reflection (3 levels)
  python -m self_evolving.scripts.se_revision --failed-content "..." --context "..." --reflection-depth 3
  
  # Load from file
  python -m self_evolving.scripts.se_revision --failed-content-file failed_code.py --context "Fix this bug"
        """
    )
    
    parser.add_argument(
        "--failed-content", "-c",
        type=str,
        help="The content that failed (code/trajectory/document)"
    )
    parser.add_argument(
        "--failed-content-file", "-f",
        type=str,
        help="Path to file containing failed content"
    )
    parser.add_argument(
        "--context", "-k",
        type=str,
        required=True,
        help="Task context (problem description, constraints)"
    )
    parser.add_argument(
        "--failure-type", "-t",
        type=str,
        choices=[ft.value for ft in FailureType],
        help="Predefined failure type (auto-detected if not specified)"
    )
    parser.add_argument(
        "--reflection-depth", "-d",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Reflection depth (1=direct, 2=direct+root, 3=deep tracing)"
    )
    parser.add_argument(
        "--no-alternatives",
        action="store_true",
        help="Do not generate alternative solutions"
    )
    parser.add_argument(
        "--alternative-count", "-n",
        type=int,
        default=2,
        help="Number of alternative solutions to generate"
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config YAML file"
    )
    parser.add_argument(
        "--output-format",
        type=str,
        choices=["json", "text"],
        default="text",
        help="Output format"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--apply",
        type=str,
        metavar="PATH",
        help="将修订内容写入指定文件（仅当 Ouroboros 审查通过时）"
    )
    parser.add_argument(
        "--git-repo",
        type=str,
        default="/mnt/d/HermesProject",
        help="Git 仓库路径（配合 --apply，自动 commit；默认 /mnt/d/HermesProject）"
    )
    
    args = parser.parse_args()
    
    # Get failed content
    failed_content = ""
    if args.failed_content_file:
        content_path = Path(args.failed_content_file)
        if content_path.exists():
            failed_content = content_path.read_text(encoding="utf-8")
        else:
            print(f"Error: File not found: {args.failed_content_file}", file=sys.stderr)
            sys.exit(1)
    elif args.failed_content:
        failed_content = args.failed_content
    else:
        # Read from stdin if no content provided
        print("Enter failed content (Ctrl+D to end):", file=sys.stderr)
        failed_content = sys.stdin.read()
    
    if not failed_content.strip():
        print("Error: No failed content provided", file=sys.stderr)
        sys.exit(1)
    
    # Load config
    config = RevisionConfig.from_yaml(args.config)
    config.reflection_depth = args.reflection_depth
    config.generate_alternatives = not args.no_alternatives
    config.alternative_count = args.alternative_count
    
    # Create operator and execute
    operator = RevisionOperator(config)
    
    if args.verbose:
        print(f"Revision Operator", file=sys.stderr)
        print(f"  Reflection depth: {config.reflection_depth}", file=sys.stderr)
        print(f"  Generate alternatives: {config.generate_alternatives}", file=sys.stderr)
        print(f"  Alternative count: {config.alternative_count}", file=sys.stderr)
        print(f"  Confidence threshold: {config.confidence_threshold}", file=sys.stderr)
        print(file=sys.stderr)
    
    result = operator.execute(
        failed_content=failed_content,
        context=args.context,
        failure_type=args.failure_type,
    )
    
    # Output
    if args.output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("=" * 60)
        print("REVISION RESULT")
        print("=" * 60)
        print()
        
        print("DIAGNOSIS:")
        print(f"  Failure Type: {result.diagnosis.failure_type.value}")
        print(f"  Confidence: {result.diagnosis.confidence:.2f}")
        print(f"  Direct Cause: {result.diagnosis.direct_cause}")
        print(f"  Root Cause: {result.diagnosis.root_cause}")
        if result.diagnosis.deep_analysis:
            print(f"  Deep Analysis: {result.diagnosis.deep_analysis}")
        print()
        
        print("REVISED CONTENT:")
        print("-" * 40)
        print(result.revised_content)
        print("-" * 40)
        print()
        
        if result.alternatives:
            print("ALTERNATIVE SOLUTIONS:")
            for alt in result.alternatives:
                print(f"\n  [{alt.solution_id}] {alt.solution_type}")
                print(f"      Description: {alt.description}")
                print(f"      Confidence: {alt.confidence:.2f}")
                print(f"      Risk Level: {alt.risk_level}")
                if alt.pros:
                    print(f"      Pros: {', '.join(alt.pros)}")
                if alt.cons:
                    print(f"      Cons: {', '.join(alt.cons)}")
        
        print()
        print(f"Overall Confidence: {result.confidence_score:.2f}")
        print("=" * 60)

    # ── Ouroboros Git 追踪（P1-3）：审查通过才允许落盘 + commit ──
    if args.apply:
        rejected = getattr(result, "ouroboros_rejected", False)
        if rejected:
            print("[ouroboros] ❌ 修订被审查拒绝，不落盘（可 revert 保护生效）", file=sys.stderr)
            sys.exit(2)
        apply_path = Path(args.apply)
        try:
            apply_path.parent.mkdir(parents=True, exist_ok=True)
            apply_path.write_text(result.revised_content, encoding="utf-8")
            print(f"[ouroboros] ✅ 修订已写入: {apply_path}")
        except Exception as e:
            print(f"[ouroboros] ❌ 写入失败: {e}", file=sys.stderr)
            sys.exit(1)

        # git commit（需在 git repo 内）
        import subprocess

        repo = Path(args.git_repo)
        if not (repo / ".git").exists():
            print(f"[ouroboros] ⚠️ {repo} 不是 git 仓库，跳过 commit", file=sys.stderr)
        else:
            try:
                rel = apply_path.resolve().relative_to(repo.resolve())
                commit_msg = f"se-revision: {result.diagnosis.failure_type.value} fix"
                subprocess.run(["git", "-C", str(repo), "add", "--", str(rel)], check=True, capture_output=True)
                subprocess.run(
                    ["git", "-C", str(repo), "commit", "-m", commit_msg, "--", str(rel)],
                    check=True, capture_output=True,
                )
                print(f"[ouroboros] ✅ git commit: {commit_msg}")
            except subprocess.CalledProcessError as e:
                if b"nothing to commit" in (e.stderr or b""):
                    print("[ouroboros] ⚠️ 无变更可提交（内容与 HEAD 相同）", file=sys.stderr)
                else:
                    print(f"[ouroboros] ❌ git commit 失败: {e.stderr.decode()[:200]}", file=sys.stderr)


if __name__ == "__main__":
    main()