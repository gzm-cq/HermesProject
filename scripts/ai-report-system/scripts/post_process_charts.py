# -*- coding: utf-8 -*-
"""后处理：从 .md 生成图表并导出 .docx

用法：
    python3 scripts/post_process_charts.py <topic> [--md <path>]

流程：
    1. 读 .md 全文
    2. LLM 分析哪些章节有适合配图的数据（表格/对比/时间线）
    3. 从 .md 正文提取对应数据
    4. matplotlib 渲染 PNG
    5. 嵌入 .docx

仅依赖 .md 文件本身，不依赖管线内的任何中间数据。
"""
from __future__ import annotations

import json as _json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# ── 项目根路径 ──
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("post_process")

# ── 导入渲染和导出工具 ──
from ai_report.export.chart_renderer import render_chart
from ai_report.export.docx_exporter import export_to_docx

# ── LLM 调用 ──
from ai_report.adapters.ai_client import call_llm


# ── Step 1: LLM 分析 .md，推荐图表 ──

CHART_SPEC_PROMPT = """你是一个图表规划专家。请分析以下报告全文，判断哪些章节适合配图。

## 报告内容
{md_content}

## 支持的图表类型
- architecture_table: 有三层架构、分层对比、角色对比时使用
- timeline: 有年份规划、实施路径、分阶段里程碑时使用
- comparison: 有投资对比、占比对比、指标对比时使用
- infographic: 有路线图、时间轴、流程图、发展历程、能力成长轨迹等适合用信息图展示的场景

## 判断标准
1. 数据必须来源于 .md 正文中的表格或文字描述
2. 同一章节最多推荐 1 张图
3. 没有合适数据时不推荐（该章设为 null）
4. 只推荐确有可视化价值的章节——不要为凑数而推荐

## 输出格式
JSON 数组，每个元素对应一个章节（索引从 0 开始，对应报告正文的章节顺序）：
[
  null,
  {{"type": "architecture_table", "data_fields": ["层名", "定位", "职能"]}},
  null,
  {{"type": "comparison", "data_fields": ["项目", "金额", "占比"]}},
  {{"type": "timeline", "data_fields": ["年份", "阶段", "投入"]}},
  null,
  null
]
只输出 JSON 数组，不要多余文字。长度必须与章节数一致。"""


def analyze_chart_specs(md_content: str) -> list[dict | None]:
    """LLM 分析报告全文，返回每章的 chart_spec 推荐。"""
    prompt = CHART_SPEC_PROMPT.format(md_content=md_content[:8000])
    response = call_llm(prompt, max_tokens=2000, temperature=0.2)

    # 解析 JSON
    text = response.strip()
    if text.startswith("```"):
        import re
        m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()

    try:
        specs = _json.loads(text)
        if isinstance(specs, list):
            return specs
    except _json.JSONDecodeError:
        logger.warning("  chart_spec 解析失败，无图表输出")
        return []
    return []


# ── Step 2: 从 .md 正文提取图表数据 ──

DATA_EXTRACT_PROMPT = """你是一个数据提取专家。请从以下报告内容中，提取某章节的图表数据。

## 章节标题
{chapter_title}

## 报告全文（相关上下文）
{context}

## 图表类型: {chart_type}

## 数据格式要求（必须严格遵守）

如果是 architecture_table：
{{"data": {{"layers": [{{"name": "互联网层", "定位": "...", "职能": "..."}}]}}}}

如果是 timeline：
{{"data": {{"phases": [{{"name": "阶段名称", "start": "2026", "end": "2027", "投入": "金额"}}]}}}}

如果是 comparison：
{{"data": {{"items": [{{"项目": "名称", "金额": "数值", "占比": "百分比"}}]}}}}

## 要求
- 只提取本章节中明确写出的事实和数据，不编造
- 值的数量必须匹配 data_fields 指定的字段
- 没有数据则输出 null

## 输出格式
仅 JSON，不要多余文字。如果没有数据，只输出 null。"""


def extract_chart_data(
    md_content: str,
    chapter_index: int,
    chapter_title: str,
    spec: dict,
) -> dict | None:
    """从 .md 正文提取指定章节的图表数据。"""
    # 找到章节在正文中的位置
    import re

    # 按 # / ## 标题分割（兼容 H1 和 H2 作为章节头）
    sections = re.split(r"\n(?=##? )", md_content)
    if chapter_index < len(sections):
        context = sections[chapter_index][:2000]
    else:
        # 回退：取全文尾部
        context = md_content[-3000:]

    data_fields = spec.get("data_fields", [])
    chart_type = spec.get("type", "")
    prompt = DATA_EXTRACT_PROMPT.format(
        chapter_title=chapter_title,
        context=context,
        chart_type=chart_type,
        data_fields=_json.dumps(data_fields, ensure_ascii=False),
    )

    response = call_llm(prompt, max_tokens=1500, temperature=0.1)
    text = response.strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()

    try:
        parsed = _json.loads(text)
        # 统一为 {data: ...} 格式
        if isinstance(parsed, dict) and "data" in parsed:
            return parsed["data"]
        if isinstance(parsed, dict) and any(k in parsed for k in ("layers", "items", "phases")):
            return parsed
        return parsed
    except _json.JSONDecodeError:
        logger.warning("  数据提取失败: %s", chapter_title)
        return None


# ── infographic: 用 sensenova-u1-fast 生成信息图 ──


def generate_infographic(
    chapter_title: str,
    data: dict,
    output_path: Path,
) -> bool:
    """调用 infogen.sh 生成信息图，下载到本地。

    Args:
        chapter_title: 章节标题，用于构建 prompt
        data: 提取的数据（phases/items/layers 等）
        output_path: 输出 PNG 路径

    Returns:
        是否成功
    """
    # 构建 prompt
    chart_type = ""
    prompt_parts = [f"一张信息图：{chapter_title}"]

    if "phases" in data:
        chart_type = "timeline"
        phases = data["phases"]
        lines = [f"阶段{p['name']}：{p.get('start','')}-{p.get('end','')}，投入{p.get('投入','')}" for p in phases]
        prompt_parts.append("时间轴，展示以下阶段：")
        prompt_parts.extend(lines)
    elif "layers" in data:
        chart_type = "architecture"
        layers = data["layers"]
        lines = [f"{l.get('name','')}：{l.get('定位','')}，{l.get('职能','')}" for l in layers]
        prompt_parts.append("分层架构图，展示：")
        prompt_parts.extend(lines)
    elif "items" in data:
        chart_type = "comparison"
        items = data["items"]
        lines = [f"{item.get('项目','')}：{item.get('金额','')}，{item.get('占比','')}" for item in items]
        prompt_parts.append("对比图，展示：")
        prompt_parts.extend(lines)
    else:
        prompt_parts.append(str(data))

    prompt_parts.append("简洁大气的信息图风格，深蓝色主色调，适合正式报告")
    prompt = "\n".join(prompt_parts)

    # 调用 infogen.sh
    import subprocess
    try:
        result = subprocess.run(
            ["/root/.hermes/scripts/infogen.sh", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning("  infogen.sh 失败: %s", result.stderr[:200])
            return False

        # 提取 URL
        output = result.stdout.strip()
        url = output.replace("图片URL: ", "").strip()
        if not url.startswith("http"):
            logger.warning("  infogen.sh 输出异常: %s", output[:100])
            return False

        # 下载图片
        import urllib.request
        urllib.request.urlretrieve(url, output_path)
        logger.info("  ✅ 信息图已下载: %s", output_path.name)
        return True

    except subprocess.TimeoutExpired:
        logger.warning("  infogen.sh 超时")
        return False
    except Exception as e:
        logger.warning("  infogen.sh 异常: %s", e)
        return False


# ── Step 3: 主流程 ──


def post_process(
    md_path: str | Path,
    output_docx: str | Path | None = None,
) -> Path | None:
    """从 .md 文件生成图表并导出 .docx。

    Args:
        md_path: .md 文件路径
        output_docx: 输出 .docx 路径，None 则与 .md 同目录同名

    Returns:
        docx 文件路径，失败返回 None
    """
    md_path = Path(md_path)
    if not md_path.exists():
        logger.error("❌ .md 文件不存在: %s", md_path)
        return None

    if output_docx is None:
        output_docx = md_path.with_suffix(".docx")
    output_docx = Path(output_docx)

    content = md_path.read_text(encoding="utf-8")
    logger.info("📄 读取 .md: %s (%d chars)", md_path.name, len(content))

    # ── Step 1: LLM 分析图表推荐 ──
    logger.info("[1/3] LLM 分析图表推荐...")
    specs = analyze_chart_specs(content)
    valid = [(i, s) for i, s in enumerate(specs) if s and isinstance(s, dict)]
    logger.info("  推荐 %d/%d 章配图", len(valid), len(specs))

    if not valid:
        logger.info("  无图表推荐，直接导出纯文本身份 .docx")
        export_to_docx(
            title=md_path.stem,
            full_content=content,
            chart_images=[],
            output_path=output_docx,
        )
        logger.info("  ✅ .docx: %s", output_docx)
        return output_docx

    # ── Step 2: 提取数据 + 渲染图表 ──
    logger.info("[2/3] 提取数据 + 渲染图表...")
    chart_dir = md_path.parent / "charts"
    shutil.rmtree(chart_dir, ignore_errors=True)
    chart_dir.mkdir(parents=True, exist_ok=True)

    # 解析章节标题
    import re
    headings = re.findall(r"^#+\s+(.+)$", content, re.MULTILINE)

    chart_images: list[tuple[int, Path]] = []

    for i, spec in valid:
        # 确保 spec 有 type
        if "type" not in spec:
            continue

        # 提取章节标题
        chapter_title = headings[i] if i < len(headings) else f"第{i+1}章"

        # 提取数据
        data = extract_chart_data(content, i, chapter_title, spec)
        if not data:
            logger.info("  跳过 %s: 无数据", chapter_title)
            continue

        # 渲染
        chart_path = chart_dir / f"chart_{i}_{spec['type']}.png"
        spec_with_data = {**spec, "data": data}

        if spec["type"] == "infographic":
            # 信息图 → 调 sensenova-u1-fast
            success = generate_infographic(chapter_title, data, chart_path)
        else:
            # 数据图表 → matplotlib
            success = render_chart(spec_with_data, chart_dir, i, chapter_title=chapter_title)

        if success:
            chart_images.append((i, chart_path))
            logger.info("  ✅ %s: %s", chapter_title, chart_path.name)
        else:
            logger.info("  ⚠️ %s: 渲染失败", chapter_title)

    # ── Step 3: 导出 .docx ──
    logger.info("[3/3] 导出 .docx...")
    export_to_docx(
        title=md_path.stem,
        full_content=content,
        chart_images=chart_images,
        output_path=output_docx,
    )
    logger.info("  ✅ .docx: %s (%d 图, %d bytes)",
                output_docx, len(chart_images), output_docx.stat().st_size)
    return output_docx


# ── CLI 入口 ──

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="从 .md 生成图表并导出 .docx")
    parser.add_argument("topic", help="报告主题（在 reports/<topic>/ 下查找 .md）")
    parser.add_argument("--md", help=".md 文件路径（覆盖 topic 自动查找）")
    parser.add_argument("--output", help="输出 .docx 路径")
    args = parser.parse_args()

    if args.md:
        md_path = Path(args.md)
    else:
        # 先搜 reports/<topic>/
        md_path = None
        reports_dir = PROJECT_DIR / "reports" / args.topic
        if reports_dir.is_dir():
            for f in reports_dir.glob("*.md"):
                content = f.read_text(encoding="utf-8")
                if "# " in content:
                    md_path = f
                    logger.info("  取用: %s (reports)", md_path)
                    break

        if md_path is None:
            # 无结果再搜 test_outputs/<topic>/
            test_dir = PROJECT_DIR / "test_outputs" / args.topic
            if test_dir.is_dir():
                candidates = []
                for f in test_dir.glob("*.md"):
                    content = f.read_text(encoding="utf-8")
                    if "# " in content:
                        candidates.append(f)
                if candidates:
                    md_path = max(candidates, key=lambda p: p.stat().st_mtime)
                    logger.info("  取用: %s (test_outputs)", md_path)

        if md_path is None:
            logger.error("❌ 未在 reports/%s/ 或 test_outputs/%s/ 下找到有效 .md 文件",
                         args.topic, args.topic)
            sys.exit(1)

    output_docx = Path(args.output) if args.output else md_path.with_suffix(".docx")
    result = post_process(md_path, output_docx)
    if result:
        print(f"\n✅ 完成: {result}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
