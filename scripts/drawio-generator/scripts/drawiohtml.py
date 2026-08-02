#!/usr/bin/env python3
"""交互式 HTML 查看器生成器 — 将 .drawio 转为自包含 HTML，支持拖拽/缩放/搜索"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

DPI = 72


def _parse_drawio(path):
    """解析 .drawio 文件，返回页面列表。每页: {id, name, nodes, edges}"""
    tree = ET.parse(path)
    root = tree.getroot()
    # mxfile > diagram > mxGraphModel > root > mxCell
    pages = []
    # 兼容两种格式: 直接 mxfile 或 mxfile/diagram
    diagrams = root.findall("diagram")
    if not diagrams:
        diagrams = [root]
    for diag in diagrams:
        pid = diag.get("id", "0")
        pname = diag.get("name", "Page-1")
        model = diag.find("mxGraphModel")
        cells_root = model.find("root") if model is not None else diag
        if cells_root is None:
            cells_root = diag
        nodes, edges = [], []
        for cell in cells_root.findall("mxCell"):
            cid = cell.get("id", "")
            parent = cell.get("parent", "1")
            if parent != "1" and parent != "0":
                continue  # 跳过子节点
            if cell.get("vertex") == "1":
                geo = cell.find("mxGeometry")
                if geo is not None:
                    x = float(geo.get("x", 0))
                    y = float(geo.get("y", 0))
                    w = float(geo.get("width", 100))
                    h = float(geo.get("height", 60))
                else:
                    x, y, w, h = 0, 0, 100, 60
                label = cell.get("value", "")
                # 清理 HTML 标签
                label = re.sub(r"<[^>]+>", "", label)
                label = label.replace("&nbsp;", " ").replace("&amp;", "&")
                style = cell.get("style", "")
                fill = "#dae8fc"
                m = re.search(r"fillColor=([^;]+)", style)
                if m:
                    fill = m.group(1)
                nodes.append({"id": cid, "label": label, "x": x, "y": y,
                              "w": w, "h": h, "fill": fill})
            elif cell.get("edge") == "1":
                src = cell.get("source", "")
                tgt = cell.get("target", "")
                label = cell.get("value", "")
                label = re.sub(r"<[^>]+>", "", label)
                label = label.replace("&nbsp;", " ").replace("&amp;", "&")
                edges.append({"from": src, "to": tgt, "label": label})
        pages.append({"id": pid, "name": pname, "nodes": nodes, "edges": edges})
    return pages


def _build_edge_path(src_node, tgt_node):
    """计算正交边路径点"""
    if not src_node or not tgt_node:
        return []
    sx, sy = src_node["x"] + src_node["w"] / 2, src_node["y"] + src_node["h"] / 2
    tx, ty = tgt_node["x"] + tgt_node["w"] / 2, tgt_node["y"] + tgt_node["h"] / 2
    mid_y = (sy + ty) / 2
    return [(sx, sy), (sx, mid_y), (tx, mid_y), (tx, ty)]


def _render_page_svg(page, page_idx):
    """将一页渲染为 SVG 字符串"""
    nodes = page["nodes"]
    edges = page["edges"]
    node_map = {n["id"]: n for n in nodes}

    # 计算 viewBox
    if not nodes:
        return f'<svg width="800" height="600" xmlns="http://www.w3.org/2000/svg"></svg>'

    xs = [n["x"] for n in nodes] + [n["x"] + n["w"] for n in nodes]
    ys = [n["y"] for n in nodes] + [n["y"] + n["h"] for n in nodes]
    min_x, min_y = min(xs) - 20, min(ys) - 20
    max_x, max_y = max(xs) + 20, max(ys) + 20
    vw, vh = max_x - min_x, max_y - min_y

    lines = [
        f'<svg width="{vw}" height="{vh}" viewBox="{min_x} {min_y} {vw} {vh}" '
        f'xmlns="http://www.w3.org/2000/svg" class="page-svg" id="svg-{page_idx}">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#666"/></marker></defs>',
    ]

    # 边
    for e in edges:
        src = node_map.get(e["from"])
        tgt = node_map.get(e["to"])
        if not src or not tgt:
            continue
        pts = _build_edge_path(src, tgt)
        if len(pts) < 2:
            continue
        d = " ".join(f"{'M' if i==0 else 'L'}{p[0]} {p[1]}" for i, p in enumerate(pts))
        lines.append(f'<path d="{d}" fill="none" stroke="#999" stroke-width="1.5" '
                     f'marker-end="url(#a)" class="edge"/>')
        if e["label"]:
            mx, my = (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2
            lines.append(f'<text x="{mx}" y="{my - 5}" text-anchor="middle" '
                         f'font-size="11" fill="#666" class="edge-label">{e["label"]}</text>')

    # 节点
    for n in nodes:
        fill = n.get("fill", "#dae8fc")
        lines.append(
            f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" '
            f'rx="4" ry="4" fill="{fill}" stroke="#6c8ebf" stroke-width="1.5" '
            f'class="node" data-id="{n["id"]}" data-label="{n["label"]}"/>'
        )
        lines.append(
            f'<text x="{n["x"] + n["w"] / 2}" y="{n["y"] + n["h"] / 2 + 4}" '
            f'text-anchor="middle" font-size="12" fill="#333" class="node-label">{n["label"]}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def _generate_html(pages, svgs):
    """生成自包含 HTML"""
    tabs_html = "".join(
        f'<button class="tab {"active" if i==0 else ""}" '
        f'onclick="switchPage({i})">{p["name"]}</button>'
        for i, p in enumerate(pages)
    )
    svg_divs = "".join(
        f'<div class="svg-container {"active" if i==0 else ""}" id="page-{i}">{s}</div>'
        for i, s in enumerate(svgs)
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Diagram Viewer</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: sans-serif; background: #f5f5f5; }}
.toolbar {{ position:fixed; top:0; left:0; right:0; height:40px;
  background:#fff; border-bottom:1px solid #ddd; display:flex;
  align-items:center; padding:0 12px; z-index:100; gap:8px; }}
.tab {{ padding:6px 14px; border:1px solid #ddd; background:#f8f8f8;
  cursor:pointer; border-radius:4px; font-size:13px; }}
.tab.active {{ background:#1976d2; color:#fff; border-color:#1976d2; }}
#search {{ padding:4px 8px; border:1px solid #ddd; border-radius:4px;
  font-size:13px; width:180px; margin-left:auto; }}
.viewer {{ position:fixed; top:40px; left:0; right:0; bottom:0;
  overflow:hidden; cursor:grab; }}
.svg-container {{ display:none; width:100%; height:100%; overflow:visible; }}
.svg-container.active {{ display:block; }}
.page-svg {{ display:block; }}
.node {{ transition:opacity 0.2s; }}
.node.highlight {{ stroke:#e53935 !important; stroke-width:3 !important; }}
.node.fade {{ opacity:0.3; }}
</style>
</head>
<body>
<div class="toolbar">{tabs_html}
<input id="search" type="text" placeholder="搜索节点..." oninput="searchNodes(this.value)">
</div>
<div class="viewer" id="viewer">{svg_divs}</div>
<script>
let scale=1, panX=0, panY=0, isPanning=false, startX, startY;
const viewer=document.getElementById('viewer');
const pages=document.querySelectorAll('.svg-container');
function switchPage(i){{
  pages.forEach((p,j)=>p.classList.toggle('active',j===i));
  scale=1; panX=0; panY=0;
  applyTransform();
}}
function applyTransform(){{
  const active=document.querySelector('.svg-container.active svg');
  if(active) active.style.transform=`translate(${{panX}}px,${{panY}}px) scale(${{scale}})`;
}}
viewer.onmousedown=e=>{{ isPanning=true; startX=e.clientX-panX; startY=e.clientY-panY; viewer.style.cursor='grabbing'; }};
viewer.onmousemove=e=>{{ if(!isPanning)return; panX=e.clientX-startX; panY=e.clientY-startY; applyTransform(); }};
viewer.onmouseup=()=>{{ isPanning=false; viewer.style.cursor='grab'; }};
viewer.onmouseleave=()=>{{ isPanning=false; viewer.style.cursor='grab'; }};
viewer.onwheel=e=>{{ e.preventDefault(); scale=Math.max(0.5,Math.min(5,scale-e.deltaY*0.001));
  applyTransform(); }};
function searchNodes(q){{
  document.querySelectorAll('.node').forEach(n=>{{
    const label=n.getAttribute('data-label')||'';
    n.classList.toggle('highlight',q&&label.includes(q));
    n.classList.toggle('fade',q&&!label.includes(q));
  }});
}}
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description=".drawio → 交互式 HTML 查看器")
    ap.add_argument("input", help="输入 .drawio 文件")
    ap.add_argument("-o", "--output", default="viewer.html", help="输出 HTML 文件")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"error: 文件不存在: {args.input}")

    pages = _parse_drawio(args.input)
    if not pages:
        sys.exit("error: 未找到页面")

    svgs = [_render_page_svg(p, i) for i, p in enumerate(pages)]
    html = _generate_html(pages, svgs)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated: {args.output} ({len(pages)} page(s))")


if __name__ == "__main__":
    main()