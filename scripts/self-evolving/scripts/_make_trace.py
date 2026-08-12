"""切分 skillopt state.json 的 failed_tasks：
- /tmp/se_trace_kn.json   : 仅 knowledge-navigation
- /tmp/se_trace_rest.json : 除 knowledge-navigation 外的全部
用法: python _make_trace.py
"""
import json

STATE = "/root/.hermes/skillopt-runner/state.json"
KN = "knowledge-navigation"
OUT_KN = "/tmp/se_trace_kn.json"
OUT_REST = "/tmp/se_trace_rest.json"

with open(STATE, "r", encoding="utf-8") as f:
    state = json.load(f)

ft = state.get("failed_tasks") or {}
flat = []
for skill, tasks in ft.items():
    if not isinstance(tasks, list):
        continue
    for t in tasks:
        if isinstance(t, dict):
            t = dict(t)
            t.setdefault("skill", skill)
            flat.append(t)

kn = [t for t in flat if t.get("skill") == KN]
rest = [t for t in flat if t.get("skill") != KN]

with open(OUT_KN, "w", encoding="utf-8") as f:
    json.dump({"failed_tasks": kn}, f, ensure_ascii=False, indent=2)
with open(OUT_REST, "w", encoding="utf-8") as f:
    json.dump({"failed_tasks": rest}, f, ensure_ascii=False, indent=2)

print(f"total={len(flat)} kn={len(kn)} rest={len(rest)}")
print(f"wrote {OUT_KN}")
print(f"wrote {OUT_REST}")
