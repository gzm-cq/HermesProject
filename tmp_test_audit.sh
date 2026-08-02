#!/bin/bash
echo "=== 测试目录结构 ==="
find /root/.hermes/scripts/drawio-generator/tests -type f -name '*.py' 2>/dev/null
echo ""
echo "=== 各文件行数 ==="
wc -l /root/.hermes/scripts/drawio-generator/tests/*.py 2>/dev/null
echo ""
echo "=== 各文件 pytest 测试数 ==="
for f in /root/.hermes/scripts/drawio-generator/tests/test_*.py; do
    count=$(grep -c 'def test_' "$f" 2>/dev/null || echo 0)
    echo "$count tests — $(basename $f)"
done
echo ""
echo "=== 测试目录中是否有非 test_ 前缀的文件 ==="
ls /root/.hermes/scripts/drawio-generator/tests/ | grep -v '^test_' | grep -v '__pycache__'
echo ""
echo "=== conftest.py 是否存在 ==="
cat /root/.hermes/scripts/drawio-generator/tests/conftest.py 2>/dev/null || echo "NO conftest.py"
