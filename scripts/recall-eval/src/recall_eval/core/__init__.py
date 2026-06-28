"""核心模块 — 评估指标、数据集、运行器。"""

from recall_eval.core.dataset import EvalDataset, EvalQuery, generate_eval_samples, load_dataset
from recall_eval.core.metrics import coverage_score, faithfulness_score, relevance_score
from recall_eval.core.runner import EvalReport, EvalResult, EvalRunner, print_report

__all__ = [
    "EvalDataset",
    "EvalQuery",
    "EvalReport",
    "EvalResult",
    "EvalRunner",
    "coverage_score",
    "faithfulness_score",
    "generate_eval_samples",
    "load_dataset",
    "print_report",
    "relevance_score",
]
