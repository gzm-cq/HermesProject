"""扫描 self-evolving/output 下所有 output JSON 的 refined_content 是否含元叙事污染。
用法: python _scan_quality.py [output_dir]
"""
import json
import sys
import glob
import os

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "/root/.hermes/self-evolving/output"

# 元叙事/第一人称污染关键词（命中即视为低质量写回）
BANNED = [
    "我需要", "我必须", "我们需要", "我们", "之前的声明有误",
    "任务执行成功", "ASYNC DELEGATION", "BATCH COMPLETE", "让我重新",
    "本 agent", "本助手", "作为 agent", "我会", "我发现",
    "Let me", "I need to", "we should", "the agent",
]

def main():
    files = sorted(glob.glob(os.path.join(OUT_DIR, "*.json")))
    total = 0
    clean = 0
    dirty = 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"  [SKIP] {os.path.basename(f)}: parse error {e}")
            continue
        rc = d.get("refined_content") or d.get("revised_content") or ""
        if not isinstance(rc, str):
            rc = json.dumps(rc, ensure_ascii=False)
        skill = d.get("skill", "?")
        tid = d.get("task_id", "?")
        applied = d.get("auto_applied")
        total += 1
        hits = [w for w in BANNED if w in rc]
        if hits:
            dirty += 1
            print(f"  [DIRTY] {skill}/{tid} applied={applied} hits={hits}")
            print(f"          >>> {rc[:160]}")
        else:
            clean += 1
    print(f"\n=== 扫描完成: total={total} clean={clean} dirty={dirty} ===")

if __name__ == "__main__":
    main()
