#!/usr/bin/env python3
"""CLI entry point for Recombination operator.

Usage:
    python -m self_evolving.scripts.se_recombine --help
    se-recombine --candidates file1.py file2.py file3.py --context "..."
    se-recombine --candidates-file candidates.txt --context "..."
    se-recombine --candidates "content1" "content2" --context "..." --criteria quality
"""
import argparse
import json
import sys
from pathlib import Path

from self_evolving.operators.recombination import RecombinationOperator, RecombinationConfig


def main():
    parser = argparse.ArgumentParser(
        description="Recombination Operator - Cross-trajectory knowledge synthesizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From files
  python -m self_evolving.scripts.se_recombine --candidates file1.py file2.py file3.py --context "Merge these implementations"
  
  # From text
  python -m self_evolving.scripts.se_recombine --candidates "content1" "content2" --context "..." --criteria quality
  
  # From file list
  python -m self_evolving.scripts.se_recombine --candidates-file candidates.txt --context "..."
  
  # JSON output
  python -m self_evolving.scripts.se_recombine --candidates "..." "..." --context "..." --output-format json
        """
    )
    
    parser.add_argument(
        "--candidates", "-c",
        type=str,
        nargs="+",
        help="Candidate content strings (space-separated)"
    )
    parser.add_argument(
        "--candidates-file", "-f",
        type=str,
        help="Path to file containing candidate file paths (one per line)"
    )
    parser.add_argument(
        "--context", "-k",
        type=str,
        required=True,
        help="Task context (goal, constraints)"
    )
    parser.add_argument(
        "--criteria",
        type=str,
        choices=["quality", "coverage", "diversity"],
        default="quality",
        help="Selection criteria for component selection"
    )
    parser.add_argument(
        "--max-components",
        type=int,
        default=5,
        help="Maximum number of components to retain"
    )
    parser.add_argument(
        "--no-conflict-detection",
        action="store_true",
        help="Disable conflict detection"
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
    
    # Get candidate contents
    candidates = []
    
    if args.candidates_file:
        file_path = Path(args.candidates_file)
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        content_path = Path(line)
                        if content_path.exists():
                            candidates.append(content_path.read_text(encoding="utf-8"))
                        else:
                            print(f"Warning: File not found: {line}", file=sys.stderr)
        else:
            print(f"Error: File not found: {args.candidates_file}", file=sys.stderr)
            sys.exit(1)
    elif args.candidates:
        candidates = args.candidates
    else:
        print("Error: No candidates provided. Use --candidates or --candidates-file", file=sys.stderr)
        sys.exit(1)
    
    if len(candidates) < 2:
        print("Error: Need at least 2 candidates for recombination", file=sys.stderr)
        sys.exit(1)
    
    # Load config
    config = RecombinationConfig.from_yaml(args.config)
    config.selection_criteria = args.criteria
    config.max_components = args.max_components
    config.detect_conflicts = not args.no_conflict_detection
    
    # Create operator and execute
    operator = RecombinationOperator(config)
    
    if args.verbose:
        print(f"Recombination Operator", file=sys.stderr)
        print(f"  Selection criteria: {config.selection_criteria}", file=sys.stderr)
        print(f"  Max components: {config.max_components}", file=sys.stderr)
        print(f"  Detect conflicts: {config.detect_conflicts}", file=sys.stderr)
        print(f"  Candidates: {len(candidates)}", file=sys.stderr)
        print(file=sys.stderr)
    
    result = operator.execute(
        candidate_contents=candidates,
        task_context=args.context,
        selection_criteria=args.criteria,
    )
    
    # Output
    if args.output_format == "json":
        output = result.to_dict()
        # Convert components to dict for JSON serialization
        output["preserved_components"] = [
            {
                "component_id": c.component_id,
                "source_index": c.source_index,
                "component_type": c.component_type,
                "quality_score": c.quality_score,
                "is_failure_pattern": c.is_failure_pattern,
            }
            for c in result.preserved_components
        ]
        output["replaced_components"] = [
            {
                "component_id": c.component_id,
                "source_index": c.source_index,
                "component_type": c.component_type,
                "quality_score": c.quality_score,
            }
            for c in result.replaced_components
        ]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print("=" * 60)
        print("RECOMBINATION RESULT")
        print("=" * 60)
        print()
        
        print("EXTRACTION STATS:")
        for key, value in result.extraction_stats.items():
            print(f"  {key}: {value}")
        print()
        
        print(f"SYNERGY SCORE: {result.synergy_score:.2f}")
        if result.synergy_score > 0:
            print("  -> 1+1>2 synergy detected!")
        print()
        
        if result.conflict_log:
            print("CONFLICTS DETECTED:")
            for conflict in result.conflict_log:
                print(f"  - {conflict}")
            print()
        
        print("RECOMBINED CONTENT:")
        print("-" * 40)
        print(result.recombined_content[:2000])
        if len(result.recombined_content) > 2000:
            print(f"\n  ... (truncated, total {len(result.recombined_content)} chars)")
        print("-" * 40)
        print()
        
        print(f"Components preserved: {len(result.preserved_components)}")
        print(f"Components replaced: {len(result.replaced_components)}")
        
        if result.component_map:
            print()
            print("COMPONENT SOURCE MAP:")
            for comp_id, source in list(result.component_map.items())[:10]:
                print(f"  {comp_id} <- {source}")
            if len(result.component_map) > 10:
                print(f"  ... and {len(result.component_map) - 10} more")
        
        print("=" * 60)


if __name__ == "__main__":
    main()
