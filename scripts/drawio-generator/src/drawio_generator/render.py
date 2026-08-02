#!/usr/bin/env python3
"""draw.io 矢量图生成器 - 渲染引擎核心库"""

import argparse
import json
import sys
import webbrowser
from xml.sax.saxutils import escape as _html_escape
from pathlib import Path

from http.server import HTTPServer, SimpleHTTPRequestHandler

from .palettes import (PALETTES, DEFAULT_PALETTE, PALETTE_INFO,
                       _resolve_color, _get_arrow_style, _apply_grayscale)
from .shapes import SHAPES, _render_svg_shape
from .validator import validate_plan
from .svg_renderer import _render_svg
from .drawio_renderer import _render_drawio, repair_drawio
from .templates import apply_template, TEMPLATES
from .diagram_presets import apply_diagram_type, PRESETS
from .style_presets import load_preset as _load_style_preset
from .legend import build_legend

VERSION = "1.2.0"


# ===== 主入口 =====
def render(plan_dict, output_path):
    """
    根据结构化布局字典渲染矢量图，自动根据 format 字段选择输出格式。
    plan_dict 必须包含:
        title, width, height, nodes, edges
    可选:
        layers, palette, format ("drawio"|"svg"), show_emoji,
        font_family, font_size, stroke_width, arrow_style, grayscale,
        gradient, shadow, paper_mode, presentation, auto_fit,
        template, diagram_type, layout_engine, flow_animation, edge_style
    """
    # 注意：本函数会原地修改 plan_dict（为节点补全坐标等）。
    # 调用方如需保留原始数据，请自行传入 deepcopy。
    plan = apply_template(plan_dict)
    plan = apply_diagram_type(plan)
    nodes = plan.get("nodes", [])
    edges = plan.get("edges", [])
    auto_fit = plan.get("auto_fit", True)

    # 自动布局：先于校验执行，为缺坐标的节点补全坐标
    auto_layout = plan.get("auto_layout", False) or any(
        n.get("x") is None or n.get("y") is None for n in nodes
    )
    if auto_layout and nodes:
        layout_engine = plan.get("layout_engine", "native")
        direction = plan.get("layout_direction", "vertical")
        if layout_engine == "graphviz":
            from .graphviz_layout import layout_plan_graphviz
            layout_kw = {"direction": direction}
            for k in ("padding", "nodesep", "ranksep"):
                if k in plan:
                    layout_kw[k] = plan[k]
            layout_result = layout_plan_graphviz(nodes, edges, **layout_kw)
        else:
            from .layout import layout_plan
            layout_kw = {"direction": direction}
            for k in ("gap", "layer_gap", "padding"):
                if k in plan:
                    layout_kw[k] = plan[k]
            layout_result = layout_plan(nodes, edges, **layout_kw)
        nodes = layout_result["nodes"]
        plan["nodes"] = nodes
        if auto_fit:
            plan["width"] = layout_result["width"]
            plan["height"] = layout_result["height"]
        if layout_result.get("has_cycle"):
            print("[WARN]  layout: 检测到环路依赖，已自动处理", file=sys.stderr)
        back_edges = set(tuple(be) for be in layout_result.get("back_edges", []))
        for edge in edges:
            if (edge.get("from", ""), edge.get("to", "")) in back_edges:
                edge["back_edge"] = True
        route_map = {(r["from"], r["to"]): r["points"]
                      for r in layout_result.get("edge_routes", [])}
        for edge in edges:
            key = (edge.get("from"), edge.get("to"))
            if key in route_map:
                edge["points"] = route_map[key]

    # 容器处理（独立于 auto_layout，有坐标即可）
    layers = plan.get("layers", [])
    has_group = any(n.get("group") for n in nodes)
    containers = None
    node_parent_map = None
    if has_group:
        from .containers import (
            parse_group_tree, compute_container_boxes,
            assign_group_colors, compute_node_offsets,
            generate_container_cells, apply_group_colors_to_nodes,
            GROUP_COLOR_CYCLE,
        )
        raw_palette = plan.get("palette", "academic")
        if isinstance(raw_palette, str):
            container_palette = dict(PALETTES.get(raw_palette, PALETTES["academic"]))
        else:
            container_palette = dict(raw_palette)
            for k, v in PALETTES["academic"].items():
                container_palette.setdefault(k, v)

        tree = parse_group_tree(nodes)
        if tree["ordered"]:
            container_boxes = compute_container_boxes(tree, nodes, padding=24)
            group_colors = assign_group_colors(tree, container_palette)
            nodes = apply_group_colors_to_nodes(nodes, tree, group_colors, container_palette)
            plan["nodes"] = nodes

            nid_counter = 100 + 1 + len(layers) * 2
            nid_counter += len(tree["ordered"]) * 2
            cells, _, path_cid_map = generate_container_cells(
                tree, container_boxes, group_colors, container_palette, nid_counter
            )

            offsets = compute_node_offsets(tree, container_boxes)
            node_parent_map = {}
            for n in nodes:
                nid = n.get("id", "")
                if nid in offsets:
                    ox, oy = offsets[nid]
                    nx = n.get("x")
                    ny = n.get("y")
                    if nx is not None and ny is not None:
                        n["x"] = int(nx - ox)
                        n["y"] = int(ny - oy)
                    path = tree["gpath"].get(nid)
                    if path and path in path_cid_map:
                        node_parent_map[nid] = path_cid_map[path]

            containers = cells

    # 输入校验
    issues = validate_plan(plan)
    has_error = False
    for typ, field, msg in issues:
        if typ == "error":
            print(f"[ERROR] {field}: {msg}", file=sys.stderr)
            has_error = True
        else:
            print(f"[WARN]  {field}: {msg}", file=sys.stderr)
    if has_error:
        raise ValueError("布局 JSON 校验失败，请修复以上 ERROR")

    title = plan.get("title", "架构图")
    width = plan.get("width", 1000)
    height = plan.get("height", 800)

    # 输出格式：文件扩展名决定优先级最高，最后判定
    fmt = plan.get("format", "drawio")
    paper_mode = plan.get("paper_mode", False)
    presentation = plan.get("presentation", False)
    if paper_mode:
        fmt = "svg"
    if presentation:
        pass

    ext = Path(output_path).suffix.lower()
    if ext == ".svg":
        fmt = "svg"
    elif ext == ".drawio":
        fmt = "drawio"

    # 解析配色（先查 style_presets，再查 PALETTES）
    raw_palette = plan.get("palette", "academic")
    if isinstance(raw_palette, str):
        preset_colors = _load_style_preset(raw_palette)
        if preset_colors is not None:
            colors = dict(preset_colors)
        else:
            colors = dict(PALETTES.get(raw_palette, PALETTES["academic"]))
    else:
        colors = dict(raw_palette)
        for k, v in PALETTES["academic"].items():
            colors.setdefault(k, v)

    # 灰度修正
    grayscale = plan.get("grayscale", False)
    if grayscale or paper_mode:
        colors = _apply_grayscale(colors)

    # 解析通用配置
    font_family = plan.get("font_family")
    font_size = plan.get("font_size", {})
    if not isinstance(font_size, dict):
        font_size = {}
    stroke_width = plan.get("stroke_width")
    arrow_style = plan.get("arrow_style", "classic")
    gradient = plan.get("gradient", False)
    shadow = plan.get("shadow", False)
    show_emoji = plan.get("show_emoji", True)
    flow_animation = plan.get("flow_animation", False)
    edge_style = plan.get("edge_style", "orthogonal")

    if paper_mode:
        font_family = font_family or "Times New Roman, serif"
        font_size = font_size or {"title": 11, "label": 9, "small": 7}
        stroke_width = stroke_width if stroke_width is not None else 0.75
        if arrow_style == "classic":
            arrow_style = "open"
        gradient = False
        shadow = False

    if presentation:
        gradient = gradient if "gradient" in plan else True
        shadow = shadow if "shadow" in plan else True

    # auto_legend: 自动图例（颜色>=3才生成）
    layers = plan.get("layers", [])
    if plan.get("auto_legend", False):
        legend = build_legend(plan, nodes, edges, colors,
                              position=plan.get("legend_position", "bottom_right"))
        if legend["nodes"]:
            layers = list(layers) + legend["layers"]
            nodes = list(nodes) + legend["nodes"]
            width = max(width, plan.get("width", 1000) + legend["width"])
            height = max(height, plan.get("height", 800) + legend["height"])

    # sketch 手绘风格：轻微抖动 + 圆角加大 + 线宽变化
    sketch_mode = plan.get("sketch", False)
    if sketch_mode:
        stroke_width = (stroke_width if stroke_width is not None else 1.5) * 1.2
        # 给节点增加 sketch style 属性（drawio_renderer / svg_renderer 识别）
        for n in nodes:
            n["_sketch"] = True

    if fmt == "svg":
        _render_svg(width, height, title, nodes, edges, layers, colors,
                     output_path, font_family, font_size, stroke_width,
                     arrow_style, gradient, shadow, auto_fit, sketch_mode,
                     flow_animation=flow_animation)
    else:
        _render_drawio(width, height, title, nodes, edges, layers, colors,
                        output_path, stroke_width, arrow_style,
                        font_family, font_size, gradient, shadow, show_emoji,
                        containers=containers, node_parent_map=node_parent_map,
                        flow_animation=flow_animation, edge_style=edge_style,
                        sketch_mode=sketch_mode)


def generate_svg(plan_json, output_path):
    """[兼容别名] 从结构化布局 JSON 生成矢量图。"""
    plan = plan_json if isinstance(plan_json, dict) else json.loads(plan_json)
    render(plan, output_path)


# ===== 预览 HTML 模板 =====
_SVG_PREVIEW_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>架构图预览</title>
<style>
* { margin: 0; padding: 0; }
body { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #f5f5f5; }
svg { max-width: 95vw; max-height: 95vh; box-shadow: 0 4px 24px rgba(0,0,0,0.1); background: #fff; border-radius: 8px; }
</style>
</head>
<body>\n"""

_DRAWIO_PREVIEW_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>架构图预览 (drawio)</title>
<style>
* {{ margin: 0; padding: 0; }}
body {{ display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #f5f5f5; font-family: sans-serif; color: #666; }}
.info {{ text-align: center; }}
a {{ color: #4A6FA5; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="info">
<h2>架构图 (drawio 格式)</h2>
<p>文件: {name}</p>
<p>请使用 <a href="https://www.draw.io">draw.io</a> 打开此文件查看</p>
<p><a href=\"{name}\">下载 .drawio 文件</a></p>
</div>
</body>\n</html>"""


# ===== CLI 入口 =====
def _open_in_browser(file_path):
    abs_path = Path(file_path).resolve()
    webbrowser.open(f"file://{abs_path}")


def _serve_http(output_path, port):
    output_path = Path(output_path).resolve()
    ext = output_path.suffix.lower()
    svg_mode = ext == ".svg"

    if svg_mode:
        wrapper = _SVG_PREVIEW_HEAD
        with open(output_path, encoding="utf-8") as f:
            svg_content = f.read()
        wrapper += svg_content
        wrapper += "\n</body>\n</html>"
    else:
        wrapper = _DRAWIO_PREVIEW_HTML.format(name=_html_escape(output_path.name))

    output_dir = output_path.parent
    html_path = output_dir / "_preview.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(wrapper)

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output_dir), **kwargs)
        def log_message(self, fmt, *args):
            print(f"[HTTP] {' '.join(str(a) for a in args)}")

    server = HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://localhost:{port}/_preview.html"
    print(f"[SERVE] 预览地址: {url}")
    print(f"[SERVE] 按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVE] 已停止")
    finally:
        server.server_close()
        if html_path.exists():
            html_path.unlink()


def main():
    parser = argparse.ArgumentParser(
        description="draw.io/SVG 矢量图生成器 - 根据结构化布局 JSON 渲染矢量图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:\n  drawio-render layout.json output.svg\n  drawio-render layout.json output.drawio --open\n  drawio-render layout.json --serve --port 8080""")
    parser.add_argument("--version", action="version",
                        version=f"drawio-generator v{VERSION}",
                        help="显示版本号并退出")
    parser.add_argument("--list-palettes", action="store_true",
                        help="列出所有可用配色方案")
    parser.add_argument("layout_json", nargs="?",
                        help="布局 JSON 文件路径")
    parser.add_argument("output_path", nargs="?",
                        help="输出文件路径")
    parser.add_argument("--open", action="store_true",
                        help="生成后在默认浏览器中打开")
    parser.add_argument("--serve", action="store_true",
                        help="启动 HTTP 预览服务器")
    parser.add_argument("--port", type=int, default=8080,
                        help="HTTP 服务器端口号")

    args = parser.parse_args()

    if args.list_palettes:
        max_len = max(len(k) for k in PALETTE_INFO)
        print("可用配色方案 (palette):")
        for name, desc in PALETTE_INFO.items():
            print(f"  {name:<{max_len}}  {desc}")
        return

    if args.layout_json is None:
        parser.error("缺少布局 JSON 文件路径参数")

    if args.layout_json == "-":
        plan = json.load(sys.stdin)
    else:
        with open(args.layout_json, encoding="utf-8") as f:
            plan = json.load(f)

    out_path = args.output_path
    if out_path is None:
        if args.layout_json == "-":
            base = "output"
        else:
            base = Path(args.layout_json).stem
        fmt = plan.get("format", "drawio")
        ext = ".svg" if fmt == "svg" else ".drawio"
        out_path = f"{base}{ext}"

    render(plan, out_path)
    print(f"[DONE] 已生成: {out_path}")

    if args.open:
        _open_in_browser(out_path)
    if args.serve:
        _serve_http(out_path, args.port)


if __name__ == "__main__":
    main()