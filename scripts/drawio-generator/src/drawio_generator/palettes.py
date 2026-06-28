#!/usr/bin/env python3
"""配色方案、箭头样式和颜色工具函数"""

# ===== 内置配色方案 =====
PALETTES = {
    # 学术论文 — Nature/PNAS 风格，低饱和度、优雅 muted、色盲友好
    "academic": {
        "node_blue":   {"fill": "#EEF2F8", "stroke": "#4A6FA5"},
        "node_green":  {"fill": "#EAF3EE", "stroke": "#4A8C6A"},
        "node_orange": {"fill": "#F6F0E4", "stroke": "#B57A4A"},
        "node_yellow": {"fill": "#F6F2DC", "stroke": "#9E904A"},
        "node_purple": {"fill": "#F0ECF4", "stroke": "#7A5A9E"},
        "node_red":    {"fill": "#F4E8E8", "stroke": "#A05050"},
        "node_cyan":   {"fill": "#E8F2F2", "stroke": "#4A7A7A"},
        "bg": "#FFFFFF",
        "layer_bg": "#F7F8FA",
        "layer_stroke": "#E0E2E6",
        "title_color": "#1A1A1A",
        "text_color": "#333333",
    },
    # 商务汇报 — Google Material 3 升级版
    "business": {
        "node_blue":   {"fill": "#E1F0FF", "stroke": "#1976D2"},
        "node_green":  {"fill": "#E6F7EC", "stroke": "#388E3C"},
        "node_orange": {"fill": "#FFF4E5", "stroke": "#F57C00"},
        "node_yellow": {"fill": "#FFFDE7", "stroke": "#FBC02D"},
        "node_purple": {"fill": "#F3E5F5", "stroke": "#7B1FA2"},
        "node_red":    {"fill": "#FFEBEE", "stroke": "#D32F2F"},
        "node_cyan":   {"fill": "#E0F7FA", "stroke": "#0097A7"},
        "bg": "#FFFFFF",
        "layer_bg": "#F4F6F8",
        "layer_stroke": "#D5DCE4",
        "title_color": "#1A1A1A",
        "text_color": "#333333",
    },
    # 极简线框 — Figma 原型风格
    "minimal": {
        "node_blue":   {"fill": "#F2F3F5", "stroke": "#6B7280"},
        "node_green":  {"fill": "#F2F3F5", "stroke": "#6B7280"},
        "node_orange": {"fill": "#F6F7F9", "stroke": "#6B7280"},
        "node_yellow": {"fill": "#F6F7F9", "stroke": "#6B7280"},
        "node_purple": {"fill": "#F2F3F5", "stroke": "#6B7280"},
        "node_red":    {"fill": "#F6F7F9", "stroke": "#6B7280"},
        "node_cyan":   {"fill": "#F2F3F5", "stroke": "#6B7280"},
        "bg": "#FFFFFF",
        "layer_bg": "#F8F9FB",
        "layer_stroke": "#E5E7EB",
        "title_color": "#000000",
        "text_color": "#374151",
    },
    # 科技公司 — Vercel/Linear 风格
    "tech": {
        "node_blue":   {"fill": "#EFF6FF", "stroke": "#3B82F6"},
        "node_green":  {"fill": "#F0FDF4", "stroke": "#22C55E"},
        "node_orange": {"fill": "#FFF7ED", "stroke": "#F97316"},
        "node_yellow": {"fill": "#FEFCE8", "stroke": "#EAB308"},
        "node_purple": {"fill": "#FAF5FF", "stroke": "#A855F7"},
        "node_red":    {"fill": "#FEF2F2", "stroke": "#EF4444"},
        "node_cyan":   {"fill": "#ECFEFF", "stroke": "#06B6D4"},
        "bg": "#FFFFFF",
        "layer_bg": "#F4F6FA",
        "layer_stroke": "#D1D5DB",
        "title_color": "#111827",
        "text_color": "#374151",
    },
    # 温暖大地 — 咖啡/陶土色调
    "warm": {
        "node_blue":   {"fill": "#F0ECE3", "stroke": "#8B7355"},
        "node_green":  {"fill": "#EDF2E0", "stroke": "#6B8F3A"},
        "node_orange": {"fill": "#F5E6DA", "stroke": "#C0703E"},
        "node_yellow": {"fill": "#F5F0D0", "stroke": "#B89B30"},
        "node_purple": {"fill": "#F0EAF0", "stroke": "#8B6B8B"},
        "node_red":    {"fill": "#F2E4E4", "stroke": "#A05555"},
        "node_cyan":   {"fill": "#E6EEEE", "stroke": "#5A8A8A"},
        "bg": "#FEFCF8",
        "layer_bg": "#F5F1EA",
        "layer_stroke": "#D4CEC4",
        "title_color": "#3C2F1F",
        "text_color": "#5C4F3F",
    },
    # 极简线框 — 论文黑白打印场景
    "paper-wireframe": {
        "node_blue":   {"fill": "#F5F5F7", "stroke": "#5C5C5C"},
        "node_green":  {"fill": "#F5F5F7", "stroke": "#5C5C5C"},
        "node_orange": {"fill": "#F8F8FA", "stroke": "#5C5C5C"},
        "node_yellow": {"fill": "#F8F8FA", "stroke": "#5C5C5C"},
        "node_purple": {"fill": "#F5F5F7", "stroke": "#5C5C5C"},
        "node_red":    {"fill": "#F8F8FA", "stroke": "#5C5C5C"},
        "node_cyan":   {"fill": "#F5F5F7", "stroke": "#5C5C5C"},
        "bg": "#FFFFFF",
        "layer_bg": "#FAFAFB",
        "layer_stroke": "#D0D0D0",
        "title_color": "#1A1A1A",
        "text_color": "#333333",
    },
    # 全灰度 — 论文黑白印刷极致优化
    "paper-grayscale": {
        "node_blue":   {"fill": "#F0F0F0", "stroke": "#333333"},
        "node_green":  {"fill": "#F0F0F0", "stroke": "#333333"},
        "node_orange": {"fill": "#E8E8E8", "stroke": "#333333"},
        "node_yellow": {"fill": "#E8E8E8", "stroke": "#333333"},
        "node_purple": {"fill": "#F0F0F0", "stroke": "#333333"},
        "node_red":    {"fill": "#E8E8E8", "stroke": "#333333"},
        "node_cyan":   {"fill": "#F0F0F0", "stroke": "#333333"},
        "bg": "#FFFFFF",
        "layer_bg": "#F5F5F5",
        "layer_stroke": "#CCCCCC",
        "title_color": "#000000",
        "text_color": "#222222",
    },
}
DEFAULT_PALETTE = PALETTES["academic"]


# ===== 配色方案描述 =====
PALETTE_INFO = {
    "academic": "学术论文 — Nature/PNAS 风格，低饱和度、色盲友好",
    "business": "商务汇报 — Google Material 3 风格",
    "minimal": "极简线框 — Figma 原型风格，节点统一灰色",
    "tech": "科技公司 — Vercel/Linear 风格，高饱和色彩",
    "warm": "温暖大地 — 咖啡/陶土暖色系",
    "paper-wireframe": "极简线框 — 论文黑白打印场景",
    "paper-grayscale": "全灰度 — 论文黑白印刷极致优化",
}


# ===== 箭头样式表 =====
ARROW_STYLES = {
    "classic": {
        "drawio": "classic",
        "svg_path": "M 0 0 L 10 5 L 0 10 z",
        "svg_fill": "#555555",
    },
    "open": {
        "drawio": "openThin",
        "svg_path": "M 0 0 L 10 5 L 0 10",
        "svg_fill": "none",
        "svg_stroke": "#555555",
    },
    "diamond": {
        "drawio": "diamondThin",
        "svg_path": "M 0 5 L 5 0 L 10 5 L 5 10 z",
        "svg_fill": "#555555",
    },
    "circle": {
        "drawio": "ovalThin",
        "svg_path": "M 5 0 A 5 5 0 1 1 4.99 0",
        "svg_fill": "#555555",
    },
    "thick": {
        "drawio": "blockThin",
        "svg_path": "M 0 -3 L 10 0 L 10 6 L 0 9 z",
        "svg_fill": "#555555",
    },
    "none": {
        "drawio": "none",
        "svg_path": "",
        "svg_fill": "none",
    },
}


# ===== 颜色工具函数 =====
def _hex_to_rgb(h):
    """#RRGGBB 或 #RGB → (R, G, B)"""
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (128, 128, 128)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r, g, b):
    """(R,G,B) → #RRGGBB"""
    return f"#{r:02X}{g:02X}{b:02X}"


def _desaturate(h):
    """将十六进制颜色转为灰度 (luminance)"""
    r, g, b = _hex_to_rgb(h)
    gray = int(0.299 * r + 0.587 * g + 0.114 * b)
    return _rgb_to_hex(gray, gray, gray)


def _lighten(h, factor=0.3):
    """将颜色变亮 factor（0~1），用于渐变浅端"""
    r, g, b = _hex_to_rgb(h)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return _rgb_to_hex(r, g, b)


def _resolve_color(colors, node):
    """从配色中取节点颜色，含兜底 fallback"""
    key = node.get("color", "node_blue")
    nc = colors.get(key)
    if nc is None:
        nc = PALETTES["academic"].get(key, {"fill": "#BBDEFB", "stroke": "#64B5F6"})
    return nc


def _get_arrow_style(edge, global_style="classic"):
    """获取边的箭头样式，优先使用 edge 级别配置"""
    name = edge.get("arrow_style") or global_style
    return ARROW_STYLES.get(name, ARROW_STYLES["classic"])


def _apply_grayscale(colors):
    """将配色中所有 fill/stroke 转为灰度"""
    out = {}
    for k, v in colors.items():
        if isinstance(v, dict) and "fill" in v and "stroke" in v:
            out[k] = {"fill": _desaturate(v["fill"]), "stroke": _desaturate(v["stroke"])}
        elif isinstance(v, str):
            out[k] = _desaturate(v)
        else:
            out[k] = v
    return out
