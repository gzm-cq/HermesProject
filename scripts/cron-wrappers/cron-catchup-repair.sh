#!/bin/bash
# cron-catchup-repair.sh — Layer 3 修复后追赶
# 人工修复根因后用此脚本触发追赶重跑
#
# 用法：
#   bash cron-catchup-repair.sh                           # 重跑所有 last_status=error 的 job
#   bash cron-catchup-repair.sh --job memory-cleanup      # 只重跑指定 job
#   bash cron-catchup-repair.sh --job memory-cleanup --force  # 强制重跑
#
# 部署路径：/root/.hermes/scripts/cron-catchup-repair.sh

set -euo pipefail

_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh (${_CRON_LIB})" >&2
    exit 2
fi

cron_init "cron-catchup-repair"
CRON_SKIP_FINISH_NOTIFY=true   # 脚本自发送追赶汇总，不让 cron_finish 重复发

# ===== 参数解析 =====
TARGET_JOB=""
FORCE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --job) TARGET_JOB="$2"; shift 2 ;;
        --force) FORCE=true; shift ;;
        *) cron_err "未知参数: $1"; cron_finish; exit 1 ;;
    esac
done

# ===== 获取目标 job 列表 =====
cron_section "获取目标 job 列表"
JOBS_FILE="${HERMES_HOME:-/root/.hermes}/cron/jobs.json"
STATE_DIR="${CRON_STATE_DIR:-/root/.hermes/lib/cron-state}"
JOB_LIST_FILE=$(mktemp /tmp/cron-repair-jobs-XXXXXX.json)
trap "rm -f '$JOB_LIST_FILE'" EXIT

export TARGET_JOB FORCE
python3 <<PY > "$JOB_LIST_FILE"
import json, os

jobs_file = os.path.expanduser("$JOBS_FILE")
state_dir = os.path.expanduser("$STATE_DIR")
target = os.environ.get("TARGET_JOB", "") or None
force = os.environ.get("FORCE", "false").lower() == "true"

def load_jobs():
    with open(jobs_file) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("jobs", [])

def load_state(name):
    p = os.path.join(state_dir, f"{name}.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)

result = []
for job in load_jobs():
    jname = job.get("name", job.get("id", ""))
    jid = job.get("id", "")

    if target and target != jname and target != jid:
        continue
    if not target and not force:
        st = load_state(jname)
        if job.get("last_status") != "error" and not st.get("overall_retries_exhausted", False):
            continue

    result.append({
        "job_name": jname,
        "job_id": jid,
        "script": job.get("script", ""),
        "workdir": job.get("workdir", ""),
        "no_agent": job.get("no_agent", False),
    })

print(json.dumps(result, ensure_ascii=False))
PY

# ===== 执行追赶 =====
TOTAL=$(python3 -c "import json; print(len(json.load(open('$JOB_LIST_FILE'))))")

if [[ "$TOTAL" -eq 0 ]]; then
    cron_ok "没有需要追赶的 job"
    cron_finish
    exit 0
fi

cron_ok "发现 $TOTAL 个 job 需要追赶"
SUCCESS_JOBS=()
FAILED_JOBS=()

for i in $(seq 0 $((TOTAL - 1))); do
    # 读第 i 个 job 信息
    JOB_NAME=$(python3 -c "import json; d=json.load(open('$JOB_LIST_FILE')); print(d[$i]['job_name'])")
    JOB_ID=$(python3 -c "import json; d=json.load(open('$JOB_LIST_FILE')); print(d[$i]['job_id'])")
    JOB_SCRIPT=$(python3 -c "import json; d=json.load(open('$JOB_LIST_FILE')); print(d[$i].get('script',''))")
    JOB_WORKDIR=$(python3 -c "import json; d=json.load(open('$JOB_LIST_FILE')); print(d[$i].get('workdir',''))")
    JOB_NO_AGENT=$(python3 -c "import json; d=json.load(open('$JOB_LIST_FILE')); print(str(d[$i].get('no_agent',False)).lower())")

    cron_section "追赶 ${JOB_NAME}"

    # 方式 1: hermes cron run
    if command -v hermes &>/dev/null; then
        cron_log "执行: hermes cron run ${JOB_ID}"
        if hermes cron run "$JOB_ID" 2>&1; then
            cron_ok "${JOB_NAME} 追赶成功"
            SUCCESS_JOBS+=("✅ $JOB_NAME")
            continue
        else
            rc=$?
            cron_err "${JOB_NAME} 追赶失败 (exit=$rc)"
            FAILED_JOBS+=("❌ $JOB_NAME")
            continue
        fi
    fi

    # 方式 2: 直接执行脚本（仅 no_agent）
    if [[ "$JOB_NO_AGENT" == "true" && -n "$JOB_SCRIPT" ]]; then
        script_path="/root/.hermes/scripts/${JOB_SCRIPT}"
        cd_cmd=""
        [[ -n "$JOB_WORKDIR" ]] && cd_cmd="cd $JOB_WORKDIR && "

        cron_log "执行: ${cd_cmd}bash ${script_path} (catchup mode)"
        # 通过 CRON_MODE 环境变量传递模式（cron_init 已支持读取环境变量作为兜底）
        if CRON_MODE=catchup bash "${script_path}" 2>&1; then
            cron_ok "${JOB_NAME} 追赶成功"
            SUCCESS_JOBS+=("✅ $JOB_NAME")
            continue
        else
            rc=$?
            cron_err "${JOB_NAME} 追赶失败 (exit=$rc)"
            FAILED_JOBS+=("❌ $JOB_NAME")
            continue
        fi
    fi

    cron_err "${JOB_NAME}: 无法执行（既无 hermes CLI 也无脚本路径）"
    FAILED_JOBS+=("❌ $JOB_NAME (无执行方式)")
done

# ===== 汇总 =====
cron_section "追赶汇总"
[[ ${#SUCCESS_JOBS[@]} -gt 0 ]] && printf '  %s\n' "${SUCCESS_JOBS[@]}"
[[ ${#FAILED_JOBS[@]} -gt 0 ]] && printf '  %s\n' "${FAILED_JOBS[@]}"

_subject="[Cron 自愈] 修复追赶完成 — ${#SUCCESS_JOBS[@]} 成功"
[[ ${#FAILED_JOBS[@]} -gt 0 ]] && _subject="${_subject}, ${#FAILED_JOBS[@]} 失败"

_body=""
[[ ${#SUCCESS_JOBS[@]} -gt 0 ]] && _body="${_body}✅ 成功: ${SUCCESS_JOBS[*]}"
[[ ${#FAILED_JOBS[@]} -gt 0 ]] && _body="${_body}${_body:+$'\n'}❌ 失败: ${FAILED_JOBS[*]}"

cron_notify "⚡ $_subject" "$_body" || true
cron_finish
[[ ${#FAILED_JOBS[@]} -gt 0 ]] && exit 1 || exit 0