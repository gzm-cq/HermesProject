#!/usr/bin/env python3
"""边样式工具 — 基础边样式、flowAnimation、端口分布、箭头头间距检查。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


# ===== A. 基础边样式 =====

def get_base_edge_style(edge_style: str = "orthogonal") -> str:
    """返回 draw.io 边的基础样式字符串。

    参数:
        edge_style: 边样式类型，当前仅支持 "orthogonal"。

    返回:
        基础样式字符串，例如:
        ``edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;labelBackgroundColor=#ffffff;``
    """
    return (
        "edgeStyle=orthogonalEdgeStyle;"
        "rounded=1;"
        "orthogonalLoop=1;"
        "jettySize=auto;"
        "html=1;"
        "labelBackgroundColor=#ffffff;"
    )


# ===== B. flowAnimation =====

def apply_flow_animation(style: str, enabled: bool = False) -> str:
    """在边样式上追加 flowAnimation。

    参数:
        style: 现有边样式字符串。
        enabled: 是否启用流动动画。

    返回:
        更新后的样式字符串（enabled=True 时追加 ``flowAnimation=1;``）。
    """
    if enabled and "flowAnimation" not in style:
        style += "flowAnimation=1;"
    return style


# ===== C. edgeports 算法 =====

def _pick_side(src_node: dict[str, Any], tgt_node: dict[str, Any], is_exit: bool) -> str:
    """根据源/目标节点相对方位选择最近侧边。

    返回 ``"top" | "bottom" | "left" | "right"``。
    """
    scx = (src_node.get("x", 0) or 0) + (src_node.get("w", 0) or 0) / 2
    scy = (src_node.get("y", 0) or 0) + (src_node.get("h", 0) or 0) / 2
    tcx = (tgt_node.get("x", 0) or 0) + (tgt_node.get("w", 0) or 0) / 2
    tcy = (tgt_node.get("y", 0) or 0) + (tgt_node.get("h", 0) or 0) / 2

    dx = tcx - scx
    dy = tcy - scy

    if is_exit:
        # 出口侧：从源节点朝向目标节点的方向
        if abs(dx) >= abs(dy):
            return "right" if dx >= 0 else "left"
        else:
            return "bottom" if dy >= 0 else "top"
    else:
        # 入口侧：从目标节点朝向源节点的方向（即边的到达方向）
        if abs(dx) >= abs(dy):
            return "left" if dx >= 0 else "right"
        else:
            return "top" if dy >= 0 else "bottom"


def _side_to_coords(side: str) -> tuple[float, float]:
    """将侧边名称映射为 (exitX, exitY) 基准坐标。"""
    if side == "top":
        return (0.5, 0.0)   # exitX=0.5, exitY=0 (top)
    if side == "bottom":
        return (0.5, 1.0)   # exitX=0.5, exitY=1 (bottom)
    if side == "left":
        return (0.0, 0.5)   # exitX=0, exitY=0.5 (left)
    # right
    return (1.0, 0.5)       # exitX=1, exitY=0.5 (right)


def _distribute_on_side(
    side: str, i: int, n: int, base_coord: float
) -> float:
    """在同侧边均匀分布坐标。

    - top/bottom: 沿水平方向均匀分布（exitX ∈ [0,1]）
    - left/right: 沿垂直方向均匀分布（exitY ∈ [0,1]）

    使用 ``coord = (i + 0.5) / N``。
    """
    if n <= 1:
        return base_coord
    return (i + 0.5) / n


def distribute_ports(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> dict[tuple[str, str], tuple[float, float, float, float]]:
    """为每条边计算端口位置 (exitX, exitY, entryX, entryY)。

    算法：
      1. 对每个节点统计出/入边数量。
      2. 根据目标方位选择最近侧边 (top/bottom/left/right)。
      3. 同侧边均匀分布：``coord = (i + 0.5) / N``。

    参数:
        nodes: 节点列表，每个节点含 ``id``, ``x``, ``y``, ``w``, ``h``。
        edges: 边列表，每条边含 ``from``, ``to``。

    返回:
        ``{(src_id, tgt_id): (exitX, exitY, entryX, entryY)}``。
        未找到节点（id 不匹配）的边被跳过。
    """
    node_map = {n.get("id"): n for n in nodes if n.get("id")}

    # 统计每个节点的出/入边（按侧边分组）
    out_edges_by_side: dict[str, dict[str, list[tuple[str, str]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    in_edges_by_side: dict[str, dict[str, list[tuple[str, str]]]] = (
        defaultdict(lambda: defaultdict(list))
    )

    for edge in edges:
        src_id = edge.get("from", "")
        tgt_id = edge.get("to", "")
        src = node_map.get(src_id)
        tgt = node_map.get(tgt_id)
        if not src or not tgt:
            continue

        out_side = _pick_side(src, tgt, is_exit=True)
        in_side = _pick_side(src, tgt, is_exit=False)

        out_edges_by_side[src_id][out_side].append((src_id, tgt_id))
        in_edges_by_side[tgt_id][in_side].append((src_id, tgt_id))

    result: dict[tuple[str, str], list[float]] = {}

    # 分配出口端口（exitX/exitY）——基于源节点出边按侧边分组均匀分布
    for src_id, sides in out_edges_by_side.items():
        for side, edge_keys in sides.items():
            base_x, base_y = _side_to_coords(side)
            n = len(edge_keys)
            for i, key in enumerate(edge_keys):
                if side in ("top", "bottom"):
                    exit_x = _distribute_on_side(side, i, n, base_x)
                    exit_y = base_y
                else:
                    # left/right: exitY 均匀分布，exitX 固定
                    exit_x = base_x
                    exit_y = _distribute_on_side(side, i, n, base_y)
                result.setdefault(key, [exit_x, exit_y, 0.5, 0.5])

    # 分配入口端口（entryX/entryY）——基于目标节点入边按侧边分组均匀分布
    for tgt_id, sides in in_edges_by_side.items():
        for side, edge_keys in sides.items():
            base_x, base_y = _side_to_coords(side)
            n = len(edge_keys)
            for i, key in enumerate(edge_keys):
                if side in ("top", "bottom"):
                    entry_x = _distribute_on_side(side, i, n, base_x)
                    entry_y = base_y
                else:
                    entry_x = base_x
                    entry_y = _distribute_on_side(side, i, n, base_y)
                if key in result:
                    result[key][2] = entry_x
                    result[key][3] = entry_y
                else:
                    result[key] = [0.5, 0.5, entry_x, entry_y]

    # 以 tuple 形式返回
    return {k: (v[0], v[1], v[2], v[3]) for k, v in result.items()}


# ===== D. 箭头头间距检查 =====

def check_arrowhead_gap(
    edge_paths: list[list[tuple[float, float]]], min_gap: float = 20.0
) -> list[str]:
    """检查箭头头间距是否过近。

    当多条边汇聚到同一个点时，如果它们的箭头头距离小于 min_gap，则发出警告。

    参数:
        edge_paths: 每条边的路径点列表，每个点 (x, y)。
        min_gap: 最小间距阈值（像素）。

    返回:
        警告消息列表，每条消息描述一个间距过近的问题。
    """
    warnings: list[str] = []
    if len(edge_paths) < 2:
        return warnings

    # 收集每条边的最后一对点（倒数第二点到倒数第一点 — 箭头段）
    arrow_segments: list[tuple[float, float, float, float]] = []
    for path in edge_paths:
        if len(path) < 2:
            continue
        x1, y1 = path[-2]
        x2, y2 = path[-1]
        arrow_segments.append((x1, y1, x2, y2))

    if len(arrow_segments) < 2:
        return warnings

    # 两两比较箭头段端点距离
    for i in range(len(arrow_segments)):
        for j in range(i + 1, len(arrow_segments)):
            _, _, ix, iy = arrow_segments[i]
            _, _, jx, jy = arrow_segments[j]
            dist = ((ix - jx) ** 2 + (iy - jy) ** 2) ** 0.5
            if dist < min_gap:
                warnings.append(
                    f"箭头头间距过近: 边 {i} 和边 {j} 端点距离 {dist:.1f}px "
                    f"(阈值 {min_gap}px)"
                )

    return warnings