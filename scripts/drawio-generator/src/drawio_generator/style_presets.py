#!/usr/bin/env python3
"""样式预设系统 — 加载 dark/colorblind-safe 等预设"""

from copy import deepcopy

from .palettes import PALETTES

BUILT_IN_PRESETS = {
    "default": deepcopy(PALETTES["academic"]),
    "dark": deepcopy({
        "node_blue":   {"fill": "#1E3A5F", "stroke": "#4DA8DA"},
        "node_green":  {"fill": "#22543d", "stroke": "#68d391"},
        "node_orange": {"fill": "#7b341e", "stroke": "#fdba74"},
        "node_yellow": {"fill": "#744210", "stroke": "#f6e05e"},
        "node_purple": {"fill": "#44337a", "stroke": "#b794f4"},
        "node_red":    {"fill": "#742a2a", "stroke": "#fc8181"},
        "node_cyan":   {"fill": "#234e52", "stroke": "#4fd1c5"},
        "bg": "#1A1A2E",
        "layer_bg": "#2d3748",
        "layer_stroke": "#4a5568",
        "title_color": "#e2e8f0",
        "text_color": "#cbd5e0",
    }),
    "colorblind-safe": deepcopy({
        "node_blue":   {"fill": "#e69f00", "stroke": "#cc7a00"},
        "node_green":  {"fill": "#56b4e9", "stroke": "#2a8fc7"},
        "node_orange": {"fill": "#009e73", "stroke": "#007a59"},
        "node_yellow": {"fill": "#f0e442", "stroke": "#d4c720"},
        "node_purple": {"fill": "#0072b2", "stroke": "#00548a"},
        "node_red":    {"fill": "#d55e00", "stroke": "#a84a00"},
        "node_cyan":   {"fill": "#cc79a7", "stroke": "#b05a8e"},
        "bg": "#FFFFFF",
        "layer_bg": "#F4F6F8",
        "layer_stroke": "#D5DCE4",
        "title_color": "#1A1A1A",
        "text_color": "#333333",
    }),
}


def load_preset(name):
    """加载内置预设，返回 palette 配置 dict，未找到时返回 None。"""
    return BUILT_IN_PRESETS.get(name)


def list_presets():
    """返回所有可用预设名列表。"""
    return list(BUILT_IN_PRESETS.keys())