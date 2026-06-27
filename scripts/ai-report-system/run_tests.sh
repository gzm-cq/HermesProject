#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 三层测试框架 — 一键全测
# ═══════════════════════════════════════════════════════════════
#
# 使用:
#   bash run_tests.sh           全部测试
#   bash run_tests.sh unit      仅单元层（纯函数，<3s）
#   bash run_tests.sh integration 仅集成层（mock LLM，<5s）
#   bash run_tests.sh script    仅脚本层（mock 管线，<5s）
#   bash run_tests.sh coverage  全部 + 覆盖率报告
#
# 需要: pip install pytest coverage  (extra_requires: dev)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

cd "$(dirname "$0")"

MODE="${1:-all}"
PYTEST_ARGS="-q --tb=short --no-header"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  报告系统测试套件                                     ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

case "$MODE" in
  unit)
    echo "▸ 单元层 — 纯函数测试（无 LLM 无 I/O）"
    echo "  tests/graph/test_report_graph.py"
    echo "  tests/scripts/test_scripts.py"
    echo ""
    python3 -m pytest tests/graph/test_report_graph.py tests/scripts/test_scripts.py $PYTEST_ARGS
    echo ""
    echo "✅ 单元层完成"
    ;;

  integration)
    echo "▸ 集成层 — mock LLM 验证 StateGraph 节点数据流"
    echo "  tests/graph/test_integration.py"
    echo ""
    python3 -m pytest tests/graph/test_integration.py $PYTEST_ARGS
    echo ""
    echo "✅ 集成层完成"
    ;;

  script)
    echo "▸ 脚本层 — mock 工具脚本逻辑"
    echo "  tests/scripts/test_scripts.py"
    echo ""
    python3 -m pytest tests/scripts/test_scripts.py $PYTEST_ARGS
    echo ""
    echo "✅ 脚本层完成"
    ;;

  coverage)
    echo "▸ 全测试 + 覆盖率报告"
    echo ""
    python3 -m coverage run --source=src --omit="*/__pycache__/*" \
      -m pytest tests/ $PYTEST_ARGS
    echo ""
    python3 -m coverage report -m --sort=-cover --skip-covered \
      --omit="*/test_*,*/conftest.py,*/__init__.py"
    echo ""
    echo "✅ 覆盖率报告完成"
    ;;

  all|*)
    echo "▸ 三层全测"
    echo ""
    echo "─── 单元层（纯函数） ───"
    python3 -m pytest tests/graph/test_report_graph.py tests/scripts/test_scripts.py $PYTEST_ARGS
    echo ""
    echo "─── 集成层（mock LLM）───"
    python3 -m pytest tests/graph/test_integration.py $PYTEST_ARGS
    echo ""
    echo "─── 遗留测试（旧架构）───"
    # 跳过 graph 和 scripts（已独立测试），只跑其余
    python3 -m pytest tests/ \
      --ignore=tests/graph \
      --ignore=tests/scripts \
      $PYTEST_ARGS
    echo ""
    # 汇总
    TOTAL=$(python3 -m pytest tests/ $PYTEST_ARGS 2>&1 | tail -1 || true)
    echo "──────────────────────────────────────────────"
    echo "✅ 全量结果: $TOTAL"
    echo "──────────────────────────────────────────────"
    ;;
esac
