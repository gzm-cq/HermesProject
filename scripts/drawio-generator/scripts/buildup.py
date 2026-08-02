#!/usr/bin/env python3
"""buildup.py — 逐步揭示动画 HTML 生成器

功能：读入 .drawio 文件，按节点的依赖顺序（拓扑排序），生成自包含的 HTML 文件，
逐帧逐步显示节点和边，适合演示时按架构层次展开。

用法:
  python scripts/buildup.py input.drawio -o output.html
"""

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

# Color palette for frames: (fill, stroke, dark_text)
FRAME_COLORS = [
    ('#e8f0fe', '#4a6fa5', '#1a3a6b'),   # blue
    ('#fce8e6', '#c5221f', '#8a1a1a'),   # red
    ('#e6f4ea', '#1e8e3e', '#0d5e1a'),   # green
    ('#fef7e0', '#f9ab00', '#8a6100'),   # amber
    ('#f3e8fd', '#9334e6', '#4a0072'),   # purple
    ('#e0f2fe', '#0284c7', '#0c4a6e'),   # sky blue
    ('#fce4ec', '#e91e63', '#880e4f'),   # pink
    ('#e0f2f1', '#00796b', '#004d40'),   # teal
    ('#fff3e0', '#e65100', '#bf360c'),   # deep orange
    ('#f1f8e9', '#558b2f', '#1b5e20'),   # light green
    ('#ede7f6', '#4527a0', '#1a237e'),   # deep purple
    ('#e1f5fe', '#0277bd', '#01579b'),   # light blue
]


# ===== .drawio 解析 =====

def parse_drawio(filepath):
    """解析 .drawio 文件，返回 (nodes, edges)。

    nodes: [{"id": str, "x": int, "y": int, "w": int, "h": int, "label": str}, ...]
    edges: [{"from": str, "to": str}, ...]
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    nodes = {}
    edges = []

    for cell in root.iter("mxCell"):
        cid = cell.get("id")
        if not cid:
            continue

        is_vertex = cell.get("vertex") == "1"
        is_edge = cell.get("edge") == "1"

        if is_vertex:
            geo = cell.find("mxGeometry")
            if geo is not None:
                x = float(geo.get("x", 0))
                y = float(geo.get("y", 0))
                w = float(geo.get("width", 100))
                h = float(geo.get("height", 50))
                label = cell.get("value", "")
                label = _clean_label(label)
                nodes[cid] = {
                    "id": cid,
                    "x": int(x),
                    "y": int(y),
                    "w": int(w),
                    "h": int(h),
                    "label": label,
                }

        if is_edge:
            src = cell.get("source", "")
            tgt = cell.get("target", "")
            if src and tgt:
                edges.append({"from": src, "to": tgt})

    return list(nodes.values()), edges


def _clean_label(label):
    """清理 drawio 标签（移除 HTML 标签，解码实体）"""
    label = re.sub(r'<br\s*/?>', '\n', label)
    label = re.sub(r'<[^>]+>', '', label)
    label = label.replace('&amp;', '&')
    label = label.replace('&lt;', '<')
    label = label.replace('&gt;', '>')
    label = label.replace('&nbsp;', ' ')
    label = label.replace('&quot;', '"')
    return label.strip()


def _xml_escape(text):
    """XML 转义"""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


# ===== 拓扑排序与帧划分 =====

def topological_sort(nodes, edges):
    """拓扑排序节点。

    有边依赖的节点按依赖关系排序（Kahn 算法），
    同层节点按 y 位置从上到下排列。
    返回排序后的节点 ID 列表。
    """
    node_map = {n['id']: n for n in nodes}
    in_degree = {n['id']: 0 for n in nodes}
    adj = {n['id']: [] for n in nodes}

    for edge in edges:
        f = edge['from']
        t = edge['to']
        if f in adj and t in adj:
            adj[f].append(t)
            in_degree[t] = in_degree.get(t, 0) + 1

    # 从无入度的节点开始
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    queue.sort(key=lambda nid: (node_map.get(nid, {}).get('y', 0),
                                 node_map.get(nid, {}).get('x', 0)))

    topo_order = []
    while queue:
        nid = queue.pop(0)
        topo_order.append(nid)
        for dep in adj.get(nid, []):
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)
        queue.sort(key=lambda nid: (node_map.get(nid, {}).get('y', 0),
                                     node_map.get(nid, {}).get('x', 0)))

    # 兜底：处理循环依赖或未覆盖节点
    remaining = [n['id'] for n in nodes if n['id'] not in topo_order]
    remaining.sort(key=lambda nid: (node_map.get(nid, {}).get('y', 0),
                                    node_map.get(nid, {}).get('x', 0)))
    topo_order.extend(remaining)

    return topo_order


def assign_depths(topo_order, edges):
    """为每个节点分配深度（最长路径距离）。"""
    depth = {nid: 0 for nid in topo_order}
    adj = {}
    for nid in topo_order:
        adj[nid] = []
    for edge in edges:
        if edge['from'] in adj and edge['to'] in adj:
            adj[edge['from']].append(edge['to'])

    for nid in topo_order:
        for dep in adj.get(nid, []):
            depth[dep] = max(depth[dep], depth[nid] + 1)

    return depth


def build_frames(nodes, edges, topo_order, depth):
    """将节点分组为帧。

    按深度分组，每帧一组节点。
    若无边或仅一帧，按 y 位置分散到多帧。
    返回 {frame_idx: [node_id, ...], ...}
    """
    node_map = {n['id']: n for n in nodes}

    # 按深度分组
    frames = {}
    for nid in topo_order:
        d = depth.get(nid, 0)
        frames.setdefault(d, []).append(nid)

    # 如果只有一帧且节点 > 1（无边的图），按 y 位置分散
    if len(frames) <= 1 and len(nodes) > 1:
        sorted_nodes = sorted(topo_order,
                              key=lambda nid: node_map.get(nid, {}).get('y', 0))
        num_frames = min(6, len(sorted_nodes))
        frames = {}
        for i, nid in enumerate(sorted_nodes):
            fi = i * num_frames // len(sorted_nodes)
            frames.setdefault(fi, []).append(nid)
        # 重新分配深度
        for fi, nids in frames.items():
            for nid in nids:
                depth[nid] = fi

    return frames


# ===== 路径计算 =====

def _compute_edge_path(src, tgt):
    """计算正交边路径，返回 [(x1,y1), (x2,y2), ...] 折线坐标列表。"""
    sx, sy, sw, sh = src['x'], src['y'], src['w'], src['h']
    tx, ty, tw, th = tgt['x'], tgt['y'], tgt['w'], tgt['h']

    scx = sx + sw // 2
    scy = sy + sh // 2
    tcx = tx + tw // 2
    tcy = ty + th // 2

    dx = tcx - scx
    dy = tcy - scy

    if abs(dx) >= abs(dy):
        # 水平主导
        if dx >= 0:
            start = (sx + sw, scy)
            end = (tx, tcy)
        else:
            start = (sx, scy)
            end = (tx + tw, tcy)
        if start[1] == end[1]:
            return [start, end]
        mid_x = (start[0] + end[0]) // 2
        return [start, (mid_x, start[1]), (mid_x, end[1]), end]
    else:
        # 垂直主导
        if dy >= 0:
            start = (scx, sy + sh)
            end = (tcx, ty)
        else:
            start = (scx, sy)
            end = (tcx, ty + th)
        if start[0] == end[0]:
            return [start, end]
        mid_y = (start[1] + end[1]) // 2
        return [start, (start[0], mid_y), (end[0], mid_y), end]


def _path_to_d(points):
    """将点列表转为 SVG path d 字符串"""
    return "M " + " ".join(f"{p[0]},{p[1]}" for p in points)


def _compute_bounding_box(nodes, padding=40):
    """计算 SVG viewBox"""
    if not nodes:
        return 0, 0, 800, 600
    xs = [n['x'] for n in nodes]
    ys = [n['y'] for n in nodes]
    xe = [n['x'] + n['w'] for n in nodes]
    ye = [n['y'] + n['h'] for n in nodes]
    min_x = max(0, min(xs) - padding)
    min_y = max(0, min(ys) - padding)
    bw = max(xe) - min_x + padding
    bh = max(ye) - min_y + padding
    return int(min_x), int(min_y), int(bw), int(bh)


# ===== HTML 生成 =====

def generate_html(nodes, edges, frames, depth, node_map, output_path,
                  font_family="Arial, sans-serif", frame_interval=1.0):
    """生成自包含的 HTML 文件，内嵌 SVG 和 CSS 动画。"""
    vx, vy, vw, vh = _compute_bounding_box(nodes)
    num_frames = len(frames)

    # 按 frame_idx 排序
    sorted_frames = sorted(frames.items())

    # 确定每条边属于哪一帧（属于较晚端点所在帧）
    edge_frame_map = {}
    for edge in edges:
        f = edge['from']
        t = edge['to']
        if f in node_map and t in node_map:
            df = depth.get(f, 0)
            dt = depth.get(t, 0)
            ef = max(df, dt)
            edge_frame_map.setdefault(ef, []).append(edge)

    # ===== CSS 构建 =====
    css_parts = []
    css_parts.append("""
@keyframes reveal-node {
  0%   { opacity: 0; transform: scale(0.92); }
  100% { opacity: 1; transform: scale(1); }
}
@keyframes dim-node {
  0%   { opacity: 1; }
  100% { opacity: 0.3; }
}
@keyframes reveal-edge {
  0%   { opacity: 0; }
  100% { opacity: 1; }
}
@keyframes dim-edge {
  0%   { opacity: 1; }
  100% { opacity: 0.15; }
}
@keyframes dot-appear {
  0%   { opacity: 0; transform: scale(0); }
  100% { opacity: 1; transform: scale(1); }
}
body {
  margin: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f5f5f5;
  font-family: """ + font_family + """;
}
svg {
  max-width: 100vw;
  max-height: 90vh;
  box-shadow: 0 2px 16px rgba(0,0,0,0.12);
  border-radius: 8px;
  background: #ffffff;
}
""")

    for fi, (frame_idx, node_ids) in enumerate(sorted_frames):
        delay = frame_idx * frame_interval
        is_last = (frame_idx == sorted_frames[-1][0])
        fill, stroke, _ = FRAME_COLORS[frame_idx % len(FRAME_COLORS)]

        # 节点动画
        if is_last:
            css_parts.append(
                f".f{frame_idx}-node {{ "
                f"animation: reveal-node 0.5s ease {delay}s forwards; "
                f"opacity: 0; }}"
            )
        else:
            next_delay = delay + frame_interval
            css_parts.append(
                f".f{frame_idx}-node {{ "
                f"animation: reveal-node 0.5s ease {delay}s forwards, "
                f"dim-node 0.4s ease {next_delay}s forwards; "
                f"opacity: 0; }}"
            )

        # 边动画
        if is_last:
            css_parts.append(
                f".f{frame_idx}-edge {{ "
                f"animation: reveal-edge 0.4s ease {delay + 0.25}s forwards; "
                f"opacity: 0; }}"
            )
        else:
            next_delay = delay + frame_interval
            css_parts.append(
                f".f{frame_idx}-edge {{ "
                f"animation: reveal-edge 0.4s ease {delay + 0.25}s forwards, "
                f"dim-edge 0.4s ease {next_delay + 0.25}s forwards; "
                f"opacity: 0; }}"
            )

        # 节点样式
        css_parts.append(
            f".f{frame_idx}-node .node-rect {{ "
            f"fill: {fill}; stroke: {stroke}; stroke-width: 2.5; "
            f"transition: fill 0.3s, stroke 0.3s; }}"
        )
        css_parts.append(
            f".f{frame_idx}-node .node-text {{ "
            f"fill: {stroke}; font-weight: bold; "
            f"transition: fill 0.3s; }}"
        )
        css_parts.append(
            f".f{frame_idx}-edge .edge-line {{ "
            f"stroke: {stroke}; stroke-width: 2; "
            f"transition: stroke 0.3s; }}"
        )

    # Step indicator 圆点
    css_parts.append("""
.step-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 10px;
  padding: 10px 20px;
  background: rgba(255,255,255,0.92);
  border-radius: 24px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.12);
}
.step-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #ddd;
}
""")

    # 为每个圆点添加颜色动画
    for fi, (frame_idx, _) in enumerate(sorted_frames):
        delay = frame_idx * frame_interval
        _, stroke, _ = FRAME_COLORS[frame_idx % len(FRAME_COLORS)]
        next_delay = (frame_idx + 1) * frame_interval
        is_last = (frame_idx == sorted_frames[-1][0])
        if is_last:
            css_parts.append(
                f".step-dot-{frame_idx} {{ "
                f"animation: dot-appear 0.3s ease {delay + 0.3}s forwards; "
                f"opacity: 0; background: {stroke}; }}"
            )
        else:
            css_parts.append(
                f".step-dot-{frame_idx} {{ "
                f"animation: dot-appear 0.3s ease {delay + 0.3}s forwards, "
                f"dim-node 0.4s ease {next_delay}s forwards; "
                f"opacity: 0; background: {stroke}; }}"
            )

    # ===== SVG 构建 =====
    svg_parts = []
    svg_parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vx} {vy} {vw} {vh}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'font-family="{font_family}">'
    )

    # 背景
    svg_parts.append(
        f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="#ffffff"/>'
    )

    # 标题
    title = "Architecture Buildup"
    svg_parts.append(
        f'<text x="{vx + vw // 2}" y="{vy + 22}" '
        f'text-anchor="middle" font-size="14" fill="#666" '
        f'font-weight="bold">{_xml_escape(title)}</text>'
    )

    # 逐帧渲染
    for fi, (frame_idx, node_ids) in enumerate(sorted_frames):
        # 渲染边
        for edge in edge_frame_map.get(frame_idx, []):
            src_node = node_map.get(edge['from'])
            tgt_node = node_map.get(edge['to'])
            if src_node and tgt_node:
                points = _compute_edge_path(src_node, tgt_node)
                d = _path_to_d(points)
                svg_parts.append(
                    f'<g class="f{frame_idx}-edge">'
                    f'<path class="edge-line" d="{d}" fill="none"/>'
                    f'</g>'
                )

        # 渲染节点
        for nid in node_ids:
            node = node_map.get(nid)
            if not node:
                continue
            nx, ny, nw, nh = node['x'], node['y'], node['w'], node['h']
            label = node.get('label', '')

            svg_parts.append(f'<g class="f{frame_idx}-node">')
            svg_parts.append(
                f'<rect class="node-rect" x="{nx}" y="{ny}" '
                f'width="{nw}" height="{nh}" rx="6" ry="6"/>'
            )

            if label:
                label_lines = label.split('\n')
                fs = 12
                line_count = len(label_lines)
                for li, line in enumerate(label_lines):
                    ly = ny + nh // 2 - (line_count - 1) * (fs // 2) + li * fs
                    svg_parts.append(
                        f'<text class="node-text" x="{nx + nw // 2}" y="{ly}" '
                        f'text-anchor="middle" font-size="{fs}">'
                        f'{_xml_escape(line)}</text>'
                    )

            svg_parts.append('</g>')

    svg_parts.append('</svg>')

    # Step indicator
    svg_parts.append('<div class="step-bar">')
    for fi, (frame_idx, _) in enumerate(sorted_frames):
        svg_parts.append(f'<div class="step-dot step-dot-{frame_idx}"></div>')
    svg_parts.append('</div>')

    # ===== 组装 HTML =====
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Architecture Buildup</title>
<style>
{chr(10).join(css_parts)}
</style>
</head>
<body>
{chr(10).join(svg_parts)}
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Generated: {output_path}")


# ===== CLI =====

def main():
    parser = argparse.ArgumentParser(
        description="从 .drawio 文件生成逐步揭示动画 HTML"
    )
    parser.add_argument("input", help="输入 .drawio 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出 HTML 文件路径")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="帧间间隔（秒，默认 1.0）")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    nodes, edges = parse_drawio(args.input)
    if not nodes:
        print("Warning: 未找到节点", file=sys.stderr)

    node_map = {n['id']: n for n in nodes}

    # 拓扑排序
    topo_order = topological_sort(nodes, edges)

    # 分配深度
    depth = assign_depths(topo_order, edges)

    # 构建帧
    frames = build_frames(nodes, edges, topo_order, depth)

    print(f"Nodes: {len(nodes)}, Edges: {len(edges)}, Frames: {len(frames)}")
    for fi in sorted(frames.keys()):
        nids = frames[fi]
        labels = [node_map.get(nid, {}).get('label', nid) for nid in nids]
        print(f"  Frame {fi}: {', '.join(labels)}")

    generate_html(nodes, edges, frames, depth, node_map, args.output,
                  frame_interval=args.interval)


if __name__ == "__main__":
    main()