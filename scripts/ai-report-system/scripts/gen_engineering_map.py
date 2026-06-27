#!/usr/bin/env python3
"""
工程手册交互式更新脚本 — 扫描 src/，比对工程文档，交互式更新。

用法:
  cd /mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system
  python3 scripts/gen_engineering_map.py

流程:
  1. AST 扫描 src/ 所有 .py 文件，提取模块/类/函数
  2. 比对 engineering.md 中的现有模块地图
  3. 显示差异（新增/移除/变化的模块）
  4. 询问是否更新 + 是否添加变更记录
"""
from __future__ import annotations

import ast
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

SRC = Path("src")
ENGINEERING = Path("engineering.md")

# ── 需要合并扫描的子目录 ──
SUB_PACKAGES = ["graph", "core", "export", "adapters", "cli"]

# ── 类/函数签名（用于比对） ──
ModuleInfo = dict[str, list[str]]   # {relative_path: [class1, func1, func2, ...]}

# ═══════════════════════════════════════════════════════════════
# 扫描
# ═══════════════════════════════════════════════════════════════

def scan_module(path: Path) -> list[str]:
    """AST 扫描一个 .py 文件，返回公开类/函数签名。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    items: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
            ]
            if methods:
                items.append(f"{node.name}[{', '.join(methods[:6])}]")
            else:
                items.append(node.name)
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            items.append(node.name)
    return items


def scan_all() -> ModuleInfo:
    """扫描 src/ 下所有 .py 文件，返回模块信息。"""
    sections: ModuleInfo = {}
    for py_file in sorted(SRC.rglob("*.py")):
        rel = py_file.relative_to(SRC)
        items = scan_module(py_file)
        if items:
            sections[str(rel)] = items
    return sections


# ═══════════════════════════════════════════════════════════════
# 比对
# ═══════════════════════════════════════════════════════════════

def parse_current_maps(text: str) -> ModuleInfo:
    """从 engineering.md 文本中解析现有模块地图。"""
    # 匹配 src/xxx.py 或 src/xxx/xxx.py 的行
    # 例如: | `ai_client.py` | `call_llm(prompt)` → str | ...
    # 或: `src/hermes_tools/ai_client.py` — xxx
    maps: ModuleInfo = {}

    # 匹配表格行: | `filename.py` | `method()` | ...
    table_pattern = re.findall(r'\|\s*`([^`]+\.py)`\s*\|\s*`([^`]+)`\s*\|', text)
    for filename, method in table_pattern:
        # 只保留实际存在于磁盘上的路径（排除 parser 假阳性）
        for sub in SUB_PACKAGES:
            candidate = Path(SRC) / sub / filename
            if candidate.exists():
                path = f"{sub}/{filename}"
                if path not in maps:
                    maps[path] = []
                maps[path].append(method)

    # 匹配模块标题行: ### 2.X 模块名（`src/xxx/xxx.py`）
    module_refs = re.findall(r'\(`src/([^)]+\.py)`\)', text)
    for ref in module_refs:
        if ref not in maps:
            maps[ref] = []

    return maps


def compute_diff(current: ModuleInfo, scanned: ModuleInfo) -> dict:
    """计算扫描结果和当前文档的差异。"""
    current_set = set(current.keys())
    scanned_set = set(scanned.keys())

    new_modules = scanned_set - current_set
    removed_modules = current_set - scanned_set
    common = scanned_set & current_set

    changed: list[Tuple[str, list[str], list[str]]] = []
    for mod in sorted(common):
        old_items = set(current[mod])
        new_items = set(scanned[mod])
        added = new_items - old_items
        removed = old_items - new_items
        if added or removed:
            changed.append((mod, list(added), list(removed)))

    return {
        "new": sorted(new_modules),
        "removed": sorted(removed_modules),
        "changed": changed,
    }


# ═══════════════════════════════════════════════════════════════
# 更新
# ═══════════════════════════════════════════════════════════════

def generate_module_table(sections: ModuleInfo) -> str:
    """生成模块表格的 Markdown。"""
    lines = ["| 模块 | 关键方法 | 说明 |", "|------|---------|------|"]
    for path, items in sorted(sections.items()):
        if items:
            first_items = items[:3]
            method_str = "; ".join(first_items)
            if len(items) > 3:
                method_str += f" (+{len(items)-3})"
            lines.append(f"| `{path}` | `{method_str}` | TBD |")
    return "\n".join(lines)


def update_engineering(diff: dict, sections: ModuleInfo) -> None:
    """交互式更新 engineering.md。"""
    content = ENGINEERING.read_text(encoding="utf-8")

    # ── 展示差异 ──
    print("\n" + "=" * 50)
    print("📊 模块扫描差异报告")
    print("=" * 50)

    if diff["new"]:
        print(f"\n🆕 新增模块 ({len(diff['new'])})")
        for m in diff["new"]:
            items = sections.get(m, [])
            item_str = ", ".join(items[:3]) if items else "(空)"
            print(f"  + {m}: {item_str}")

    if diff["removed"]:
        print(f"\n🗑️ 已移除模块 ({len(diff['removed'])})")
        for m in diff["removed"]:
            print(f"  - {m}")

    if diff["changed"]:
        print(f"\n🔄 变化模块 ({len(diff['changed'])})")
        for mod, added, removed in diff["changed"]:
            if added:
                print(f"  ~ {mod} 新增: {', '.join(added)}")
            if removed:
                print(f"  ~ {mod} 移除: {', '.join(removed)}")

    if not any([diff["new"], diff["removed"], diff["changed"]]):
        print("\n✅ 模块地图无变化")

    # ── 询问是否更新模块地图 ──
    update_module_map = input("\n🔄 更新模块地图？(y/N): ").strip().lower() == "y"
    if update_module_map:
        table = generate_module_table(sections)
        # 替换 "### 项目中使用的工具" 表格部分
        table_start = content.find("### 项目中使用的工具")
        if table_start >= 0:
            table_end = content.find("\n### ", table_start + 10)
            if table_end < 0:
                table_end = len(content)
            before = content[:table_start]
            after = content[table_end:]
            new_content = before + "### 项目中使用的工具\n\n" + table + "\n" + after
            ENGINEERING.write_text(new_content, encoding="utf-8")
            print("✅ 模块地图已更新")
            content = new_content
        else:
            print("⚠️ 未找到「### 项目中使用的工具」段落，跳过模块地图更新")

    # ── 询问是否添加变更记录 ──
    print("\n" + "-" * 50)
    print("📝 变更记录")
    print("  最近一次记录: 2026-04-26 创建工程手册")
    has_new = input("  是否添加新的变更记录？(y/N): ").strip().lower() == "y"
    if has_new:
        changes: list[str] = []
        print("  逐条输入变更内容（空行结束）:")
        print("  格式: 改动 | 涉及文件")
        while True:
            line = input("  > ").strip()
            if not line:
                break
            changes.append(line)

        if changes:
            today = datetime.now().strftime("%Y-%m-%d")
            record_section = "\n| 时间 | 改动 | 涉及文件 |\n|------|------|---------|\n"
            records_lines = []
            for c in changes:
                parts = c.split("|", 1)
                if len(parts) == 2:
                    records_lines.append(f"| {today} | {parts[0].strip()} | {parts[1].strip()} |")
                else:
                    records_lines.append(f"| {today} | {c.strip()} | TBD |")

            # 替换变更记录表格
            record_marker = "| 时间 | 改动 | 涉及文件 |"
            if record_marker in content:
                record_start = content.find(record_marker)
                record_end = content.find("\n---", record_start)
                if record_end < 0:
                    record_end = len(content)
                before = content[:record_start]
                after = content[record_end:]
                new_content = (
                    before
                    + record_section
                    + "\n".join(records_lines)
                    + "\n" + after
                )
                ENGINEERING.write_text(new_content, encoding="utf-8")
                print(f"✅ {len(changes)} 条变更记录已添加")
            else:
                print("⚠️ 未找到变更记录表格，请在文档末尾手工添加")

    # ── 最终提示 ──
    print("\n" + "=" * 50)
    print("💡 提示: 表格中的「说明」列标记为 TBD，请手动编辑补充具体描述。")
    print("   文件: engineering.md")
    print("=" * 50)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    print("🧠 工程手册交互式更新")
    print("=" * 50)

    if not ENGINEERING.exists():
        print(f"❌ 未找到 engineering.md（当前目录: {Path.cwd()}）")
        sys.exit(1)

    # 1. 扫描
    print("\n🔍 扫描 src/ 模块...")
    scanned = scan_all()
    total_files = len(scanned)
    total_items = sum(len(v) for v in scanned.values())
    print(f"   共 {total_files} 文件, {total_items} 个类/函数")

    # 2. 手动比对（解析现有文档 vs 扫描结果）
    current_content = ENGINEERING.read_text(encoding="utf-8")
    current_maps = parse_current_maps(current_content)
    diff = compute_diff(current_maps, scanned)

    # 3. 交互式更新
    update_engineering(diff, scanned)

    sys.exit(0)


if __name__ == "__main__":
    main()
