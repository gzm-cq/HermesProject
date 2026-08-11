#!/usr/bin/env python3
"""test_feishu_live.py — 使用真实的 2026-08-09 报告验证飞书通知逻辑。"""
import json
import re
import sys
from pathlib import Path

REPORT_FILE = Path("/root/.hermes/logs/reports/flywheel-report-2026-08-09.md")

if not REPORT_FILE.is_file():
    print(f"Error: Report file not found: {REPORT_FILE}")
    sys.exit(1)

text = REPORT_FILE.read_text(encoding="utf-8")

def section_body(md, header):
    m = re.search(rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    return m.group(1) if m else ""

def count_int(s, pat):
    m = re.search(pat, s)
    return int(m.group(1)) if m else 0

# 1. 概览
overview = section_body(text, "概览")
p0_count = count_int(overview, r"P0 问题:\s*\*\*(\d+)\*\*")
p1_count = count_int(overview, r"P1 问题:\s*\*\*(\d+)\*\*")

# 2. 任务可靠性
cron_tbl = section_body(text, "📊 任务可靠性")
success_n = fail_n = skip_n = 0
for line in cron_tbl.splitlines():
    if not line.startswith("|") or "----" in line or "任务" in line:
        continue
    cells = [c.strip() for c in line.split("|")]
    if len(cells) >= 4:
        status_cell = cells[3]
        if "✅" in status_cell: success_n += 1
        elif "❌" in status_cell: fail_n += 1
        elif "⚪" in status_cell: skip_n += 1

# 3. 优化建议
recs_body = section_body(text, "💡 优化方向")
rec_count = len(re.findall(r"^- \*\*", recs_body, re.M))

# 4. P0/P1 列表
def extract_issues(header):
    body = section_body(text, header)
    out = []
    for line in body.splitlines():
        if not line.startswith("|") or "----" in line or "飞轮" in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 4:
            fw = cells[1]
            desc = cells[2]
            desc = re.sub(r"\*\*([^*]+)\*\*", r"\1", desc)
            if len(desc) > 60: desc = desc[:60] + "…"
            if fw: out.append({"fw": fw, "desc": desc})
    return out[:5]

p0_issues = extract_issues("🔴 P0 - 需要立即处理")
p1_issues = extract_issues("🟡 P1 - 需要关注")

# 5. 构造 BODY
BODY = "📊 概览"
BODY += f"\n· P0: {p0_count} | P1: {p1_count}"
BODY += f"\n· 任务: ✅{success_n} ❌{fail_n} ⚪{skip_n}"
BODY += f"\n· 优化建议: {rec_count} 条"

if p0_count > 0:
    BODY += "\n\n🔴 P0"
    for it in p0_issues:
        BODY += f"\n· {it['fw']}: {it['desc']}"
if p1_count > 0:
    BODY += "\n\n🟡 P1"
    for it in p1_issues:
        BODY += f"\n· {it['fw']}: {it['desc']}"

BODY += f"\n\n📄 完整报告: {REPORT_FILE}"

print("=" * 60)
print("  真实报告 Dry-Run：飞书通知预览 (2026-08-09)")
print("=" * 60)
print(BODY)
print("=" * 60)

# 关键断言
assert p0_count == 0, f"真实报告 08-09 应有 0 个 P0，实际 {p0_count}"
# 根据实际报告内容验证
assert success_n > 0, "应有成功任务"
print("✅ 断言通过：P0=0, 成功任务>0")
