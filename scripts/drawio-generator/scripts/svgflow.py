#!/usr/bin/env python3
"""svgflow.py — 从 .drawio 文件生成带蚂蚁线动画的 SVG

功能：读入 .drawio 文件，解析节点和边，输出一个 SVG 文件，
边线带有沿路径流动的动画点（marching ants）。

用法:
  python scripts/svgflow.py input.drawio -o output.svg

动画实现:
  - 使用 stroke-dasharray + stroke-dashoffset animate 实现虚线流动效果
  - 使用 animateMotion 沿路径移动圆点（marching ants）
  - 所有边线 parallel 动画，2s 循环
"""

import argparse
import math
import os
import re
import sys
import xml.etree.ElementTree as ET


# ===== 路径计算 =====

def _compute_edge_path(src, tgt):
    """计算正交边路径，返回 [(x1,y1), (x2,y2), ...] 折线坐标列表。
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


def _path_to_d(points):
    """将点列表转为 SVG path d 字符串"""
    return "M " + " ".join(f"{p[0]},{p[1]}" for p in points)


def _path_length_estimate(points):
    """估算路径长度"""
    total = 0.0
    for i in range(1, len(points)):
        total += math.sqrt((points[i][0] - points[i-1][0]) ** 2 +
                           (points[i][1] - points[i-1][1]) ** 2)
    return total


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


# ===== SVG 生成 =====

def _xml_escape(text):
    """XML 转义"""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def _compute_bounding_box(nodes, padding=30):
    """计算 SVG viewBox"""
    if not nodes:
        return 0, 0, 800, 600
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    xe = [n["x"] + n["w"] for n in nodes]
    ye = [n["y"] + n["h"] for n in nodes]
    min_x = max(0, min(xs) - padding)
    min_y = max(0, min(ys) - padding)
    bw = max(xe) - min_x + padding
    bh = max(ye) - min_y + padding
    return int(min_x), int(min_y), int(bw), int(bh)


def _generate_edge_svg(edge_id, points, color="#4a6fa5", stroke_width=1.5):
    """生成带蚂蚁线动画的 SVG 边元素。

    返回 (lines_list, path_d_string)
    """
    d = _path_to_d(points)
    path_len = _path_length_estimate(points)

    # 动态计算虚线参数：虚线长度约为路径长度的 5%，至少 4px
    dash_len = max(4, int(path_len * 0.05))
    gap_len = int(dash_len * 1.5)
    total = dash_len + gap_len

    lines = []

    # 1. 基础边线（半透明细实线）
    lines.append(
        f'<path d="{d}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke_width}" opacity="0.35"/>'
    )

    # 2. 流动虚线（蚂蚁线效果）— 使用 stroke-dashoffset 动画
    lines.append(
        f'<path d="{d}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke_width}" '
        f'stroke-dasharray="{dash_len},{gap_len}">'
        f'<animate attributeName="stroke-dashoffset" '
        f'from="{total}" to="0" '
        f'dur="2s" repeatCount="indefinite"/>'
        f'</path>'
    )

    # 3. 移动圆点（marching ants 圆点）— 使用 animateMotion 沿路径前进
    dot_r = max(2.5, stroke_width * 1.5)
    lines.append(
        f'<circle r="{dot_r}" fill="{color}">'
        f'<animateMotion dur="2s" repeatCount="indefinite" rotate="auto">'
        f'<mpath href="#{edge_id}_path"/>'
        f'</animateMotion>'
        f'</circle>'
    )

    # 4. 第二个圆点，相位偏移 1s，实现双点流动效果
    lines.append(
        f'<circle r="{dot_r}" fill="{color}" opacity="0.6">'
        f'<animateMotion dur="2s" repeatCount="indefinite" rotate="auto" begin="-1s">'
        f'<mpath href="#{edge_id}_path"/>'
        f'</animateMotion>'
        f'</circle>'
    )

    return lines, d


def _generate_node_svg(node):
    """生成 SVG 节点元素"""
    nx, ny, nw, nh = node["x"], node["y"], node["w"], node["h"]
    label = node.get("label", "")

    lines = []

    # 圆角矩形背景
    lines.append(
        f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}" '
        f'rx="4" fill="#e8f0fe" stroke="#4a6fa5" stroke-width="1.5"/>'
    )

    # 标签（支持多行）
    if label:
        label_lines = label.split("\n")
        fs = 12
        line_count = len(label_lines)
        for li, line in enumerate(label_lines):
            ly = ny + nh // 2 - (line_count - 1) * (fs // 2) + li * fs
            lines.append(
                f'<text x="{nx + nw // 2}" y="{ly}" '
                f'text-anchor="middle" font-size="{fs}" '
                f'fill="#333333">{_xml_escape(line)}</text>'
            )

    return lines


def generate_svg(nodes, edges, output_path, font_family="Arial, sans-serif"):
    """生成带蚂蚁线动画的 SVG 文件"""
    vx, vy, vw, vh = _compute_bounding_box(nodes)

    lines = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="100%" viewBox="{vx} {vy} {vw} {vh}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'font-family="{font_family}" font-size="12">'
    )

    # <defs> — 每条边的路径供 animateMotion 引用
    lines.append("<defs>")
    for i, edge in enumerate(edges):
        src = edge["from"]
        tgt = edge["to"]
        src_node = next((n for n in nodes if n["id"] == src), None)
        tgt_node = next((n for n in nodes if n["id"] == tgt), None)
        if src_node and tgt_node:
            points = _compute_edge_path(src_node, tgt_node)
            d = _path_to_d(points)
            path_id = f"e{i}_path"
            lines.append(f'<path id="{path_id}" d="{d}"/>')
    lines.append("</defs>")

    # 背景
    lines.append(
        f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="#ffffff"/>'
    )

    # 边线（带动画）
    for i, edge in enumerate(edges):
        src = edge["from"]
        tgt = edge["to"]
        src_node = next((n for n in nodes if n["id"] == src), None)
        tgt_node = next((n for n in nodes if n["id"] == tgt), None)
        if src_node and tgt_node:
            points = _compute_edge_path(src_node, tgt_node)
            edge_lines, _ = _generate_edge_svg(f"e{i}", points)
            lines.extend(edge_lines)

    # 节点
    for node in nodes:
        lines.extend(_generate_node_svg(node))

    lines.append("</svg>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Generated: {output_path}")


# ===== CLI =====

def main():
    parser = argparse.ArgumentParser(
        description="从 .drawio 文件生成带蚂蚁线动画的 SVG"
    )
    parser.add_argument("input", help="输入 .drawio 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出 SVG 文件路径")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not args.input.lower().endswith(".drawio"):
        print("Error: 输入文件必须是 .drawio 格式", file=sys.stderr)
        sys.exit(1)

    nodes, edges = parse_drawio(args.input)
    if not nodes:
        print("Warning: 未找到节点", file=sys.stderr)
    if not edges:
        print("Warning: 未找到边", file=sys.stderr)

    generate_svg(nodes, edges, args.output)


if __name__ == "__main__":
    main()