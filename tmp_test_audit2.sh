#!/bin/bash
echo "=== drawio-generator 项目结构 ==="
find /mnt/d/HermesProject/scripts/drawio-generator -type f 2>/dev/null | head -50
echo ""
echo "=== tests 目录位置 ==="
find /mnt/d/HermesProject -path '*/tests/*' -name 'test_*.py' 2>/dev/null | head -30
echo ""
echo "=== 项目根目录查找 ==="
ls -la /mnt/d/HermesProject/scripts/drawio-generator/ 2>/dev/null
echo ""
echo "=== 查找 drawio 相关测试 ==="
find /mnt/d/HermesProject -name 'test_*.py' -path '*drawio*' 2>/dev/null
find /mnt/d/HermesProject -name 'test_*.py' -path '*diagram*' 2>/dev/null
find /mnt/d/HermesProject -name 'test_*.py' -path '*generator*' 2>/dev/null
