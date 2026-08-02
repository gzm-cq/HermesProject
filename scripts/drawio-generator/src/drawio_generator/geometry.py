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
    sx, sy, sw, sh = src.get("x", 0) or 0, src.get("y", 0) or 0, src.get("w", 0) or 0, src.get("h", 0) or 0
    tx, ty, tw, th = tgt.get("x", 0) or 0, tgt.get("y", 0) or 0, tgt.get("w", 0) or 0, tgt.get("h", 0) or 0

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


def _clip_line_to_rect(x1, y1, x2, y2, rx, ry, rw, rh):
    """
    计算从 (x1,y1) 朝 (x2,y2) 方向的射线与矩形边界 [rx,ry,rw,rh] 的交点。
    假设 (x1,y1) 在矩形内部。返回交点坐标 (ix, iy)。
    """
    dx = x2 - x1
    dy = y2 - y1
    left, right = rx, rx + rw
    top, bottom = ry, ry + rh

    t_candidates = []
    if dx > 0:
        t_candidates.append((right - x1) / dx)
    elif dx < 0:
        t_candidates.append((left - x1) / dx)
    if dy > 0:
        t_candidates.append((bottom - y1) / dy)
    elif dy < 0:
        t_candidates.append((top - y1) / dy)

    if not t_candidates:
        return x1, y1
    valid_t = [t for t in t_candidates if t >= 0]
    if not valid_t:
        return x1, y1
    t = min(valid_t)
    return x1 + t * dx, y1 + t * dy


def _compute_straight_edge(src, tgt):
    """
    计算直线边路径，起点/终点裁剪到节点边界。
    返回 [(x1,y1), (x2,y2)]。
    """
    sx, sy, sw, sh = src.get("x", 0) or 0, src.get("y", 0) or 0, src.get("w", 0) or 0, src.get("h", 0) or 0
    tx, ty, tw, th = tgt.get("x", 0) or 0, tgt.get("y", 0) or 0, tgt.get("w", 0) or 0, tgt.get("h", 0) or 0

    scx = sx + sw // 2
    scy = sy + sh // 2
    tcx = tx + tw // 2
    tcy = ty + th // 2

    # 起点：从 src 中心朝 tgt 中心，与 src 边界相交
    x1, y1 = _clip_line_to_rect(scx, scy, tcx, tcy, sx, sy, sw, sh)
    # 终点：从 tgt 中心朝 src 中心，与 tgt 边界相交
    x2, y2 = _clip_line_to_rect(tcx, tcy, scx, scy, tx, ty, tw, th)

    return [(int(round(x1)), int(round(y1))),
            (int(round(x2)), int(round(y2)))]


def _compute_bezier_edge(src, tgt):
    """
    计算三次贝塞尔曲线边路径。
    返回控制点列表 [(start), (cp1), (cp2), (end)]，用于 SVG 的 C 命令。
    起点/终点已裁剪到节点边界。
    """
    sx, sy, sw, sh = src.get("x", 0) or 0, src.get("y", 0) or 0, src.get("w", 0) or 0, src.get("h", 0) or 0
    tx, ty, tw, th = tgt.get("x", 0) or 0, tgt.get("y", 0) or 0, tgt.get("w", 0) or 0, tgt.get("h", 0) or 0

    scx = sx + sw // 2
    scy = sy + sh // 2
    tcx = tx + tw // 2
    tcy = ty + th // 2

    dx = tcx - scx
    dy = tcy - scy

    # 控制点基于方向偏移（约 1/3 距离）
    offset = max(abs(dx), abs(dy)) // 3
    if abs(dx) >= abs(dy):
        cp1 = (scx + offset, scy)
        cp2 = (tcx - offset, tcy)
    else:
        cp1 = (scx, scy + offset)
        cp2 = (tcx, tcy - offset)

    # 起点/终点裁剪到边界
    x1, y1 = _clip_line_to_rect(scx, scy, cp1[0], cp1[1], sx, sy, sw, sh)
    x2, y2 = _clip_line_to_rect(tcx, tcy, cp2[0], cp2[1], tx, ty, tw, th)

    return [(int(round(x1)), int(round(y1))),
            (int(round(cp1[0])), int(round(cp1[1]))),
            (int(round(cp2[0])), int(round(cp2[1]))),
            (int(round(x2)), int(round(y2)))]


def compute_edge_path(src, tgt, curve="orthogonal"):
    """
    根据曲线类型计算边路径。

    参数:
        src, tgt: 源节点和目标节点 dict
        curve: "orthogonal" | "straight" | "bezier"

    返回:
        points 列表，格式取决于 curve 类型:
        - orthogonal/straight: [(x1,y1), (x2,y2), ...]
        - bezier: [(start), (cp1), (cp2), (end)]
    """
    if curve == "straight":
        return _compute_straight_edge(src, tgt)
    if curve == "bezier":
        return _compute_bezier_edge(src, tgt)
    return _compute_orthogonal_edge(src, tgt)
