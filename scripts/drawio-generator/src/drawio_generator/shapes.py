#!/usr/bin/env python3
"""节点形状定义和 SVG 形状渲染"""

from .palettes import _resolve_color, _sanitize_color


# ===== 节点形状表 =====
SHAPES = {
    "rect": {
        "drawio": "rounded=1;whiteSpace=wrap;html=1;",
    },
    "rounded": {
        "drawio": "rounded=1;whiteSpace=wrap;html=1;",
    },
    "process": {
        "drawio": "shape=process;whiteSpace=wrap;html=1;",
    },
    "cylinder": {
        "drawio": "shape=cylinder;whiteSpace=wrap;html=1;",
    },
    "cylinder3": {
        "drawio": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;",
    },
    "hexagon": {
        "drawio": "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;",
    },
    "cloud": {
        "drawio": "shape=cloud;whiteSpace=wrap;html=1;",
    },
    "note": {
        "drawio": "shape=note;whiteSpace=wrap;html=1;",
    },
    "document": {
        "drawio": "shape=document;whiteSpace=wrap;html=1;",
    },
    "cube": {
        "drawio": "shape=cube;whiteSpace=wrap;html=1;",
    },
    "card": {
        "drawio": "shape=card;whiteSpace=wrap;html=1;",
    },
    "step": {
        "drawio": "shape=step;whiteSpace=wrap;html=1;",
    },
    "parallelogram": {
        "drawio": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;",
    },
    "rhombus": {
        "drawio": "shape=rhombus;whiteSpace=wrap;html=1;",
    },
}


# ===== SVG 形状渲染 =====
def _render_svg_shape(node, nx, ny, nw, nh, fill_val, stroke_color, sw, shadow_attr, sketch_attr=""):
    """根据节点 shape 返回对应的 SVG 元素字符串"""
    # 防御性清理：防止颜色值注入 SVG 属性（跳过 gradient URL 引用）
    if not fill_val.startswith("url("):
        fill_val = _sanitize_color(fill_val)
    stroke_color = _sanitize_color(stroke_color)
    shape = node.get("shape", "rect")
    # 合并 filter 属性：SVG 规范中同元素同名属性只取最后一个，
    # 需合并为单个 filter="url(#shadow) url(#sketch)" 形式
    filters = []
    if shadow_attr.strip():
        filters.append("url(#shadow)")
    if sketch_attr.strip():
        filters.append("url(#sketch)")
    filter_str = f' filter="{" ".join(filters)}"' if filters else ""

    if shape == "process":
        # 双边框流程
        inner_w = max(nw - 6, 2)
        inner_h = max(nh - 6, 2)
        outer = (f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}" rx="0" '
                 f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')
        inner = (f'<rect x="{nx + 3}" y="{ny + 3}" width="{inner_w}" height="{inner_h}" rx="0" '
                 f'fill="none" stroke="{stroke_color}" stroke-width="{sw}"/>')
        return outer + "\n" + inner

    if shape == "cylinder":
        top_ry = min(max(int(nh * 0.12), 3), max(nh // 3, 3))
        cx = nx + nw / 2
        body_top = ny + top_ry
        body_h = max(nh - top_ry * 2, 1)
        parts = [
            # 顶部椭圆
            f'<ellipse cx="{cx}" cy="{body_top}" rx="{nw / 2}" ry="{top_ry}" '
            f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>',
            # 矩形身体
            f'<rect x="{nx}" y="{body_top}" width="{nw}" height="{body_h}" '
            f'fill="{fill_val}" stroke="none"{filter_str}/>',
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
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')

    if shape == "cloud":
        # 云朵形状 — 使用二次贝塞尔曲线
        d = f"M {nx + 0.3 * nw} {ny + nh}"
        d += f" Q {nx} {ny + nh}, {nx} {ny + 0.6 * nh}"
        d += f" Q {nx} {ny + 0.2 * nh}, {nx + 0.25 * nw} {ny + 0.15 * nh}"
        d += f" Q {nx + 0.3 * nw} {ny}, {nx + 0.55 * nw} {ny}"
        d += f" Q {nx + 0.75 * nw} {ny}, {nx + 0.85 * nw} {ny + 0.15 * nh}"
        d += f" Q {nx + nw} {ny + 0.25 * nh}, {nx + nw} {ny + 0.55 * nh}"
        d += f" Q {nx + nw} {ny + nh}, {nx + 0.7 * nw} {ny + nh} Z"
        return (f'<path d="{d}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')

    if shape == "note":
        # 便签 — 右上角折叠
        fold = 12
        d = f"M {nx} {ny}"
        d += f" L {nx + nw - fold} {ny}"
        d += f" L {nx + nw} {ny + fold}"
        d += f" L {nx + nw} {ny + nh}"
        d += f" L {nx} {ny + nh} Z"
        # 折叠线
        fold_line = (
            f'<line x1="{nx + nw - fold}" y1="{ny}" '
            f'x2="{nx + nw - fold}" y2="{ny + fold}" '
            f'stroke="{stroke_color}" stroke-width="{sw}"/>'
        )
        fold_line2 = (
            f'<line x1="{nx + nw - fold}" y1="{ny + fold}" '
            f'x2="{nx + nw}" y2="{ny + fold}" '
            f'stroke="{stroke_color}" stroke-width="{sw}"/>'
        )
        return (f'<path d="{d}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>'
                f'\n{fold_line}\n{fold_line2}')

    if shape == "document":
        # 文档 — 左下角卷页
        curl = 15
        d = f"M {nx} {ny}"
        d += f" L {nx + nw} {ny}"
        d += f" L {nx + nw} {ny + nh}"
        d += f" L {nx + curl} {ny + nh}"
        d += f" L {nx} {ny + nh - curl} Z"
        # 卷页轮廓线
        curl_line = (
            f'<line x1="{nx}" y1="{ny + nh - curl}" '
            f'x2="{nx + curl}" y2="{ny + nh - curl}" '
            f'stroke="{stroke_color}" stroke-width="{sw}"/>'
        )
        curl_line2 = (
            f'<line x1="{nx + curl}" y1="{ny + nh - curl}" '
            f'x2="{nx + curl}" y2="{ny + nh}" '
            f'stroke="{stroke_color}" stroke-width="{sw}"/>'
        )
        return (f'<path d="{d}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>'
                f'\n{curl_line}\n{curl_line2}')

    if shape == "cube":
        # 3D 立方体 — 三个可见面
        gap = min(nw, nh) * 0.15
        # 顶面
        top_pts = [
            (nx + gap, ny),
            (nx + nw, ny),
            (nx + nw - gap, ny + gap),
            (nx, ny + gap),
        ]
        # 前面
        front_pts = [
            (nx, ny + gap),
            (nx + nw - gap, ny + gap),
            (nx + nw - gap, ny + nh - gap),
            (nx, ny + nh - gap),
        ]
        # 右面
        right_pts = [
            (nx + nw - gap, ny + gap),
            (nx + nw, ny),
            (nx + nw, ny + nh - gap),
            (nx + nw - gap, ny + nh - gap),
        ]
        top_str = " ".join(f"{p[0]},{p[1]}" for p in top_pts)
        front_str = " ".join(f"{p[0]},{p[1]}" for p in front_pts)
        right_str = " ".join(f"{p[0]},{p[1]}" for p in right_pts)
        parts = [
            # 前面（主填充）
            f'<polygon points="{front_str}" '
            f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>',
            # 顶面（稍亮）
            f'<polygon points="{top_str}" '
            f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"/>',
            # 右面（稍暗 — 用半透明黑叠加）
            f'<polygon points="{right_str}" '
            f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"/>',
            # 右面微暗叠加
            f'<polygon points="{right_str}" '
            f'fill="rgba(0,0,0,0.07)" stroke="none"/>',
        ]
        return "\n".join(parts)

    if shape == "card":
        # 圆角卡片（大圆角）
        rx = 8
        return (f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}" rx="{rx}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')

    if shape == "step":
        # 步骤箭头 — 右侧三角箭头
        mid = nx + nw * 0.75
        d = f"M {nx} {ny}"
        d += f" L {mid} {ny}"
        d += f" L {nx + nw} {ny + nh / 2}"
        d += f" L {mid} {ny + nh}"
        d += f" L {nx} {ny + nh} Z"
        return (f'<path d="{d}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')

    if shape == "parallelogram":
        # 平行四边形
        skew = nw * 0.25
        pts = [
            (nx + skew, ny),
            (nx + nw, ny),
            (nx + nw - skew, ny + nh),
            (nx, ny + nh),
        ]
        points_str = " ".join(f"{p[0]},{p[1]}" for p in pts)
        return (f'<polygon points="{points_str}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')

    if shape == "rhombus":
        # 菱形（决策节点）
        cx = nx + nw / 2
        cy = ny + nh / 2
        pts = [
            (cx, ny),
            (nx + nw, cy),
            (cx, ny + nh),
            (nx, cy),
        ]
        points_str = " ".join(f"{p[0]},{p[1]}" for p in pts)
        return (f'<polygon points="{points_str}" '
                f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')

    # rect（默认带圆角）
    return (f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}" rx="4" '
            f'fill="{fill_val}" stroke="{stroke_color}" stroke-width="{sw}"{filter_str}/>')