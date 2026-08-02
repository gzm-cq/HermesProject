#!/usr/bin/env python3
"""
给已有 .drawio 文件换配色。

读取 .drawio 文件，解析所有 mxCell 的 fillColor/strokeColor，
替换为指定 palette 的对应色，保持布局和边路径不变。

用法:
  python scripts/restyle.py input.drawio --palette dark -o output.drawio

palette 支持: academic, business, tech, warm, dark, colorblind-safe
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# 复用项目内置配色方案
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from drawio_generator.palettes import PALETTES  # noqa: E402

# 接受 palette 映射的节点 key 范围
_NODE_KEYS = [
    "node_blue", "node_green", "node_orange", "node_yellow",
    "node_purple", "node_red", "node_cyan",
]

# 颜色属性白名单（style 中的 key）
_COLOR_ATTRS = {"fillColor", "strokeColor", "gradientColor"}


def _build_color_index(source_palette: dict) -> dict[str, str]:
    """
    从源 palette 构建 hex → node_key 索引。
    返回 { "fill:#dae8fc": "node_blue", "stroke:#6c8ebf": "node_blue", ... }
    """
    index: dict[str, str] = {}
    for key in _NODE_KEYS:
        entry = source_palette.get(key)
        if entry is None:
            continue
        for role in ("fill", "stroke"):
            color = entry.get(role, "").lower()
            if color:
                index[f"{role}:{color}"] = key
    # 也索引通用背景色
    for role, color_key in [("fill", "bg"), ("fill", "layer_bg"),
                              ("stroke", "layer_stroke")]:
        color = source_palette.get(color_key, "").lower()
        if color:
            index[f"{role}:{color}"] = color_key
    return index


def _normalize_hex(color: str) -> str:
    """规范化十六进制颜色：小写，#RRGGBB 格式。"""
    c = color.strip().lower()
    if c.startswith("#"):
        c = c[1:]
    # 扩展 #RGB → #RRGGBB
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    if len(c) == 6:
        return f"#{c}"
    return color.strip()  # 非标准格式保留原样


def _parse_style(style: str) -> dict[str, str]:
    """解析 style 字符串为 key→value 字典。"""
    parts = {}
    for item in style.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            k, v = item.split("=", 1)
            parts[k.strip()] = v.strip()
        else:
            parts[item] = ""
    return parts


def _build_style(parts: dict[str, str]) -> str:
    """将 key→value 字典重建为 style 字符串。"""
    items = []
    for k, v in parts.items():
        if v == "":
            items.append(k)
        else:
            items.append(f"{k}={v}")
    return ";".join(items) + ";"


def _build_target_map(target_palette: dict) -> dict[str, str]:
    """
    构建目标 palette 的替换映射。
    返回 { "fill:#dae8fc": "#1E3A5F", "stroke:#6c8ebf": "#4DA8DA", ... }
    其中 key 是源 palette 的 (role:color)，value 是目标 palette 的对应颜色。
    """
    tmap: dict[str, str] = {}
    # 使用 academic 作为默认源 palette
    source = PALETTES.get("academic", {})
    for key in _NODE_KEYS:
        src_entry = source.get(key)
        tgt_entry = target_palette.get(key)
        if src_entry is None or tgt_entry is None:
            continue
        for role in ("fill", "stroke"):
            src_color = _normalize_hex(src_entry.get(role, ""))
            tgt_color = _normalize_hex(tgt_entry.get(role, ""))
            if src_color and tgt_color:
                tmap[f"{role}:{src_color}"] = tgt_color
    # 通用背景色
    for role, color_key in [("fill", "bg"), ("fill", "layer_bg"),
                              ("stroke", "layer_stroke")]:
        src_color = _normalize_hex(source.get(color_key, ""))
        tgt_color = _normalize_hex(target_palette.get(color_key, ""))
        if src_color and tgt_color:
            tmap[f"{role}:{src_color}"] = tgt_color
    return tmap


def _resolve_fallback_remap(source_palette_name: str, target_palette: dict) -> dict[str, str]:
    """
    当源 palette 不是 academic 时，直接从源 palette 到目标 palette 建立映射。
    """
    source = PALETTES.get(source_palette_name, PALETTES["academic"])
    tmap: dict[str, str] = {}
    for key in _NODE_KEYS:
        src_entry = source.get(key)
        tgt_entry = target_palette.get(key)
        if src_entry is None or tgt_entry is None:
            continue
        for role in ("fill", "stroke"):
            src_color = _normalize_hex(src_entry.get(role, ""))
            tgt_color = _normalize_hex(tgt_entry.get(role, ""))
            if src_color and tgt_color:
                tmap[f"{role}:{src_color}"] = tgt_color
    for role, color_key in [("fill", "bg"), ("fill", "layer_bg"),
                              ("stroke", "layer_stroke")]:
        src_color = _normalize_hex(source.get(color_key, ""))
        tgt_color = _normalize_hex(target_palette.get(color_key, ""))
        if src_color and tgt_color:
            tmap[f"{role}:{src_color}"] = tgt_color
    return tmap


def _detect_source_palette(root: ET.Element) -> str | None:
    """
    从已有的 mxCell 颜色中反向检测最可能的源 palette。
    返回 palette 名称，或 None（无法检测）。
    """
    colors_found: dict[str, int] = {}
    for cell in root.iter("mxCell"):
        style = cell.get("style", "")
        if not style:
            continue
        parts = _parse_style(style)
        for attr in _COLOR_ATTRS:
            val = parts.get(attr, "")
            if val and val.lower() != "none":
                colors_found[_normalize_hex(val)] = \
                    colors_found.get(_normalize_hex(val), 0) + 1

    if not colors_found:
        return None

    # 对每个 palette 评分：匹配到的颜色越多分数越高
    scores: dict[str, int] = {}
    for pname, palette in PALETTES.items():
        score = 0
        for key in _NODE_KEYS:
            entry = palette.get(key)
            if entry is None:
                continue
            for role in ("fill", "stroke"):
                pcolor = _normalize_hex(entry.get(role, ""))
                if pcolor and pcolor in colors_found:
                    score += colors_found[pcolor]
        # 加背景色
        for color_key in ("bg", "layer_bg", "layer_stroke"):
            pcolor = _normalize_hex(palette.get(color_key, ""))
            if pcolor and pcolor in colors_found:
                score += colors_found[pcolor]
        scores[pname] = score

    if not scores:
        return None
    best = max(scores.keys(), key=lambda k: scores[k])
    return best if scores[best] > 0 else None


def restyle_drawio(input_path: str, palette_name: str, output_path: str) -> int:
    """
    读取 .drawio 文件，替换颜色为指定 palette。
    返回被替换的 mxCell 数量。
    """
    target_palette = PALETTES.get(palette_name)
    if target_palette is None:
        print(f"错误: 未知 palette '{palette_name}'，可用: {', '.join(PALETTES)}",
              file=sys.stderr)
        return -1

    # 解析 XML
    try:
        tree = ET.parse(input_path)
    except ET.ParseError as e:
        print(f"错误: XML 解析失败 — {e}", file=sys.stderr)
        return -1
    except FileNotFoundError:
        print(f"错误: 文件不存在 — {input_path}", file=sys.stderr)
        return -1

    root = tree.getroot()

    # 检测源 palette 并建立颜色映射
    source_palette_name = _detect_source_palette(root)
    if source_palette_name:
        color_map = _resolve_fallback_remap(source_palette_name, target_palette)
    else:
        color_map = _build_target_map(target_palette)

    if not color_map:
        print("警告: 未找到可匹配的颜色映射，文件保持不变。", file=sys.stderr)
        return 0

    replaced = 0

    # 1. 更新 mxGraphModel 的 background 属性
    for model in root.iter("mxGraphModel"):
        bg = model.get("background", "")
        if bg:
            normalized = _normalize_hex(bg)
            key = f"fill:{normalized}"
            new_bg = color_map.get(key)
            if new_bg:
                model.set("background", new_bg.upper())
                replaced += 1

    # 2. 更新所有 mxCell 的 style 颜色
    for cell in root.iter("mxCell"):
        style = cell.get("style", "")
        if not style:
            continue
        parts = _parse_style(style)
        changed = False
        for attr in _COLOR_ATTRS:
            old_val = parts.get(attr)
            if old_val is None:
                continue
            old_val_stripped = old_val.strip()
            if old_val_stripped.lower() == "none":
                continue
            normalized = _normalize_hex(old_val_stripped)
            # 属性名 → role 映射: fillColor→fill, strokeColor→stroke, gradientColor→fill
            role = "fill" if attr in ("fillColor", "gradientColor") else "stroke"
            key = f"{role}:{normalized}"
            new_val = color_map.get(key)
            if new_val and new_val.upper() != old_val_stripped.upper():
                parts[attr] = new_val.upper()
                changed = True
        if changed:
            cell.set("style", _build_style(parts))
            replaced += 1

    # 写入输出
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return replaced


def main():
    parser = argparse.ArgumentParser(
        description="给已有 .drawio 文件换配色",
    )
    parser.add_argument("input", help="输入 .drawio 文件路径")
    parser.add_argument("--palette", "-p", required=True,
                        choices=[k for k in PALETTES if k not in (
                            "minimal", "paper-wireframe", "paper-grayscale")],
                        help="目标配色方案")
    parser.add_argument("-o", "--output", required=True,
                        help="输出 .drawio 文件路径")
    args = parser.parse_args()

    count = restyle_drawio(args.input, args.palette, args.output)
    if count < 0:
        sys.exit(1)
    print(f"已完成: 替换了 {count} 个 mxCell 的颜色")
    print(f"  输入: {args.input}")
    print(f"  输出: {args.output}")


if __name__ == "__main__":
    main()