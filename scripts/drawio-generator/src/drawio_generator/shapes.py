#!/usr/bin/env python3
"""节点形状定义和 SVG 形状渲染"""

from .palettes import _resolve_color


# ===== 节点形状表 =====
SHAPES = {
    "rect": {
        "drawio": "rounded=1;whiteSpace=wrap;html=1;",
    },
    "process": {
        "drawio": "rounded=0;whiteSpace=wrap;html=1;",
    },
    "cylinder": {
        "drawio": "shape=cylinder;whiteSpace=wrap;html=1;",
    },
    "hexagon": {
        "drawio": "shape=hexagon;whiteSpace=wrap;html=1;",
    },
}


# ===== SVG 形状渲染 =====
def _render_svg_shape(node, nx, ny, nw, nh, fill_val, stroke_color, sw, shadow_attr):
    """根据节点 shape 返回对应的 SVG 元素字符串"""
    shape = node.get("shape", "rect")
    shadow_str = shadow_attr

    if shape == "process":
        return (f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}" rx="0" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{shadow_str}/>')

    if shape == "cylinder":
        top_ry = max(int(nh * 0.12), 6)
        cx = nx + nw / 2
        body_top = ny + top_ry
        body_h = nh - top_ry * 2
        parts = [
            # 顶部椭圆
            f'<ellipse cx="{cx}" cy="{body_top}" rx="{nw / 2}" ry="{top_ry}" '
            f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{shadow_str}/>',
            # 矩形身体
            f'<rect x="{nx}" y="{body_top}" width="{nw}" height="{body_h}" '
            f'fill="{fill_val}" stroke="none"{shadow_str}/>',
            # 底部弧线
            f'<path d="M {nx} {body_top + body_h} A {nw / 2} {top_ry} 0 0 0 {nx + nw} {body_top + body_h}" '
            f'fill="none" stroke="{stroke_color}" stroke-width="{sw}"/>',
            # 两条竖线 + 底边（补全轮廓）
            f'<line x1="{nx}" y1="{body_top}" x2="{nx}" y2="{body_top + body_h}" '
            f'stroke="{stroke_color}" stroke-width="{sw}"/>',
            f'<line x1="{nx + nw}" y1="{body_top}" x2="{nx + nw}" y2="{body_top + body_h}" '
            f'stroke="{stroke_color}" stroke-width="{sw}"/>',
            f'<line x1="{nx}" y1="{body_top + body_h}" x2="{nx + nw}" y2="{body_top + body_h}" '
            f'stroke="{stroke_color}" stroke-width="{sw}"/>',
        ]
        return "\n".join(parts)

    if shape == "hexagon":
        inset = nw * 0.25
        pts = [
            (nx + inset, ny),
            (nx + nw - inset, ny),
            (nx + nw, ny + nh / 2),
            (nx + nw - inset, ny + nh),
            (nx + inset, ny + nh),
            (nx, ny + nh / 2),
        ]
        points_str = " ".join(f"{p[0]},{p[1]}" for p in pts)
        return (f'<polygon points="{points_str}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{shadow_str}/>')

    # rect（默认带圆角）
    return (f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}" rx="4" '
            f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{shadow_str}/>')
