"""Step 4: LLM 节点命名 — 科目名/知识点名

对每个节点用 LLM 生成名称：
- 非叶子节点 → 科目名（4-8 字，子节点的共同主题）
- 叶子节点 → 知识点名（该知识点的简短名称）
"""

from __future__ import annotations

from typing import Any

from knowledge_tree_builder.llm.client import call_llm


def name_node(
    points_or_texts: list[str],
    node_type: str,
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> str:
    """LLM 命名一个节点。

    Args:
        points_or_texts: 叶子节点的知识点文本 / 非叶子节点的子节点文本代表
        node_type: 'leaf' 或 'subject'
        api_url: LLM API 地址
        api_key: API 密钥
        model: 模型名

    Returns:
        节点名称（通常 4-8 个汉字）
    """
    if not points_or_texts:
        return "未命名"

    sample = points_or_texts[:5]  # 取前 5 条避免超长

    if node_type == "leaf":
        prompt = (
            "以下是一个知识点的描述。请用 2-8 个字概括这个知识点的名称：\n\n"
            + "\n".join(f"  {t}" for t in sample)
            + "\n\n只返回名称，不要多余内容。"
        )
    else:
        prompt = (
            "以下是一组相关知识点/子科目的文本。请找出它们的共同主题，"
            "用 4-8 个字概括为科目名称：\n\n"
            + "\n".join(f"  {t}" for t in sample)
            + "\n\n只返回名称，不要多余内容。"
        )

    name = call_llm(
        prompt=prompt,
        temperature=0,
        max_tokens=8192,
        api_url=api_url,
        api_key=api_key,
        model=model,
    )
    name = name.strip().strip('"').strip("'").strip("「").strip("」")
    return name if name else "未命名"


def name_tree(
    tree: list[dict[str, Any]],
    *,
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    api_key: str = "",
    model: str = "s-deepseek-v4-flash",
) -> list[dict[str, Any]]:
    """递归命名整个树。

    Args:
        tree: 校验后的树结构（含 "structure" 字段）
        api_url: LLM API 地址
        api_key: API 密钥
        model: 模型名

    Returns:
        命名后的树（每个节点增加 "name" 字段）
    """
    named: list[dict[str, Any]] = []
    for node in tree:
        named.append(_name_node(node, api_url=api_url, api_key=api_key, model=model))
    return named


def _name_node(
    node: dict[str, Any],
    *,
    api_url: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    """递归命名单个节点"""
    children = node.get("children", [])

    if node["type"] == "leaf" or not children:
        # 叶子节点：从知识点文本命名
        texts = node.get("points", [])
        name = name_node(texts, "leaf", api_url=api_url, api_key=api_key, model=model)
        return {**node, "name": name}

    # 非叶子节点：递归命名子节点后，用子节点名做代表
    named_children = [
        _name_node(child, api_url=api_url, api_key=api_key, model=model) for child in children
    ]

    child_names = [c.get("name", "") for c in named_children if c.get("name")]
    name = name_node(child_names if child_names else _collect_first_texts(named_children), "subject",
                     api_url=api_url, api_key=api_key, model=model)

    return {**node, "name": name, "children": named_children}


def _collect_first_texts(nodes: list[dict[str, Any]]) -> list[str]:
    """从节点列表中收集首批文本用于命名"""
    texts: list[str] = []
    for node in nodes:
        if node["type"] == "leaf":
            pts = node.get("points", [])
            if pts:
                texts.append(pts[0])
        else:
            child_texts = _collect_first_texts(node.get("children", []))
            if child_texts:
                texts.append(child_texts[0])
        if len(texts) >= 3:
            break
    return texts
