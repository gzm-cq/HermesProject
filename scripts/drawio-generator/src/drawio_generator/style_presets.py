#!/usr/bin/env python3
"""样式预设系统 — 加载 dark/colorblind-safe 等预设"""

from copy import deepcopy

from .palettes import PALETTES

BUILT_IN_PRESETS = {
    # 单一真源：dark / colorblind-safe 直接复用 palettes.PALETTES，
    # 消除 style_presets 与 palettes 两处重复定义导致的漂移风险。
    "default": deepcopy(PALETTES["academic"]),
    "dark": deepcopy(PALETTES["dark"]),
    "colorblind-safe": deepcopy(PALETTES["colorblind-safe"]),
}


def load_preset(name):
    """加载内置预设，返回 palette 配置 dict，未找到时返回 None。"""
    return BUILT_IN_PRESETS.get(name)


def list_presets():
    """返回所有可用预设名列表。"""
    return list(BUILT_IN_PRESETS.keys())