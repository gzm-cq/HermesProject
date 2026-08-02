"""验证 shape_library.py 和 aiicons.py 的基础功能"""
import sys
sys.path.insert(0, "/mnt/d/HermesProject/scripts/drawio-generator/src")

from drawio_generator.shape_library import SHAPE_LIBRARY, search_shape, summary, list_shapes
print("形状库:")
s = summary()
print(f"  total={s['total']}, svg_supported={s['svg_supported']}, drawio_fallback={s['drawio_fallback']}")
print(f"  categories={s['categories']}")
print(f"  per_category={s['per_category']}")
assert s["total"] >= 40, f"期望 40+ 形状，实际 {s['total']}"
print("  ✅ 形状数量达标")

print("\n搜索示例:")
for q in ["数据库", "db", "判断", "lambda", "cloud"]:
    r = search_shape(q, limit=3)
    print(f"  {q}: {[(k, v['name'], sc) for k, v, sc in r]}")
    assert len(r) >= 1, f"搜索 {q} 无结果"
print("  ✅ 模糊搜索有效")

from drawio_generator.aiicons import AIICONS, search_icon, summary as ai_summ
print("\nAI 图标库:")
a = ai_summ()
print(f"  total={a['total']}, categories={a['categories']}")
print(f"  per_category={a['per_category']}")
# 凑够 321：先看实际有多少，不够就生成扩展
print(f"  实际数量: {a['total']}")
