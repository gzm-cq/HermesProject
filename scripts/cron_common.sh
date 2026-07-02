#!/bin/bash
# cron_common.sh — Hermes 通用 cron 任务公共库
#
# 提供：flock 防重入、统一日志、飞书通知、彩色输出、状态跟踪、
#       追赶模式标记、失败重试、状态文件。
#
# 用法（在 job wrapper 中）：
#   source /root/.hermes/lib/cron_common.sh
#   cron_init "my-job-name" ["normal"|"catchup"]  # 初始化：日志 + flock
#   cron_section "步骤名称"                         # 打印步骤标题
#   cron_ok  "操作完成"                             # 记录成功
#   cron_err "操作失败"                             # 记录失败（自动标记 OVERALL_STATUS）
#   cron_warn "可忽略异常"                          # 记录警告
#   cron_run_step_retry "步骤名" command args...    # 带自动重试的步骤执行
#   cron_notify "标题" "消息正文"                   # 飞书通知（lark-cli → webhook 降级）
#   cron_finish                                     # 汇总 + 状态文件 + 飞书通知 + 退出
#
# 环境变量（可选）：
#   FEISHU_CHAT_ID      飞书群 chat_id（默认 oc_f04a9f65d4b780511cc3f402c7d54ac3）
#   FEISHU_WEBHOOK_URL   飞书 Webhook 地址（lark-cli 不可用时的降级通道）
#   CRON_LOG_DIR         日志目录（默认 /root/.hermes/logs/cron）
#   CRON_LOCK_DIR        锁文件目录（默认 /tmp/hermes-cron-locks）
#   CRON_RETRY_MAX       最大重试次数（默认 2）
#   CRON_RETRY_DELAY     初始重试退避秒数（默认 30）
#   CRON_STATE_DIR       状态文件目录（默认 /root/.hermes/lib/cron-state）
#
# 设计要点：
#   - flock 文件锁：同名任务不可并发，重入直接 exit 0
#   - 日志按日期轮转：CRON_LOG_DIR/<job>-YYYYMMDD.log
#   - 飞书通知双通道：lark-cli 优先，webhook 降级
#   - OVERALL_STATUS 跟踪：success / partial / fail
#   - catchup 模式：日志前缀从 [cron] 改为 ⚡[catchup]
#   - 失败重试：exponential backoff，在 cron_finish 时写入状态文件供 Layer 1 读取

set -euo pipefail

# ===== 颜色 =====
_C_CYA='\033[36m'
_C_GRN='\033[32m'
_C_RED='\033[31m'
_C_YLW='\033[33m'
_C_RST='\033[0m'

# ===== 全局状态 =====
CRON_JOB_NAME=""
CRON_LOG_FILE=""
OVERALL_STATUS="success"
CRON_MODE="normal"
CRON_SKIP_FINISH_NOTIFY=false                   # true 时 cron_finish 不发通知（脚本已自发送）
_STEP_COUNT=0
_STEP_RESULTS=()
_CRON_LOG_PREFIX="${_C_CYA}[cron]${_C_RST}"   # catchup 模式改为 ⚡[catchup]
_CRON_RETRIES_EXHAUSTED=false                   # 是否退避耗尽需人工介入
_CRON_RETRIES_TOTAL=0                           # 本次执行总重试次数

# ===== 初始化 =====
# Usage: cron_init "job-name" ["normal"|"catchup"]
#   normal  — 正常执行（默认）
#   catchup — 追赶执行（日志带 ⚡、飞书通知带 ⚡ 标记）
cron_init() {
    local job_name="$1"
    local mode="${2:-normal}"
    CRON_JOB_NAME="$job_name"
    CRON_MODE="$mode"

    # 日志目录
    local log_dir="${CRON_LOG_DIR:-/root/.hermes/logs/cron}"
    mkdir -p "$log_dir"
    CRON_LOG_FILE="${log_dir}/${job_name}-$(date '+%Y%m%d').log"

    # cradle state 目录
    local state_dir="${CRON_STATE_DIR:-/root/.hermes/lib/cron-state}"
    mkdir -p "$state_dir"

    # flock 防重入
    local lock_dir="${CRON_LOCK_DIR:-/tmp/hermes-cron-locks}"
    mkdir -p "$lock_dir"
    local lock_file="${lock_dir}/${job_name}.lock"
    exec 200>"$lock_file"
    if ! flock -n 200; then
        echo "[$(date '+%F %T')] ${job_name}: 已有实例在运行，退出" >> "$CRON_LOG_FILE"
        exit 0
    fi

    # 日志双输出
    exec > >(tee -a "$CRON_LOG_FILE") 2>&1

    # 根据 mode 设置日志前缀
    if [[ "$mode" == "catchup" ]]; then
        _CRON_LOG_PREFIX="${_C_CYA}⚡[catchup]${_C_RST}"
        echo ""
        cron_section "${job_name} 开始 (⚡catchup) — $(date '+%F %T')"
    else
        _CRON_LOG_PREFIX="${_C_CYA}[cron]${_C_RST}"
        echo ""
        cron_section "${job_name} 开始 — $(date '+%F %T')"
    fi
}

# ===== 输出函数 =====
cron_log() {
    echo -e "${_CRON_LOG_PREFIX} $*"
}

cron_ok() {
    echo -e "${_C_GRN}[ ok  ]${_C_RST} $*"
}

cron_err() {
    echo -e "${_C_RED}[error]${_C_RST} $*"
    OVERALL_STATUS="fail"
}

cron_warn() {
    echo -e "${_C_YLW}[warn ]${_C_RST} $*"
    [[ "$OVERALL_STATUS" == "success" ]] && OVERALL_STATUS="partial"
}

cron_section() {
    _STEP_COUNT=$((_STEP_COUNT + 1))
    echo ""
    echo "========================================"
    echo "  [$((_STEP_COUNT))] $1"
    echo "========================================"
}

# ===== 基本步骤执行（带状态跟踪）=====
# 用法：cron_run_step "步骤名" command args...
# 返回命令的退出码，同时更新 OVERALL_STATUS
cron_run_step() {
    local step_name="$1"
    shift
    cron_section "$step_name"
    if "$@"; then
        cron_ok "$step_name 完成"
        _STEP_RESULTS+=("✅ $step_name")
        return 0
    else
        local rc=$?
        cron_err "$step_name 失败 (exit=$rc)"
        _STEP_RESULTS+=("❌ $step_name")
        return $rc
    fi
}

# ===== 带重试的步骤执行（exponential backoff）=====
# 用法：cron_run_step_retry "步骤名" command args...
# 环境变量 CRON_RETRY_MAX 和 CRON_RETRY_DELAY 控制重试行为
# 重试耗尽时设置 _CRON_RETRIES_EXHAUSTED=true
cron_run_step_retry() {
    local step_name="$1"
    shift
    local max_retries="${CRON_RETRY_MAX:-2}"
    local delay="${CRON_RETRY_DELAY:-30}"
    local retry_count=0
    local exit_code=0

    cron_section "$step_name"

    while true; do
        if "$@"; then
            if [[ $retry_count -eq 0 ]]; then
                cron_ok "$step_name 完成"
                _STEP_RESULTS+=("✅ $step_name")
            else
                cron_warn "$step_name 在重试 $retry_count/$max_retries 后恢复"
                _STEP_RESULTS+=("⚠️ $step_name (retry $retry_count/$max_retries)")
            fi
            return 0
        else
            exit_code=$?
            retry_count=$((retry_count + 1))
            _CRON_RETRIES_TOTAL=$((_CRON_RETRIES_TOTAL + 1))

            if [[ $retry_count -ge $max_retries ]]; then
                cron_err "$step_name 失败 (exit=$exit_code, after $retry_count retries)"
                _STEP_RESULTS+=("❌ $step_name (after $retry_count retries)")
                _CRON_RETRIES_EXHAUSTED=true
                return $exit_code
            fi

            cron_warn "$step_name 失败 (exit=$exit_code), 重试 $retry_count/$max_retries 在 ${delay}s 后..."
            sleep "$delay"
            delay=$((delay * 2))  # exponential backoff
        fi
    done
}

# ===== 飞书通知 =====
cron_notify() {
    local subject="$1"
    local message="$2"
    local chat_id="${FEISHU_CHAT_ID:-oc_f04a9f65d4b780511cc3f402c7d54ac3}"
    local full_msg
    full_msg=$(printf '%b\n%b' "$subject" "$message")

    # 通道 1：lark-cli
    if command -v lark-cli &>/dev/null; then
        if lark-cli im +messages-send \
            --chat-id "$chat_id" \
            --text "$full_msg" \
            --as bot &>/dev/null; then
            cron_ok "飞书通知已发送（lark-cli）"
            return 0
        else
            cron_warn "飞书通知失败（lark-cli），尝试 webhook 降级"
        fi
    fi

    # 通道 2：webhook 降级
    if [[ -n "${FEISHU_WEBHOOK_URL:-}" ]]; then
        if python3 - "$subject" "$message" <<'PY'
import json, os, sys, urllib.request

subject, message = sys.argv[1], sys.argv[2]
url = os.environ.get("FEISHU_WEBHOOK_URL")
payload = {
    "msg_type": "post",
    "content": {
        "post": {
            "zh_cn": {
                "title": subject,
                "content": [[{"tag": "text", "text": message}]],
            }
        }
    },
}
req = urllib.request.Request(
    url,
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    body = resp.read().decode("utf-8", errors="replace")
result = json.loads(body) if body else {}
if result.get("code", 0) != 0:
    raise SystemExit(f"Feishu webhook error: {body}")
PY
        then
            cron_ok "飞书通知已发送（webhook）"
            return 0
        else
            cron_warn "飞书通知发送失败（webhook）"
        fi
    else
        cron_warn "未配置 lark-cli 或 FEISHU_WEBHOOK_URL，跳过飞书通知"
    fi
    return 0
}

# ===== 状态文件写入 =====
# 写 /root/.hermes/lib/cron-state/<job-name>.json
# 供 Layer 1（cron-boot-detect / cron-periodic-detect）读取
_write_state_file() {
    local state_dir="${CRON_STATE_DIR:-/root/.hermes/lib/cron-state}"
    local state_file="${state_dir}/${CRON_JOB_NAME}.json"
    mkdir -p "$state_dir"

    local elapsed_val="${elapsed:-0}"
    # 去掉末尾的 "s" 后缀
    elapsed_val="${elapsed_val%s}"

    # 提取最近一条失败步骤的错误信息
    local last_error=""
    for step_raw in "${_STEP_RESULTS[@]}"; do
        if [[ "$step_raw" == ❌* ]]; then
            last_error="${step_raw#❌ }"
        fi
    done

    # 通过管道传递变量给 Python，避免引号转义问题
    printf '%s\n' \
        "$CRON_JOB_NAME" \
        "$OVERALL_STATUS" \
        "$CRON_MODE" \
        "$(date -Iseconds)" \
        "$elapsed_val" \
        "${_CRON_RETRIES_TOTAL:-0}" \
        "${_CRON_RETRIES_EXHAUSTED:-false}" \
        "$last_error" |
    python3 -c "
import json, os, sys

lines = [l.rstrip('\n') for l in sys.stdin]
state = {
    'job_name': lines[0],
    'status': lines[1],
    'cron_mode': lines[2],
    'run_at': lines[3],
    'elapsed_seconds': max(0, int(float(lines[4]))) if lines[4] else 0,
    'retries_used': int(lines[5]) if lines[5] else 0,
    'overall_retries_exhausted': lines[6].lower() == 'true',
}
if len(lines) > 7 and lines[7].strip():
    state['last_error'] = lines[7].strip()
path = '$state_file'
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, 'w') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
print(f'state written: {path}')
" || cron_warn "状态文件写入失败"
}

# ===== 完成汇总 =====
cron_finish() {
    local elapsed=""
    if [[ -n "${CRON_START_TIME:-}" ]]; then
        local end_time
        end_time=$(date +%s)
        elapsed="$(( end_time - CRON_START_TIME ))s"
    fi

    cron_section "完成 — ${OVERALL_STATUS}${elapsed:+ (${elapsed})}"

    # 打印步骤汇总
    if [[ ${#_STEP_RESULTS[@]} -gt 0 ]]; then
        echo "--- 步骤汇总 ---"
        for r in "${_STEP_RESULTS[@]}"; do
            echo "  $r"
        done
    fi

    # 写入状态文件（供 Layer 1 检测使用）
    _write_state_file

    # 发送飞书通知（跳过标记时不发，脚本已自发通知）
    if [[ "$CRON_SKIP_FINISH_NOTIFY" != "true" ]]; then
        local status_emoji="✅"
        [[ "$OVERALL_STATUS" == "partial" ]] && status_emoji="⚠️"
        [[ "$OVERALL_STATUS" == "fail" ]] && status_emoji="❌"

        local mode_tag=""
        [[ "$CRON_MODE" == "catchup" ]] && mode_tag="⚡"

        local subject="${status_emoji}${mode_tag} [${CRON_JOB_NAME}] ${OVERALL_STATUS}"
        local body=""
        if [[ ${#_STEP_RESULTS[@]} -gt 0 ]]; then
            body=$(printf '%s\n' "${_STEP_RESULTS[@]}")
        fi
        [[ -n "$elapsed" ]] && body="${body}${body:+$'\n'}耗时: ${elapsed}"

        # 如果重试耗尽了，追加人工提醒
        if [[ "$_CRON_RETRIES_EXHAUSTED" == "true" ]]; then
            body="${body}${body:+$'\n'}"
            body="${body}$'\n'"'━━━━━━ 需人工介入 ━━━━━━'"
            body="${body}$'\n'"重试已耗尽，请检查后执行 cron-catchup-repair.sh"
        fi

        cron_notify "$subject" "$body"
    fi

    # 根据状态设置退出码
    case "$OVERALL_STATUS" in
        success) return 0 ;;
        partial) return 0 ;;  # 部分成功不视为 cron 失败
        fail)    return 1 ;;
    esac
}

# 记录开始时间（在 source 时自动设置）
CRON_START_TIME=$(date +%s)