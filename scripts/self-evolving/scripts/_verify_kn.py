import json, glob, os, re, sys

OUT = "/root/.hermes/self-evolving/output"
BAD = ["我需要", "之前的声明有误", "任务执行成功", "ASYNC DELEGATION",
       "我们", "本 agent", "之前的修复", "重新检查测试", "已按建议全面修复"]

files = sorted(glob.glob(os.path.join(OUT, "knowledge-navigation_task_*.json")))
print(f"KN output 文件数: {len(files)}")
all_clean = True
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    skill = d.get("skill")
    tid = d.get("task_id")
    rc = d.get("revised_content", "") or ""
    rf = d.get("refined_content", "") or ""
    hit = [b for b in BAD if (b in rc) or (b in rf)]
    applied = d.get("auto_applied")
    status = "CLEAN" if not hit else f"DIRTY={hit}"
    if hit:
        all_clean = False
    print(f"  [{status}] {tid} applied={applied} rlen={len(rc)}")
    if hit:
        print(f"      >>> {rf[:200]}")

print("\n总判定:", "ALL_CLEAN ✅" if all_clean else "HAS_DIRTY ❌")
sys.exit(0 if all_clean else 1)
