#!/usr/bin/env python3
"""draw.io 矢量图生成器 - 渲染引擎核心库"""

import argparse
import json
import sys
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from http.server import HTTPServer, SimpleHTTPRequestHandler

from .palettes import (PALETTES, DEFAULT_PALETTE, ARROW_STYLES,
                       PALETTE_INFO,
                       _hex_to_rgb, _rgb_to_hex, _desaturate, _lighten,
                       _resolve_color, _get_arrow_style, _apply_grayscale)
from .shapes import SHAPES, _render_svg_shape
from .validator import validate_plan
from .geometry import _compute_bounding_box, _get_node_by_id
from .svg_renderer import _render_svg
from .drawio_renderer import _render_drawio, repair_drawio

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
        gradient, shadow, paper_mode, presentation, auto_fit
    """
    plan = plan_dict
    nodes = plan.get("nodes", [])
    edges = plan.get("edges", [])
    auto_fit = plan.get("auto_fit", True)

    # 自动布局：先于校验执行，为缺坐标的节点补全坐标
    auto_layout = plan.get("auto_layout", False) or any(
        n.get("x") is None or n.get("y") is None for n in nodes
    )
    if auto_layout and nodes:
        from .layout import layout_plan
        direction = plan.get("layout_direction", "vertical")
        layout_kw = {"direction": direction}
        for k in ("gap", "layer_gap", "padding"):
            if k in plan:
                layout_kw[k] = plan[k]
        layout_result = layout_plan(nodes, edges, **layout_kw)
        nodes = layout_result["nodes"]
        # 将计算结果写回 plan_dict 使后续校验通过
        plan_dict["nodes"] = nodes
        if auto_fit:
            plan_dict["width"] = layout_result["width"]
            plan_dict["height"] = layout_result["height"]
        if layout_result.get("has_cycle"):
            print("[WARN]  layout: 检测到环路依赖，已自动处理", file=sys.stderr)
        # 标记回边（反馈边），渲染层使用不同样式
        back_edges = set(tuple(be) for be in layout_result.get("back_edges", []))
        for edge in edges:
            if (edge.get("from", ""), edge.get("to", "")) in back_edges:
                edge["back_edge"] = True
        # 复用预计算边路径，渲染端不再重复计算
        route_map = {(r["from"], r["to"]): r["points"]
                      for r in layout_result.get("edge_routes", [])}
        for edge in edges:
            key = (edge.get("from"), edge.get("to"))
            if key in route_map:
                edge["points"] = route_map[key]

    # 输入校验
    issues = validate_plan(plan_dict)
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
    layers = plan.get("layers", [])

    # 输出格式：文件扩展名决定优先级最高，最后判定
    fmt = plan.get("format", "drawio")
    paper_mode = plan.get("paper_mode", False)
    presentation = plan.get("presentation", False)
    if paper_mode:
        fmt = "svg"
    if presentation:
        pass  # 仅影响 gradient/shadow/arrow 等

    # 文件扩展名优先于所有 JSON 字段
    ext = Path(output_path).suffix.lower()
    if ext == ".svg":
        fmt = "svg"
    elif ext == ".drawio":
        fmt = "drawio"

    # 解析配色
    raw_palette = plan.get("palette", "academic")
    if isinstance(raw_palette, str):
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

    if fmt == "svg":
        _render_svg(width, height, title, nodes, edges, layers, colors,
                     output_path, font_family, font_size, stroke_width,
                     arrow_style, gradient, shadow, auto_fit)
    else:
        _render_drawio(width, height, title, nodes, edges, layers, colors,
                        output_path, stroke_width, arrow_style,
                        font_family, font_size, gradient, shadow, show_emoji)


def generate_svg(plan_json, output_path):
    """
    [兼容别名] 从结构化布局 JSON 生成矢量图。
    内部调用 render()，支持 dict 或 JSON 字符串输入。
    """
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
    """在默认浏览器中打开生成的 SVG/drawio 文件"""
    abs_path = Path(file_path).resolve()
    webbrowser.open(f"file://{abs_path}")


def _serve_http(output_path, port):
    """启动 HTTP 预览服务器，提供居中 HTML 包装页"""
    output_path = Path(output_path).resolve()
    ext = output_path.suffix.lower()
    svg_mode = ext == ".svg"

    # 构建居中 HTML 包装页
    if svg_mode:
        wrapper = _SVG_PREVIEW_HEAD
        with open(output_path, encoding="utf-8") as f:
            svg_content = f.read()
        wrapper += svg_content
        wrapper += "\n</body>\n</html>"
    else:
        wrapper = _DRAWIO_PREVIEW_HTML.format(name=output_path.name)

    output_dir = output_path.parent
    html_path = output_dir / "_preview.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(wrapper)

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output_dir), **kwargs)

        def log_message(self, fmt, *args):
            print(f"[HTTP] {args[0]} {args[1]} {args[2]}")

    server = HTTPServer(("0.0.0.0", port), _Handler)
    url = f"http://localhost:{port}/_preview.html"
    print(f"[SERVE] 预览地址: {url}")
    print(f"[SERVE] 按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVE] 已停止")
        server.server_close()
    finally:
        if html_path.exists():
            html_path.unlink()


def main():
    """CLI 入口：从 JSON 文件读取布局，生成 .drawio / SVG"""
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
                        help="输出文件路径 (省略时根据 JSON 内容决定)")
    parser.add_argument("--open", action="store_true",
                        help="生成后在默认浏览器中打开")
    parser.add_argument("--serve", action="store_true",
                        help="启动 HTTP 预览服务器")
    parser.add_argument("--port", type=int, default=8080,
                        help="HTTP 服务器端口号 (默认: 8080)")

    args = parser.parse_args()

    # --list-palettes: 列出配色方案后直接退出
    if args.list_palettes:
        max_len = max(len(k) for k in PALETTE_INFO)
        print("可用配色方案 (palette):")
        for name, desc in PALETTE_INFO.items():
            print(f"  {name:<{max_len}}  {desc}")
        return

    # --version 由 argparse 自动处理

    if args.layout_json is None:
        parser.error("缺少布局 JSON 文件路径参数")

    # stdin 输入
    if args.layout_json == "-":
        plan = json.load(sys.stdin)
    else:
        with open(args.layout_json, encoding="utf-8") as f:
            plan = json.load(f)

    # 自动推导输出路径
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
