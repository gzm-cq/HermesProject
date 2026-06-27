#!/usr/bin/env python3
"""几何计算和节点查找工具函数"""


def _compute_bounding_box(nodes, layers, default_w, default_h, padding=30):
    """根据节点和层次计算自适应 viewBox 范围"""
    if not nodes and not layers:
        return 0, 0, default_w, default_h

    xs = []
    ys = []
    xe = []
    ye = []

    for n in nodes:
        xs.append(n.get("x", 0))
        ys.append(n.get("y", 0))
        xe.append(n.get("x", 0) + n.get("w", 100))
        ye.append(n.get("y", 0) + n.get("h", 50))
    for layer in layers:
        xs.append(layer.get("x", 0))
        ys.append(layer.get("y", 0))
        xe.append(layer.get("x", 0) + layer.get("w", 200))
        ye.append(layer.get("y", 0) + layer.get("h", 100))

    min_x = max(0, min(xs) - padding)
    min_y = max(0, min(ys) - padding)
    bw = max(xe) - min_x + padding
    bh = max(ye) - min_y + padding

    return int(min_x), int(min_y), int(bw), int(bh)


def _get_node_by_id(nodes, nid):
    """按 id 查找节点"""
    for n in nodes:
        if n.get("id") == nid:
            return n
    return None


def _compute_orthogonal_edge(src, tgt):
    """
    计算正交（曼哈顿）边路径，返回 [(x1,y1), (x2,y2), ...] 折线坐标列表。
    自动选择出口/入口边，中间最多一个拐点（3 段线），保持视觉整洁。
    """
    sx, sy, sw, sh = src["x"], src["y"], src["w"], src["h"]
    tx, ty, tw, th = tgt["x"], tgt["y"], tgt["w"], tgt["h"]

    scx = sx + sw // 2
    scy = sy + sh // 2
    tcx = tx + tw // 2
    tcy = ty + th // 2

    dx = tcx - scx
    dy = tcy - scy

    if abs(dx) >= abs(dy):
        # 水平主导：出口/入口在左右侧
        if dx >= 0:
            start = (sx + sw, scy)   # 源右侧
            end = (tx, tcy)          # 目标左侧
        else:
            start = (sx, scy)        # 源左侧
            end = (tx + tw, tcy)     # 目标右侧
        if start[1] == end[1]:
            return [start, end]
        mid_x = (start[0] + end[0]) // 2
        return [start, (mid_x, start[1]), (mid_x, end[1]), end]
    else:
        # 垂直主导：出口/入口在上下侧
        if dy >= 0:
            start = (scx, sy + sh)   # 源底部
            end = (tcx, ty)          # 目标顶部
        else:
            start = (scx, sy)        # 源顶部
            end = (tcx, ty + th)     # 目标底部
        if start[0] == end[0]:
            return [start, end]
        mid_y = (start[1] + end[1]) // 2
        return [start, (start[0], mid_y), (end[0], mid_y), end]
