"""recall-eval 包 — Recall 质量评估框架。

RAGAS faithfulness 评估，量化 Hindsight/知识树的召回质量。
"""

from recall_eval.config import AppConfig
from recall_eval.core import (
    EvalDataset,
    EvalQuery,
    EvalReport,
    EvalResult,
    EvalRunner,
    coverage_score,
    faithfulness_score,
    print_report,
    relevance_score,
)

__version__ = "0.1.0"
__author__ = "Hermes Team"
__license__ = "MIT"

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "AppConfig",
    "EvalDataset",
    "EvalQuery",
    "EvalReport",
    "EvalResult",
    "EvalRunner",
    "coverage_score",
    "faithfulness_score",
    "print_report",
    "relevance_score",
]
