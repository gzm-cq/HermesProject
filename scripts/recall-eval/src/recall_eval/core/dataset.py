"""测试数据集管理 — 加载评估查询、生成评估样本。"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from recall_eval.config import AppConfig, _resolve_config_path

logger = logging.getLogger(__name__)


@dataclass
class EvalQuery:
    """评估查询项。"""

    query_id: str
    query: str
    category: str = ""
    expected_context: str = ""
    expected_answer: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalQuery":
        """从字典创建 EvalQuery 实例。"""
        return cls(
            query_id=str(data.get("query_id", "")),
            query=str(data.get("query", "")),
            category=str(data.get("category", "")),
            expected_context=str(data.get("expected_context", "")),
            expected_answer=str(data.get("expected_answer", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "query_id": self.query_id,
            "query": self.query,
            "category": self.category,
            "expected_context": self.expected_context,
            "expected_answer": self.expected_answer,
        }


@dataclass
class EvalDataset:
    """评估数据集。"""

    name: str = "default"
    queries: list[EvalQuery] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.queries)

    def __iter__(self):
        return iter(self.queries)

    def __getitem__(self, index: int) -> EvalQuery:
        return self.queries[index]

    @classmethod
    def load(cls, dataset_path: str) -> "EvalDataset":
        """从 JSON 文件加载数据集。

        支持两种格式：
        1. 数组格式: [{"query_id": "...", "query": "..."}, ...]
        2. 对象格式: {"name": "...", "queries": [...]}
        """
        resolved_path = _resolve_config_path(dataset_path)
        if not resolved_path.exists():
            logger.warning("数据集文件不存在: %s", resolved_path)
            return cls(name=Path(dataset_path).stem, queries=[])

        with open(resolved_path, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            queries = [EvalQuery.from_dict(item) for item in data]
            name = Path(dataset_path).stem
        elif isinstance(data, dict):
            queries = [EvalQuery.from_dict(item) for item in data.get("queries", [])]
            name = str(data.get("name", Path(dataset_path).stem))
        else:
            logger.warning("未知的数据集格式: %s", type(data))
            queries = []
            name = Path(dataset_path).stem

        logger.info("加载数据集 %s: %d 条查询", name, len(queries))
        return cls(name=name, queries=queries)

    def save(self, output_path: str) -> None:
        """保存数据集到 JSON 文件。"""
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "name": self.name,
            "queries": [q.to_dict() for q in self.queries],
        }

        with open(output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("数据集已保存到: %s (%d 条)", output_path, len(self.queries))

    def by_category(self) -> dict[str, list[EvalQuery]]:
        """按类别分组查询。"""
        categories: dict[str, list[EvalQuery]] = {}
        for q in self.queries:
            cat = q.category or "uncategorized"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(q)
        return categories

    def categories(self) -> list[str]:
        """获取所有类别。"""
        return sorted({q.category or "uncategorized" for q in self.queries})

    def filter_by_category(self, category: str) -> list[EvalQuery]:
        """按类别筛选查询。"""
        return [q for q in self.queries if (q.category or "uncategorized") == category]


def load_dataset(dataset_path: str) -> EvalDataset:
    """加载评估数据集（便捷函数）。"""
    return EvalDataset.load(dataset_path)


def generate_eval_samples(
    dataset: EvalDataset,
    context_provider=None,
    answer_provider=None,
) -> list[dict[str, Any]]:
    """生成评估样本。

    Args:
        dataset: 评估数据集
        context_provider: 上下文提供函数，接收 query 返回 context 字符串
        answer_provider: 回答生成函数，接收 query 和 context 返回 answer 字符串

    Returns:
        评估样本列表，每个样本包含 query_id、query、context、answer 等字段
    """
    samples: list[dict[str, Any]] = []

    for eval_query in dataset:
        sample: dict[str, Any] = {
            "query_id": eval_query.query_id,
            "query": eval_query.query,
            "category": eval_query.category,
            "expected_context": eval_query.expected_context,
            "expected_answer": eval_query.expected_answer,
        }

        if context_provider is not None:
            try:
                sample["context"] = context_provider(eval_query.query)
            except Exception as e:
                logger.warning("获取上下文失败 [%s]: %s", eval_query.query_id, e)
                sample["context"] = ""
                sample["context_error"] = str(e)
        else:
            sample["context"] = eval_query.expected_context

        if answer_provider is not None:
            try:
                context = sample.get("context", "")
                sample["answer"] = answer_provider(eval_query.query, context)
            except Exception as e:
                logger.warning("生成回答失败 [%s]: %s", eval_query.query_id, e)
                sample["answer"] = ""
                sample["answer_error"] = str(e)
        else:
            sample["answer"] = eval_query.expected_answer

        samples.append(sample)

    return samples
