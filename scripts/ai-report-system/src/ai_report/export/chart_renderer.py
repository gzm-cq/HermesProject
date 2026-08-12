"""
ChartRenderer — 按 chart_spec 渲染真实图表
=========================================
从源文档提取真实数据，生成 PNG 图片文件。
支持：
- architecture_diagram: 架构对比图（matplotlib）
- timeline: 五年路径规划时间线（matplotlib 柱状图）
- comparison: 对比图（matplotlib 柱状图）

遵循 Hermes Code Rules 规范
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# matplotlib 后端配置
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# ── 中文字体 ──────────────────────────────────────────────

# 三态标记：None=未初始化, str=找到的字体路径, ""=查找过但没找到
_ZH_FONT: Optional[str] = None
_ZH_FONT_INITIALIZED: bool = False

_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
]


def _find_zh_font() -> str | None:
    """查找可用的中文字体。"""
    for candidate in _CANDIDATES:
        if Path(candidate).exists():
            return candidate
    # 尝试通过 matplotlib 查找
    for f in fm.findSystemFonts():
        try:
            prop = fm.FontProperties(fname=f)
            name = prop.get_name()
            if any(cjk in name.lower() for cjk in ["cjk", "noto", "wenquanyi", "wqy",
                                                      "simhei", "simsun", "microsoft"]):
                return f
        except (ValueError, OSError, RuntimeError):
            continue
    return None


def _setup_zh() -> None:
    """配置 matplotlib 中文字体（幂等，多次调用安全）。"""
    global _ZH_FONT, _ZH_FONT_INITIALIZED
    if _ZH_FONT_INITIALIZED:
        return
    _ZH_FONT = _find_zh_font()
    _ZH_FONT_INITIALIZED = True
    if _ZH_FONT:
        font_prop = fm.FontProperties(fname=_ZH_FONT)
        plt.rcParams["font.family"] = font_prop.get_name()
        plt.rcParams["font.sans-serif"] = [font_prop.get_name()]
    else:
        plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False


# ── 颜色方案 ──────────────────────────────────────────────

_COLORS = ["#2E86AB", "#A23B72", "#F18F01", "#C73E1D", "#3B1F2B"]
_COLORS_LIGHT = ["#7FB3D8", "#C87B9D", "#F5B342", "#D97A5C", "#6F4F5E"]


# ── 辅助函数 ──────────────────────────────────────────────


def _save_and_close(fig: Any, output_path: Path, dpi: int = 200) -> None:
    """Save figure and close it cleanly."""
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)


# ── 渲染函数 ──────────────────────────────────────────────


def _render_architecture(
    data: dict[str, Any],
    output_path: Path,
    title: str = "",
) -> bool:
    """渲染三层架构对比图。

    data 格式:
        {"layers": [{"name": "...", "定位": "...", "职能": "...", "策略": "..."}]}
    """
    _setup_zh()
    layers = data.get("layers", [])
    if not layers:
        return False

    fig_height = max(4.5, len(layers) * 1.8)
    fig, ax = plt.subplots(figsize=(12, fig_height))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, len(layers) * 2 + 1)
    ax.axis("off")
    ax.set_facecolor("#FAFAFA")
    fig.patch.set_facecolor("#FAFAFA")

    if title:
        ax.set_title(title, fontsize=14, fontweight="bold", color="#333333",
                      pad=15, loc="left")

    for i, layer in enumerate(layers):
        y = len(layers) * 2 - i * 2
        color = _COLORS[i % len(_COLORS)]
        light = _COLORS_LIGHT[i % len(_COLORS_LIGHT)]

        # 层名标签
        ax.text(0.3, y, layer.get("name", ""), fontsize=13, fontweight="bold",
                va="center", color=color)

        # 定位
        ax.text(3.5, y + 0.3, layer.get("定位", ""), fontsize=10,
                va="center", color="#333333")
        # 职能
        ax.text(3.5, y - 0.3, layer.get("职能", ""), fontsize=9,
                va="center", color="#666666")

        # 策略
        ax.text(8, y, layer.get("策略", ""), fontsize=9,
                va="center", ha="center", color="#555555",
                bbox=dict(boxstyle="round,pad=0.3", facecolor=light,
                          edgecolor=color, alpha=0.3))

        # 分隔线
        ax.axhline(y - 0.8, xmin=0.02, xmax=0.98, color="#DDDDDD", linewidth=0.5)

    _save_and_close(fig, output_path)
    logger.info("  architecture_chart saved: %s", output_path)
    return True


def _render_timeline(
    data: dict[str, Any],
    output_path: Path,
    title: str = "",
) -> bool:
    """渲染五年路径时间线图。

    data 格式:
        {"phases": [
            {"year": "年份1", "label": "阶段标签", "value": 1},
            {"year": "年份2", "label": "阶段标签", "value": 2},
        ]}
    """
    _setup_zh()
    phases = data.get("phases", [])
    if not phases:
        return False

    fig_height = max(3.5, len(phases) * 0.6)
    fig, ax = plt.subplots(figsize=(10, fig_height))
    years = [p.get("year", "") for p in phases]
    labels = [p.get("label", "") for p in phases]
    values = [p.get("value", 0) for p in phases]

    bars = ax.barh(range(len(phases)), values, color=[_COLORS[i % len(_COLORS)] for i in range(len(phases))], height=0.5)
    ax.set_yticks(range(len(phases)))
    ax.set_yticklabels([f"{y} {l}" for y, l in zip(years, labels)], fontsize=10)
    ax.set_xlabel("阶段进度", fontsize=10)

    if title:
        ax.set_title(title, fontsize=14, fontweight="bold", color="#333333",
                      pad=12, loc="left")

    # 在柱子末端标值
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + 0.05, bar.get_y() + bar.get_height() / 2,
                str(v), va="center", fontsize=9)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    _save_and_close(fig, output_path)
    logger.info("  timeline chart saved: %s", output_path)
    return True


def _render_comparison(
    data: dict[str, Any],
    output_path: Path,
    title: str = "",
) -> bool:
    """渲染对比图。

    data 格式:
        {"items": [{"name": "...", "value": 100, "category": "..."}]}
    """
    _setup_zh()
    items = data.get("items", [])
    if not items:
        return False

    fig_height = max(3.5, len(items) * 0.5)
    fig, ax = plt.subplots(figsize=(10, fig_height))
    names = [it.get("label", it.get("name", "")) for it in items]
    values = [it.get("amount", it.get("value", 0)) for it in items]
    colors = [_COLORS[i % len(_COLORS)] for i in range(len(items))]

    bars = ax.barh(names, values, color=colors, height=0.5)
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                str(v), va="center", fontsize=9)

    ax.set_xlabel("投资金额（万元）", fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    if title:
        ax.set_title(title, fontsize=14, fontweight="bold", color="#333333",
                      pad=12, loc="left")

    _save_and_close(fig, output_path)
    logger.info("  comparison chart saved: %s", output_path)
    return True


# ── 去重 ──────────────────────────────────────────────────
# 模块级缓存：用于同一次 render_all_charts 调用中按内容去重。
# 注意：直接调用 render_chart（而非 render_all_charts）会累积缓存，
# 如需独立调用，应先手动调用 _reset_dedup()。

_DEDUP_CACHE: set[str] = set()
_DEDUP_LOCK = threading.Lock()


def _reset_dedup() -> None:
    """重置去重缓存。

    render_all_charts 会自动调用；直接使用 render_chart 时需手动调用。
    """
    with _DEDUP_LOCK:
        _DEDUP_CACHE.clear()


def _data_hash(data: dict[str, Any]) -> str:
    """对 chart data 内容做哈希，用于去重。"""
    with _DEDUP_LOCK:
        raw = json.dumps(data, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()


# ── 统一入口 ──────────────────────────────────────────────

_RENDERERS: dict[str, Any] = {
    "architecture_diagram": _render_architecture,
    "architecture_table": _render_architecture,
    "timeline": _render_timeline,
    "comparison": _render_comparison,
}


def render_chart(
    chart_spec: dict[str, Any] | None,
    output_dir: Path,
    chapter_index: int,
    chapter_title: str = "",
) -> Path | None:
    """根据 chart_spec 渲染图表，返回 PNG 文件路径。

    Args:
        chart_spec: StateGraph 规划的图表规格
        output_dir: 图片输出目录
        chapter_index: 章节序号
        chapter_title: 章节标题（用于图表标题和去重）

    Returns:
        PNG 文件路径，如无法渲染返回 None
    """
    if not chart_spec:
        return None

    chart_type = chart_spec.get("type", "")
    data = chart_spec.get("data")
    if not data:
        logger.debug("  skip chart '%s': no data", chart_type)
        return None

    # 空数据/数据太少不渲染
    data_points = data.get("items") or data.get("layers") or data.get("phases") or []
    if len(data_points) < 2 and chart_type in ("comparison", "timeline", "architecture_table"):
        logger.info("  skip chart '%s' for '%s': insufficient data (%d point(s))",
                     chart_type, chapter_title[:20], len(data_points))
        return None

    # 去重检查：按内容+章节位置双重去重（同数据但不同章节仍渲染）
    dhash = _data_hash(data) + f"_{chapter_index}"
    if dhash in _DEDUP_CACHE:
        logger.info("  skip duplicate chart '%s' for '%s' (same data already rendered)",
                     chart_type, chapter_title[:20])
        return None
    _DEDUP_CACHE.add(dhash)

    renderer = _RENDERERS.get(chart_type)
    if not renderer:
        logger.debug("  skip chart '%s': unknown type", chart_type)
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"chart_{chapter_index}_{chart_type}.png"
    try:
        success = renderer(data, output_path, title=chapter_title)
        if success:
            return output_path
    except (ValueError, OSError, TypeError) as e:
        logger.warning("  render chart failed: %s", e)
    return None


def render_all_charts(
    chapter_prompts: list[dict[str, Any]],
    output_dir: Path,
) -> list[tuple[int, Path]]:
    """为所有章节渲染图表。

    Returns:
        [(chapter_index, image_path), ...]
    """
    _reset_dedup()
    chart_dir = output_dir / "charts"
    results: list[tuple[int, Path]] = []
    for i, cp in enumerate(chapter_prompts):
        spec = cp.get("chart_spec")
        title = cp.get("title", "")
        path = render_chart(spec, chart_dir, i, chapter_title=title)
        if path is not None:
            results.append((i, path))
    logger.info("charts rendered: %d/%d", len(results), len(chapter_prompts))
    return results
