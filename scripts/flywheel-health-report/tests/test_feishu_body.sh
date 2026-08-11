#!/bin/bash
# 端到端验证 flywheel-health-report.sh 中飞书通知 BODY 构造逻辑
# 用法: bash test_feishu_body.sh
# 不依赖 lark-cli，只验证 BODY 字符串内容

set -euo pipefail

REPORT_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test_report_sample.md"
TODAY_CN="2026-08-10"
STEP_RESULTS=("✅ Runner 阶段 0" "✅ 报告生成（无 P0）" "✅ Auto-Tuner")

# ===== 从 flywheel-health-report.sh 复制的 BODY 构造逻辑 =====
SUMMARY_JSON=$(python3 - "$REPORT_FILE" <<'PY' 2>/dev/null || echo '{}'
import json, re, sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    print("{}"); sys.exit(0)
text = path.read_text(encoding="utf-8")

def section_body(md, header):
    m = re.search(rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    return m.group(1) if m else ""

def count_int(s, pat):
    m = re.search(pat, s)
    return int(m.group(1)) if m else 0

overview = section_body(text, "概览")
p0_count = count_int(overview, r"P0 问题:\s*\*\*(\d+)\*\*")
p1_count = count_int(overview, r"P1 问题:\s*\*\*(\d+)\*\*")

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

recs_body = section_body(text, "💡 优化方向")
rec_count = len(re.findall(r"^- \*\*", recs_body, re.M))

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

print(json.dumps({
    "p0_count": p0_count, "p1_count": p1_count,
    "success_n": success_n, "fail_n": fail_n, "skip_n": skip_n,
    "rec_count": rec_count,
    "p0_issues": p0_issues, "p1_issues": p1_issues,
}, ensure_ascii=False))
PY
)

P0_COUNT=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('p0_count',0))" "$SUMMARY_JSON" 2>/dev/null || echo 0)
P1_COUNT=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('p1_count',0))" "$SUMMARY_JSON" 2>/dev/null || echo 0)
SUCCESS_N=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('success_n',0))" "$SUMMARY_JSON" 2>/dev/null || echo 0)
FAIL_N=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('fail_n',0))" "$SUMMARY_JSON" 2>/dev/null || echo 0)
SKIP_N=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('skip_n',0))" "$SUMMARY_JSON" 2>/dev/null || echo 0)
REC_COUNT=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('rec_count',0))" "$SUMMARY_JSON" 2>/dev/null || echo 0)

BODY=""
BODY=$(printf '%b' "${BODY}📊 概览")
BODY=$(printf '%b' "${BODY}\n· P0: ${P0_COUNT} | P1: ${P1_COUNT}")
BODY=$(printf '%b' "${BODY}\n· 任务: ✅${SUCCESS_N} ❌${FAIL_N} ⚪${SKIP_N}")
BODY=$(printf '%b' "${BODY}\n· 优化建议: ${REC_COUNT} 条")

if [[ "$P0_COUNT" -gt 0 ]]; then
    P0_LINES=$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
for it in d.get('p0_issues', []):
    print(f\"· {it['fw']}: {it['desc']}\")
" "$SUMMARY_JSON" 2>/dev/null || echo "")
    [[ -n "$P0_LINES" ]] && BODY=$(printf '%b' "${BODY}\n\n🔴 P0\n${P0_LINES}")
fi

if [[ "$P1_COUNT" -gt 0 ]]; then
    P1_LINES=$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
for it in d.get('p1_issues', []):
    print(f\"· {it['fw']}: {it['desc']}\")
" "$SUMMARY_JSON" 2>/dev/null || echo "")
    [[ -n "$P1_LINES" ]] && BODY=$(printf '%b' "${BODY}\n\n🟡 P1\n${P1_LINES}")
fi

BODY=$(printf '%b' "${BODY}\n\n📄 完整报告: ${REPORT_FILE}")

for r in "${STEP_RESULTS[@]:-}"; do
    if [[ "$r" == *"Auto-Tuner"* ]]; then
        BODY=$(printf '%b' "${BODY}\n🔧 ${r}")
        break
    fi
done

echo "===== 飞书通知 BODY（bash 端到端） ====="
printf '%b\n' "$BODY"
echo "===== END ====="

# 断言
[[ "$P0_COUNT" == "1" ]] || { echo "FAIL: P0_COUNT=$P0_COUNT"; exit 1; }
[[ "$P1_COUNT" == "2" ]] || { echo "FAIL: P1_COUNT=$P1_COUNT"; exit 1; }
[[ "$SUCCESS_N" == "2" ]] || { echo "FAIL: SUCCESS_N=$SUCCESS_N"; exit 1; }
[[ "$FAIL_N" == "1" ]] || { echo "FAIL: FAIL_N=$FAIL_N"; exit 1; }
[[ "$SKIP_N" == "1" ]] || { echo "FAIL: SKIP_N=$SKIP_N"; exit 1; }
[[ "$REC_COUNT" == "3" ]] || { echo "FAIL: REC_COUNT=$REC_COUNT"; exit 1; }

case "$BODY" in
    *"📊 概览"*) echo "✅ 包含概览段" ;;
    *) echo "FAIL: 缺少概览段"; exit 1 ;;
esac
case "$BODY" in
    *"P0: 1 | P1: 2"*) echo "✅ P0/P1 数量正确" ;;
    *) echo "FAIL: P0/P1 数量错误"; exit 1 ;;
esac
case "$BODY" in
    *"✅2 ❌1 ⚪1"*) echo "✅ 任务可靠性统计正确" ;;
    *) echo "FAIL: 任务可靠性统计错误"; exit 1 ;;
esac
case "$BODY" in
    *"优化建议: 3 条"*) echo "✅ 优化建议数量正确" ;;
    *) echo "FAIL: 优化建议数量错误"; exit 1 ;;
esac
case "$BODY" in
    *"🔴 P0"*) echo "✅ 包含 P0 段" ;;
    *) echo "FAIL: 缺少 P0 段"; exit 1 ;;
esac
case "$BODY" in
    *"🟡 P1"*) echo "✅ 包含 P1 段" ;;
    *) echo "FAIL: 缺少 P1 段"; exit 1 ;;
esac
case "$BODY" in
    *"📄 完整报告:"*) echo "✅ 包含报告路径" ;;
    *) echo "FAIL: 缺少报告路径"; exit 1 ;;
esac
case "$BODY" in
    *"🔧 ✅ Auto-Tuner"*) echo "✅ 包含 Auto-Tuner 结果" ;;
    *) echo "FAIL: 缺少 Auto-Tuner 结果"; exit 1 ;;
esac
case "$BODY" in
    *"错误率 35%"*) echo "✅ P0 问题内容正确" ;;
    *) echo "FAIL: P0 问题内容错误"; exit 1 ;;
esac

echo ""
echo "🟢 bash 端到端测试通过 — 飞书通知 BODY 构造正确"
