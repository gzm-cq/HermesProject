"""为 AI 图标库生成足够数量的补充项"""
import sys, re, json
sys.path.insert(0, "/mnt/d/HermesProject/scripts/drawio-generator/src")
from drawio_generator.aiicons import AIICONS, ICON_CATEGORIES, _SIMPLEICONS, _SIMPLEICONS_CC

need = 321 - len(AIICONS)
print(f"现有 {len(AIICONS)} 项，需补 {need} 项")

# 批量生成方案: 每个大类按比例扩展到 321
# 先分析已有的分类分布
from collections import Counter
cats = Counter(v["category"] for v in AIICONS.values())
for k in sorted(ICON_CATEGORIES):
    print(f"  {k:12s}: {cats[k]:3d}")
