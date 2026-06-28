"""Step 3: 子簇结构判断 + LLM 校验

对每个非叶子簇，判断子簇之间的关系：
- 平行结构（各自独立，保留多叉）
- 上下位关系（走二分校验）
"""

from __future__ import annotations

from typing import Any

import json
import logging

from knowledge_tree_builder.llm.client import call_llm

logger = logging.getLogger(__name__)


def classify_knowledge_types(
    knowledge_points: list[str],
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> dict[str, Any]:
    """批量标记知识点类型。

    判断每条知识点是（A）原理/方法论/标准流程 还是（B）配置/操作。
    用于 Step 2→3 检查点：如果配置类 > 5%，提示强化提取 prompt。

    Args:
        knowledge_points: 知识点文本列表
        api_url: LLM API 地址
        api_key: API 密钥
        model: 模型名

    Returns:
        {"types": [每条的类型], "config_ratio": 配置类占比}
    """
    if not knowledge_points:
        return {"types": [], "config_ratio": 0.0}

    # 分批标记，每批 20 条
    batch_size = 20
    all_types: list[str] = []
    for i in range(0, len(knowledge_points), batch_size):
        batch = knowledge_points[i:i + batch_size]
        lines = "\n".join(f"{j}. {pt}" for j, pt in enumerate(batch))
        prompt = (
            "判断以下每条知识属于哪一类，每行只返回 A 或 B：\n\n"
            f"{lines}\n\n"
            "A = 原理/方法论/标准流程（持久知识，如定理、算法、框架）\n"
            "B = 配置参数/版本号/操作记录（易过时知识，如端口号、版本、路径）"
        )
        result = call_llm(
            prompt=prompt,
            temperature=0,
            api_url=api_url,
            api_key=api_key,
            model=model,
        )
        batch_types: list[str] = []
        for line in result.strip().splitlines():
            line = line.strip().upper()
            if "B" in line:
                batch_types.append("B")
            else:
                batch_types.append("A")
        # 校验对齐：LLM 返回行数不足时补默认值
        if len(batch_types) < len(batch):
            logger.warning(
                "知识类型标记对齐失败：期望 %d 条，得到 %d 条，补默认值",
                len(batch), len(batch_types),
            )
            batch_types.extend(["A"] * (len(batch) - len(batch_types)))
        elif len(batch_types) > len(batch):
            batch_types = batch_types[:len(batch)]
        all_types.extend(batch_types)

    config_count = sum(1 for t in all_types if t == "B")
    config_ratio = round(config_count / len(all_types), 4) if all_types else 0.0

    return {"types": all_types, "config_ratio": config_ratio}


def judge_subcluster_structure(
    subcluster_texts: dict[str, list[str]],
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> str:
    """LLM 判断子簇之间的关系。

    Args:
        subcluster_texts: 子簇 ID → 该簇知识点文本列表

    Returns:
        'parallel' — 平行结构，保留多叉
        'hierarchical' — 上下位关系，走二分校验
    """
    cluster_descriptions = []
    for cid, texts in subcluster_texts.items():
        preview = texts[:3]  # 每个子簇取前 3 条作为代表
        desc = f"子簇 {cid}:\n" + "\n".join(f"  - {t}" for t in preview)
        cluster_descriptions.append(desc)

    prompt = (
        "根据这些知识点文本，判断子簇之间的关系：\n\n"
        + "\n".join(cluster_descriptions)
        + "\n\n"
        "A：平行结构（各自独立，同层级，互不包含）\n"
        "B：上下位关系（一些子簇是另一些子簇的子领域/子分类）\n\n"
        "只返回 A 或 B，不要其他内容。"
    )

    result = call_llm(
        prompt=prompt,
        temperature=0,
        api_url=api_url,
        api_key=api_key,
        model=model,
    )

    result = result.strip().upper()
    # 严格解析：取第一个出现的 A 或 B 字符
    for ch in result:
        if ch == "A":
            return "parallel"
        if ch == "B":
            return "hierarchical"
    # 兜底：检查关键词
    if "平行" in result:
        return "parallel"
    return "hierarchical"


def binary_verify(
    subcluster_texts: dict[str, list[str]],
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> dict[str, Any]:
    """二分校验：LLM 判断最合理的二分判据。

    当子簇被判断为上下位关系时，进一步问"最合理的二分判据是什么"。

    Args:
        subcluster_texts: 子簇 ID → 知识点文本列表

    Returns:
        {"criterion": str, "agreement": bool}
        criterion: 二分判据描述
        agreement: 与 sub-clustering 结果是否一致
    """
    cluster_descriptions = []
    for cid, texts in subcluster_texts.items():
        preview = texts[:3]
        desc = f"子簇 {cid}:\n" + "\n".join(f"  - {t}" for t in preview)
        cluster_descriptions.append(desc)

    prompt = (
        "以下是一组知识点子簇，请判断它们之间的层次关系。\n\n"
        + "\n".join(cluster_descriptions)
        + "\n\n"
        "如果这些子簇要分成两个上层科目，最合理的二分判据是什么？\n"
        "格式：判据：<简短描述>"
    )

    result = call_llm(
        prompt=prompt,
        temperature=0,
        api_url=api_url,
        api_key=api_key,
        model=model,
    )

    result = result.strip()
    criterion = result.replace("判据：", "").replace("判据:", "").strip()
    if not criterion:
        criterion = result[:100]

    # 校验二分判据是否与 sub-clustering 的子簇对齐
    # 中文短句不适合按空格分词，改用字符级重叠判断
    all_texts = []
    for cid, texts in subcluster_texts.items():
        all_texts.extend(texts)
    combined = "".join(all_texts)
    # 取判据中的汉字字符，与子簇文本做字符级重叠
    criterion_chars = set(c for c in criterion if '\u4e00' <= c <= '\u9fff')
    combined_chars = set(c for c in combined if '\u4e00' <= c <= '\u9fff')
    if criterion_chars and combined_chars:
        overlap = len(criterion_chars & combined_chars) / len(criterion_chars)
        agreement = overlap >= 0.3
    else:
        agreement = None

    return {"criterion": criterion, "agreement": agreement}


def validate_tree(
    tree: list[dict[str, Any]],
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> list[dict[str, Any]]:
    """对整个树进行 LLM 结构校验。

    对每个非叶子节点：
    1. 判断子节点间是平行还是上下位关系
    2. 上下位时执行二分校验，问最合理的二分判据
    3. 统计人工确认率（structure='hierarchical' 且未确认的占比）

    Args:
        tree: 聚类产出的树结构
        api_url: LLM API 地址
        api_key: API 密钥
        model: 模型名

    Returns:
        [校验后的子树列表]，每个非叶子节点增加 "structure" 字段
    """
    validated: list[dict[str, Any]] = []
    for node in tree:
        validated.append(_validate_node(node, api_url=api_url, api_key=api_key, model=model))
    return validated


def _validate_node(
    node: dict[str, Any],
    *,
    api_url: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    """递归校验单个节点"""
    # 噪声浮动节点跳过校验
    if node.get("noise"):
        return {**node, "structure": "noise"}
    if node["type"] == "leaf":
        return node

    children = node.get("children", [])
    if not children:
        return {**node, "structure": "leaf"}

    # 收集每个子节点的知识点文本
    subcluster_texts: dict[str, list[str]] = {}
    for i, child in enumerate(children):
        texts = _collect_node_texts(child)
        if texts:
            subcluster_texts[str(i)] = texts

    if len(subcluster_texts) < 2:
        return {**node, "structure": "single", "children": children}

    structure = judge_subcluster_structure(
        subcluster_texts,
        api_url=api_url,
        api_key=api_key,
        model=model,
    )

    result_node: dict[str, Any] = {**node, "structure": structure}

    # 上下位关系 → 执行二分校验
    if structure == "hierarchical":
        verify_result = binary_verify(
            subcluster_texts,
            api_url=api_url,
            api_key=api_key,
            model=model,
        )
        result_node["binary_criterion"] = verify_result["criterion"]
        result_node["binary_agreement"] = verify_result["agreement"]

    # 递归校验子节点
    validated_children = [
        _validate_node(child, api_url=api_url, api_key=api_key, model=model)
        for child in children
    ]
    result_node["children"] = validated_children

    return result_node


def _collect_node_texts(node: dict[str, Any]) -> list[str]:
    """收集节点下的所有知识点文本"""
    if node["type"] == "leaf":
        return node.get("points", [])
    texts: list[str] = []
    for child in node.get("children", []):
        texts.extend(_collect_node_texts(child))
    return texts
