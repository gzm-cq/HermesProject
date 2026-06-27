"""测试共享 Fixture"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# 确保源码可导入
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


@pytest.fixture
def sample_article_text() -> str:
    return """# 精读笔记：SE-Agent 自进化智能体

SE-Agent 提出了三种进化算子：Revision、Recombination 和 Refinement。

## Revision 算子
Revision 通过分析失败轨迹来修改代码，从错误中学习正确的实现方式。
它维护一个失败模式库，每次失败后提取关键模式并匹配解决方案。

## Recombination 算子
Recombination 将已有功能模块重新组合来解决新问题，
类似于遗传算法中的交叉操作。

## Refinement 算子
Refinement 在成功基础上进行微调优化，不需要失败触发，
适合逐步改进已有功能。

## 三大算子的协同工作
三个算子按优先级执行：Revision（最高）→ Refinement → Recombination。
Revision 在失败后触发，Refinement 在成功后触发，
Recombination 在遇到全新问题时触发。
"""


@pytest.fixture
def sample_knowledge_points() -> list[str]:
    return [
        "SE-Agent 三大进化算子：Revision、Recombination、Refinement",
        "Revision 算子通过分析失败轨迹修改代码实现",
        "Recombination 算子重新组合已有功能模块解决新问题",
        "Refinement 算子在成功基础上进行微调优化",
        "三大算子按 Revision → Refinement → Recombination 优先级执行",
        "HDBSCAN 聚类算法通过密度可达性自动发现任意形状簇",
        "DBSCAN 需要手动指定 epsilon 参数，HDBSCAN 自动选择最优层次",
        "余弦相似度衡量两个向量在方向上的相似程度",
        "Embedding 模型将文本映射到高维语义空间",
        "bge-m3 是 BAAI 提出的多语言 embedding 模型",
    ]


@pytest.fixture
def sample_embeddings() -> list[list[float]]:
    """模拟 10 个 8 维 embedding（用于测试聚类逻辑）"""
    import random

    random.seed(42)
    # 生成 5 个相近的向量（模拟同一簇）和 5 个分散的向量
    base = [random.random() for _ in range(8)]
    cluster = [[base[i] + random.gauss(0, 0.1) for i in range(8)] for _ in range(5)]
    scattered = [[random.random() for _ in range(8)] for _ in range(5)]
    return cluster + scattered


@pytest.fixture
def default_config() -> "AppConfig":
    """Phase A 测试用的默认配置。"""
    from knowledge_tree_builder.config import AppConfig
    return AppConfig(
        llm_api_url="http://test:8080/v1/chat/completions",
        llm_api_key="test-key",
        llm_model="test-model",
        max_candidates_per_article=15,
        split_max_rounds=2,
        self_explanatory_rules=True,
    )


@pytest.fixture
def sample_analysis_report() -> dict:
    """阶1样本产物，用于测试阶2。"""
    return {
        "article_title": "SE-Agent 精读笔记",
        "analysis": {"content_summary": "SE-Agent 三大进化算子综述", "empty_article": False},
        "candidates": [
            {"text": "Revision 算子通过分析失败轨迹来修改代码实现", "type": "principle", "claims_count": 1, "claim_list": ["Revision 算子通过分析失败轨迹来修改代码实现"]},
            {"text": "Recombination 算子重新组合已有功能模块解决新问题，Refinement 在成功基础上微调优化", "type": "principle", "claims_count": 2, "claim_list": ["Recombination 算子重新组合已有功能模块解决新问题", "Refinement 在成功基础上微调优化"]},
            {"text": "HDBSCAN 通过层次聚类覆盖不同密度的簇", "type": "principle", "claims_count": 1, "claim_list": ["HDBSCAN 通过层次聚类覆盖不同密度的簇"]},
        ],
    }
