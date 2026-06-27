"""Step 2: 自底向上聚类 — HDBSCAN 递归 sub-clustering + 建树报告

自底向上聚类知识点 → 递归 sub-clustering → 生成建树报告 + 自动干跑迭代。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

try:
    from sklearn.cluster import HDBSCAN as HDBSCANCluster

    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCANCluster = None  # type: ignore[assignment]
    HDBSCAN_AVAILABLE = False


def build_tree(
    knowledge_points: list[str],
    embeddings: np.ndarray,
    *,
    min_cluster_size: int = 5,
    cluster_selection_method: str = "eom",
    max_depth: int = 5,
) -> dict[str, Any]:
    """自底向上聚类知识点，产出树结构 + 建树报告。

    Args:
        knowledge_points: 知识点文本列表
        embeddings: 对应的 embedding 矩阵 (N, D)
        min_cluster_size: HDBSCAN 最小簇大小
        cluster_selection_method: HDBSCAN 簇选择方法 ('eom' | 'leaf')
        max_depth: 递归 sub-clustering 最大深度

    Returns:
        dict: {
            "tree": [子树列表],          # 根层级子树（含噪声浮动节点）
            "noise": [知识点文本],        # 噪声点列表
            "report": {建树报告指标}       # 建树报告
        }
    """
    if not HDBSCAN_AVAILABLE:
        return {
            "tree": [],
            "noise": knowledge_points,
            "report": {"error": "HDBSCAN not available"},
        }

    # 首层 HDBSCAN 聚类
    labels = _run_hdbscan(
        embeddings,
        min_cluster_size=min_cluster_size,
        cluster_selection_method=cluster_selection_method,
    )

    # 对每个簇递归 sub-clustering
    tree: list[dict[str, Any]] = []
    noise_points: list[str] = []

    unique_labels = set(labels) - {-1}
    for cluster_id in sorted(unique_labels):
        mask = labels == cluster_id
        cluster_embeddings = embeddings[mask]
        cluster_points = [knowledge_points[i] for i in range(len(knowledge_points)) if mask[i]]
        sub_tree = _sub_cluster(
            cluster_embeddings,
            cluster_points,
            depth=0,
            max_depth=max_depth,
            min_cluster_size=min_cluster_size,
            cluster_selection_method=cluster_selection_method,
        )
        tree.append(sub_tree)

    # 噪声点
    for i, label in enumerate(labels):
        if label == -1:
            noise_points.append(knowledge_points[i])

    # 噪声点挂到根层级浮动节点下（设计：不单独成科，但可被找到）
    if noise_points:
        tree.append({
            "type": "leaf",
            "points": noise_points,
            "noise": True,
        })

    report = _generate_report(tree, noise_points, knowledge_points)
    try:
        from knowledge_tree_builder.core.validator import classify_knowledge_types
        type_result = classify_knowledge_types(knowledge_points)
        report["config_ratio"] = type_result["config_ratio"]
    except Exception:
        report["config_ratio"] = 0.0

    return {
        "tree": tree,
        "noise": noise_points,
        "labels": labels.tolist() if isinstance(labels, np.ndarray) else labels,
        "report": report,
    }


def _run_hdbscan(
    embeddings: np.ndarray,
    min_cluster_size: int = 5,
    cluster_selection_method: str = "eom",
) -> np.ndarray:
    """运行 HDBSCAN 聚类"""
    if embeddings.shape[0] < min_cluster_size:
        return np.full(embeddings.shape[0], -1, dtype=int)

    clusterer = HDBSCANCluster(
        min_cluster_size=min_cluster_size,
        cluster_selection_method=cluster_selection_method,
        metric="euclidean",
        copy=False,
    )
    return clusterer.fit_predict(embeddings)


def _sub_cluster(
    embeddings: np.ndarray,
    points: list[str],
    depth: int,
    max_depth: int,
    min_cluster_size: int,
    cluster_selection_method: str,
) -> dict[str, Any]:
    """递归 sub-clustering。

    递归终止条件：
    - 知识点数 <= min_cluster_size → 叶子节点
    - 达到最大深度 → 叶子节点
    - HDBSCAN 产出单簇（K=1，聚不成分散子簇）→ 叶子节点
    """
    if len(points) <= min_cluster_size or depth >= max_depth:
        return {"type": "leaf", "points": points}

    labels = _run_hdbscan(
        embeddings,
        min_cluster_size=min_cluster_size,
        cluster_selection_method=cluster_selection_method,
    )

    unique_labels = set(labels) - {-1}
    if len(unique_labels) <= 1:
        # 聚不成分散子簇 → 叶子
        return {"type": "leaf", "points": points}

    children: list[dict[str, Any]] = []
    for cluster_id in sorted(unique_labels):
        mask = labels == cluster_id
        sub_embeddings = embeddings[mask]
        sub_points = [points[i] for i in range(len(points)) if mask[i]]
        child = _sub_cluster(
            sub_embeddings,
            sub_points,
            depth=depth + 1,
            max_depth=max_depth,
            min_cluster_size=min_cluster_size,
            cluster_selection_method=cluster_selection_method,
        )
        children.append(child)

    return {"type": "node", "children": children}


def _generate_report(
    tree: list[dict[str, Any]],
    noise_points: list[str],
    all_points: list[str],
) -> dict[str, Any]:
    """生成建树报告指标。"""
    total = len(all_points)
    noise_count = len(noise_points)
    noise_ratio = round(noise_count / total, 4) if total > 0 else 0

    leaf_counts = _count_leaves(tree)
    total_clusters = len(leaf_counts)
    avg_depth = _avg_depth(tree)
    leaf_avg = round(sum(leaf_counts) / len(leaf_counts), 2) if leaf_counts else 0

    return {
        "total_points": total,
        "noise_count": noise_count,
        "noise_ratio": noise_ratio,
        "cluster_count": total_clusters,
        "avg_depth": round(avg_depth, 2),
        "leaf_avg_points": leaf_avg,
        "review_rate": None,  # Step 3 校验后填充
    }


def _count_leaves(tree: list[dict[str, Any]]) -> list[int]:
    """统计所有叶子节点的知识点数"""
    counts: list[int] = []
    for node in tree:
        if node["type"] == "leaf":
            counts.append(len(node["points"]))
        else:
            for child in node.get("children", []):
                counts.extend(_count_leaves([child]))
    return counts


def _avg_depth(tree: list[dict[str, Any]]) -> float:
    """计算树的平均深度"""

    def _depths(node: dict[str, Any], d: int) -> list[int]:
        if node["type"] == "leaf":
            return [d]
        result: list[int] = []
        for child in node.get("children", []):
            result.extend(_depths(child, d + 1))
        return result

    all_depths: list[int] = []
    for node in tree:
        all_depths.extend(_depths(node, 0))

    return sum(all_depths) / len(all_depths) if all_depths else 0


def auto_dry_run(
    knowledge_points: list[str],
    embeddings: np.ndarray,
    *,
    max_attempts: int = 3,
    min_cluster_size: int = 5,
    cluster_selection_method: str = "eom",
    max_depth: int = 5,
) -> dict[str, Any]:
    """自动干跑迭代。

    指标异常时自动调整参数重跑，最多尝试 max_attempts 次。

    Args:
        knowledge_points: 知识点文本列表
        embeddings: 对应的 embedding 矩阵
        max_attempts: 最大尝试次数
        min_cluster_size: 初始 min_cluster_size
        cluster_selection_method: HDBSCAN 方法
        max_depth: 最大深度

    Returns:
        最后一次建树结果
    """
    params = {
        "min_cluster_size": min_cluster_size,
        "cluster_selection_method": cluster_selection_method,
        "max_depth": max_depth,
    }

    diagnostics: list[str] = []

    for attempt in range(max_attempts):
        result = build_tree(
            knowledge_points,
            embeddings,
            **params,
        )
        report = result["report"]
        if "error" in report:
            return result

        issues: list[str] = []
        config_ratio = report.get("config_ratio", 0)
        if config_ratio > 0.05:
            issues.append(f"配置类知识占比 {config_ratio:.1%} > 5%（建议强化提取 prompt）")
        if report["noise_ratio"] > 0.30:
            issues.append(f"噪声比 {report['noise_ratio']:.1%} > 30%")
        if report["cluster_count"] < 3:
            issues.append(f"簇数 {report['cluster_count']} < 3")
        if report["avg_depth"] > 5:
            issues.append(f"平均深度 {report['avg_depth']} > 5")
        if report["leaf_avg_points"] < 2:
            issues.append(f"叶子平均知识点数 {report['leaf_avg_points']} < 2")

        if not issues:
            return result  # 指标正常

        diagnostics.append(f"  尝试 {attempt + 1}: {', '.join(issues)}")

        # 调整参数（每种参数每轮最多调一次，避免多指标同时异常时重复递减）
        adjusted_min = False
        adjusted_depth = False
        if report["noise_ratio"] > 0.30 and not adjusted_min:
            params["min_cluster_size"] = max(3, params["min_cluster_size"] - 1)
            adjusted_min = True
        if report["avg_depth"] > 5 and not adjusted_depth:
            params["max_depth"] = max(3, params["max_depth"] - 1)
            adjusted_depth = True
        if report["leaf_avg_points"] < 2 and not adjusted_min:
            params["min_cluster_size"] = max(3, params["min_cluster_size"] - 1)

    # 3 次后仍异常，输出诊断报告
    result["diagnostics"] = diagnostics
    result["auto_dry_run_exhausted"] = True
    return result
