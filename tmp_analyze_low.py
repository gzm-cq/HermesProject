"""深入分析低分查询：区分功能问题 vs 查询问题。"""
import json
from pathlib import Path

baseline_path = Path("/root/.hermes/plugins/knowledge-navigation/baselines/baseline_latest.json")
with open(baseline_path) as f:
    baseline = json.load(f)

# 分类低分查询
zero_score = [(qid, d) for qid, d in baseline.items() if d.get("avg_score", 0) == 0]

# 关键区分：total=0 vs total>0
no_recall = [(qid, d) for qid, d in zero_score if d.get("avg_total_results", 0) == 0]
recall_but_filtered = [(qid, d) for qid, d in zero_score if d.get("avg_total_results", 0) > 0]

print(f"=== 零分查询分类 ({len(zero_score)} 条) ===")
print(f"  A. 完全无召回 (total=0): {len(no_recall)} 条 → 查询问题")
print(f"  B. 有召回但全被过滤 (total>0, kept=0): {len(recall_but_filtered)} 条 → 功能问题")

print(f"\n=== A. 完全无召回 (查询问题) ===")
for qid, d in no_recall:
    dim = d.get("dimension", "?")
    print(f"  [{dim}] '{qid[:50]}'")

print(f"\n=== B. 有召回但全被过滤 (功能问题) ===")
print(f"{'查询':<40} {'total':>6} {'HS':>4} {'KT':>4} {'SAG':>4} {'主要来源':>8}")
print("-" * 70)
for qid, d in recall_but_filtered:
    dim = d.get("dimension", "?")
    total = d.get("avg_total_results", 0)
    hs = d.get("avg_hs_kept", 0)
    kt = d.get("avg_kt_kept", 0)
    sag = d.get("avg_sag_kept", 0)
    # 判断主要召回来源
    # 从 per_source 数据分析
    per_source = d.get("per_source", {})
    sources = []
    for src in ["hindsight", "knowledge_tree", "skill", "sag"]:
        info = per_source.get(src, {})
        if info and info.get("count", 0) > 0:
            sources.append(f"{src[:2]}:{info['count']}")
    src_str = " ".join(sources) if sources else "?"
    print(f"  [{dim}] '{qid[:35]}' {total:>6.0f} {hs:>4.0f} {kt:>4.0f} {sag:>4.0f}  {src_str}")

# 检查基线采集脚本是否使用 eval_queries
print(f"\n=== 检查基线采集数据来源 ===")
# 看看 trace.log 中的查询是否来自真实对话
print("基线查询来自 trace.log 中的真实用户对话")
print("eval_queries.json 中的 105 条测试查询未被基线采集使用")

# 检查 collect_baseline.py 是否支持 eval 模式
import subprocess
result = subprocess.run(
    ["python3", "scripts/collect_baseline.py", "--help"],
    capture_output=True, text=True, cwd="/root/.hermes/plugins/knowledge-navigation"
)
print(f"\ncollect_baseline.py 帮助:")
print(result.stdout[:500] if result.stdout else result.stderr[:500])

# 分析分数分布
print(f"\n=== 全基线分数分布 ===")
scores = [d.get("avg_score", 0) for d in baseline.values()]
buckets = {"0": 0, "0-0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7+": 0}
for s in scores:
    if s == 0:
        buckets["0"] += 1
    elif s < 0.3:
        buckets["0-0.3"] += 1
    elif s < 0.5:
        buckets["0.3-0.5"] += 1
    elif s < 0.7:
        buckets["0.5-0.7"] += 1
    else:
        buckets["0.7+"] += 1
print(f"  score=0:    {buckets['0']:>4} ({buckets['0']*100//len(scores)}%)")
print(f"  0-0.3:      {buckets['0-0.3']:>4} ({buckets['0-0.3']*100//len(scores)}%)")
print(f"  0.3-0.5:    {buckets['0.3-0.5']:>4} ({buckets['0.3-0.5']*100//len(scores)}%)")
print(f"  0.5-0.7:    {buckets['0.5-0.7']:>4} ({buckets['0.5-0.7']*100//len(scores)}%)")
print(f"  0.7+:       {buckets['0.7+']:>4} ({buckets['0.7+']*100//len(scores)}%)")
print(f"  总计:       {len(scores):>4}")
