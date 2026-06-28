#!/usr/bin/env python3
"""Drawio 渲染 — 将布局数据渲染为 .drawio XML 格式"""

import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .palettes import _resolve_color, _lighten, _get_arrow_style
from .shapes import SHAPES


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


def _add_drawio_edge(root, eid, parent, style, source, target, label=""):
    cell = ET.SubElement(root, "mxCell", id=eid, parent=parent, style=style,
                         source=source, target=target, edge="1", value=escape(label))
    ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})


# ===== Drawio 模板函数 =====

def _drawio_title_style(font_size, font_family=None):
    style = f"text;html=1;strokeColor=none;fillColor=none;align=center;fontSize={font_size};"
    if font_family:
        style += f"fontFamily={font_family.split(',')[0].strip()};"
    return style


def _drawio_layer_style(lbg, lstroke, sw):
    return (f"rounded=0;fillColor={lbg};"
            f"strokeColor={lstroke};strokeWidth={sw};")


def _drawio_node_style(shape_style, nc, sw, font_family=None, font_size=None,
                        gradient=False, shadow=False):
    parts = [shape_style,
             f"fillColor={nc['fill']};strokeColor={nc['stroke']};strokeWidth={sw};"]
    if font_family:
        parts.insert(0, f"fontFamily={font_family.split(',')[0].strip()};")
    if font_size:
        parts.insert(0, f"fontSize={font_size.get('label', 11)};")
    if gradient:
        parts.append(f"gradientColor={_lighten(nc['fill'], 0.2)};")
    if shadow:
        parts.append("shadow=1;")
    return "".join(parts)


def _drawio_node_label(node, label, nc, show_emoji):
    """构建节点显示内容（含 emoji / sub_label / bold / image）"""
    result = label
    if node.get("emoji") and show_emoji:
        result = f'<div style="font-size:24px;text-align:center">{node["emoji"]}</div>' + result
    sub = node.get("sub_label", "")
    if sub:
        result = result + f'<hr size="1" style="border-color:{nc["stroke"]}30"/><span style="font-size:9px;color:{nc["stroke"]}">{escape(sub)}</span>'
    return result


def _drawio_edge_style(as_info, sw, dashed=False, has_label=False):
    style = f"endArrow={as_info['drawio']};html=1;strokeWidth={sw};"
    if dashed:
        style += "dashed=1;"
    if has_label:
        style += "verticalLabelPosition=bottom;align=center;"
    return style


# ===== Drawio 渲染主函数 =====
def _render_drawio(width, height, title, nodes, edges, layers, colors, path,
                    stroke_width=None, arrow_style="classic",
                    font_family=None, font_size=None, gradient=False, shadow=False, show_emoji=True):
    """渲染为 .drawio 格式"""
    sw = stroke_width if stroke_width is not None else 1
    mxfile = ET.Element("mxfile", host="Electron", version="24.6.4")
    d = ET.SubElement(mxfile, "diagram", id="1", name=title[:20])
    g = ET.SubElement(d, "mxGraphModel", dx="0", dy="0", grid="1", gridSize="10",
                      pageWidth=str(width), pageHeight=str(height + 100),
                      background=colors.get("bg", "#ffffff"))
    r = ET.SubElement(g, "root")
    ET.SubElement(r, "mxCell", id="0")
    ET.SubElement(r, "mxCell", id="1", parent="0")

    nid = 100
    node_ids = {}

    # Title
    fs_title = font_size.get("title", 16) if font_size else 16
    title_style = _drawio_title_style(fs_title, font_family)
    _add_drawio_cell(r, str(nid), "1", f"<b>{escape(title)}</b>",
                     title_style,
                     width // 2 - 200, 5, 400, 30, vertex=True)
    nid += 1

    # Layers (background)
    for layer in layers:
        cid = str(nid)
        nid += 1
        _add_drawio_cell(r, cid, "1", "",
                         _drawio_layer_style(
                             colors.get('layer_bg', '#f5f5f5'),
                             colors.get('layer_stroke', '#ccc'), sw),
                         layer["x"], layer["y"], layer["w"], layer["h"], vertex=True)
        if "label" in layer:
            _add_drawio_cell(r, str(nid), "1", f"<b>{escape(layer['label'])}</b>",
                             "text;html=1;strokeColor=none;fillColor=none;align=center;fontSize=12;fontStyle=1;",
                             layer["x"] + layer["w"] // 2 - 100, layer["y"] + 3, 200, 20, vertex=True)
            nid += 1

    # Nodes
    for node in nodes:
        cid = str(nid)
        nid += 1
        node_ids[node.get("id", cid)] = cid
        nc = _resolve_color(colors, node)
        shape = node.get("shape", "rect")
        shape_style = SHAPES.get(shape, SHAPES["rect"])["drawio"]
        style = _drawio_node_style(shape_style, nc, sw, font_family, font_size,
                                    gradient, shadow)

        label = node.get("label", "")
        full_label = _drawio_node_label(node, label, nc, show_emoji)

        if node.get("image"):
            style = f"shape=image;image={node['image']};"
            val = escape(label)
        elif node.get("bold") and not node.get("emoji"):
            val = f"<b>{label}</b>"
        elif not node.get("emoji"):
            val = full_label
        else:
            val = full_label

        _add_drawio_cell(r, cid, "1", val, style,
                         node["x"], node["y"], node["w"], node["h"], vertex=True)

    # Edges
    for edge in edges:
        src = node_ids.get(edge.get("from", ""), "")
        tgt = node_ids.get(edge.get("to", ""), "")
        if src and tgt:
            eid = str(nid)
            nid += 1
            as_info = _get_arrow_style(edge, arrow_style)
            is_back = edge.get("back_edge", False)
            estyle = _drawio_edge_style(as_info, sw,
                                         dashed=is_back or edge.get("dashed", False),
                                         has_label=bool(edge.get("label")))
            if is_back:
                estyle += "strokeColor=#E74C3C;"
            _add_drawio_edge(r, eid, "1", estyle, src, tgt, label=edge.get("label", ""))

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

    # 2. 收集所有 mxCell id → element 映射，同时检测重复
    cells = {}
    seen_ids = set()
    for cell in root.iter("mxCell"):
        cid = cell.get("id")
        if cid:
            if cid in seen_ids:
                issues.append(("warn", f"发现重复 cell id='{cid}'"))
            seen_ids.add(cid)
            cells[cid] = cell

    # 3. 检查 parent 引用有效性
    for cell in root.iter("mxCell"):
        parent = cell.get("parent")
        cid = cell.get("id", "(none)")
        if parent and parent not in cells:
            issues.append(("warn", f"cell id='{cid}' 引用未定义 parent='{parent}'"))
            # 修复为默认根节点
            if "1" in cells:
                cell.set("parent", "1")
                fixed += 1
                issues.append(("fixed", f"cell id='{cid}' parent→'1'"))

    # 4. 检查 edge 的 mxGeometry 是否含 relative="1"
    for cell in root.iter("mxCell"):
        if cell.get("edge") == "1":
            for geo in cell.iter("mxGeometry"):
                if geo.get("relative") != "1":
                    geo.set("relative", "1")
                    fixed += 1
                    issues.append(("fixed", f"edge id='{cell.get('id')}' 已添加 relative=1"))

    # 5. 检查 id 是否含非 ASCII 字符（仅检查非纯数字 ID，避免对自动映射的 ID 误报）
    for cell in root.iter("mxCell"):
        cid = cell.get("id", "")
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
