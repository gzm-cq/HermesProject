#!/usr/bin/env python3
"""输入校验 — 验证布局字典的完整性和合法性"""

from .palettes import PALETTES
from .shapes import SHAPES
from .geometry import _compute_orthogonal_edge
from .style_presets import BUILT_IN_PRESETS


# ===== 空间网格索引 =====
def _build_spatial_grid(nodes, cell_size=100):
    """构建空间网格索引，将节点按位置分配到网格单元。
    返回 (grid, cell_size) 其中 grid = {(cx, cy): [node, ...]}
    """
    grid = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nx = n.get("x", 0) or 0
        ny = n.get("y", 0) or 0
        nw = n.get("w", 0) or 0
        nh = n.get("h", 0) or 0
        # 节点可能跨越多个网格单元
        cx_start = int(nx // cell_size)
        cy_start = int(ny // cell_size)
        cx_end = int((nx + nw) // cell_size)
        cy_end = int((ny + nh) // cell_size)
        for cx in range(cx_start, cx_end + 1):
            for cy in range(cy_start, cy_end + 1):
                grid.setdefault((cx, cy), []).append(n)
    return grid


def _grid_query(grid, cell_size, x1, y1, x2, y2):
    """查询线段 (x1,y1)-(x2,y2) 经过的网格单元中的节点列表（去重）。"""
    cx_start = int(min(x1, x2) // cell_size)
    cx_end = int(max(x1, x2) // cell_size)
    cy_start = int(min(y1, y2) // cell_size)
    cy_end = int(max(y1, y2) // cell_size)
    seen_ids = set()
    candidates = []
    for cx in range(cx_start, cx_end + 1):
        for cy in range(cy_start, cy_end + 1):
            for n in grid.get((cx, cy), ()):
                nid = n.get("id", "") or ""
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    candidates.append(n)
    return candidates


# ===== 布局质量校验 =====
def _point_in_rect(x, y, rx, ry, rw, rh):
    """判断点 (x, y) 是否在矩形 [rx, ry, rw, rh] 内部（含边界）"""
    return rx <= x <= rx + rw and ry <= y <= ry + rh


def _segment_intersects_rect(x1, y1, x2, y2, rx, ry, rw, rh):
    """
    判断线段 (x1,y1)-(x2,y2) 是否与矩形 [rx,ry,rw,rh] 相交。
    支持水平、垂直和一般线段。
    """
    left, right = rx, rx + rw
    top, bottom = ry, ry + rh

    if x1 == x2:
        # 垂直线段
        if not (left <= x1 <= right):
            return False
        y_min, y_max = min(y1, y2), max(y1, y2)
        return not (y_max < top or y_min > bottom)
    elif y1 == y2:
        # 水平线段
        if not (top <= y1 <= bottom):
            return False
        x_min, x_max = min(x1, x2), max(x1, x2)
        return not (x_max < left or x_min > right)
    else:
        # 一般线段
        # 检查端点是否在矩形内
        if _point_in_rect(x1, y1, rx, ry, rw, rh) or _point_in_rect(x2, y2, rx, ry, rw, rh):
            return True
        # 检查与四条边的交点
        dx = x2 - x1
        dy = y2 - y1
        # 与左/右边 x = left/right 求交
        if dx != 0:
            for tx in (left - x1) / dx, (right - x1) / dx:
                if 0 <= tx <= 1:
                    ty = y1 + tx * dy
                    if top <= ty <= bottom:
                        return True
        # 与上/下边 y = top/bottom 求交
        if dy != 0:
            for ty in (top - y1) / dy, (bottom - y1) / dy:
                if 0 <= ty <= 1:
                    tx = x1 + ty * dx
                    if left <= tx <= right:
                        return True
        return False


def check_edge_through_vertex(nodes, edges):
    """
    检测边路径是否穿过非端点节点的包围盒。

    使用空间网格索引将 O(E×V) 降为 O(E×k)，k 为局部节点数。

    返回 [(edge_from, edge_to, through_node_id), ...]。
    """
    results = []
    seen = set()
    node_map = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id", "") or ""
        if nid:
            node_map[nid] = n

    # 构建空间网格索引
    grid = _build_spatial_grid(nodes, cell_size=100)

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src_id = edge.get("from", "") or ""
        tgt_id = edge.get("to", "") or ""

        src_node = node_map.get(src_id) if src_id else None
        tgt_node = node_map.get(tgt_id) if tgt_id else None

        if not src_node or not tgt_node:
            continue

        try:
            path = _compute_orthogonal_edge(src_node, tgt_node)
        except (TypeError, KeyError):
            continue

        for i in range(len(path) - 1):
            x1, y1 = path[i]
            x2, y2 = path[i + 1]

            # 仅查询线段经过的网格单元中的节点（O(k) 而非 O(V)）
            for node in _grid_query(grid, 100, x1, y1, x2, y2):
                nid = node.get("id", "") or ""

                # 跳过端点节点本身（起点和终点）
                if nid == src_id or nid == tgt_id:
                    continue
                rx = node.get("x", 0) or 0
                ry = node.get("y", 0) or 0
                rw = node.get("w", 0) or 0
                rh = node.get("h", 0) or 0

                if _segment_intersects_rect(x1, y1, x2, y2, rx, ry, rw, rh):
                    key = (src_id, tgt_id, nid)
                    if key not in seen:
                        seen.add(key)
                        results.append(key)

    return results


def _segments_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """
    判断两条线段是否相交（含端点接触），使用向量叉积法。
    """
    def _ccw(ax_, ay_, bx_, by_, cx_, cy_):
        return (cy_ - ay_) * (bx_ - ax_) - (by_ - ay_) * (cx_ - ax_)

    d1 = _ccw(ax1, ay1, ax2, ay2, bx1, by1)
    d2 = _ccw(ax1, ay1, ax2, ay2, bx2, by2)
    d3 = _ccw(bx1, by1, bx2, by2, ax1, ay1)
    d4 = _ccw(bx1, by1, bx2, by2, ax2, ay2)

    # 一般相交情况
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    # 共线端点接触
    if d1 == 0 and min(ax1, ax2) <= bx1 <= max(ax1, ax2) and min(ay1, ay2) <= by1 <= max(ay1, ay2):
        return True
    if d2 == 0 and min(ax1, ax2) <= bx2 <= max(ax1, ax2) and min(ay1, ay2) <= by2 <= max(ay1, ay2):
        return True
    if d3 == 0 and min(bx1, bx2) <= ax1 <= max(bx1, bx2) and min(by1, by2) <= ay1 <= max(by1, by2):
        return True
    if d4 == 0 and min(bx1, bx2) <= ax2 <= max(bx1, bx2) and min(by1, by2) <= ay2 <= max(by1, by2):
        return True

    return False


def check_edge_crossings(nodes, edges):
    """
    检测边之间的交叉（使用向量叉积判断线段相交）。

    返回 [(edge_key_1, edge_key_2), ...]，其中 edge_key 为 (from_id, to_id)。
    共享端点的边不视为交叉。

    参数:
        nodes: 节点列表。
        edges: 边列表。

    返回:
        交叉边对列表。
    """
    # 预计算所有边的正交路径
    edge_paths = []
    node_map = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id", "") or ""
        if nid:
            node_map[nid] = n
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src_id = edge.get("from", "") or ""
        tgt_id = edge.get("to", "") or ""
        src_node = node_map.get(src_id) if src_id else None
        tgt_node = node_map.get(tgt_id) if tgt_id else None
        if not src_node or not tgt_node:
            continue
        try:
            path = _compute_orthogonal_edge(src_node, tgt_node)
        except (TypeError, KeyError):
            continue
        edge_paths.append(((src_id, tgt_id), path))

    # 预计算每条边的包围盒，用于快速排除不可能相交的边对
    edge_bboxes = []
    for key, path in edge_paths:
        xs = [p[0] for p in path]
        ys = [p[1] for p in path]
        edge_bboxes.append((min(xs), min(ys), max(xs), max(ys)))

    # 构建空间网格索引（网格大小基于平均包围盒尺寸自适应）
    CELL = 120.0
    grid = {}  # (cx, cy): [edge_idx, ...]
    for idx, (bbox, (key, path)) in enumerate(zip(edge_bboxes, edge_paths)):
        x1, y1, x2, y2 = bbox
        cx_start = int(x1 // CELL)
        cy_start = int(y1 // CELL)
        cx_end = int(x2 // CELL)
        cy_end = int(y2 // CELL)
        for cx in range(cx_start, cx_end + 1):
            for cy in range(cy_start, cy_end + 1):
                grid.setdefault((cx, cy), []).append(idx)

    results = []
    counted_pairs = set()  # 去重：同一对边可能通过多个网格单元命中

    for idx_i, (key_i, path_i) in enumerate(edge_paths):
        bbox_i = edge_bboxes[idx_i]

        # 查询边 path_i 经过的网格单元
        x1, y1, x2, y2 = bbox_i
        cx_start = int(min(x1, x2) // CELL)
        cx_end = int(max(x1, x2) // CELL)
        cy_start = int(min(y1, y2) // CELL)
        cy_end = int(max(y1, y2) // CELL)

        seen_j = set()
        for cx in range(cx_start, cx_end + 1):
            for cy in range(cy_start, cy_end + 1):
                for idx_j in grid.get((cx, cy), ()):
                    if idx_j <= idx_i:
                        continue
                    if idx_j in seen_j:
                        continue
                    seen_j.add(idx_j)

                    key_j, path_j = edge_paths[idx_j]

                    # 共享端点的边不视为交叉
                    if key_i[0] == key_j[0] or key_i[0] == key_j[1] or \
                       key_i[1] == key_j[0] or key_i[1] == key_j[1]:
                        continue

                    # 包围盒预过滤
                    bbox_j = edge_bboxes[idx_j]
                    if bbox_i[2] < bbox_j[0] or bbox_j[2] < bbox_i[0] or \
                       bbox_i[3] < bbox_j[1] or bbox_j[3] < bbox_i[1]:
                        continue

                    crossed = False
                    for si in range(len(path_i) - 1):
                        xi1, yi1 = path_i[si]
                        xi2, yi2 = path_i[si + 1]
                        for sj in range(len(path_j) - 1):
                            xj1, yj1 = path_j[sj]
                            xj2, yj2 = path_j[sj + 1]
                            if _segments_intersect(xi1, yi1, xi2, yi2, xj1, yj1, xj2, yj2):
                                crossed = True
                                break
                        if crossed:
                            break

                    if crossed:
                        pair_key = (min(idx_i, idx_j), max(idx_i, idx_j))
                        if pair_key not in counted_pairs:
                            counted_pairs.add(pair_key)
                            results.append((key_i, key_j))

    return results


def score_layout(nodes, edges, _precomputed=None):
    """
    对布局质量进行评分。

    评分公式：score = through_vertex × 20 + crossings × 10 + total_length / 10000

    等级：
        - 优秀: score < 0.1
        - 良好: score < 0.5
        - 一般: score < 1.0
        - 较差: score >= 1.0

    参数:
        nodes: 节点列表。
        edges: 边列表。
        _precomputed: 可选，(through_vertex_list, crossings_list) 预计算结果，避免重复调用。

    返回:
        dict: {score, through_vertex, crossings, total_length, grade}
    """
    # 守卫：非 list 类型直接返回兜底
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {"score": 0.0, "through_vertex": 0, "crossings": 0,
                "total_length": 0.0, "grade": "优秀"}

    # 预计算所有边路径，用于 total_length
    node_map = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id", "") or ""
        if nid:
            node_map[nid] = n
    edge_paths = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src_id = edge.get("from", "") or ""
        tgt_id = edge.get("to", "") or ""
        src_node = node_map.get(src_id) if src_id else None
        tgt_node = node_map.get(tgt_id) if tgt_id else None
        if src_node and tgt_node:
            try:
                edge_paths.append(((src_id, tgt_id), _compute_orthogonal_edge(src_node, tgt_node)))
            except (TypeError, KeyError):
                continue

    # 使用预计算结果或重新计算（避免 validate_plan 中的双重调用）
    if _precomputed is not None:
        through_vertex_list, crossings_list = _precomputed
    else:
        through_vertex_list = check_edge_through_vertex(nodes, edges)
        crossings_list = check_edge_crossings(nodes, edges)

    through_vertex_count = len(through_vertex_list)
    crossings_count = len(crossings_list)

    total_length = 0.0
    for _, path in edge_paths:
        for i in range(len(path) - 1):
            total_length += abs(path[i + 1][0] - path[i][0]) + abs(path[i + 1][1] - path[i][1])

    # 评分: 穿过节点(权重20) + 交叉(权重10) + 边长归一化(权重1)
    score = through_vertex_count * 20 + crossings_count * 10 + total_length / 10000

    if score < 0.1:
        grade = "优秀"
    elif score < 0.5:
        grade = "良好"
    elif score < 1.0:
        grade = "一般"
    else:
        grade = "较差"

    return {
        "score": score,
        "through_vertex": through_vertex_count,
        "crossings": crossings_count,
        "total_length": total_length,
        "grade": grade,
    }


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

    # 配色校验 (需合并 style_presets 中的预设)
    valid_palettes = set(PALETTES.keys()) | set(BUILT_IN_PRESETS.keys())
    palette = plan.get("palette")
    if isinstance(palette, str) and palette not in valid_palettes:
        issues.append(("warning", "palette", f"未知配色 '{palette}'，将使用 academic"))

    # 节点校验
    node_ids = set()
    valid_colors = {k for k in PALETTES.get("academic", {}) if k.startswith("node_")}
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
                if color not in valid_colors:
                    issues.append(("warning", f"{prefix}.color",
                                   f"未知 color '{color}'，将 fallback 到 node_blue"))
            shape = node.get("shape")
            if shape and shape not in SHAPES:
                issues.append(("warning", f"{prefix}.shape",
                               f"未知 shape '{shape}'，将使用 rect"))

    # 边校验
    valid_curves = {"orthogonal", "straight", "bezier"}
    self_loop_edges = []
    connected_node_ids = set()
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
            # 自环检测
            if src and tgt and src == tgt:
                self_loop_edges.append(i)
            # 记录参与边的节点
            if src:
                connected_node_ids.add(src)
            if tgt:
                connected_node_ids.add(tgt)
            curve = edge.get("curve")
            if curve is not None and curve not in valid_curves:
                issues.append(("warning", f"{prefix}.curve",
                               f"未知 curve '{curve}'，仅支持 orthogonal/straight/bezier"))
            bidirectional = edge.get("bidirectional")
            if bidirectional is not None and not isinstance(bidirectional, bool):
                issues.append(("warning", f"{prefix}.bidirectional", "必须是 bool 类型"))
            points = edge.get("points")
            if points is not None and not isinstance(points, list):
                issues.append(("warning", f"{prefix}.points", "必须是 list 类型"))

    if self_loop_edges:
        issues.append(("warning", "edges",
                       f"发现自环边 (edges {self_loop_edges})，将独立排布"))

    # 孤立节点检测
    if node_ids:
        isolated = node_ids - connected_node_ids
        if isolated:
            issues.append(("warning", "nodes",
                           f"发现孤立节点 {list(isolated)}，将独立排布"))

    # group 一致性 + 深度检测
    if isinstance(nodes, list):
        max_depth = 0
        for i, node in enumerate(nodes):
            prefix = f"nodes[{i}]"
            if not isinstance(node, dict):
                continue
            group = node.get("group")
            if group is not None:
                if not isinstance(group, str):
                    issues.append(("warning", f"{prefix}.group", "必须是字符串"))
                else:
                    if group.startswith("/") or group.endswith("/"):
                        issues.append(("warning", f"{prefix}.group",
                                       "group 路径不应以 '/' 开头或结尾"))
                    if "//" in group:
                        issues.append(("warning", f"{prefix}.group",
                                       "group 路径包含连续的 '/'"))
                    depth = len([p for p in group.split("/") if p])
                    if depth > max_depth:
                        max_depth = depth
        if max_depth > 3:
            issues.append(("warning", "nodes",
                           f"容器嵌套深度 {max_depth} 超过建议值 3，可能影响可读性"))

    # P6: 布局质量检测（仅在 validate_layout 为 True 时执行）
    if plan.get("validate_layout") and isinstance(nodes, list) and isinstance(edges, list):
        # 边穿过节点检测
        through_vertex = check_edge_through_vertex(nodes, edges)
        for tv in through_vertex:
            issues.append(("warning", "layout",
                           f"边 {tv[0]}→{tv[1]} 穿过节点 '{tv[2]}'"))

        # 边交叉检测
        crossings = check_edge_crossings(nodes, edges)
        for c in crossings:
            issues.append(("warning", "layout",
                           f"边 {c[0][0]}↔{c[0][1]} 与边 {c[1][0]}↔{c[1][1]} 交叉"))

        # 布局质量评分（传入预计算结果，避免重复调用检测函数）
        score = score_layout(nodes, edges, _precomputed=(through_vertex, crossings))
        issues.append(("warning", "layout",
                       f"布局质量评分: {score['score']:.4f} ({score['grade']})"))

    return issues