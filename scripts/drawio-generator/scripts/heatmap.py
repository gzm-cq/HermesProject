#!/usr/bin/env python3
"""
heatmap.py — 给 .drawio 节点按数据值着色

功能：读入 JSON 格式的指标数据（node_id → value），对 .drawio 中对应节点按值梯度着色。

用法:
  python scripts/heatmap.py diagram.drawio --data metrics.json -o heatmap.drawio
  python scripts/heatmap.py diagram.drawio --data metrics.json -o heatmap.drawio --palette blue
  python scripts/heatmap.py diagram.drawio --data metrics.json -o heatmap.drawio --match-by value

metrics.json 格式: {"node1": 0.1, "node2": 0.8, ...}

着色逻辑:
- 值最小 → 绿色 (#d5e8d4)
- 值最大 → 红色 (#f8cecc)
- 中间值 → 黄色梯度 (#fff2cc)
- 可加 --palette 参数选择配色方案
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET


# ===== 调色板定义 =====

PALETTES = {
    "default": {
        "name": "Default (Green → Yellow → Red)",
        "low": "#d5e8d4",    # 绿色
        "mid": "#fff2cc",    # 黄色
        "high": "#f8cecc",   # 红色
    },
    "red-green": {
        "name": "Reversed (Red → Yellow → Green)",
        "low": "#f8cecc",    # 红色
        "mid": "#fff2cc",    # 黄色
        "high": "#d5e8d4",   # 绿色
    },
    "blue": {
        "name": "Blue gradient",
        "low": "#dae8fc",    # 浅蓝
        "mid": "#b0d4f1",    # 中蓝
        "high": "#6c8ebf",   # 深蓝
    },
    "purple": {
        "name": "Purple gradient",
        "low": "#e1d5e7",    # 浅紫
        "mid": "#b39ddb",    # 中紫
        "high": "#7e57c2",   # 深紫
    },
    "gray": {
        "name": "Gray scale",
        "low": "#f5f5f5",    # 浅灰
        "mid": "#cccccc",    # 中灰
        "high": "#666666",   # 深灰
    },
    "heat": {
        "name": "Heat map (Black → Red → Yellow → White)",
        "low": "#000000",    # 黑
        "mid": "#ff6600",    # 橙
        "high": "#ffff00",    # 黄
    },
}


def hex_to_rgb(hex_color):
    """将 #RRGGBB 转为 (r, g, b) 整数元组"""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(r, g, b):
    """将 (r, g, b) 转为 #RRGGBB 字符串"""
    return f"#{r:02x}{g:02x}{b:02x}"


def lerp_color(c1, c2, t):
    """线性插值两个颜色，t 在 [0, 1] 范围"""
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return rgb_to_hex(r, g, b)


def value_to_color(value, vmin, vmax, palette):
    """
    将值映射到颜色。
    使用两段式插值：
      t ∈ [0, 0.5] → low → mid
      t ∈ [0.5, 1] → mid → high
    """
    if vmax == vmin:
        return palette["mid"]

    t = (value - vmin) / (vmax - vmin)  # 归一化到 [0, 1]

    if t <= 0.5:
        # 低段：low → mid
        return lerp_color(palette["low"], palette["mid"], t * 2)
    else:
        # 高段：mid → high
        return lerp_color(palette["mid"], palette["high"], (t - 0.5) * 2)


def parse_style(style_str):
    """将 style 字符串解析为字典"""
    result = {}
    for part in style_str.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
            result[key] = val
        else:
            result[part] = None
    return result


def build_style(style_dict):
    """将字典重新构建为 style 字符串"""
    parts = []
    for key, val in style_dict.items():
        if val is None:
            parts.append(key)
        else:
            parts.append(f"{key}={val}")
    return ";".join(parts) + ";"


def apply_heatmap(input_path, metrics, output_path, palette_name, match_by):
    """
    主函数：读取 .drawio，应用热力图着色，写出结果。
    """
    palette = PALETTES.get(palette_name)
    if palette is None:
        available = ", ".join(sorted(PALETTES.keys()))
        print(f"Error: Unknown palette '{palette_name}'. Available: {available}", file=sys.stderr)
        sys.exit(1)

    # 解析输入文件
    try:
        tree = ET.parse(input_path)
    except ET.ParseError as e:
        print(f"Error: XML parse error in '{input_path}': {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: File not found: '{input_path}'", file=sys.stderr)
        sys.exit(1)

    root = tree.getroot()

    if not metrics:
        print("Warning: metrics data is empty, no coloring applied.", file=sys.stderr)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        print(f"Generated: {output_path}")
        return

    # 计算值的范围
    values = list(metrics.values())
    vmin = min(values)
    vmax = max(values)

    # 收集所有 mxCell
    cells_found = []
    cells_skipped = []

    # 遍历所有 mxCell 元素（支持嵌套结构）
    for cell in root.iter("mxCell"):
        cid = cell.get("id", "")
        cvalue = cell.get("value", "")

        # 跳过根节点 (id=0, id=1) 和 edge 节点
        if cid in ("0", "1"):
            continue
        if cell.get("edge") == "1":
            continue

        # 尝试匹配
        matched_value = None
        match_key = None

        if match_by in ("auto", "id"):
            if cid in metrics:
                matched_value = metrics[cid]
                match_key = f"id={cid}"

        if match_by in ("auto", "value") and matched_value is None:
            # 从 value 属性中提取纯文本（去掉 HTML 标签）
            text = re.sub(r"<[^>]+>", "", cvalue).strip()
            if text in metrics:
                matched_value = metrics[text]
                match_key = f"value={text}"

        if matched_value is not None:
            color = value_to_color(matched_value, vmin, vmax, palette)
            # 修改 style 中的 fillColor
            style_str = cell.get("style", "")
            style_dict = parse_style(style_str)
            style_dict["fillColor"] = color
            # 保持 fillColor 不带透明度
            cell.set("style", build_style(style_dict))
            cells_found.append((cid, cvalue, matched_value, color))
        else:
            cells_skipped.append(cid)

    # 输出统计信息
    print(f"Processed: {len(cells_found)} cells colored, {len(cells_skipped)} cells unmatched")
    if cells_found:
        print(f"  Value range: [{vmin:.4f}, {vmax:.4f}]")
        print(f"  Palette: {palette['name']}")
        for cid, val_str, mval, color in cells_found[:10]:
            text = re.sub(r"<[^>]+>", "", val_str).strip()[:30]
            print(f"    [{cid}] '{text}' = {mval:.4f} → {color}")
        if len(cells_found) > 10:
            print(f"    ... and {len(cells_found) - 10} more")

    # 写出结果
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"Generated: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="给 .drawio 节点按数据值着色（热力图）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", nargs="?", help="输入 .drawio 文件路径")
    parser.add_argument("--data", "-d", help="metrics JSON 文件路径 (node_id → value)")
    parser.add_argument("--output", "-o", help="输出 .drawio 文件路径")
    parser.add_argument(
        "--palette", "-p",
        default="default",
        choices=sorted(PALETTES.keys()),
        help=f"配色方案 (默认: default). 可选: {', '.join(sorted(PALETTES.keys()))}",
    )
    parser.add_argument(
        "--match-by", "-m",
        default="auto",
        choices=["auto", "id", "value"],
        help="匹配方式: auto (先 id 后 value), id, value (默认: auto)",
    )
    parser.add_argument("--list-palettes", action="store_true", help="列出所有可用配色方案")

    args = parser.parse_args()

    if args.list_palettes:
        print("Available palettes:")
        for name, p in sorted(PALETTES.items()):
            print(f"  {name:15s}  {p['low']} → {p['mid']} → {p['high']}  ({p['name']})")
        return

    # 验证必需参数
    if not args.input:
        parser.error("the following arguments are required: input")
    if not args.data:
        parser.error("the following arguments are required: --data/-d")
    if not args.output:
        parser.error("the following arguments are required: --output/-o")

    # 读取 metrics
    try:
        with open(args.data, "r", encoding="utf-8") as f:
            metrics = json.load(f)
    except FileNotFoundError:
        print(f"Error: metrics file not found: '{args.data}'", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in '{args.data}': {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(metrics, dict):
        print(f"Error: metrics must be a JSON object (dict), got {type(metrics).__name__}", file=sys.stderr)
        sys.exit(1)

    apply_heatmap(args.input, metrics, args.output, args.palette, args.match_by)


if __name__ == "__main__":
    main()