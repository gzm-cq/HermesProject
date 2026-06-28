#!/usr/bin/env python3
"""输入校验 — 验证布局字典的完整性和合法性"""

from .palettes import PALETTES
from .shapes import SHAPES


# ===== 输入校验 =====
def validate_plan(plan):
    """
    校验布局字典的完整性和合法性。
    返回 [(type, field, msg), ...]，type 为 "error" 或 "warning"。
    """
    issues = []

    if not isinstance(plan, dict):
        return [("error", "root", "plan 必须是 dict")]

    # 必填字段
    title = plan.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        issues.append(("error", "title", "缺少或无效"))

    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        issues.append(("error", "nodes", "缺少 nodes 或不是 list"))

    edges = plan.get("edges")
    if not isinstance(edges, list):
        issues.append(("error", "edges", "缺少 edges 或不是 list"))

    width = plan.get("width")
    if width is not None and (not isinstance(width, (int, float)) or width <= 0):
        issues.append(("warning", "width", "必须为正数"))

    height = plan.get("height")
    if height is not None and (not isinstance(height, (int, float)) or height <= 0):
        issues.append(("warning", "height", "必须为正数"))

    fmt = plan.get("format", "drawio")
    if fmt not in ("svg", "drawio"):
        issues.append(("warning", "format", f"未知格式 '{fmt}'，仅支持 svg/drawio"))

    # 配色校验
    palette = plan.get("palette")
    if isinstance(palette, str) and palette not in PALETTES:
        issues.append(("warning", "palette", f"未知配色 '{palette}'，将使用 academic"))

    # 节点校验
    node_ids = set()
    if isinstance(nodes, list):
        for i, node in enumerate(nodes):
            prefix = f"nodes[{i}]"
            if not isinstance(node, dict):
                issues.append(("error", prefix, "不是 dict"))
                continue
            nid = node.get("id")
            if not nid:
                issues.append(("warning", f"{prefix}.id", "缺失 id（将自动分配）"))
            else:
                if nid in node_ids:
                    issues.append(("error", f"{prefix}.id", f"id '{nid}' 重复"))
                node_ids.add(nid)
            for field in ("x", "y", "w", "h"):
                val = node.get(field)
                if val is None or not isinstance(val, (int, float)):
                    issues.append(("error", f"{prefix}.{field}", "缺失或不是数字"))
            color = node.get("color")
            if color:
                valid_colors = {k for k in PALETTES.get("academic", {}) if k.startswith("node_")}
                if color not in valid_colors:
                    issues.append(("warning", f"{prefix}.color",
                                   f"未知 color '{color}'，将 fallback 到 node_blue"))
            shape = node.get("shape")
            if shape and shape not in SHAPES:
                issues.append(("warning", f"{prefix}.shape",
                               f"未知 shape '{shape}'，将使用 rect"))

    # 边校验
    if isinstance(edges, list):
        for i, edge in enumerate(edges):
            prefix = f"edges[{i}]"
            if not isinstance(edge, dict):
                issues.append(("error", prefix, "不是 dict"))
                continue
            src = edge.get("from", "")
            tgt = edge.get("to", "")
            if not src:
                issues.append(("warning", f"{prefix}.from", "缺失"))
            if not tgt:
                issues.append(("warning", f"{prefix}.to", "缺失"))
            if src and tgt and node_ids:
                if src not in node_ids:
                    issues.append(("warning", f"{prefix}.from", f"引用未定义节点 '{src}'"))
                if tgt not in node_ids:
                    issues.append(("warning", f"{prefix}.to", f"引用未定义节点 '{tgt}'"))

    return issues
