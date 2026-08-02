#!/usr/bin/env python3
"""分组容器 — 解析嵌套 group 路径、计算包围盒、分配颜色、生成容器 mxCell"""

from .palettes import _resolve_color


# ===== 容器颜色循环 =====
GROUP_COLOR_CYCLE = [
    "node_blue", "node_green", "node_orange",
    "node_purple", "node_yellow", "node_red", "node_cyan",
]


def parse_group_tree(nodes):
    """
    解析嵌套 group 路径。

    参数:
        nodes: [{id, group, ...}] 节点列表，group 为 '/' 分隔的路径
    返回:
        {gpath, direct, children, ordered}
          gpath: {node_id: (path_tuple)} — 每个节点的最深容器路径
          direct: {path_tuple: [node_id, ...]} — 直属于该路径的节点
          children: {path_tuple: [child_path_tuple, ...]} — 子容器
          ordered: [path_tuple, ...] — 所有容器路径，浅到深
    """
    gpath = {}
    direct = {}
    children = {}
    all_paths = set()

    for n in nodes:
        group_str = n.get("group", "")
        if not group_str:
            continue
        nid = n.get("id", "")
        if not nid:
            continue

        # 拆分路径，例如 "server/db" → ("server", "db")
        parts = tuple(group_str.strip("/").split("/"))
        gpath[nid] = parts

        # 收集所有祖先路径
        for i in range(1, len(parts) + 1):
            ancestor = parts[:i]
            all_paths.add(ancestor)

    # 构建 direct 和 children
    for path in all_paths:
        direct.setdefault(path, [])
        children.setdefault(path, [])

    for nid, path in gpath.items():
        direct[path].append(nid)

    # 构建 children 关系 — O(P)：对每个路径，直接查找其父路径
    for path in all_paths:
        if len(path) > 1:
            parent = path[:-1]
            if parent in all_paths:
                children[parent].append(path)

    # 排序 children 列表，确保顺序稳定（按路径字典序）
    for path in children:
        children[path].sort()

    # 排序：浅到深
    ordered = sorted(all_paths, key=lambda p: (len(p), p))

    return {
        "gpath": gpath,
        "direct": direct,
        "children": children,
        "ordered": ordered,
    }


def compute_container_boxes(tree, positioned_nodes, padding=24):
    """
    从最深容器开始向上计算包围盒。

    参数:
        tree: parse_group_tree 的输出
        positioned_nodes: [{id, x, y, w, h, ...}]
        padding: 容器内边距（默认 24）
    返回:
        {(path_tuple): (x, y, w, h)}
    """
    node_map = {n["id"]: n for n in positioned_nodes if n.get("id")}
    boxes = {}
    ordered = tree["ordered"]
    direct = tree["direct"]
    children = tree["children"]

    # 从最深到最浅处理
    for path in reversed(ordered):
        xs, ys, xe, ye = [], [], [], []

        # 直属于该容器的节点
        for nid in direct.get(path, []):
            n = node_map.get(nid)
            if n:
                nx = n.get("x", 0) or 0
                ny = n.get("y", 0) or 0
                nw = n.get("w", 0) or 0
                nh = n.get("h", 0) or 0
                xs.append(nx)
                ys.append(ny)
                xe.append(nx + nw)
                ye.append(ny + nh)

        # 子容器（已计算过包围盒）
        for child_path in children.get(path, []):
            cb = boxes.get(child_path)
            if cb:
                cx, cy, cw, ch = cb
                xs.append(cx)
                ys.append(cy)
                xe.append(cx + cw)
                ye.append(cy + ch)

        if xs:
            min_x = min(xs) - padding
            min_y = min(ys) - padding
            max_x = max(xe) + padding
            max_y = max(ye) + padding
            boxes[path] = (min_x, min_y, max_x - min_x, max_y - min_y)

    return boxes


def assign_group_colors(tree, palette):
    """
    为每个顶层 group 分配颜色，节点无自定义 color 时自动着色。

    参数:
        tree: parse_group_tree 的输出
        palette: 完整配色字典（含 node_blue, node_green 等）
    返回:
        {group_path: color_key}
    """
    # 找出顶层组（一级路径）
    top_level = set()
    for path in tree["ordered"]:
        if len(path) == 1:
            top_level.add(path)

    group_colors = {}
    color_idx = 0

    for path in sorted(top_level):
        color_key = GROUP_COLOR_CYCLE[color_idx % len(GROUP_COLOR_CYCLE)]
        group_colors[path] = color_key
        color_idx += 1

    return group_colors


def apply_group_colors_to_nodes(nodes, tree, group_colors, palette):
    """
    将组颜色应用到节点（修改节点 style 追加 fillColor/strokeColor）。
    仅对没有自定义 color 的节点生效。

    参数:
        nodes: 节点列表（不会修改原列表及元素，返回新列表）
        tree: parse_group_tree 的输出
        group_colors: assign_group_colors 的输出
        palette: 完整配色字典
    返回:
        新的 nodes 列表（无 color 的节点获得 group 颜色）
    """
    gpath = tree["gpath"]
    result_nodes = []
    for n in nodes:
        n_copy = dict(n)
        nid = n_copy.get("id", "")
        if nid and nid in gpath and not n_copy.get("color"):
            path = gpath[nid]
            top_key = path[:1]  # 一级路径
            color_key = group_colors.get(top_key)
            if color_key:
                n_copy["color"] = color_key
        result_nodes.append(n_copy)

    return result_nodes


def generate_container_cells(tree, container_boxes, group_colors, palette, nid_counter):
    """
    生成 draw.io 容器 mxCell 列表。

    参数:
        tree: parse_group_tree 的输出
        container_boxes: compute_container_boxes 的输出
        group_colors: assign_group_colors 的输出
        palette: 完整配色字典（用于解析实际颜色 hex 值）
        nid_counter: 起始 ID 编号
    返回:
        (cells, new_nid_counter, path_cid_map)
        cells: [(cid, parent, style, x, y, w, h, label), ...]
        path_cid_map: {path_tuple: cid}
    """
    cells = []
    path_cid_map = {}
    nid = nid_counter

    # 按浅到深顺序（外层先画，内层后画）
    for path in tree["ordered"]:
        if path not in container_boxes:
            continue

        cx, cy, cw, ch = container_boxes[path]

        # 该容器的颜色
        top_key = path[:1]
        color_key = group_colors.get(top_key, "node_blue")

        # 将 color_key 解析为实际 hex 颜色值
        stroke_color = palette.get(color_key, {}).get("stroke", "#6c8ebf")

        label = path[-1]  # 最后一段作为容器标题

        style = (
            "rounded=0;whiteSpace=wrap;html=1;"
            "fillColor=none;"
            f"strokeColor={stroke_color};"
            "dashed=1;verticalAlign=top;fontStyle=2;"
        )

        cid = str(nid)
        nid += 1
        cells.append((cid, "1", style, cx, cy, cw, ch, label))
        path_cid_map[path] = cid

    return cells, nid, path_cid_map


def compute_node_offsets(tree, container_boxes):
    """
    计算每个节点相对于其直接容器的偏移量。

    参数:
        tree: parse_group_tree 的输出
        container_boxes: compute_container_boxes 的输出
    返回:
        {node_id: (offset_x, offset_y)}
    """
    gpath = tree["gpath"]
    offsets = {}

    for nid, path in gpath.items():
        if not path or path not in container_boxes:
            continue
        cx, cy, _, _ = container_boxes[path]
        offsets[nid] = (cx, cy)

    return offsets