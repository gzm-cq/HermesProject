import json, re, os, shutil

TRACE = "/root/.hermes/plugins/knowledge-navigation/trace.log"
BAK_DIR = "/root/.hermes/backups/trace-cleanup-2026-08-16"
os.makedirs(BAK_DIR, exist_ok=True)
bak = os.path.join(BAK_DIR, "trace.log.bak")
shutil.copy2(TRACE, bak)

# Session 1 & 2: 整个 session 删除（共 10 条调试副产物）
DROP_SESSIONS = {
    "controlled-empty-test-1786849831",  # session1: 3 条（mask 全关 -> skip）
    "controlled-empty-test-1786849890",  # session2: 7 条（mask 强开，recall 仍返回结果）
}
# Session 3: 只保留唯一有效的 recall_empty_results，删掉其余 5 条调试副产物
SESSION3 = "controlled-empty-test-1786849945"
KEEP_EVENT_SESSION3 = {"recall_empty_results"}

def parse(line):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        m = re.search(r"\{.*\}", line)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None

with open(TRACE, encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

out_raw = []
dropped = 0
kept_s3 = 0
for line in lines:
    rec = parse(line)
    if rec is None:
        out_raw.append(line)  # 无法解析的行原样保留，绝不误删
        continue
    sid = rec.get("session_id", "")
    ev = rec.get("event", "")
    if sid in DROP_SESSIONS:
        dropped += 1
        continue
    if sid == SESSION3:
        if ev in KEEP_EVENT_SESSION3:
            out_raw.append(line)
            kept_s3 += 1
        else:
            dropped += 1
        continue
    out_raw.append(line)  # 其余（含 38 条历史 fixture）全部保留

# 原地改写，保留 inode（gateway 仍持有 fd 6，不能换文件）
with open(TRACE, "r+", encoding="utf-8") as f:
    f.seek(0)
    f.truncate(0)
    f.writelines(out_raw)
    f.flush()

print(f"backup -> {bak}")
print(f"dropped = {dropped}  (session1+2: 10, session3 debug: 5)")
print(f"session3 kept valid evidence = {kept_s3}")
print(f"kept lines total = {len(out_raw)}")
