"""Auto Legend: 自动生成图例

识别节点使用过的颜色和角色，生成图例 Layer/文本条目。
"""

def build_legend(plan, nodes, edges, palette, position="bottom_right", legend_pad=20, cell_w=140, cell_h=32):
    """构造图例节点（不修改原 plan）

    返回: {
        "layers": [...extra layers...],     # 图例背景块
        "nodes": [...extra text nodes...], # 图例文字
        "width": int, "height": int        # 可能的画布增量
    }
    """
    plan_w = int(plan.get("width") or 800)
    plan_h = int(plan.get("height") or 600)

    # 收集使用过的颜色（取 node.color，或默认 node_blue 兜底）
    # 同时支持 palette key（如 node_blue）和自定义 hex 颜色（如 #FF0000）
    used_colors = []
    seen = set()
    for n in nodes:
        c = n.get("color") or "node_blue"
        if c not in seen and (c in palette or c.startswith("#")):
            seen.add(c)
            used_colors.append(c)

    # 收集边使用的颜色（edge.color，若为合法 palette key 或 hex 颜色也纳入）
    for e in edges or []:
        ec = e.get("color")
        if isinstance(ec, str) and ec not in seen and (ec in palette or ec.startswith("#")):
            seen.add(ec)
            used_colors.append(ec)

    # 颜色少于 3 个时不画图例
    if len(used_colors) < 3:
        return {"layers": [], "nodes": [], "width": 0, "height": 0}

    cols = min(len(used_colors), 4)
    rows = (len(used_colors) + cols - 1) // cols
    box_w = cell_w * cols + legend_pad * 2
    box_h = cell_h * rows + legend_pad * 2 + 24  # +标题

    # 定位
    margin = 24
    if position == "top_left":
        bx, by = margin, margin
    elif position == "top_right":
        bx, by = plan_w - box_w - margin, margin
    elif position == "bottom_left":
        bx, by = margin, plan_h - box_h - margin
    else:  # bottom_right
        bx, by = plan_w - box_w - margin, plan_h - box_h - margin

    # 越界则扩展画布（返回增量，不直接改）
    dw = max(0, bx + box_w + margin - plan_w)
    dh = max(0, by + box_h + margin - plan_h)

    layer_bg = palette.get("layer_bg", "#F4F6F8")
    layer_stroke = palette.get("layer_stroke", "#D5DCE4")
    text_color = palette.get("text_color", "#333333")

    out_layers = [{
        "x": bx, "y": by, "w": box_w, "h": box_h,
        "label": None, "_legend": True,
        "_bg": layer_bg, "_stroke": layer_stroke,
    }]

    title_node = {
        "id": "__legend_title__",
        "label": "图例 (Legend)",
        "x": bx + legend_pad, "y": by + 6,
        "w": box_w - legend_pad * 2, "h": 18,
        "shape": "rect", "color": None,
        "bold": True, "font_size_override": 11,
        "_legend": True,
    }
    out_nodes = [title_node]

    for idx, color_key in enumerate(used_colors):
        col = idx % cols
        row = idx // cols
        nx = bx + legend_pad + col * cell_w
        ny = by + legend_pad + 22 + row * cell_h
        # 颜色方块（伪节点: 用 emoji 占位；SVG/drawio 里直接画矩形色块）
        palette_entry = palette.get(color_key, {})
        fill = palette_entry.get("fill", "#CCCCCC") if isinstance(palette_entry, dict) else str(palette_entry)
        label_name = _guess_color_name(color_key)
        swatch = {
            "id": f"__legend_swatch_{idx}__",
            "label": "",
            "x": nx, "y": ny + 6, "w": 18, "h": 18,
            "shape": "rect",
            "_fill_override": fill,
            "_legend": True,
        }
        text = {
            "id": f"__legend_text_{idx}__",
            "label": f"{label_name}",
            "x": nx + 26, "y": ny, "w": cell_w - 28, "h": cell_h - 2,
            "shape": "rect", "color": None,
            "text_color_override": text_color,
            "_legend": True,
        }
        out_nodes.extend([swatch, text])

    return {
        "layers": out_layers,
        "nodes": out_nodes,
        "width": dw,
        "height": dh,
    }


def _guess_color_name(key):
    mapping = {
        "node_blue": "通用/核心",
        "node_green": "服务/业务",
        "node_orange": "外部/接入",
        "node_yellow": "数据/中间结果",
        "node_purple": "模型/AI",
        "node_red": "告警/边界",
        "node_cyan": "存储/查询",
    }
    return mapping.get(key, key.replace("node_", "").replace("_", " ").title())
