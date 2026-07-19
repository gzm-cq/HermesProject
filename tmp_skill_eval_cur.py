import json
import os

# 当前保存的 prev (其实是今天的 cur，已被覆盖)
cur_path = "/root/.hermes/data/flywheel/skill_eval_prev.json"
with open(cur_path, "r", encoding="utf-8") as f:
    cur = json.load(f)

print("=== 当前 skill_eval_prev.json (实为今天 cur) ===")
print("meta:", json.dumps(cur.get("meta", {}), indent=2, ensure_ascii=False))
print()
print("top-level keys:", list(cur.keys()))
