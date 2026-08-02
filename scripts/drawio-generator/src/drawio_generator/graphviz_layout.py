#!/usr/bin/env python3
"""Graphviz 可选布局引擎 — 调用系统 dot 计算节点坐标"""

import subprocess
import sys

DPI = 72  # graphviz 默认 DPI


def _inches_to_px(v):
    """英寸转像素"""
    return int(round(v * DPI))


def _build_dot_source(nodes, edges, direction="vertical", **kwargs):
    """构建 DOT 源码字符串"""
    lines = ["digraph G {"]

    rankdir = "TB" if direction in ("vertical", "auto") else "LR"
    lines.append(f'  rankdir={rankdir};')

    # 关闭默认节点/边样式，避免干扰尺寸
    lines.append('  node [shape=box margin=0 fontsize=10];')
    lines.append('  edge [fontsize=9];')

    # 允许用户覆盖部分 dot 属性（如 nodesep, ranksep）
    if "nodesep" in kwargs:
        lines.append(f'  nodesep={kwargs["nodesep"]};')
    if "ranksep" in kwargs:
        lines.append(f'  ranksep={kwargs["ranksep"]};')

    # 收集节点 id
    node_id_set = set()
    for n in nodes:
        nid = n.get("id", "")
        if nid:
            node_id_set.add(nid)

    # 节点定义
    for n in nodes:
        nid = n.get("id", "")
        if not nid:
            continue
        w_in = (n.get("w", 160)) / DPI
        h_in = (n.get("h", 60)) / DPI
        label = n.get("label", nid)
        # 转义 DOT 字符串中的特殊字符
        label_escaped = label.replace('"', '\\"').replace("\n", "\\n")
        lines.append(
            f'  "{nid}" [label="{label_escaped}" '
            f'width={w_in} height={h_in} fixedsize=true];'
        )

    # 边（只添加两端节点都存在的边）
    for e in edges:
        src = e.get("from", "")
        tgt = e.get("to", "")
        if src in node_id_set and tgt in node_id_set:
            lines.append(f'  "{src}" -> "{tgt}";')

    lines.append("}")
    return "\n".join(lines)


def layout_plan_graphviz(nodes, edges, direction="vertical", **kwargs):
    """
    使用 Graphviz/dot 计算节点坐标。

    参数与 layout_plan 保持一致：
      nodes: [{id, label?, w?, h?, ...}]
      edges: [{from, to, label?, dashed?}]
      direction: "vertical" | "horizontal"

    返回:
      {
        "nodes": [...],      # 含 x, y, w, h 的节点
        "width": int,
        "height": int,
        "has_cycle": bool,
        "back_edges": [],    # Graphviz 不区分回边，留空
        "edge_routes": [],   # 由 render.py 用正交路由重新计算
      }

    异常:
      RuntimeError: dot 执行失败或不可用（dot -V）
    """

    if not nodes:
        return {
            "nodes": [],
            "width": kwargs.get("padding", 40) * 2,
            "height": kwargs.get("padding", 40) * 2,
            "has_cycle": False,
            "back_edges": [],
            "edge_routes": [],
        }

    # 构建 DOT 源码字符串并调用 dot -Tplain
    dot_src = _build_dot_source(nodes, edges, direction=direction, **kwargs)

    try:
        result = subprocess.run(
            ["dot", "-Tplain"],
            input=dot_src,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        plain_text = result.stdout
    except FileNotFoundError as exc:
        raise RuntimeError(
            "dot command not found. Install graphviz (e.g. apt install graphviz)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Graphviz layout failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Graphviz layout timed out (exceeded 10s)."
        ) from exc

    # 解析 plain 输出（逻辑不变）
    # graph scale width height
    # node name x y width height label style shape color fillcolor
    # edge tail head n x1 y1 ... xn yn style color
    lines = plain_text.strip().splitlines()
    if not lines or not lines[0].startswith("graph"):
        raise RuntimeError("Unexpected Graphviz plain output")

    graph_parts = lines[0].split()
    graph_scale = float(graph_parts[1])
    graph_w_in = float(graph_parts[2])
    graph_h_in = float(graph_parts[3])

    positions = {}  # {nid: (cx, cy, w, h)} in inches
    for line in lines[1:]:
        if line.startswith("node "):
            parts = line.split()
            nid = parts[1]
            cx = float(parts[2])
            cy = float(parts[3])
            nw = float(parts[4])
            nh = float(parts[5])
            positions[nid] = (cx, cy, nw, nh)
        elif line == "stop":
            break

    # 转换坐标：graphviz 原点在左下角，我们使用左上角
    # x_left = (cx - w/2) * DPI
    # y_top  = (graph_h - cy - h/2) * DPI
    graph_h_px = _inches_to_px(graph_h_in)

    result_nodes = []
    iso_idx = 0
    for n in nodes:
        nid = n.get("id", "")
        if nid in positions:
            cx, cy, nw, nh = positions[nid]
            x = _inches_to_px(cx - nw / 2)
            y = _inches_to_px(graph_h_in - cy - nh / 2)
            w = _inches_to_px(nw)
            h = _inches_to_px(nh)
            new_n = dict(n)
            new_n["x"] = x
            new_n["y"] = y
            new_n["w"] = w
            new_n["h"] = h
            result_nodes.append(new_n)
        else:
            # 未在 graphviz 输出中的节点（孤立节点），水平排布避免重叠
            new_n = dict(n)
            iso_idx += 1
            # 放在画布底部下方，水平排列
            iso_y = graph_h_px + 40 + (iso_idx // 5) * 100
            iso_x = 40 + (iso_idx % 5) * 200
            new_n.setdefault("x", iso_x)
            new_n.setdefault("y", iso_y)
            new_n.setdefault("w", 160)
            new_n.setdefault("h", 60)
            result_nodes.append(new_n)

    # 计算画布尺寸（包含所有节点 + padding）
    padding = kwargs.get("padding", 40)
    xs = [n["x"] for n in result_nodes]
    ys = [n["y"] for n in result_nodes]
    xes = [n["x"] + n["w"] for n in result_nodes]
    yes = [n["y"] + n["h"] for n in result_nodes]
    width = max(xes) + padding if xes else padding * 2
    height = max(yes) + padding if yes else padding * 2

    return {
        "nodes": result_nodes,
        "width": width,
        "height": height,
        "has_cycle": False,
        "back_edges": [],
        "edge_routes": [],
    }


def is_available():
    """返回 Graphviz 布局引擎是否可用（检查 dot -V）"""
    try:
        subprocess.run(
            ["dot", "-V"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
