#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 报告生成调度脚本 — 三步走，每步等你确认
# ═══════════════════════════════════════════════════════════════
#
# 用法:
#   bash scripts/run_report.sh "报告主题"
#
# 流程:
#   1. 提取目标 → 展示让你确认
#   2. 并行跑 extract_facts + pre_search
#   3. 检查冲突 → 让你处理
#   4. 全部就绪 → 启动管线
#
# 前置条件：
#   - 素材已放在 reports/<报告主题>/inputs/ 下
#   - Python 环境已配好
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── 参数 ──
TOPIC="${1:-}"
REPORT_DIR="reports/${TOPIC}"
INPUTS_DIR="${REPORT_DIR}/inputs"
GOAL_FILE="${REPORT_DIR}/report_goal.json"
FACT_BANK="${REPORT_DIR}/fact_bank.json"

if [ -z "$TOPIC" ]; then
  echo -e "${RED}❌ 用法: bash scripts/run_report.sh \"报告主题\"${NC}"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo -e "${BLUE}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  报告生成调度脚本                                 ║${NC}"
echo -e "${BLUE}║  主题: ${TOPIC}${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 0: 检查素材 ────────────────────────────────────
echo -e "${YELLOW}[0/4] 检查素材...${NC}"
if [ ! -d "$INPUTS_DIR" ] || [ -z "$(ls -A "$INPUTS_DIR" 2>/dev/null)" ]; then
  echo -e "${RED}❌ 素材目录为空: ${INPUTS_DIR}${NC}"
  echo "   请先将源素材文件放入该目录"
  exit 1
fi

FILE_COUNT=$(ls -1 "$INPUTS_DIR" | wc -l)
echo -e "${GREEN}  ✅ 素材目录: ${INPUTS_DIR} (${FILE_COUNT} 个文件)${NC}"
echo ""

# ── Step 1: 提取目标 → 确认 ────────────────────────────
echo -e "${YELLOW}[1/4] 提取报告目标...${NC}"

# 如果已有目标文件，先展示
if [ -f "$GOAL_FILE" ]; then
  echo -e "  📄 已有目标文件: ${GOAL_FILE}"
  python3 -c "
import json
g = json.load(open('${GOAL_FILE}'))
print(f'  标题: {g.get(\"title\",\"?\")}')
p = g.get('purpose','')
print(f'  目的: {p[:120]}...' if len(p)>120 else f'  目的: {p}')
print(f'  读者: {g.get(\"target_audience\",\"?\")}')
"
  echo ""
  echo -ne "${YELLOW}  复用上次目标？(y/n): ${NC}"
  read -r REUSE
  if [ "$REUSE" != "y" ]; then
    echo "  🔄 重新提取..."
    python3 -c "
import sys; sys.path.insert(0, '.')
from ai_report.graph.report_graph import run_goal_definition
from pathlib import Path

# 读取所有素材合并
inputs_dir = Path('${INPUTS_DIR}')
parts = []
for f in sorted(inputs_dir.iterdir()):
    text = f.read_text(encoding='utf-8', errors='replace')
    parts.append(f'📄 {f.name}\\n{text}')
source = '\\n\\n'.join(parts)

topic = '${TOPIC}'
goal = run_goal_definition(topic, source)
print('✅ 目标已提取')
"
    echo ""
    python3 -c "
import json
g = json.load(open('${GOAL_FILE}'))
print(f'  标题: {g.get(\"title\",\"?\")}')
p = g.get('purpose','')
print(f'  目的: {p[:120]}...' if len(p)>120 else f'  目的: {p}')
print(f'  策略: {g.get(\"overall_strategy\",\"\")[:80]}...')
wr = g.get('writing_role',{})
print(f'  角色: {wr.get(\"role\",\"?\")} | 语调: {wr.get(\"tone\",\"?\")}')
"
  fi
else
  # 首次提取
  echo -e "  🔄 首次提取..."
  python3 -c "
import sys; sys.path.insert(0, '.')
from ai_report.graph.report_graph import run_goal_definition
from pathlib import Path

inputs_dir = Path('${INPUTS_DIR}')
parts = []
for f in sorted(inputs_dir.iterdir()):
    text = f.read_text(encoding='utf-8', errors='replace')
    parts.append(f'📄 {f.name}\\n{text}')
source = '\\n\\n'.join(parts)

goal = run_goal_definition('${TOPIC}', source)
print('✅ 目标已提取')
"
  echo ""
  python3 -c "
import json
g = json.load(open('${GOAL_FILE}'))
print(f'  标题: {g.get(\"title\",\"?\")}')
p = g.get('purpose','')
print(f'  目的: {p[:120]}...' if len(p)>120 else f'  目的: {p}')
print(f'  策略: {g.get(\"overall_strategy\",\"\")[:80]}...')
wr = g.get('writing_role',{})
print(f'  角色: {wr.get(\"role\",\"?\")} | 语调: {wr.get(\"tone\",\"?\")}')
"
fi

# ── 等你确认 ──
echo ""
echo -ne "${YELLOW}  这个目标可以吗？(y=继续, n=退出): ${NC}"
read -r CONFIRM
if [ "$CONFIRM" != "y" ]; then
  echo -e "${RED}❌ 用户取消。请调目标后重试。${NC}"
  exit 1
fi
echo -e "${GREEN}  ✅ 目标已确认${NC}"
echo ""

# ── Step 2: extract_facts ─────────────────────────────
echo -e "${YELLOW}[2/4] 提取事实...${NC}"
echo -e "  📦 启动: extract_facts..."
python3 scripts/extract_facts.py "$TOPIC" &
PID_FACTS=$!
echo -e "     PID: ${PID_FACTS}"
echo -e "  ⏳ 等待 extract_facts 完成..."
wait $PID_FACTS
FACTS_EXIT=$?
if [ $FACTS_EXIT -ne 0 ]; then
  echo -e "${RED}❌ extract_facts 失败 (exit=$FACTS_EXIT)${NC}"
  exit 1
fi
echo -e "${GREEN}  ✅ extract_facts 完成${NC}"
echo ""

# ── Step 3: 检查冲突 ──────────────────────────────────
echo -e "${YELLOW}[3/4] 检查事实冲突...${NC}"

python3 -c "
import json, sys
fb = json.load(open('${FACT_BANK}'))
conflicts = fb.get('conflicts', [])
if conflicts:
    print(f'⚠️  发现 {len(conflicts)} 个冲突:')
    for c in conflicts:
        key = c.get('fact_key', '未知冲突')
        print(f'     [{key}]')
        for v in c.get('variants', []):
            print(f'       - {v.get(\"fact\",\"\")}')
    print()
    print('请手动编辑 ${FACT_BANK}')
    print('将 conflicts 数组中的条目移到 resolved_conflicts，清空 conflicts')
    sys.exit(1)
else:
    print('  ✅ 无未处理冲突')
"

if [ $? -ne 0 ]; then
  echo ""
  echo -ne "${YELLOW}  冲突处理完成后输入 y 继续: ${NC}"
  read -r CONTINUE
  if [ "$CONTINUE" != "y" ]; then
    echo -e "${RED}❌ 用户取消${NC}"
    exit 1
  fi
  # 二次检查
  python3 -c "
import json, sys
fb = json.load(open('${FACT_BANK}'))
if fb.get('conflicts'):
    print('❌ conflicts 仍未清空')
    sys.exit(1)
else:
    print('✅ 冲突已处理')
" || exit 1
fi

echo -e "${GREEN}  ✅ 全部前置就绪${NC}"
echo ""

# ── Step 4: 启动管线 ─────────────────────────────────
echo -e "${GREEN}[4/4] 全部就绪，启动管线...${NC}"
echo ""
echo -e "  📄 report_goal.json  ✅"
echo -e "  📄 fact_bank.json    ✅"
echo -e "  📄 冲突已处理        ✅"
echo ""

echo -e "${BLUE}══════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  启动管线...${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════${NC}"
echo ""

python3 scripts/run_full_pipeline_test.py "$TOPIC"

echo ""
echo -e "${GREEN}✅ 全部完成${NC}"
