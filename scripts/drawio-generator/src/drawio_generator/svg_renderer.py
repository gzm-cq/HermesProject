#!/usr/bin/env python3
"""SVG 渲染 — 将布局数据渲染为 SVG 矢量图"""

from xml.sax.saxutils import escape

from .palettes import ARROW_STYLES, _resolve_color, _lighten
from .shapes import _render_svg_shape
from .geometry import _compute_bounding_box, _get_node_by_id, _compute_orthogonal_edge


# ===== SVG 模板函数 =====

def _svg_header(vx, vy, vw, vh, font_family, font_size):
    """SVG 根元素（响应式 viewBox）"""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="100%" viewBox="{vx} {vy} {vw} {vh}" '
            f'preserveAspectRatio="xMidYMid meet" '
            f'font-family="{font_family}" font-size="{font_size}">')


def _svg_footer():
    return "</svg>"


def _svg_marker_def(arrow_style, edge_color, sw):
    """生成箭头 marker 定义"""
    arrow_info = ARROW_STYLES.get(arrow_style, ARROW_STYLES["classic"])
    if not arrow_info["svg_path"]:
        return []
    is_open = (arrow_info["svg_fill"] == "none")
    svg_fill = "none" if is_open else edge_color
    svg_stroke = edge_color if is_open else "none"
    lines = [
        (f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
         f'markerWidth="{min(sw*4, 8)}" markerHeight="{min(sw*4, 8)}" orient="auto">'),
        (f'<path d="{arrow_info["svg_path"]}" fill="{svg_fill}" '
         f'stroke="{svg_stroke}" stroke-width="{min(sw, 1.5)}"/>'),
        "</marker>",
    ]
    return lines


def _svg_shadow_filter():
    """投影 filter 定义"""
    return ('<filter id="shadow" x="-10%" y="-10%" width="130%" height="130%">'
            '<feDropShadow dx="2" dy="2" stdDeviation="3" flood-opacity="0.15"/>'
            '</filter>')


def _svg_gradient_def(grad_id, light_color, base_color):
    """线性渐变定义"""
    return (f'<linearGradient id="{grad_id}" x1="0%" y1="0%" x2="0%" y2="100%">'
            f'<stop offset="0%" stop-color="{light_color}"/>'
            f'<stop offset="100%" stop-color="{base_color}"/>'
            f'</linearGradient>')


def _svg_background(vx, vy, vw, vh, color):
    """背景矩形"""
    return f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="{color}"/>'


def _svg_title(vx, vw, vy, text, font_size, color):
    """标题文本"""
    return (f'<text x="{vx + vw // 2}" y="{vy + font_size + 4}" text-anchor="middle" '
            f'font-size="{font_size}" font-weight="bold" fill="{color}">'
            f'{escape(text)}</text>')


def _svg_layer_rect(lx, ly, lw, lh, bg, stroke, sw):
    """层次背景矩形"""
    return (f'<rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" rx="6" '
            f'fill="{bg}" stroke="{stroke}" stroke-width="{min(sw, 1.5)}"/>')


def _svg_layer_label(lx, lw, ly, text, font_size, color):
    """层次标签"""
    return (f'<text x="{lx + lw // 2}" y="{ly + font_size + 5}" text-anchor="middle" '
            f'font-size="{font_size}" font-weight="bold" fill="{color}">{escape(text)}</text>')


def _svg_node_label(nx, nw, ny, nh, label_lines, font_size, text_color):
    """节点文本标签（支持多行）"""
    lines = []
    for i, line in enumerate(label_lines):
        ly_pos = ny + nh // 2 - (len(label_lines) - 1) * (font_size // 2) + i * font_size
        lines.append(f'<text x="{nx + nw // 2}" y="{ly_pos}" text-anchor="middle" '
                     f'font-size="{font_size}" fill="{text_color}">{escape(line)}</text>')
    return "\n".join(lines)


def _svg_sub_label(nx, nw, ny, nh, text, font_size, text_color):
    """数据标注（小字）"""
    return (f'<text x="{nx + nw // 2}" y="{ny + nh - 5}" text-anchor="middle" '
            f'font-size="{font_size}" fill="{text_color}" opacity="0.65">{escape(text)}</text>')


def _svg_edge_line(x1, y1, x2, y2, color, sw, dashed=False):
    """连接线"""
    dash = 'stroke-dasharray="5,3"' if dashed else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{sw}" {dash} marker-end="url(#a)"/>')


def _svg_edge_path(points, color, sw, dashed=False):
    """正交折线路径，points 为 [(x1,y1), (x2,y2), ...]"""
    dash = 'stroke-dasharray="5,3"' if dashed else ""
    d = " ".join(f"{p[0]},{p[1]}" for p in points)
    return (f'<path d="M {d}" fill="none" '
            f'stroke="{color}" stroke-width="{sw}" {dash} marker-end="url(#a)"/>')


def _svg_edge_label_bg(lx, ly, text, font_size):
    """边标签白色半透明背景框"""
    # 估算文本宽度（中文字符≈font_size，ASCII≈font_size*0.6）
    widths = [font_size if ord(c) > 127 else font_size * 0.6 for c in text]
    tw = int(sum(widths))
    bw = tw + 10  # 左右内边距
    bh = font_size + 6  # 上下内边距
    bx = lx - bw // 2
    by = ly - font_size - 2
    return (f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" '
            f'rx="4" fill="#ffffff" fill-opacity="0.85" '
            f'stroke="none"/>')


def _svg_edge_label(lx, ly, text, font_size, color):
    """边标签（含白色半透明背景）"""
    bg = _svg_edge_label_bg(lx, ly, text, font_size)
    return (bg + f'<text x="{lx}" y="{ly}" text-anchor="middle" '
            f'font-size="{font_size}" fill="{color}" opacity="0.85">{escape(text)}</text>')


# ===== SVG 渲染主函数 =====
def _render_svg(width, height, title, nodes, edges, layers, colors, path,
                font_family=None, font_size=None, stroke_width=None,
                arrow_style="classic", gradient=False, shadow=False, auto_fit=True):
    """渲染为 SVG 格式，支持全套视觉配置"""
    title_color = colors.get("title_color", "#1a1a1a")
    text_color = colors.get("text_color", "#333333")
    edge_color = colors.get("edge_color", text_color)
    font_family = font_family or "SimSun, Arial, sans-serif"
    fs = font_size or {}
    fs_title = fs.get("title", 16)
    fs_label = fs.get("label", 11)
    fs_small = fs.get("small", 9)
    sw = stroke_width if stroke_width is not None else 1.5

    # 自动裁剪
    if auto_fit:
        vx, vy, vw, vh = _compute_bounding_box(nodes, layers, width, height)
    else:
        vx, vy, vw, vh = 0, 0, width, height

    lines = []
    lines.append(_svg_header(vx, vy, vw, vh, font_family, fs_label))
    lines.append("<defs>")

    # 箭头 marker
    lines.extend(_svg_marker_def(arrow_style, edge_color, sw))

    # 投影 filter
    if shadow:
        lines.append(_svg_shadow_filter())

    # 渐变定义
    grad_ids = set()
    if gradient:
        for node in nodes:
            nc = _resolve_color(colors, node)
            fid = f"g_{id(node)}"
            if fid not in grad_ids:
                grad_ids.add(fid)
                lines.append(_svg_gradient_def(fid, _lighten(nc["fill"], 0.2), nc["fill"]))

    lines.append("</defs>")

    # 背景
    bg_color = colors.get("bg", "#ffffff")
    lines.append(_svg_background(vx, vy, vw, vh, bg_color))

    # 标题
    lines.append(_svg_title(vx, vw, vy, title, fs_title, title_color))

    # 层次
    for layer in layers:
        lx, ly, lw, lh = layer["x"], layer["y"], layer["w"], layer["h"]
        lbg = colors.get("layer_bg", "#f5f5f5")
        lstroke = colors.get("layer_stroke", "#ccc")
        lines.append(_svg_layer_rect(lx, ly, lw, lh, lbg, lstroke, sw))
        if "label" in layer:
            lines.append(_svg_layer_label(lx, lw, ly, layer["label"], fs_label, title_color))

    # 节点
    for node in nodes:
        nx, ny, nw, nh = node["x"], node["y"], node["w"], node["h"]
        nc = _resolve_color(colors, node)
        shadow_attr = ' filter="url(#shadow)"' if shadow else ""
        fill_val = f"url(#g_{id(node)})" if gradient else nc["fill"]
        lines.append(_render_svg_shape(node, nx, ny, nw, nh, fill_val,
                                        nc["stroke"], sw, shadow_attr))
        # 主标签
        label = node.get("label", "")
        if label:
            lines.append(_svg_node_label(nx, nw, ny, nh, label.replace("<br>", "\n").replace("<br/>", "\n").split("\n"),
                                          fs_label, text_color))
        # 数据标注
        sub = node.get("sub_label", "")
        if sub:
            lines.append(_svg_sub_label(nx, nw, ny, nh, sub, fs_small, text_color))

    # 边
    for edge in edges:
        src_id = edge.get("from", "")
        tgt_id = edge.get("to", "")
        src_node = _get_node_by_id(nodes, src_id)
        tgt_node = _get_node_by_id(nodes, tgt_id)
        if src_node and tgt_node:
            # 优先使用预计算路径，否则实时计算
            edge_path = edge.get("points") or _compute_orthogonal_edge(src_node, tgt_node)
            # 回边（反馈边）用橙色 + 虚线
            is_back = edge.get("back_edge", False)
            ec = "#E74C3C" if is_back else edge_color
            lines.append(_svg_edge_path(edge_path, ec, sw,
                                         dashed=is_back or edge.get("dashed", False)))
            # 边标签（取中间段的中点）
            if edge.get("label"):
                if len(edge_path) >= 3:
                    # 折线：取中间段的两个端点
                    mid_idx = len(edge_path) // 2
                    lx = (edge_path[mid_idx - 1][0] + edge_path[mid_idx][0]) // 2
                    ly = (edge_path[mid_idx - 1][1] + edge_path[mid_idx][1]) // 2
                else:
                    lx = (edge_path[0][0] + edge_path[-1][0]) // 2
                    ly = (edge_path[0][1] + edge_path[-1][1]) // 2
                lines.append(_svg_edge_label(lx, ly, edge["label"],
                                              fs_small, text_color))

    lines.append(_svg_footer())
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Generated: {path}")
