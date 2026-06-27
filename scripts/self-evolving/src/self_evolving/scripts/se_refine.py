#!/usr/bin/env python3
"""CLI entry point for Refinement operator.

Usage:
    python -m self_evolving.scripts.se_refine --help
    se-refine --content "..." --context "..."
    se-refine --content-file code.py --iterations 5
    se-refine --content "..." --risk-threshold 0.2
"""
import argparse
import json
import sys
from pathlib import Path

from self_evolving.operators.refinement import RefinementOperator, RefinementConfig


def main():
    parser = argparse.ArgumentParser(
        description="Refinement Operator - Risk-aware content optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python -m self_evolving.scripts.se_refine --content-file code.py --context "Refine this code"
  
  # Custom risk threshold
  python -m self_evolving.scripts.se_refine --content "..." --risk-threshold 0.2
  
  # More iterations
  python -m self_evolving.scripts.se_refine --content-file document.md --iterations 5
  
  # JSON output
  python -m self_evolving.scripts.se_refine --content "..." --output-format json
        """
    )
    
    parser.add_argument(
        "--content", "-c",
        type=str,
        help="Content to refine"
    )
    parser.add_argument(
        "--content-file", "-f",
        type=str,
        help="Path to file containing content to refine"
    )
    parser.add_argument(
        "--context", "-k",
        type=str,
        default="Refine this content",
        help="Task context"
    )
    parser.add_argument(
        "--risk-threshold",
        type=float,
        default=0.3,
        help="Risk threshold (0-1)"
    )
    parser.add_argument(
        "--iterations", "-i",
        type=int,
        default=3,
        help="Number of optimization iterations"
    )
    parser.add_argument(
        "--target-reduction",
        type=float,
        default=0.5,
        help="Target reduction ratio (0-1)"
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Disable output compression"
    )
    parser.add_argument(
        "--failure-patterns-file",
        type=str,
        help="Path to file containing failure patterns (one per line)"
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
    
    args = parser.parse_args()
    
    # Get content
    content = ""
    if args.content_file:
        content_path = Path(args.content_file)
        if content_path.exists():
            content = content_path.read_text(encoding="utf-8")
        else:
            print(f"Error: File not found: {args.content_file}", file=sys.stderr)
            sys.exit(1)
    elif args.content:
        content = args.content
    else:
        # Read from stdin
        print("Enter content to refine (Ctrl+D to end):", file=sys.stderr)
        content = sys.stdin.read()
    
    if not content.strip():
        print("Error: No content provided", file=sys.stderr)
        sys.exit(1)
    
    # Load failure patterns
    failure_patterns = []
    if args.failure_patterns_file:
        patterns_path = Path(args.failure_patterns_file)
        if patterns_path.exists():
            with open(patterns_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        failure_patterns.append(line)
    
    # Load config
    config = RefinementConfig.from_yaml(args.config)
    config.risk_threshold = args.risk_threshold
    config.optimization_budget = args.iterations
    config.target_reduction_ratio = args.target_reduction
    config.compress_output = not args.no_compress
    
    # Create operator and execute
    operator = RefinementOperator(config)
    
    if args.verbose:
        print(f"Refinement Operator", file=sys.stderr)
        print(f"  Risk threshold: {config.risk_threshold}", file=sys.stderr)
        print(f"  Optimization iterations: {config.optimization_budget}", file=sys.stderr)
        print(f"  Target reduction: {config.target_reduction_ratio:.0%}", file=sys.stderr)
        print(f"  Compress output: {config.compress_output}", file=sys.stderr)
        print(f"  Content length: {len(content)} chars", file=sys.stderr)
        print(file=sys.stderr)
    
    result = operator.execute(
        candidate_content=content,
        failure_patterns=failure_patterns if failure_patterns else None,
        risk_threshold=args.risk_threshold,
    )
    
    # Output
    if args.output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("=" * 60)
        print("REFINEMENT RESULT")
        print("=" * 60)
        print()
        
        print("REDUCTION STATS:")
        for key, value in result.reduction_stats.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.2%}")
            else:
                print(f"  {key}: {value}")
        print()
        
        print("RISK ASSESSMENT:")
        print(f"  Overall Risk: {result.risk_assessment.overall_risk.value}")
        print(f"  Risk Score: {result.risk_assessment.risk_score:.2f}")
        print(f"  Risk Factors: {len(result.risk_assessment.risk_factors)}")
        
        if result.risk_assessment.risk_factors:
            print()
            print("  Risk Details:")
            for factor in result.risk_assessment.risk_factors[:5]:
                print(f"    - [{factor.severity.value}] {factor.category.value}: {factor.description}")
            if len(result.risk_assessment.risk_factors) > 5:
                print(f"    ... and {len(result.risk_assessment.risk_factors) - 5} more")
        
        if result.risk_assessment.recommendations:
            print()
            print("  Recommendations:")
            for rec in result.risk_assessment.recommendations:
                print(f"    - {rec}")
        print()
        
        print("REMOVED REDUNDANCIES:")
        if result.removed_redundancies:
            for redundancy in result.removed_redundancies[:5]:
                print(f"  - {redundancy[:100]}...")
            if len(result.removed_redundancies) > 5:
                print(f"  ... and {len(result.removed_redundancies) - 5} more")
        else:
            print("  (none)")
        print()
        
        print("REPLACED RISKY PARTS:")
        if result.replaced_risky_parts:
            for part in result.replaced_risky_parts:
                print(f"  - {part}")
        else:
            print("  (none)")
        print()
        
        print("OPTIMIZED CONTENT:")
        print("-" * 40)
        print(result.refined_content[:2000])
        if len(result.refined_content) > 2000:
            print(f"\n  ... (truncated, total {len(result.refined_content)} chars)")
        print("-" * 40)
        print()
        
        if result.optimization_log:
            print(f"Optimization steps: {len(result.optimization_log)}")
        
        print("=" * 60)


if __name__ == "__main__":
    main()
