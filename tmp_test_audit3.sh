#!/bin/bash
TEST_DIR="/mnt/d/HermesProject/scripts/drawio-generator/tests"

echo "=== 1. 测试文件清单 + 行数 + 测试数 ==="
for f in "$TEST_DIR"/test_*.py; do
    fname=$(basename "$f")
    lines=$(wc -l < "$f")
    count=$(grep -c 'def test_' "$f" 2>/dev/null || echo 0)
    echo "$fname | ${lines}行 | ${count}测试"
done

echo ""
echo "=== 2. 测试分类（核心模块 vs 辅助脚本）==="
echo "--- 核心模块测试 ---"
for f in test_render.py test_layout.py test_containers.py test_diagram_presets.py test_edge_styles.py test_validation.py test_graphviz_layout.py test_regression_bugs.py test_aiicons.py test_legend.py test_shape_library.py; do
    if [[ -f "$TEST_DIR/$f" ]]; then
        count=$(grep -c 'def test_' "$TEST_DIR/$f" 2>/dev/null || echo 0)
        echo "  $f — $count tests"
    fi
done
echo "--- 辅助脚本测试 ---"
for f in test_buildup.py test_drawiohtml.py test_heatmap.py test_restyle.py test_svgflow.py; do
    if [[ -f "$TEST_DIR/$f" ]]; then
        count=$(grep -c 'def test_' "$TEST_DIR/$f" 2>/dev/null || echo 0)
        echo "  $f — $count tests"
    fi
done

echo ""
echo "=== 3. 检查是否有重复的测试函数名 ==="
grep -h 'def test_' "$TEST_DIR"/test_*.py | sort | uniq -d

echo ""
echo "=== 4. conftest.py 内容 ==="
cat "$TEST_DIR/conftest.py" 2>/dev/null || echo "NO conftest.py"

echo ""
echo "=== 5. __init__.py ==="
cat "$TEST_DIR/__init__.py" 2>/dev/null || echo "NO __init__.py"

echo ""
echo "=== 6. 运行完整测试套件统计 ==="
cd /mnt/d/HermesProject/scripts/drawio-generator && python -m pytest tests/ --co -q 2>/dev/null | tail -5
