"""SE-Agent Evolution Operators.

Core implementation modules for the three evolution operators.

Module Structure:
    operators/
        __init__.py
        revision.py       # Revision operator (correction)
        recombination.py  # Recombination operator (synthesis)
        refinement.py     # Refinement operator (optimization)

Usage:
    from operators.revision import RevisionOperator
    from operators.recombination import RecombinationOperator
    from operators.refinement import RefinementOperator

    # Revision
    revision = RevisionOperator(config)
    result = revision.execute(
        failed_content="...",
        context="...",
        failure_type="argument_mismatch"
    )

    # Recombination
    recombine = RecombinationOperator(config)
    result = recombine.execute(
        candidate_contents=["...", "..."],
        task_context="..."
    )

    # Refinement
    refine = RefinementOperator(config)
    result = refine.execute(
        candidate_content="...",
        failure_patterns=["..."]
    )

Chain Modes:
    # Mode B: Revision -> Refinement
    result = revision.execute(...)
    refined = refine.execute(result.revised_content)

    # Mode D: Full loop
    result = revision.execute(...)
    recombined = recombine.execute([result.revised_content, ...], context)
    final = refine.execute(recombined.recombined_content)
"""

from .revision import RevisionOperator, RevisionConfig
from .recombination import RecombinationOperator, RecombinationConfig
from .refinement import RefinementOperator, RefinementConfig

__all__ = [
    "RevisionOperator", "RevisionConfig",
    "RecombinationOperator", "RecombinationConfig",
    "RefinementOperator", "RefinementConfig",
]
