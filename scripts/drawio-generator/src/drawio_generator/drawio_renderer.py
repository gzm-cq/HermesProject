#!/usr/bin/env python3
"""Drawio 渲染 — 将布局数据渲染为 .drawio XML 格式"""

import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .edge_styles import (
    apply_flow_animation,
    check_arrowhead_gap,
    distribute_ports,
    get_base_edge_style,
)
from .palettes import _resolve_color, _lighten, _get_arrow_style, _sanitize_color
from .shapes import SHAPES
from .shape_library import get_shape, shape_to_drawio_style  # 新增: 优先从形状库查找
from .geometry import compute_edge_path


# drawio 允许的安全 HTML 标签（value 属性中的 html=1 模式）
# 仅允许裸标签，不允许携带任何属性（防止 onclick/style 等属性注入）
_SAFE_HTML_TAGS = [
    r"<br\s*/?>",
    r"</?b\s*>",
    r"</?i\s*>",
    r"</?u\s*>",
    r"</?em\s*>",
    r"</?strong\s*>",
    r"</?div\s*>",
    r"</?span\s*>",
    r"<hr\s*/?>",
    r"</?font\s*>",
]
_SAFE_HTML_RE = re.compile("|".join(_SAFE_HTML_TAGS), re.IGNORECASE)


def _drawio_escape_html_label(text):
    """
    为 drawio value 属性转义用户文本，同时保留合法 HTML 标签（如 <br>, <b> 等）。
    - 将合法标签暂存为占位符
    - 对剩余文本做 XML escape（防止 XSS / 样式注入）
    - 还原合法标签
    """
    if not text:
        return ""
    placeholders = []

    def _replace_tag(m):
        placeholders.append(m.group(0))
        return f"\x00TAG{len(placeholders) - 1}\x00"

    protected = _SAFE_HTML_RE.sub(_replace_tag, text)
    escaped = escape(protected)
    for i, tag in enumerate(placeholders):
        escaped = escaped.replace(f"\x00TAG{i}\x00", tag)
    return escaped


# ===== Drawio 辅助函数 =====

def _add_drawio_cell(root, cid, parent, value, style, x, y, w, h, vertex=False, edge=False):
    attrs = {"id": cid, "parent": parent, "value": value, "style": style}
    if vertex:
        attrs["vertex"] = "1"
    if edge:
        attrs["edge"] = "1"
    cell = ET.SubElement(root, "mxCell", **attrs)
    if vertex:
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
    elif edge:
        ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})


def _add_drawio_edge(root, eid, parent, style, source, target, label="",
                     exitX=None, exitY=None, entryX=None, entryY=None):
    cell = ET.SubElement(root, "mxCell", id=eid, parent=parent, style=style,
                         source=source, target=target, edge="1", value=_drawio_escape_html_label(label))
    geo_attrs = {"relative": "1", "as": "geometry"}
    if exitX is not None:
        geo_attrs["exitX"] = str(exitX)
        geo_attrs["exitY"] = str(exitY)
    if entryX is not None:
        geo_attrs["entryX"] = str(entryX)
        geo_attrs["entryY"] = str(entryY)
    ET.SubElement(cell, "mxGeometry", **geo_attrs)


# ===== Drawio 模板函数 =====

def _sanitize_style_value(val):
    """清理 drawio style 值，移除分隔符防止属性注入"""
    return str(val).replace(";", "").replace("=", "").strip()


def _drawio_title_style(font_size, font_family=None):
    style = f"text;html=1;strokeColor=none;fillColor=none;align=center;fontSize={int(font_size)};"
    if font_family:
        style += f"fontFamily={_sanitize_style_value(font_family.split(',')[0])};"
    return style


def _drawio_layer_style(lbg, lstroke, sw):
    return (f"rounded=0;fillColor={lbg};"
            f"strokeColor={lstroke};strokeWidth={sw};")


def _drawio_node_style(shape_style, nc, sw, font_family=None, font_size=None,
                        gradient=False, shadow=False, sketch=False):
    parts = [shape_style,
             f"fillColor={_sanitize_color(nc['fill'])};strokeColor={_sanitize_color(nc['stroke'])};strokeWidth={sw};"]
    if font_family:
        parts.insert(0, f"fontFamily={_sanitize_style_value(font_family.split(',')[0])};")
    if font_size:
        parts.insert(0, f"fontSize={int(font_size.get('label', 11))};")
    if gradient:
        parts.append(f"gradientColor={_lighten(nc['fill'], 0.2)};")
    if shadow:
        parts.append("shadow=1;")
    if sketch:
        parts.append("sketch=1;")
    return "".join(parts)


def _drawio_node_label(node, label, nc, show_emoji):
    """构建节点显示内容（含 emoji / sub_label / bold / image）"""
    result = _drawio_escape_html_label(label)
    if node.get("emoji") and show_emoji:
        emoji = _drawio_escape_html_label(str(node["emoji"]))
        result = f'<div style="font-size:24px;text-align:center">{emoji}</div>' + result
    sub = node.get("sub_label", "")
    if sub:
        sc = _sanitize_color(nc["stroke"])
        result = result + f'<hr size="1" style="border-color:{sc}30"/><span style="font-size:9px;color:{sc}">{_drawio_escape_html_label(sub)}</span>'
    return result


def _drawio_edge_style(as_info, sw, dashed=False, has_label=False, bidirectional=False,
                       flow_animation=False, edge_style="orthogonal"):
    style = get_base_edge_style(edge_style)
    style += f"endArrow={as_info['drawio']};strokeWidth={sw};"
    if bidirectional:
        style += f"startArrow={as_info['drawio']};"
    if dashed:
        style += "dashed=1;"
    if has_label:
        style += "verticalLabelPosition=bottom;align=center;"
    style = apply_flow_animation(style, enabled=flow_animation)
    return style


# ===== Drawio 渲染主函数 =====
def _render_drawio(width, height, title, nodes, edges, layers, colors, path,
                    stroke_width=None, arrow_style="classic",
                    font_family=None, font_size=None, gradient=False, shadow=False, show_emoji=True,
                    containers=None, node_parent_map=None, flow_animation=False, edge_style="orthogonal",
                    sketch_mode=False):
    """渲染为 .drawio 格式"""
    sw = stroke_width if stroke_width is not None else 1
    mxfile = ET.Element("mxfile", host="Electron", version="24.6.4")
    d = ET.SubElement(mxfile, "diagram", id="1", name=(title or "Diagram")[:20])
    g = ET.SubElement(d, "mxGraphModel", dx="0", dy="0", grid="1", gridSize="10",
                      pageWidth=str(width), pageHeight=str(height + 100),
                      background=colors.get("bg", "#ffffff"))
    r = ET.SubElement(g, "root")
    ET.SubElement(r, "mxCell", id="0")
    ET.SubElement(r, "mxCell", id="1", parent="0")

    nid = 100
    node_ids = {}
    reserved_ids = set()

    # 如果有 containers，先收集所有已用的 cid 避免碰撞
    if containers:
        for cid, *_rest in containers:
            try:
                reserved_ids.add(int(cid))
            except (ValueError, TypeError):
                pass

    # Title
    fs_title = font_size.get("title", 16) if font_size else 16
    title_style = _drawio_title_style(fs_title, font_family)
    while nid in reserved_ids:
        nid += 1
    _add_drawio_cell(r, str(nid), "1", f"<b>{_drawio_escape_html_label(title)}</b>",
                     title_style,
                     width // 2 - 200, 5, 400, 30, vertex=True)
    nid += 1

    # Layers (background)
    for layer in layers:
        while nid in reserved_ids:
            nid += 1
        cid = str(nid)
        nid += 1
        _add_drawio_cell(r, cid, "1", "",
                         _drawio_layer_style(
                             _sanitize_color(colors.get('layer_bg', '#f5f5f5')),
                             _sanitize_color(colors.get('layer_stroke', '#ccc')), sw),
                         layer.get("x", 0) or 0, layer.get("y", 0) or 0,
                         layer.get("w", 0) or 0, layer.get("h", 0) or 0, vertex=True)
        if "label" in layer:
            while nid in reserved_ids:
                nid += 1
            _add_drawio_cell(r, str(nid), "1", f"<b>{_drawio_escape_html_label(layer['label'])}</b>",
                             "text;html=1;strokeColor=none;fillColor=none;align=center;fontSize=12;fontStyle=1;",
                             (layer.get("x", 0) or 0) + (layer.get("w", 0) or 0) // 2 - 100, (layer.get("y", 0) or 0) + 3, 200, 20, vertex=True)
            nid += 1

    # Containers (outer before inner)
    node_parent = {}
    if containers:
        # 预建 cid → [node_ids] 反向映射，避免 O(C×N) 嵌套
        cid_to_nids = {}
        for nid_orig, cid in node_parent_map.items():
            cid_to_nids.setdefault(cid, []).append(nid_orig)
        for cid, parent, style, cx, cy, cw, ch, label in containers:
            # 解析 strokeColor 用于容器标签颜色
            container_style = style
            _add_drawio_cell(r, cid, parent, f"<b>{_drawio_escape_html_label(label)}</b>",
                             container_style,
                             cx, cy, cw, ch, vertex=True)
            # 记录哪个容器包含哪些节点
            for nid_orig in cid_to_nids.get(cid, []):
                node_parent[nid_orig] = cid

    # Nodes
    for node in nodes:
        while nid in reserved_ids:
            nid += 1
        cid = str(nid)
        nid += 1
        node_ids[node.get("id", cid)] = cid
        nc = _resolve_color(colors, node)
        shape = node.get("shape", "rect")
        # 优先从 shape_library 查找，回退到 SHAPES
        shape_style = shape_to_drawio_style(shape)
        style = _drawio_node_style(shape_style, nc, sw, font_family, font_size,
                                    gradient, shadow, sketch=sketch_mode)

        label = node.get("label", "")
        full_label = _drawio_node_label(node, label, nc, show_emoji)

        if node.get("image"):
            # 防御性转义：URL 中的 ; / = 可能注入 style
            safe_url = urllib.parse.quote(str(node["image"]), safe='/:?&=@%#+()[]*!$,~')
            style = f"shape=image;image={safe_url};"
            val = _drawio_escape_html_label(label)
        elif node.get("bold") and not node.get("emoji"):
            val = f"<b>{_drawio_escape_html_label(label)}</b>"
        else:
            val = full_label

        # 确定节点父容器
        parent_id = "1"
        node_orig_id = node.get("id", "")
        if node_orig_id and node_orig_id in node_parent:
            parent_id = node_parent[node_orig_id]

        _add_drawio_cell(r, cid, parent_id, val, style,
                         node.get("x", 0) or 0, node.get("y", 0) or 0,
                         node.get("w", 0) or 0, node.get("h", 0) or 0, vertex=True)

    # 端口分布：为每条边计算 exitX/exitY/entryX/entryY
    port_map = distribute_ports(nodes, edges)

    # Edges
    for edge in edges:
        src = node_ids.get(edge.get("from", ""), "")
        tgt = node_ids.get(edge.get("to", ""), "")
        if src and tgt:
            while nid in reserved_ids:
                nid += 1
            eid = str(nid)
            nid += 1
            as_info = _get_arrow_style(edge, arrow_style)
            is_back = edge.get("back_edge", False)
            bidirectional = edge.get("bidirectional", False)
            # 边级 flow_animation 优先于全局设置
            edge_flow_anim = edge.get("flow_animation", flow_animation)
            estyle = _drawio_edge_style(as_info, sw,
                                         dashed=is_back or edge.get("dashed", False),
                                         has_label=bool(edge.get("label")),
                                         bidirectional=bidirectional,
                                         flow_animation=edge_flow_anim,
                                         edge_style=edge_style)
            if is_back:
                estyle += "strokeColor=#E74C3C;"
            # 端口位置
            exitX = exitY = entryX = entryY = None
            key = (edge.get("from", ""), edge.get("to", ""))
            if key in port_map:
                exitX, exitY, entryX, entryY = port_map[key]
            _add_drawio_edge(r, eid, "1", estyle, src, tgt, label=edge.get("label", ""),
                             exitX=exitX, exitY=exitY, entryX=entryX, entryY=entryY)

    # 箭头头间距检查（基于边路径）
    node_map = {n.get("id"): n for n in nodes if n.get("id")}
    edge_paths: list[list[tuple[float, float]]] = []
    for edge in edges:
        src_n = node_map.get(edge.get("from", ""))
        tgt_n = node_map.get(edge.get("to", ""))
        if src_n and tgt_n:
            pts = compute_edge_path(src_n, tgt_n, curve=edge_style)
            edge_paths.append(pts)

    gap_warnings = check_arrowhead_gap(edge_paths)
    for w in gap_warnings:
        print(f"  [WARN]  {w}")

    tree = ET.ElementTree(mxfile)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    print(f"Generated: {path}")

    # 后处理校验 + 自动修复
    fixed, rep_issues = repair_drawio(path)
    if rep_issues:
        for typ, msg in rep_issues:
            if typ == "error":
                print(f"  [ERROR] {msg}", file=sys.stderr)
            elif typ == "fixed":
                print(f"  [FIXED] {msg}")
            else:
                print(f"  [WARN]  {msg}")


# ===== drawio 文件后处理修复 =====
def repair_drawio(path):
    """
    校验并自动修复 .drawio 文件中的常见问题。
    返回 (fixed_count, issues)，issues 为 [(type, msg), ...]。
    type 取值: "error" | "warn" | "fixed"
    """
    issues = []
    fixed = 0

    # 1. XML 解析校验
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return (0, [("error", f"XML 解析失败: {e}")])
    except FileNotFoundError:
        return (0, [("error", f"文件不存在: {path}")])

    root = tree.getroot()

    # 2. 两遍扫描：先收集全部 cell id，再校验引用，避免父容器 cell
    #    晚于子节点出现时被误判为未定义 parent 而错误重置为 "1"。
    cells = {}
    seen_ids = set()

    # 2a. 第一遍：收集 id → cell 映射，检测重复
    for cell in root.iter("mxCell"):
        cid = cell.get("id")
        if cid:
            if cid in seen_ids:
                issues.append(("warn", f"发现重复 cell id='{cid}'"))
            seen_ids.add(cid)
            cells[cid] = cell

    # 2b. 第二遍：parent 引用 / edge geometry / ASCII 检查
    for cell in root.iter("mxCell"):
        cid = cell.get("id")

        # 检查 parent 引用有效性
        parent = cell.get("parent")
        if parent and parent not in cells:
            issues.append(("warn", f"cell id='{cid or '(none)'}' 引用未定义 parent='{parent}'"))
            if "1" in cells:
                cell.set("parent", "1")
                fixed += 1
                issues.append(("fixed", f"cell id='{cid or '(none)'}' parent→'1'"))

        # 检查 edge 的 mxGeometry 是否含 relative="1"
        if cell.get("edge") == "1":
            for geo in cell.iter("mxGeometry"):
                if geo.get("relative") != "1":
                    geo.set("relative", "1")
                    fixed += 1
                    issues.append(("fixed", f"edge id='{cid}' 已添加 relative=1"))

        # 检查 id 是否含非 ASCII 字符
        if cid and not cid.isdigit() and any(ord(c) > 127 for c in cid):
            issues.append(("warn", f"cell id='{cid}' 含非 ASCII 字符，建议纯英文"))

    # 6. 检查根节点完整性
    if "0" not in cells:
        issues.append(("warn", "缺少根 cell id='0'"))
    if "1" not in cells:
        issues.append(("warn", "缺少根 cell id='1'"))

    # 7. 写出修复
    if fixed > 0:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="utf-8", xml_declaration=True)

    return (fixed, issues)
