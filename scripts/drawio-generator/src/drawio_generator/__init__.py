"""drawio-generator: 根据自然语言描述生成 draw.io/SVG 矢量图"""

from .render import generate_svg, render, PALETTES, DEFAULT_PALETTE, VERSION
from .layout import layout_plan  # noqa: F401

__version__ = VERSION

__all__ = ["generate_svg", "render", "layout_plan",
           "PALETTES", "DEFAULT_PALETTE"]
