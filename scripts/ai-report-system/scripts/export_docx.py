#!/usr/bin/env python3
"""export_docx.py — Markdown 转精美 DOCX 通用导出工具

零路径依赖，任意位置可用。支持配图插入、商汤信息图生成、VLM 审核、封面目录。

用法:
    # 最简：md → docx
    python3 export_docx.py input.md -o output.docx

    # 带配图（按标题关键字匹配 charts/ 目录下的 PNG）
    python3 export_docx.py input.md -o output.docx --charts ./charts/

    # 配置化配图映射（JSON 文件）
    python3 export_docx.py input.md -o output.docx --charts ./charts/ --chart-map chart_map.json

    # 自动生成缺失图表（调用 sn-image-base）
    python3 export_docx.py input.md -o output.docx --charts ./charts/ --chart-map chart_map.json --generate

    # 生成 + VLM 审核
    python3 export_docx.py input.md -o output.docx --charts ./charts/ --generate --review

    # 封面 + 目录
    python3 export_docx.py input.md -o output.docx --title "报告标题" --toc --subtitle "2026年度"

chart_map.json 格式:
    {
      "组织架构": "组织架构.png",
      "数据仓库": "数仓架构.png",
      "路线图": "路线图.png"
    }
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("export_docx")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from ai_report.export.docx_exporter import export_to_docx

DEFAULT_CHART_MAP: dict[str, str] = {
    "组织架构": "组织架构.png",
    "数据仓库": "数仓架构.png",
    "路线图": "路线图.png",
    "实施路线": "路线图.png",
    "依赖关系": "依赖关系.png",
    "对比": "对比图.png",
    "架构": "架构图.png",
}

DATA_TYPE_MAP: dict[str, tuple[str, list[str]]] = {
    "architecture": ("structural-breakdown", ["multi-scale", "deconstruction"]),
    "datawarehouse": ("hierarchical-layers", ["axial-expansion", "deconstruction"]),
    "timeline": ("linear-progression", ["winding-roadmap", "step-staircase"]),
    "roadmap": ("linear-progression", ["winding-roadmap", "step-staircase"]),
    "dependency": ("hub-spoke", ["jigsaw", "multi-focal", "venn-diagram"]),
    "comparison": ("binary-comparison", ["four-quadrant-grid", "conflict-contrast"]),
    "org": ("hierarchical-layers", ["axial-expansion", "deconstruction"]),
}

DEFAULT_LAYOUT = "hub-spoke"
DEFAULT_STYLE = "technical-schematic"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_IMAGE_SIZE = "2k"


def _find_sn_agent_runner() -> Path | None:
    candidates = [
        Path("/root/.hermes/skills/sensenova/sn-image-base/scripts/sn_agent_runner.py"),
        Path.home() / ".hermes" / "skills" / "sensenova" / "sn-image-base" / "scripts" / "sn_agent_runner.py",
        PROJECT_DIR / ".hermes" / "skills" / "sensenova" / "sn-image-base" / "scripts" / "sn_agent_runner.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _find_sn_skill_dir() -> Path | None:
    for base in [
        Path("/root/.hermes/skills/sensenova"),
        Path.home() / ".hermes" / "skills" / "sensenova",
        PROJECT_DIR / ".hermes" / "skills" / "sensenova",
    ]:
        if (base / "sn-infographic" / "SKILL.md").exists():
            return base / "sn-infographic"
    return None


SN_AGENT_RUNNER = _find_sn_agent_runner()


def load_chart_map(chart_map_path: Path | None) -> dict[str, str]:
    if chart_map_path and chart_map_path.exists():
        return json.loads(chart_map_path.read_text(encoding="utf-8"))
    return DEFAULT_CHART_MAP


def _parse_chapter_indices(md_text: str) -> list[tuple[int, str]]:
    """解析 md 文本中所有 H1/H2 标题，返回 [(chapter_idx, title), ...]。

    章节计数逻辑必须与 docx_exporter._process_markdown_line 一致：
    - 任何以 # 开头的行都视为标题（不强制要求 # 后有空格）
    - level = len(stripped) - len(stripped.lstrip('#'))
    - 仅 level <= 2 时计数
    - 去除首尾 #（Bug12 修复同步）

    注意：docx_exporter._process_markdown_line 在流式渲染时同样维护章节计数，
    本函数用于一次性预扫描（build_chart_images / _build_title_index_map）。
    如果修改章节计数逻辑（如新增 H3 计数、忽略特定前缀），必须同步修改
    docx_exporter._process_markdown_line 中 "# 标题行" 分支，否则会导致
    chart_images 的 chapter_idx 与渲染时实际计数不一致（Bug18 类问题）。
    """
    lines = md_text.split("\n")
    chapters: list[tuple[int, str]] = []
    chapter_idx = 0
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        level = min(len(stripped) - len(stripped.lstrip("#")), 9)
        if level > 2:
            continue
        chapter_idx += 1
        title = stripped.lstrip("#").rstrip("#").strip()
        chapters.append((chapter_idx, title))
    return chapters


def build_chart_images(
    md_path: Path,
    charts_dir: Path,
    chart_map: dict[str, str],
    *,
    md_text: str | None = None,
) -> list[tuple[int, Path]]:
    if md_text is None:
        md_text = md_path.read_text(encoding="utf-8")
    result: list[tuple[int, Path]] = []
    matched_keys: set[str] = set()

    for chapter_idx, title in _parse_chapter_indices(md_text):
        for key, fname in chart_map.items():
            if key in matched_keys:
                continue
            if key in title:
                img_path = charts_dir / fname
                if img_path.exists():
                    result.append((chapter_idx, img_path))
                    matched_keys.add(key)
                    logger.info("  ✅ [%d] \"%s\" → %s", chapter_idx, title, fname)
                    break
    return result


# ── Mermaid 缓存 ──────────────────────────────────────────
# 缓存文件（.mermaid_cache.json）记录 {code_hash: filename} 映射。
# 下次运行时若 hash 匹配且图片文件仍存在，则直接复用，跳过调用 sn-image-generate。
_MERMAID_CACHE_FILE = ".mermaid_cache.json"


def _load_mermaid_cache(cache_path: Path) -> dict[str, str]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_mermaid_cache(cache_path: Path, cache: dict[str, str]) -> None:
    try:
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("无法写入 Mermaid 缓存: %s", e)


def _mermaid_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _detect_mermaid_type(mermaid_code: str) -> str:
    """从 mermaid 代码推断图表类型，用于选择布局和风格。"""
    code = mermaid_code.strip()
    first_line = code.split("\n")[0].strip().lower() if code else ""

    type_map = {
        "graph": "flowchart",
        "flowchart": "flowchart",
        "sequencediagram": "sequence",
        "classdiagram": "class",
        "statediagram": "state",
        "erdiagram": "er",
        "pie": "pie",
        "gantt": "gantt",
        "journey": "journey",
        "mindmap": "mindmap",
        "timeline": "timeline",
    }
    for key, value in type_map.items():
        if first_line.startswith(key):
            return value
    return "diagram"


def _extract_mermaid_blocks(md_text: str) -> list[dict[str, Any]]:
    """提取 md 中的所有 mermaid 代码块，返回列表。

    每项: {index, code, type, start_line, end_line, closed}
    未闭合的块会记录 closed=False，end_line 指向最后一行，code 仍被保留以便诊断。
    """
    lines = md_text.split("\n")
    blocks: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```mermaid"):
            start = i
            code_lines: list[str] = []
            i += 1
            closed = False
            while i < len(lines):
                if lines[i].strip().startswith("```"):
                    closed = True
                    break
                code_lines.append(lines[i])
                i += 1
            # 到达文件尾仍未闭合
            end_line = i if closed else max(i, len(lines) - 1)
            code = "\n".join(code_lines)
            if not closed:
                logger.warning(
                    "Mermaid 块 (第 %d 行起) 未闭合 ```，内容仍保留以便诊断",
                    start + 1,
                )
            blocks.append({
                "index": len(blocks),
                "code": code,
                "type": _detect_mermaid_type(code),
                "start_line": start,
                "end_line": end_line,
                "closed": closed,
            })
        i += 1
    return blocks


def _mermaid_to_image_prompt(mermaid_code: str, mtype: str, index: int) -> str:
    """把 mermaid 代码转化为 sn-image-generate 的 prompt。"""
    layout_map = {
        "flowchart": "structural-breakdown",
        "sequence": "linear-progression",
        "class": "hierarchical-layers",
        "state": "circular-flow",
        "er": "hub-spoke",
        "pie": "pie-chart",
        "gantt": "timeline-horizontal",
        "journey": "path-journey",
        "mindmap": "radial-mindmap",
        "timeline": "linear-progression",
        "diagram": DEFAULT_LAYOUT,
    }
    layout = layout_map.get(mtype, DEFAULT_LAYOUT)

    # 提取 mermaid 中的关键文本节点作为 prompt 内容
    text_nodes = []
    for line in mermaid_code.split("\n"):
        line = line.strip()
        # 提取 ["text"] 或 ("text") 中的文本
        matches = re.findall(r'["\']([^"\']{2,50})["\']', line)
        text_nodes.extend(matches)
        # 提取 -->|label| 中的 label
        arrow_matches = re.findall(r'\|([^|]{2,30})\|', line)
        text_nodes.extend(arrow_matches)

    content_summary = "、".join(text_nodes[:8]) if text_nodes else f"{mtype} 示意图"
    title = f"{mtype.capitalize()} {index + 1}"

    return _build_image_prompt(
        title=title,
        layout=layout,
        style=DEFAULT_STYLE,
        aspect_ratio=DEFAULT_ASPECT_RATIO,
    ) + f"\n## Mermaid Code Reference\n```\n{mermaid_code[:500]}\n```\n"


def _generate_with_retry(
    *,
    prompt: str,
    save_path: Path,
    label: str,
    max_rounds: int,
    review: bool,
    image_size: str,
    aspect_ratio: str,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    timeout: float,
    critic_path: Path | None,
) -> Path | None:
    """通用"生成 → 审核 → 失败重试"循环。

    Args:
        prompt: 生成 prompt
        save_path: 目标保存路径
        label: 日志标签（如 "mermaid-0 (flowchart)" 或 "'架构' (structural-breakdown)"）
        其他参数同 generate_image / review_image

    Returns:
        成功返回 save_path；失败返回 None
    """
    for round_num in range(1, max_rounds + 1):
        logger.info("  [Round %d/%d] %s → %s", round_num, max_rounds, label, save_path.name)

        if not generate_image(
            SN_AGENT_RUNNER,
            prompt=prompt,
            save_path=save_path,
            image_size=image_size,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
        ):
            logger.warning("  ❌ Generation failed round %d", round_num)
            continue

        if not review:
            return save_path

        review_result = review_image(
            SN_AGENT_RUNNER,
            save_path,
            system_prompt_path=critic_path,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

        if review_result["status"] == "pass":
            logger.info("  ✅ PASS after round %d", round_num)
            return save_path
        elif review_result["status"] == "fail":
            logger.warning(
                "  ❌ FAIL round %d: %s",
                round_num,
                review_result["reasoning"][:80] if review_result["reasoning"] else "",
            )
            if save_path.exists():
                save_path.unlink()
        else:
            # 审核错误（超时/返回非 JSON 等）：删除图片并立即标记失败，不再重试。
            # VLM 自身出错时重试无意义，且残留图片会被 render_mermaid_images
            # 中的 "if save_path.exists()" 直接复用，绕过审核。
            logger.warning(
                "  ⚠️  Review error round %d, marking as failed: %s",
                round_num,
                review_result["reasoning"][:80] if review_result["reasoning"] else "",
            )
            if save_path.exists():
                save_path.unlink()
            break

    # 兜底清理：所有轮次耗尽仍失败时，确保 save_path 不残留
    # （防止下次 render_mermaid_images 误用未验证图片）
    if save_path.exists():
        save_path.unlink()
    logger.warning("  ❌ %s failed after %d rounds", label, max_rounds)
    return None


def render_mermaid_images(
    md_path: Path,
    output_dir: Path,
    *,
    max_rounds: int = 1,
    review: bool = False,
    image_size: str = DEFAULT_IMAGE_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 300.0,
    md_text: str | None = None,
    force: bool = False,
) -> list[Path | None]:
    """渲染 md 中的所有 mermaid 代码块为图片，返回图片路径列表。

    图片按 index 顺序命名: mermaid_00.png, mermaid_01.png, ...
    生成失败的位置为 None。未闭合的 mermaid 块也会返回 None（不参与渲染）。

    Args:
        force: True 时忽略缓存，强制重新生成所有 mermaid 图片
    """
    if not SN_AGENT_RUNNER or not SN_AGENT_RUNNER.exists():
        logger.warning(
            "sn_agent_runner.py not found. Skipping mermaid rendering."
        )
        return []

    if md_text is None:
        md_text = md_path.read_text(encoding="utf-8")
    blocks = _extract_mermaid_blocks(md_text)
    if not blocks:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("发现 %d 个 Mermaid 代码块，开始生成图片...", len(blocks))

    sn_skill_dir = _find_sn_skill_dir()
    critic_path = sn_skill_dir / "references" / "prompts-critic-system.md" if sn_skill_dir else None

    # 加载缓存（基于 mermaid 代码内容 hash）
    cache_path = output_dir / _MERMAID_CACHE_FILE
    cache = {} if force else _load_mermaid_cache(cache_path)
    if force:
        logger.info("  ⚠️  --force 模式：忽略缓存，强制重新生成")
        # 强制模式下清空已有图片
        for old_file in output_dir.glob("mermaid_*.png"):
            try:
                old_file.unlink()
            except OSError:
                pass
    cache_updated = False

    result: list[Path | None] = []

    for block in blocks:
        idx = block["index"]
        mtype = block["type"]
        code = block["code"]
        save_path = output_dir / f"mermaid_{idx:02d}.png"

        # 未闭合块：直接记为失败，保留原代码
        if not block.get("closed", True):
            logger.warning("  ⚠️  mermaid-%d 未闭合 ```，跳过生成", idx)
            result.append(None)
            continue

        # 1. 文件已存在 → 跳过
        if save_path.exists():
            logger.info("  📋 [%d] %s 已存在，跳过", idx, mtype)
            result.append(save_path)
            # 更新缓存
            code_hash = _mermaid_code_hash(code)
            if cache.get(code_hash) != save_path.name:
                cache[code_hash] = save_path.name
                cache_updated = True
            continue

        # 2. 缓存命中：相同代码已生成过 → 复用文件名
        code_hash = _mermaid_code_hash(code)
        cached_fname = cache.get(code_hash)
        if cached_fname:
            cached_path = output_dir / cached_fname
            if cached_path.exists():
                logger.info("  📋 [%d] %s 缓存命中 → %s", idx, mtype, cached_fname)
                # 复用已生成的图片文件
                try:
                    shutil.copy2(cached_path, save_path)
                    result.append(save_path)
                    continue
                except OSError as e:
                    logger.warning("  缓存复用失败: %s，重新生成", e)

        prompt = _mermaid_to_image_prompt(code, mtype, idx)
        label = f"mermaid-{idx} ({mtype})"

        path = _generate_with_retry(
            prompt=prompt,
            save_path=save_path,
            label=label,
            max_rounds=max_rounds,
            review=review,
            image_size=image_size,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            critic_path=critic_path,
        )
        if path is not None:
            logger.info("  ✅ mermaid-%d (%s) → %s", idx, mtype, save_path.name)
            # 写入缓存
            cache[code_hash] = save_path.name
            cache_updated = True
        result.append(path)

    if cache_updated:
        _save_mermaid_cache(cache_path, cache)

    return result


def replace_mermaid_with_images(md_text: str, image_paths: list[Path | None]) -> str:
    """把 md 中的 mermaid 代码块替换为 ![alt](path) 图片标记。

    image_paths 中 None 表示该块生成失败，保留原代码块。
    """
    blocks = _extract_mermaid_blocks(md_text)
    if not blocks or not image_paths:
        return md_text

    lines = md_text.split("\n")
    new_lines: list[str] = []
    last_end = 0

    for block, img_path in zip(blocks, image_paths):
        # 加入 mermaid 块之前的内容
        new_lines.extend(lines[last_end:block["start_line"]])

        # 未闭合或生成失败的块：保留原代码
        if (not block.get("closed", True)) or img_path is None or not img_path.exists():
            new_lines.extend(lines[block["start_line"]:block["end_line"] + 1])
        else:
            # 替换为图片标记
            alt_text = f"Mermaid {block['type']} {block['index'] + 1}"
            new_lines.append(f"![{alt_text}]({img_path})")
            new_lines.append("")

        last_end = block["end_line"] + 1

    # 加入剩余内容
    new_lines.extend(lines[last_end:])
    return "\n".join(new_lines)


def _get_layout_for_key(key: str) -> tuple[str, str]:
    """根据 chart_map 的 key 推荐布局。

    匹配规则：key 中包含预定义关键词（如 "架构" "对比" "timeline"）即命中。
    只做单向匹配 dt_key in key.lower()，避免 "比" 误匹配到 "comparison"。
    """
    key_lower = key.lower()
    for dt_key, (primary, _) in DATA_TYPE_MAP.items():
        if dt_key in key_lower:
            return primary, DEFAULT_STYLE
    mapping = {
        "架构": ("structural-breakdown", DEFAULT_STYLE),
        "数据仓库": ("hierarchical-layers", DEFAULT_STYLE),
        "数据": ("hierarchical-layers", DEFAULT_STYLE),
        "路线": ("linear-progression", DEFAULT_STYLE),
        "依赖": ("hub-spoke", DEFAULT_STYLE),
        "对比": ("binary-comparison", DEFAULT_STYLE),
        "组织": ("hierarchical-layers", DEFAULT_STYLE),
    }
    for kw, (layout, style) in mapping.items():
        if kw in key:
            return layout, style
    return DEFAULT_LAYOUT, DEFAULT_STYLE


def _build_image_prompt(
    title: str,
    layout: str,
    style: str,
    aspect_ratio: str,
) -> str:
    return (
        f"Create a professional infographic following these specifications:\n"
        f"\n"
        f"## Image Specifications\n"
        f"- Type: Infographic\n"
        f"- Layout: {layout}\n"
        f"- Style: {style}\n"
        f"- Aspect Ratio: {aspect_ratio}\n"
        f"- Language: Chinese\n"
        f"\n"
        f"## Core Principles\n"
        f"- Follow the layout structure precisely for information architecture\n"
        f"- Apply style aesthetics consistently throughout\n"
        f"- Keep information concise, highlight keywords and core concepts\n"
        f"- Use ample whitespace for visual clarity\n"
        f"- Maintain clear visual hierarchy with distinct priority levels\n"
        f"\n"
        f"## Visual Elements\n"
        f"- At least one primary illustration or icon set corresponding to the topic\n"
        f"- Distinct visual markers for each section or module\n"
        f"- Background texture: clean professional\n"
        f"- Font style: clear and readable\n"
        f"\n"
        f"## Text Requirements\n"
        f"- Main titles should be prominent and readable\n"
        f"- Key concepts visually emphasized (bold, larger size, color contrast)\n"
        f"- Labels clear and appropriately sized\n"
        f"- Use Chinese for all text content\n"
        f"- Hard data in visually distinct formats: bold, callout boxes, or data badges\n"
        f"\n"
        f"## Content\n"
        f"{title}\n"
    )


def generate_image(
    runner_path: Path,
    prompt: str,
    save_path: Path,
    *,
    image_size: str = DEFAULT_IMAGE_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 300.0,
) -> bool:
    cmd = [
        sys.executable,
        str(runner_path),
        "sn-image-generate",
        "--prompt", prompt,
        "--image-size", image_size,
        "--aspect-ratio", aspect_ratio,
        "--save-path", str(save_path),
        "--timeout", str(timeout),
        "-o", "json",
    ]
    if api_key:
        cmd += ["--api-key", api_key]
    if base_url:
        cmd += ["--base-url", base_url]
    if model:
        cmd += ["--model", model]

    logger.info("Generating image: %s → %s", prompt[:60], save_path.name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(timeout + 30),
        )
    except subprocess.TimeoutExpired:
        logger.error("sn-image-generate timed out after %.0fs", timeout)
        return False

    # 子进程崩溃（非零退出码）：stderr 通常会有 traceback
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()
        last_line = stderr_tail[-1] if stderr_tail else "(no stderr)"
        logger.error(
            "sn_agent_runner exited with code %d: %s",
            result.returncode, last_line[:200],
        )
        # 如果 stdout 仍然有 JSON，继续解析以提取 error 字段
        if not result.stdout.strip():
            return False

    stdout = result.stdout.strip()
    if not stdout:
        logger.warning("sn_agent_runner returned empty output")
        return False

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("sn_agent_runner output not JSON: %s", stdout[:200])
        return False

    if output.get("status") == "ok":
        logger.info("  ✅ Generated: %s", save_path.name)
        return True

    error = output.get("error", output.get("error_type", "unknown error"))
    logger.error("  ❌ Generation failed: %s", error)
    return False


def review_image(
    runner_path: Path,
    image_path: Path,
    *,
    system_prompt_path: Path | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(runner_path),
        "sn-image-recognize",
        "--user-prompt", "Evaluate the diagram in the image against the rules. Output your assessment.",
        "--images", str(image_path),
        "--timeout", str(timeout),
        "-o", "json",
    ]
    if system_prompt_path:
        cmd += ["--system-prompt-path", str(system_prompt_path)]
    if api_key:
        cmd += ["--api-key", api_key]
    if base_url:
        cmd += ["--base-url", base_url]
    if model:
        cmd += ["--model", model]

    logger.info("Reviewing image: %s", image_path.name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(timeout + 30),
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "violations": [], "reasoning": f"VLM review timed out ({timeout}s)"}

    # 子进程崩溃：返回错误状态，附带 stderr 末行
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()
        last_line = stderr_tail[-1] if stderr_tail else "(no stderr)"
        if result.stdout.strip():
            # stdout 仍有内容，继续解析
            pass
        else:
            return {
                "status": "error",
                "violations": [],
                "reasoning": f"VLM exited code={result.returncode}: {last_line[:200]}",
            }

    stdout = result.stdout.strip()
    if not stdout:
        return {"status": "error", "violations": [], "reasoning": "Empty VLM response"}

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "error", "violations": [], "reasoning": f"VLM output not JSON: {stdout[:200]}"}

    if output.get("status") != "ok":
        err = output.get("error", "unknown")
        return {"status": "error", "violations": [], "reasoning": f"VLM call failed: {err}"}

    result_text = output.get("result", "")
    try:
        vlm_json = json.loads(result_text)
    except json.JSONDecodeError:
        logger.warning("VLM result not JSON, treating as FAIL: %s", result_text[:200])
        return {"status": "fail", "violations": [], "reasoning": "VLM returned non-JSON, assumed FAIL"}

    verdict = vlm_json.get("result", "FAIL").upper()
    violations = vlm_json.get("violations", [])
    reasoning = vlm_json.get("reasoning", "")

    return {
        "status": "pass" if verdict == "PASS" else "fail",
        "violations": violations,
        "reasoning": reasoning,
    }


def _build_title_index_map(md_path: Path, *, md_text: str | None = None) -> dict[str, int]:
    """预扫描 md，构建 (title → chapter_idx) 映射。"""
    if md_text is None:
        md_text = md_path.read_text(encoding="utf-8")
    return {title: idx for idx, title in _parse_chapter_indices(md_text)}


def generate_missing_charts(
    charts_dir: Path,
    md_path: Path,
    chart_map: dict[str, str],
    *,
    max_rounds: int = 1,
    review: bool = False,
    image_size: str = DEFAULT_IMAGE_SIZE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 300.0,
    md_text: str | None = None,
) -> list[tuple[int, Path]]:
    if not SN_AGENT_RUNNER or not SN_AGENT_RUNNER.exists():
        logger.warning(
            "sn_agent_runner.py not found.\n"
            "  Install sn-image-base skill to enable chart generation.\n"
            "  Skipping chart generation."
        )
        return []

    sn_skill_dir = _find_sn_skill_dir()
    critic_path = sn_skill_dir / "references" / "prompts-critic-system.md" if sn_skill_dir else None

    # 预构建标题索引映射，避免累加 bug
    title_to_idx = _build_title_index_map(md_path, md_text=md_text)
    existing_files = {p.name for p in charts_dir.glob("*") if p.is_file()}

    result: list[tuple[int, Path]] = []

    for key, fname in chart_map.items():
        if fname in existing_files:
            img_path = charts_dir / fname
            # 已存在文件：尝试从 md 中找到对应章节索引
            matched_idx = 0
            for title, idx in title_to_idx.items():
                if key in title:
                    matched_idx = idx
                    break
            result.append((matched_idx, img_path))
            continue

        # 找匹配标题
        matched_title = ""
        matched_idx = -1
        for title, idx in title_to_idx.items():
            if key in title:
                matched_title = title
                matched_idx = idx
                break

        if not matched_title:
            logger.info("  ⚠️  No matching title for key '%s', skipping generation", key)
            continue

        layout, style = _get_layout_for_key(key)
        prompt = _build_image_prompt(
            title=matched_title,
            layout=layout,
            style=style,
            aspect_ratio=aspect_ratio,
        )
        save_path = charts_dir / fname
        label = f"'{key}' ({layout})"

        path = _generate_with_retry(
            prompt=prompt,
            save_path=save_path,
            label=label,
            max_rounds=max_rounds,
            review=review,
            image_size=image_size,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            critic_path=critic_path,
        )
        if path is not None:
            result.append((matched_idx, save_path))
            logger.info("  ✅ '%s' → %s", key, save_path.name)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Markdown 转精美 DOCX — 零路径依赖，支持配图/信息图生成/VLM审核"
    )
    parser.add_argument("input", type=Path, help="输入 Markdown 文件路径")
    parser.add_argument("-o", "--output", type=Path, default=None, help="输出 DOCX 路径（默认同目录同名.docx）")

    parser.add_argument("--title", type=str, default=None, help="报告标题（默认取 md 文件名）")
    parser.add_argument("--subtitle", type=str, default=None, help="封面副标题")
    parser.add_argument("--author", type=str, default=None, help="文档作者（写入 docx 元数据，默认 Hermes AI）")
    parser.add_argument("--subject", type=str, default=None, help="文档主题（写入 docx 元数据，默认取标题）")
    parser.add_argument(
        "--no-toc",
        dest="toc",
        action="store_false",
        default=True,
        help="不添加目录页（默认开启目录）",
    )

    parser.add_argument("--charts", type=Path, default=None, help="配图目录路径")
    parser.add_argument("--chart-map", type=Path, default=None, help="配图映射 JSON 文件")

    parser.add_argument(
        "--generate",
        action="store_true",
        help="自动生成缺失图表（调用 sn-image-base，需 --charts 和 --chart-map）",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=1,
        help="生成轮数（默认 1，多轮用于 --review 失败重试）",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="生成后启用 VLM 审核（需 --generate），PASS 才保留",
    )
    parser.add_argument("--image-size", default=DEFAULT_IMAGE_SIZE, help=f"生成图片尺寸（默认 {DEFAULT_IMAGE_SIZE}）")
    parser.add_argument("--aspect-ratio", default=DEFAULT_ASPECT_RATIO, help=f"生成图片宽高比（默认 {DEFAULT_ASPECT_RATIO}）")
    parser.add_argument("--api-key", default=None, help="sn-image-base API Key（默认读环境变量）")
    parser.add_argument("--base-url", default=None, help="sn-image-base API Base URL（默认读环境变量）")
    parser.add_argument("--model", default=None, help="sn-image-base 图片模型名（默认读环境变量）")

    parser.add_argument(
        "--render-mermaid",
        action="store_true",
        help="将 Mermaid 代码块渲染为信息图（调用 sn-image-base），插入原位置",
    )
    parser.add_argument(
        "--mermaid-output",
        type=Path,
        default=None,
        help="Mermaid 生成图片的输出目录（默认同目录 mermaid_images/）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略 Mermaid 缓存，强制重新生成（配合 --render-mermaid 使用）",
    )

    parser.add_argument("--dry-run", action="store_true", help="只打印匹配结果，不导出")
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示 DEBUG 级日志（含 sn_agent_runner 的 stderr 详情）",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="只显示 WARNING 及以上日志",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"export_docx {__import__('ai_report').__version__}",
    )

    args = parser.parse_args()

    # 日志级别：--quiet 优先；--verbose 次之；默认 INFO
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
    elif args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    md_path = args.input.resolve()
    if not md_path.exists():
        logger.error("输入文件不存在: %s", md_path)
        sys.exit(1)

    title = args.title or md_path.stem
    output_path = args.output or md_path.with_suffix(".docx")
    output_path = output_path.resolve()

    print(f"输入: {md_path}")
    print(f"标题: {title}")
    print(f"输出: {output_path}")

    # 全程只读一次 md 文件
    md_text = md_path.read_text(encoding="utf-8")

    chart_map = load_chart_map(args.chart_map)
    chart_images: list[tuple[int, Path]] = []

    if args.charts:
        charts_dir = args.charts.resolve()
        if not charts_dir.exists():
            charts_dir.mkdir(parents=True, exist_ok=True)
            logger.info("创建配图目录: %s", charts_dir)

        if args.generate:
            logger.info("─" * 40)
            logger.info("生成缺失图表（sn-image-generate）")
            logger.info("─" * 40)
            generated = generate_missing_charts(
                charts_dir=charts_dir,
                md_path=md_path,
                chart_map=chart_map,
                max_rounds=args.max_rounds,
                review=args.review,
                image_size=args.image_size,
                aspect_ratio=args.aspect_ratio,
                api_key=args.api_key,
                base_url=args.base_url,
                model=args.model,
                md_text=md_text,
            )
            logger.info("─" * 40)
            logger.info("生成完成: %d 张新图表", len(generated))

        print("\n匹配图表:")
        chart_images = build_chart_images(md_path, charts_dir, chart_map, md_text=md_text)
        if not chart_images:
            print("  ⚠️  无匹配图表，将导出纯文字 docx")
    else:
        chart_images = []

    if args.dry_run:
        if args.render_mermaid:
            mermaid_blocks = _extract_mermaid_blocks(md_text)
            print(f"\n发现 Mermaid 代码块: {len(mermaid_blocks)} 个")
            for b in mermaid_blocks:
                closed = "✓" if b.get("closed", True) else "✗"
                print(f"  #{b['index']}: {b['type']} (第 {b['start_line']}-{b['end_line']} 行, closed={closed})")
        print(f"\n[DRY-RUN] 共 {len(chart_images)} 张配图，未导出")
        return

    full_content = md_text

    # Mermaid 渲染
    if args.render_mermaid:
        mermaid_output_dir = (
            args.mermaid_output.resolve()
            if args.mermaid_output
            else md_path.parent / "mermaid_images"
        )
        mermaid_output_dir.mkdir(parents=True, exist_ok=True)

        print("\n" + "─" * 40)
        print("Mermaid 代码块渲染（sn-image-generate）")
        print("─" * 40)
        mermaid_images = render_mermaid_images(
            md_path=md_path,
            output_dir=mermaid_output_dir,
            max_rounds=args.max_rounds,
            review=args.review,
            image_size=args.image_size,
            aspect_ratio=args.aspect_ratio,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            md_text=md_text,
            force=args.force,
        )
        generated_count = sum(1 for p in mermaid_images if p is not None and p.exists())
        print(f"渲染完成: {generated_count}/{len(mermaid_images)} 张成功")

        if mermaid_images:
            full_content = replace_mermaid_with_images(full_content, mermaid_images)
            print("  ✅ Mermaid 代码块已替换为图片标记")

    try:
        result = export_to_docx(
            title=title,
            full_content=full_content,
            chart_images=chart_images,
            output_path=output_path,
            subtitle=args.subtitle,
            toc=args.toc,
            author=args.author,
            subject=args.subject,
        )
    except PermissionError as e:
        logger.error(str(e))
        sys.exit(2)
    size_kb = result.stat().st_size / 1024
    print(f"\n✅ DOCX 导出成功: {result}")
    print(f"   大小: {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
