# -*- coding: utf-8 -*-
"""
素材事实提取 — 从源文档中提取关键事实，构建结构化事实库。

流程：
  Phase 1: LLM 逐篇自由提取关键事实（不预设类别）
  Phase 2: 同一 LLM 调用内嵌完整性自检
  Phase 3: 多文件合并 → 冲突检测

输出: reports/<topic>/fact_bank.json
"""

from __future__ import annotations

import json as _json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# 加入项目根到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 设 dummy 环境变量（防止 Dify 等依赖报错）
import os
os.environ.setdefault("DIFY_DATASET_API_KEY", "dummy")
os.environ.setdefault("DIFY_DATASET_ID", "dummy")
os.environ.setdefault("DIFY_API", "http://localhost:5001")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("extract_facts")

from ai_report.adapters.ai_client import call_llm
from ai_report.config import load_report_config


# ── 事实提取 prompt ────────────────────────────────────────

def _build_extract_prompt(source_text: str) -> str:
    """构建事实提取 prompt，用 f-string 避免 .format() 和 JSON 花括号冲突。"""
    json_example = """```json
{
  "facts": [
    {
      "fact": "事实内容描述",
      "evidence": "原文依据（20-80字原文摘录）",
      "category": "信息类别"
    }
  ],
  "completeness_note": "自检说明：确认你是否遗漏了什么，与第一次提取的差异是什么"
}
```"""
    return f"""你是一个专业的事实提取助手。你的任务是从以下源文档中提取所有重要的事实，构建一个结构化的事实库。

## 要求
1. 通读整个源文档，提取你认为**对于写一份企业战略规划报告（面向CEO汇报）** 有价值的、重要的事实
2. 每条事实独立成一条，不要合并或概括
3. 每条事实必须包含：
   - **事实内容**：用一句话准确描述事实，具体、可验证
   - **原文依据**：从源文档中摘录支持这句话的关键原文（20-80字）
   - **信息类别**：由你判断该事实属于什么类别（建议类别：投资金额、时间节点、技术路线、架构方案、风险评估、战略定位；也可自拟其他类别，不限制）
4. 几条重要准则：
   - 宁多勿漏：不确定是否重要也先提取
   - 具体数字优先：金额、百分比、年份、时长等具体数据至关重要
   - 决策性信息优先：涉及"为什么选这个方案"、"不做什么"、"约束条件"的信息
   - 原文依据必须有出处：直接从原文摘录，不要自己编
   - 建议提取5-20条最核心的事实，优先覆盖决策方向和关键数据
5. 提取完成后，请检查提取结果覆盖情况，看是否有重要的事实被你遗漏了。如果有，追加到列表中。

## 输出格式
返回一个 JSON 对象，包含两个字段：
{json_example}

## 源文档
---文档开始---
{source_text}
---文档结束---"""


# ── 文件读取 ───────────────────────────────────────────────

def read_source_files(topic: str, report_type: str) -> list[dict[str, str]]:
    """读取指定主题的所有源文件。

    Returns:
        [{"filename": "xxx.txt", "content": "...", "label": "文件1-文件名"}, ...]
    """
    report_config = load_report_config(topic, report_type=report_type)
    from ai_report.config import get_source_extensions
    exts = get_source_extensions(report_config)

    # 只搜索 inputs/ 目录（源素材目录）
    base = Path("reports") / topic
    inputs_dir = base / "inputs"
    search_dirs = [inputs_dir]

    # 如果是 inputs/ 不存在，回退到根目录（兼容旧流程）
    if not inputs_dir.exists():
        search_dirs.append(base)

    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for sd in search_dirs:
        if not sd.exists():
            continue
        for f in sorted(sd.iterdir()):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            if f.name in seen:
                continue
            seen.add(f.name)

            content = _read_file(f)
            if content:
                files.append({
                    "filename": f.name,
                    "content": content,
                    "label": f"{len(files)+1}-{f.name}",
                })

    return files


def _read_file(fp: Path) -> str | None:
    """读取单个文件，自动处理编码和格式。"""
    ext = fp.suffix.lower()
    try:
        if ext == ".docx":
            try:
                import docx
                doc = docx.Document(str(fp))
                return "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                logger.warning("  python-docx 未安装，跳过 docx: %s", fp.name)
                return None
        elif ext == ".txt":
            for enc in ["utf-8", "gbk", "gb2312"]:
                try:
                    return fp.read_text(encoding=enc)
                except UnicodeDecodeError:
                    continue
            logger.warning("  无法解码: %s", fp.name)
            return None
        else:
            return fp.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("  读取失败 %s: %s", fp.name, e)
        return None


# ── 单文件事实提取 ─────────────────────────────────────────

def extract_facts_from_file(
    filename: str,
    content: str,
    max_chars: int = 8000,
) -> list[dict[str, Any]]:
    """对一个源文件进行事实提取。

    如果内容超过 max_chars，截取开头 + 结尾确保不遗漏尾部关键信息。
    """
    # 太长则取开头和结尾
    if len(content) > max_chars:
        half = max_chars // 2
        source_text = (
            content[:half]
            + "\n\n...（中间省略）...\n\n"
            + content[-half:]
        )
        logger.info("  内容 %d 字，截取首尾各 %d 字", len(content), half)
    else:
        source_text = content

    prompt = _build_extract_prompt(source_text)

    try:
        raw = call_llm(
            system_prompt="你是一个专业的事实提取助手。输出格式请严格按照用户指令中的JSON格式要求。",
            prompt=prompt,
            temperature=0.3,
            max_tokens=8192,
        )
    except Exception as e:
        logger.warning("  LLM 调用失败: %s", e)
        return []

    # 解析 JSON
    result = _parse_json_response(raw)
    if not result or "facts" not in result:
        logger.warning("  解析失败，raw=%s", str(raw)[:200])
        return []

    facts = result["facts"]
    # 为每条事实加上来源文件
    for f in facts:
        f["source"] = filename

    note = result.get("completeness_note", "")
    if note:
        logger.info("  自检说明: %s", note[:120])

    logger.info("  提取 %d 条事实 (类别: %s)",
                len(facts),
                ", ".join(sorted(set(f.get("category", "?") for f in facts))))
    return facts


def _parse_json_response(raw: str) -> dict | None:
    """从 LLM 输出中解析 JSON，兼容 ```json 包裹和截断 JSON。"""
    text = raw.strip()

    # Step 1: 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end+1]
        else:
            # 只有 { 没有 } → 截断了，从 { 取到结尾
            start = text.find("{")
            if start >= 0:
                text = text[start:]

    # Step 2: 尝试标准解析
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        pass

    # Step 3: 尝试修复截断 JSON
    # 常见问题：数组或对象最后被截断，缺少 ] 或 }
    try:
        # 找到第一个 { 和最后一个完整的事实对象
        repaired = _repair_truncated_json(text)
        if repaired:
            return repaired
    except Exception:
        pass

    # Step 4: 尝试 json_parse（使用 strict=False）
    try:
        from ai_report.adapters import json_parse as jp  # noqa: F401 — fallback if json_parse exists
        return jp(text)
    except (ImportError, Exception):
        pass

    return None


def _repair_truncated_json(text: str) -> dict | None:
    """修复截断的 JSON。支持：
    - 缺少闭合 ] 或 }
    - 最后一个对象/数组后有逗号但无后续内容
    - 字符串值被截断
    """
    text = text.strip()

    # 找到第一个 {
    first_brace = text.find("{")
    if first_brace < 0:
        return None
    text = text[first_brace:]

    # 策略 1: 尝试 json_parse（strict=False）
    try:
        from ai_report.adapters import json_parse as jp  # noqa: F401 — fallback if json_parse exists
        return jp(text)
    except (ImportError, Exception):
        pass

    # 策略 2: 去掉末尾不完整的键值对，然后补全括号
    # 去掉最后一个逗号后的不完整内容
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 如果一行以 , 结尾且下一行不完整，保留 ,
        # 如果一行没有冒号且不是括号，可能是截断的垃圾，跳过
        cleaned_lines.append(line)
    
    # 找最后一个完整的事实对象 - 按 "{" 和 "}" 匹配
    # 直接从末尾往前找，砍掉最后一个不完整的对象
    depth = 0
    last_complete = len(text)
    in_str = False
    esc = False
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"' and not esc:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                # 找到了一个完整的对象结束位置
                last_complete = i
                break

    # 截取到最后一个完整对象后
    if last_complete > 0:
        trimmed = text[:last_complete + 1]
    else:
        trimmed = text

    # 补全括号
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in trimmed:
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"' and not esc:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()

    # 补全缺少的闭合括号
    repaired = trimmed.rstrip().rstrip(",")
    for ch in reversed(stack):
        repaired += "]" if ch == "[" else "}"

    try:
        return _json.loads(repaired)
    except _json.JSONDecodeError:
        pass

    # 策略 3: 最后尝试标准 json.loads + 末尾补 ], }, ]
    for suffix in ['"]}', '"]}', ']}', ']}']:
        try:
            return _json.loads(text.rstrip().rstrip(",") + suffix)
        except _json.JSONDecodeError:
            continue

    return None


# ── 多文件合并 ─────────────────────────────────────────────

def merge_facts(all_facts: list[dict[str, Any]]) -> dict[str, Any]:
    """多文件事实合并 + 智能冲突检测。

    策略：
    - 在同一类别内，按关键词相似度聚类（如投资金额、时间年份）
    - 同类相似事实如有不同数值 → 标记冲突
    - 不同类别的事实不交叉比较

    Returns:
        {"facts": [...], "stats": {...}, "conflicts": [...]}
    """
    # Step 1: 按类别分组
    by_category: dict[str, list[dict]] = {}
    for f in all_facts:
        cat = f.get("category", "其他")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(f)

    merged: list[dict] = []
    conflicts: list[dict] = []

    for cat, items in by_category.items():
        # Step 2: 按事实前40字去重合并（同文件或跨文件的重复）
        groups: dict[str, list[dict]] = {}
        for item in items:
            key = f"{cat}|{item.get('fact','')[:20].strip()}|{hash(item.get('fact',''))}"
            if key not in groups:
                groups[key] = []
            groups[key].append(item)

        for key, group in groups.items():
            sources = set(it["source"] for it in group)
            if len(sources) == 1:
                # 单来源重复 → 只保留一条
                merged.append(group[0])
            else:
                # 多来源 → 检查是否真的冲突
                texts = set(it.get("fact", "").strip() for it in group)
                if len(texts) == 1:
                    # 内容一致 → 合并来源
                    first = dict(group[0])
                    first["source"] = " + ".join(sorted(sources))
                    merged.append(first)
                else:
                    # 文字不同 → 判断是同一件事的不同说法还是不同事
                    if _is_same_topic(key, texts):
                        # 同一件事→冲突
                        merged.append(group[0])
                        conflicts.append({
                            "fact_key": key,
                            "category": cat,
                            "variants": [
                                {"fact": it.get("fact",""), "source": it["source"]}
                                for it in group
                            ],
                        })
                    else:
                        # 不同的事 → 各自保留
                        merged.extend(group)

    # Step 3: 按类别+关键词检测跨组冲突
    # 比如"实施周期4年(2026-2029)"和"三阶段(2026-2027,2028-2029,2030)"
    # 已经在groups里被分到不同key了，但其实是同一件事的不同说法
    _detect_cross_group_conflicts(merged, conflicts)

    # 统计类别分布
    categories: dict[str, int] = {}
    for f in merged:
        cat = f.get("category", "未分类")
        categories[cat] = categories.get(cat, 0) + 1

    stats = {
        "total_files": len(set(f["source"] for f in all_facts)),
        "total_raw_facts": len(all_facts),
        "total_merged_facts": len(merged),
        "total_conflicts": len(conflicts),
        "categories": categories,
        "sources": sorted(set(f["source"] for f in merged)),
    }

    return {"facts": merged, "stats": stats, "conflicts": conflicts}


def _is_same_topic(key: str, texts: set[str]) -> bool:
    """判断多个说法是否在讲同一件事。

    判断依据：
    - 都提到同一组关键数字（如都含"5.2"或"2026"）
    - 都提到同一组关键词（如都含"总投资"或"实施周期"）
    - 文字长度相近且含义相似
    """
    if len(texts) < 2:
        return False

    texts_list = list(texts)
    # 提取所有数字
    import re
    nums_list = []
    for t in texts_list:
        nums = set(re.findall(r'\d+[\.\d]*', t))
        nums_list.append(nums)

    # 检查是否有共同的数字
    if len(nums_list) >= 2:
        common_nums = nums_list[0]
        for ns in nums_list[1:]:
            common_nums = common_nums & ns
        if common_nums:
            return True

    # 检查是否有共同关键词（取前10字比较）
    key_words = set(key[:8])
    for t in texts_list:
        if t.startswith(key[:8]):
            return True

    return False


def _detect_cross_group_conflicts(
    merged: list[dict],
    conflicts: list[dict],
) -> None:
    """检测跨组的冲突——同一事物在不同文件中数字不一致。

    只检测真正的数据矛盾，不检测"都提到同一年份"的假冲突。
    """
    import re
    from collections import defaultdict

    # 按类别分组
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for f in merged:
        by_cat[f.get("category", "")].append(f)

    # 对投资金额类：检查"总投资"、"合计"相关事实是否一致
    invest_facts = by_cat.get("投资金额", [])
    total_amounts = []
    for f in invest_facts:
        text = f.get("fact", "")
        # 匹配含"总"、"合计"、"累计"的金额表述
        if re.search(r'(总|累计|合计)', text):
            amounts = re.findall(r'(\d+\.?\d*)\s*(万亿|亿|万|元|美元|欧元)', text)
            for num, unit in amounts:
                total_amounts.append((f"{num}{unit}", f["fact"], f["source"]))

    if len(total_amounts) >= 2:
        # 检查是否有不同的金额
        unique_amounts = set(a for a, _, _ in total_amounts)
        if len(unique_amounts) > 1:
            conflicts.append({
                "fact_key": "[数字冲突] 总投资金额不一致",
                "category": "投资金额",
                "variants": [
                    {"fact": fact, "source": src}
                    for _, fact, src in total_amounts
                ],
            })

    # 对时间节点类：检查整体实施周期是否一致
    time_facts = by_cat.get("时间节点", [])
    periods = []
    for f in time_facts:
        text = f.get("fact", "")
        # 提取年份范围: "2026-2029" "2026—2029" "从2026到2029" "2026-2029年"
        years = re.findall(r'(?:从)?(\d{4})\s*[-~到至—–]\s*(\d{4})(?:年)?', text)
        if years:
            for start, end in years:
                periods.append((start, end, text, f["source"]))
        # 提取"2026-2030"这种跨越
        spans = re.findall(r'(\d{4})年[前后]', text)
        if spans:
            for y in spans:
                if int(y) >= 2030:
                    periods.append(("2026", y, text, f["source"]))

    if len(periods) >= 2:
        unique_periods = set(f"{s}-{e}" for s, e, _, _ in periods)
        if len(unique_periods) > 1:
            conflicts.append({
                "fact_key": "[数字冲突] 实施周期不一致",
                "category": "时间节点",
                "variants": [
                    {"fact": text, "source": src}
                    for _, _, text, src in periods
                ],
            })


# ── 保存 ───────────────────────────────────────────────────

def save_fact_bank(
    topic: str,
    fact_bank: dict[str, Any],
) -> Path:
    """保存事实库到 reports/<topic>/fact_bank.json。"""
    out_dir = Path("reports") / topic
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "fact_bank.json"

    # 备份旧文件（带时间戳）
    if out_path.exists():
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = out_dir / f"fact_bank_{ts}.json"
        out_path.rename(backup_path)
        logger.info("📦 旧文件已备份: %s", backup_path.name)

    out_path.write_text(
        _json.dumps(fact_bank, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("✅ 事实库已保存: %s (%d 条事实)",
                out_path, len(fact_bank.get("facts", [])))
    return out_path


# ── 主流程 ─────────────────────────────────────────────────

def main(topic: str | None = None) -> dict[str, Any] | None:
    if topic is None:
        # 无 topic 参数时从 CLI 参数读取
        import sys
        topic = sys.argv[1] if len(sys.argv) > 1 else None
    if not topic:
        logger.error("❌ 未指定 topic，用法: python3 scripts/extract_facts.py <topic>")
        return None
    TOPIC = topic
    REPORT_TYPE = "tech"

    logger.info("=" * 50)
    logger.info("🧩 素材事实提取: %s", TOPIC)
    logger.info("=" * 50)

    # Step 1: 读取源文件
    files = read_source_files(TOPIC, REPORT_TYPE)
    if not files:
        logger.error("❌ 未找到源文件")
        return None

    logger.info("📂 找到 %d 个源文件:", len(files))
    for f in files:
        logger.info("   %s (%d 字)", f["filename"], len(f["content"]))

    # Step 2: 逐篇提取
    all_facts: list[dict] = []
    t0 = time.time()

    for i, f in enumerate(files):
        logger.info("\n[%d/%d] 提取: %s", i+1, len(files), f["filename"])
        facts = extract_facts_from_file(f["filename"], f["content"])
        all_facts.extend(facts)

    elapsed = time.time() - t0

    # Step 3: 合并 + 冲突检测
    logger.info("\n🔄 合并 %d 条原始事实...", len(all_facts))
    fact_bank = merge_facts(all_facts)

    # Step 4: 保存
    save_fact_bank(TOPIC, fact_bank)

    # Step 5: 打印摘要
    stats = fact_bank["stats"]
    conflicts = fact_bank["conflicts"]

    print("\n" + "=" * 50)
    print("📊 事实提取摘要")
    print("=" * 50)
    print(f"   源文件数:        {stats['total_files']}")
    print(f"   原始事实:        {stats['total_raw_facts']} 条")
    print(f"   合并后:          {stats['total_merged_facts']} 条")
    print(f"   冲突数:          {stats['total_conflicts']} 个")
    print(f"   耗时:            {elapsed:.0f}s")
    print(f"\n   类别分布:")
    for cat, count in sorted(stats["categories"].items(),
                              key=lambda x: -x[1]):
        print(f"     {cat}: {count} 条")

    if conflicts:
        print(f"\n⚠️  发现 {len(conflicts)} 个冲突:")
        for c in conflicts:
            print(f"   [{c['fact_key']}]:")
            for v in c["variants"]:
                print(f"     - {v['fact']} ({v['source']})")

    print(f"\n✅ 完成")

    return fact_bank


if __name__ == "__main__":
    main()
