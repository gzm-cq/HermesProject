"""验证飞书通知摘要提取逻辑（从 markdown 报告提取 P0/P1/任务可靠性/优化建议）。"""
import json
import re
import sys
from pathlib import Path

SAMPLE = Path(__file__).parent / "test_report_sample.md"
text = SAMPLE.read_text(encoding="utf-8")


def section_body(md, header):
    """提取 ## header 到下一个 ## 之间的正文。"""
    m = re.search(rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    return m.group(1) if m else ""


def count_int(s, pat):
    m = re.search(pat, s)
    return int(m.group(1)) if m else 0


# 1. 概览数字
overview = section_body(text, "概览")
p0_count = count_int(overview, r"P0 问题:\s*\*\*(\d+)\*\*")
p1_count = count_int(overview, r"P1 问题:\s*\*\*(\d+)\*\*")
assert p0_count == 1, f"P0 数量应为 1，实际 {p0_count}"
assert p1_count == 2, f"P1 数量应为 2，实际 {p1_count}"
print(f"✅ 概览提取：P0={p0_count}, P1={p1_count}")

# 2. 任务可靠性统计
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
assert success_n == 2, f"成功数应为 2，实际 {success_n}"
assert fail_n == 1, f"失败数应为 1，实际 {fail_n}"
assert skip_n == 1, f"跳过数应为 1，实际 {skip_n}"
print(f"✅ 任务可靠性：✅{success_n} ❌{fail_n} ⚪{skip_n}")

# 3. 优化建议数量
recs_body = section_body(text, "💡 优化方向")
rec_count = len(re.findall(r"^- \*\*", recs_body, re.M))
assert rec_count == 3, f"优化建议应为 3 条，实际 {rec_count}"
print(f"✅ 优化建议：{rec_count} 条")

# 4. P0/P1 问题列表
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
assert len(p0_issues) == 1, f"P0 问题应有 1 条，实际 {len(p0_issues)}"
assert p0_issues[0]["fw"] == "Router"
assert "错误率" in p0_issues[0]["desc"]
assert len(p1_issues) == 3, f"P1 问题应有 3 条（top 5），实际 {len(p1_issues)}"
print(f"✅ P0/P1 列表：P0={len(p0_issues)}条, P1={len(p1_issues)}条")
for it in p0_issues + p1_issues:
    print(f"   · {it['fw']}: {it['desc']}")

# 5. 模拟 BODY 构造（与 bash 脚本一致）
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
BODY += "\n\n📄 完整报告: /root/.hermes/logs/reports/flywheel-report-2026-08-10.md"

print("\n===== 模拟飞书通知 BODY =====")
print(BODY)
print("===== END =====")

# 验证 BODY 包含关键信息
assert "📊 概览" in BODY
assert "P0: 1 | P1: 2" in BODY
assert "✅2 ❌1 ⚪1" in BODY
assert "优化建议: 3 条" in BODY
assert "🔴 P0" in BODY
assert "🟡 P1" in BODY
assert "📄 完整报告:" in BODY
assert "错误率" in BODY
assert "全关率" in BODY
print("\n🟢 全部单测通过 — 飞书通知摘要提取逻辑正确")
