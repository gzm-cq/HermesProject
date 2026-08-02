#!/usr/bin/env python3
"""自动布局引擎 — 纯 Python dagre 风格层级布局，零依赖"""

from .geometry import _compute_orthogonal_edge


# ===== 布局常量 =====
DEFAULT_NODE_W = 160
DEFAULT_NODE_H = 60
HORIZONTAL_GAP = 60      # 同层节点水平间距
VERTICAL_GAP = 120       # 层间垂直间距
LAYER_PADDING = 40       # 画布四边内边距

# 动态间距常量（按节点复杂度）
SIMPLE_GAP = 200         # ≤5 节点
SIMPLE_LAYER_GAP = 150
SIMPLE_CORRIDOR = 60
MEDIUM_GAP = 280         # 6-10 节点
MEDIUM_LAYER_GAP = 200
MEDIUM_CORRIDOR = 80
COMPLEX_GAP = 350        # >10 节点
COMPLEX_LAYER_GAP = 250
COMPLEX_CORRIDOR = 100


def _get_spacing_by_complexity(node_count):
    """
    根据节点复杂度自动计算间距。

    参数:
        node_count: 节点总数
    返回:
        (gap, layer_gap, corridor) 三元组
    """
    if node_count <= 5:
        return (SIMPLE_GAP, SIMPLE_LAYER_GAP, SIMPLE_CORRIDOR)
    elif node_count <= 10:
        return (MEDIUM_GAP, MEDIUM_LAYER_GAP, MEDIUM_CORRIDOR)
    else:
        return (COMPLEX_GAP, COMPLEX_LAYER_GAP, COMPLEX_CORRIDOR)


def layout_plan(nodes, edges, direction="vertical", **kwargs):
    """
    自动计算节点坐标。

    参数:
        nodes: [{id, label, w?, h?, ...}] 节点列表
        edges: [{from, to, ...}] 边列表
        direction: "vertical" | "horizontal" | "auto" (自动检测)
        gap: 同层节点间距 (默认 60)
        layer_gap: 层间间距 (默认 120)
        padding: 画布内边距 (默认 40)
        gap_auto: 是否根据节点数自动计算间距 (默认 True)
        corridor_gap: 路由走廊额外间距 (默认 0，gap_auto 模式自动设置)
    返回:
        {"nodes": [{id, label, x, y, w, h, ...}], "width": N, "height": N,
         "has_cycle": bool, "back_edges": [...], "edge_routes": [...]}
    """
    gap_auto = kwargs.get("gap_auto", True)
    user_gap = kwargs.get("gap")
    user_layer_gap = kwargs.get("layer_gap")
    user_corridor = kwargs.get("corridor_gap", 0)

    # 自动间距计算
    if gap_auto and user_gap is None and user_layer_gap is None:
        auto_gap, auto_layer_gap, auto_corridor = _get_spacing_by_complexity(len(nodes))
        gap = auto_gap
        layer_gap = auto_layer_gap
        corridor_gap = auto_corridor if user_corridor == 0 else user_corridor
    else:
        gap = user_gap if user_gap is not None else HORIZONTAL_GAP
        layer_gap = user_layer_gap if user_layer_gap is not None else VERTICAL_GAP
        corridor_gap = user_corridor

    padding = kwargs.get("padding", LAYER_PADDING)
    # 为缺失尺寸的节点填充默认值
    resolved = []
    for n in nodes:
        node = dict(n)
        node.setdefault("w", DEFAULT_NODE_W)
        node.setdefault("h", DEFAULT_NODE_H)
        resolved.append(node)

    # 分离孤立节点（不参与任何边的节点）
    isolated_nodes, connected_nodes = _separate_isolated(resolved, edges)

    # 预构建 node_map，供多个函数复用
    node_map = {n.get("id"): n for n in connected_nodes if n.get("id")}

    topo_order = []
    predecessors = {}
    if connected_nodes:
        # 构建邻接关系（仅连通节点）
        adj, in_degree = _build_adjacency(connected_nodes, edges)

        # 拓扑排序
        topo_order, has_cycle = _topological_sort(adj, in_degree, connected_nodes)

        # 构建前驱关系（供 _assign_layers 和 _reduce_crossings 共用）
        for pred_id, targets in adj.items():
            for tgt in targets:
                predecessors.setdefault(tgt, []).append(pred_id)

        # 层级分配（复用预构建的 node_map 和 predecessors）
        layers = _assign_layers(topo_order, adj, node_map, predecessors)

        # 自动检测布局方向
        if direction == "auto":
            layer_counts = {}
            for nid, lid in layers.items():
                layer_counts[lid] = layer_counts.get(lid, 0) + 1
            depth = len(layer_counts)
            width = max(layer_counts.values()) if layer_counts else 1
            direction = "horizontal" if width > depth else "vertical"

        # 坐标计算（仅连通节点，复用预构建的 node_map 和 predecessors）
        positioned = _compute_coordinates(layers, node_map, predecessors, adj, direction,
                                          gap=gap, layer_gap=layer_gap, padding=padding,
                                          corridor_gap=corridor_gap)
    else:
        positioned = []
        has_cycle = False

    # 放置孤立节点（在连通图上方独立区域）
    _place_isolated(positioned, isolated_nodes, connected_nodes,
                    direction, gap, layer_gap, padding)

    # 识别回边
    back_edges = _identify_back_edges(topo_order, edges)

    # 计算边路径
    edge_routes = _compute_edge_routes(positioned, edges)

    # 计算总尺寸
    max_x = max((n.get("x", 0) + n.get("w", DEFAULT_NODE_W) for n in positioned), default=800)
    max_y = max((n.get("y", 0) + n.get("h", DEFAULT_NODE_H) for n in positioned), default=600)
    total_w = max_x + padding
    total_h = max_y + padding

    return {
        "nodes": positioned,
        "width": int(total_w),
        "height": int(total_h),
        "has_cycle": has_cycle,
        "back_edges": back_edges,
        "edge_routes": edge_routes,
    }


# ===== 内部函数 =====

def _build_adjacency(nodes, edges):
    """构建邻接表 {node_id: [target_id]} 和入度表 {node_id: count}"""
    adj = {}
    in_degree = {}
    for n in nodes:
        nid = n.get("id")
        if nid:
            adj.setdefault(nid, [])
            in_degree.setdefault(nid, 0)
    for e in edges:
        src = e.get("from", "")
        tgt = e.get("to", "")
        if src in adj and tgt in adj:
            adj[src].append(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1
    return adj, in_degree


def _topological_sort(adj, in_degree, nodes):
    """Kahn 拓扑排序，返回 (有序列表, 是否有环)"""
    from collections import deque

    # 入度表副本
    indeg = dict(in_degree)
    # 按节点原始顺序排队（保持稳定）
    queue = deque()
    for n in nodes:
        nid = n.get("id")
        if nid and nid in indeg and indeg[nid] == 0:
            queue.append(nid)

    order = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for tgt in adj.get(nid, []):
            indeg[tgt] -= 1
            if indeg[tgt] == 0:
                queue.append(tgt)

    # 检测环
    has_cycle = len(order) < len(adj)
    if has_cycle:
        # 将环中节点追加到末尾（按原始顺序）
        in_order = set(order)
        for n in nodes:
            nid = n.get("id")
            if nid and nid not in in_order:
                order.append(nid)

    return order, has_cycle


def _identify_back_edges(topo_order, edges):
    """
    根据拓扑序识别回边（反向边/反馈边）。
    在拓扑序中，如果 src 出现在 tgt 之后（或同级），则该边为回边。
    返回 [("src_id", "tgt_id"), ...] 列表。
    """
    pos = {nid: i for i, nid in enumerate(topo_order)}
    back = []
    for e in edges:
        src = e.get("from", "")
        tgt = e.get("to", "")
        if src in pos and tgt in pos and pos[src] >= pos[tgt]:
            back.append((src, tgt))
    return back


def _separate_isolated(nodes, edges):
    """分离孤立节点（不参与任何边的节点）与连通节点"""
    all_ids = {n["id"] for n in nodes if n.get("id")}
    edge_nids = set()
    for e in edges:
        src = e.get("from", "")
        tgt = e.get("to", "")
        if src in all_ids:
            edge_nids.add(src)
        if tgt in all_ids:
            edge_nids.add(tgt)
    isolated = [n for n in nodes if n.get("id") and n["id"] not in edge_nids]
    connected = [n for n in nodes if not n.get("id") or n["id"] in edge_nids]
    return isolated, connected


def _place_isolated(positioned, isolated_nodes, connected_nodes,
                    direction, gap, layer_gap, padding):
    """将孤立节点排布在连通图上方独立区域"""
    if not isolated_nodes:
        return
    label_height = 20
    is_vertical = direction in ("vertical", "v")

    if not connected_nodes:
        # 全部是孤立节点：简单排布成一行/一列
        x = padding
        y = padding + label_height
        for n in isolated_nodes:
            node = dict(n)
            node["x"] = int(x)
            node["y"] = int(y)
            positioned.append(node)
            if is_vertical:
                x += n.get("w", DEFAULT_NODE_W) + gap
            else:
                y += n.get("h", DEFAULT_NODE_H) + gap
        return

    # 计算孤立行/列的区域尺寸
    if is_vertical:
        iso_w = sum(n.get("w", DEFAULT_NODE_W) + gap for n in isolated_nodes) - gap
        iso_h = max((n.get("h", DEFAULT_NODE_H) for n in isolated_nodes),
                    default=DEFAULT_NODE_H)
        # 主图最大宽度
        main_max_x = max((n.get("x", 0) + n.get("w", DEFAULT_NODE_W)
                          for n in positioned), default=0)
        # 孤立行居中
        y = padding + label_height
        offset = max(0, (main_max_x - padding - iso_w) // 2)
        x = padding + offset
        for n in isolated_nodes:
            node = dict(n)
            node["x"] = int(x)
            node["y"] = int(y)
            positioned.append(node)
            x += n.get("w", DEFAULT_NODE_W) + gap
        # 连通图下移
        shift = iso_h + layer_gap
        conn_ids = {x["id"] for x in connected_nodes if x.get("id")}
        for n in positioned:
            if n.get("id") in conn_ids:
                n["y"] = int(n["y"] + shift)
    else:
        # 水平布局：孤立列在左侧
        iso_w = max((n.get("w", DEFAULT_NODE_W) for n in isolated_nodes),
                    default=DEFAULT_NODE_W)
        iso_h = sum(n.get("h", DEFAULT_NODE_H) + gap for n in isolated_nodes) - gap
        # 主图最大高度
        main_max_y = max((n.get("y", 0) + n.get("h", DEFAULT_NODE_H)
                          for n in positioned), default=0)
        # 孤立列居中
        x = padding
        offset = max(0, (main_max_y - padding - iso_h) // 2)
        y = padding + label_height + offset
        for n in isolated_nodes:
            node = dict(n)
            node["x"] = int(x)
            node["y"] = int(y)
            positioned.append(node)
            y += n.get("h", DEFAULT_NODE_H) + gap
        # 连通图右移
        shift = iso_w + layer_gap
        conn_ids = {x["id"] for x in connected_nodes if x.get("id")}
        for n in positioned:
            if n.get("id") in conn_ids:
                n["x"] = int(n["x"] + shift)


def _compute_edge_routes(nodes, edges):
    """
    根据已定位节点计算每条边的正交路径点。
    返回 [{"from": src_id, "to": tgt_id, "points": [(x,y), ...]}, ...]
    """
    node_map = {n["id"]: n for n in nodes if "id" in n}
    routes = []
    for e in edges:
        src = e.get("from", "")
        tgt = e.get("to", "")
        src_node = node_map.get(src)
        tgt_node = node_map.get(tgt)
        if src_node and tgt_node:
            points = _compute_orthogonal_edge(src_node, tgt_node)
            routes.append({"from": src, "to": tgt, "points": points})
    return routes


def _assign_layers(topo_order, adj, node_map, predecessors):
    """最长路径层级分配。返回 {node_id: layer_index (0-based)}

    复用外部预构建的 node_map 和 predecessors，避免重复 O(V+E) 构建。
    """
    layers = {}
    for nid in topo_order:
        if nid not in node_map:
            continue
        # 取所有前驱节点的最大层级 + 1
        max_pred_layer = -1
        for pred_id in predecessors.get(nid, []):
            max_pred_layer = max(max_pred_layer, layers.get(pred_id, -1))
        layers[nid] = max_pred_layer + 1
    return layers


def _compute_coordinates(layers, node_map, predecessors, adj, direction,
                         gap=HORIZONTAL_GAP, layer_gap=VERTICAL_GAP,
                         padding=LAYER_PADDING, corridor_gap=0):
    """根据层级分配计算具体 x/y 坐标，按实际节点尺寸压缩层间距

    复用外部预构建的 node_map 和 predecessors，避免重复 O(V+E) 构建。
    """
    # 按层级分组
    layer_groups = {}
    for nid, layer in layers.items():
        layer_groups.setdefault(layer, []).append(nid)

    # 重心启发式层内排序（减少边交叉），复用预构建的 predecessors
    layer_groups = _reduce_crossings(layer_groups, adj, predecessors)

    positioned = []
    is_vertical = direction in ("vertical", "v")
    label_height = 20  # 留给标题的空间

    if is_vertical:
        # 垂直布局：行高取决于该层最大节点高度，层间间距 = 最大高 + VERTICAL_GAP
        layer_max_h = {}
        layer_total_w = {}
        for lid, nids in layer_groups.items():
            layer_max_h[lid] = max(
                (node_map.get(nid, {}).get("h", DEFAULT_NODE_H) for nid in nids),
                default=DEFAULT_NODE_H
            )
            # 计算每行总宽度（节点宽 + 间距），用于居中
            total = sum(
                node_map.get(nid, {}).get("w", DEFAULT_NODE_W) + gap
                for nid in nids
            ) - gap  # 去掉最后一个间距
            layer_total_w[lid] = max(total, 0)
        max_layer_w = max(layer_total_w.values(), default=0)

        y_cursor = padding + label_height
        for layer_id in sorted(layer_groups.keys()):
            nids = layer_groups[layer_id]
            offset = (max_layer_w - layer_total_w[layer_id]) // 2
            x_cursor = padding + offset
            for nid in nids:
                node = dict(node_map[nid])
                node["x"] = int(x_cursor)
                node["y"] = int(y_cursor)
                positioned.append(node)
                x_cursor += node.get("w", DEFAULT_NODE_W) + gap
            y_cursor += layer_max_h[layer_id] + layer_gap + corridor_gap
    else:
        # 水平布局：列宽取决于该层最大节点宽度，列间距 = 最大宽 + layer_gap
        layer_max_w = {}
        layer_total_h = {}
        for lid, nids in layer_groups.items():
            layer_max_w[lid] = max(
                (node_map.get(nid, {}).get("w", DEFAULT_NODE_W) for nid in nids),
                default=DEFAULT_NODE_W
            )
            # 计算每列总高度（节点高 + 间距），用于居中
            total = sum(
                node_map.get(nid, {}).get("h", DEFAULT_NODE_H) + gap
                for nid in nids
            ) - gap
            layer_total_h[lid] = max(total, 0)
        max_layer_h = max(layer_total_h.values(), default=0)

        x_cursor = padding
        for layer_id in sorted(layer_groups.keys()):
            nids = layer_groups[layer_id]
            offset = (max_layer_h - layer_total_h[layer_id]) // 2
            y_cursor = padding + label_height + offset
            for nid in nids:
                node = dict(node_map[nid])
                node["x"] = int(x_cursor)
                node["y"] = int(y_cursor)
                positioned.append(node)
                y_cursor += node.get("h", DEFAULT_NODE_H) + gap
            x_cursor += layer_max_w[layer_id] + layer_gap + corridor_gap

    return positioned


# ===== 重心启发式层内排序 =====

def _reduce_crossings(layer_groups, adj, predecessors):
    """
    重心启发式层内排序：多层扫描（2 前向 + 1 反向）减少边交叉。

    对每个节点，计算其相邻层连接节点的平均位置索引（重心），
    按重心值排序使相连节点尽量对齐，从而减少边交叉。

    复用外部预构建的 predecessors，避免重复 O(V+E) 构建。
    """
    groups = {lid: list(nids) for lid, nids in layer_groups.items()}
    if len(groups) <= 1:
        return groups

    # 层 ID 有序列表
    sorted_layers = sorted(groups.keys())

    # 预建位置索引 {layer: {nid: idx}}，增量更新避免每次 .index() O(n)
    pos_index = {lid: {nid: i for i, nid in enumerate(nids)}
                 for lid, nids in groups.items()}

    def _pos(nid, layer):
        return pos_index.get(layer, {}).get(nid, 0)

    def _refresh_layer_index(lid):
        """层 lid 排序后刷新位置索引"""
        pos_index[lid] = {nid: i for i, nid in enumerate(groups[lid])}

    # 前向扫描：按前驱重心排序（从上到下）
    for _ in range(2):
        for lid in sorted_layers[1:]:
            _sort_layer_by_barycenter(groups, lid, lid - 1, predecessors, _pos)
            _refresh_layer_index(lid)

    # 反向扫描：按后继重心排序（从下到上）
    for lid in reversed(sorted_layers[:-1]):
        _sort_layer_by_barycenter(groups, lid, lid + 1, adj, _pos)
        _refresh_layer_index(lid)

    return groups


def _sort_layer_by_barycenter(groups, lid, neighbor_lid, conn_map, pos_fn):
    """按相邻层连接节点的重心值对层 lid 排序（使用预建的 pos_fn，无 O(n) 查找）"""
    if lid not in groups or neighbor_lid not in groups:
        return
    neighbor_set = set(groups.get(neighbor_lid, ()))
    # 预取当前层原始 idx（兜底用），避免重复查询当前层 idx
    barycenters = {}
    current_layer = groups[lid]
    for idx, nid in enumerate(current_layer):
        connected = conn_map.get(nid, ())
        # 只考虑在相邻层中的节点（用 set 做 O(1) 成员判断）
        neighbors = [c for c in connected if c in neighbor_set]
        if neighbors:
            avg = sum(pos_fn(c, neighbor_lid) for c in neighbors) / len(neighbors)
        else:
            # 无连接节点：取原始位置索引
            avg = idx
        barycenters[nid] = avg
    current_layer.sort(key=lambda nid: barycenters.get(nid, 0))