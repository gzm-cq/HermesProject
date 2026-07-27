#!/bin/bash
# uninstall.sh — Hermes-Kit 卸载脚本
#
# 用法:
#   ./uninstall.sh                  # dry-run 模式，只展示要删除什么
#   ./uninstall.sh --apply          # 真正执行卸载
#   ./uninstall.sh --keep-cron      # 保留 cron 任务
#   ./uninstall.sh --keep-config    # 保留配置文件
#
# 原则: 代码卸载，数据保留。默认 --dry-run。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="$SCRIPT_DIR"
REPO_ROOT="$(cd "$KIT_DIR/../.." && pwd)"
DEPLOY_SCRIPT="$REPO_ROOT/deploy/deploy.sh"
KIT_HOME="${HERMES_KIT_HOME:-$HOME/.hermes-kit}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# ===== 颜色 =====
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'
  C_BLU=$'\033[34m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YLW=''; C_BLU=''; C_DIM=''; C_BOLD=''; C_RST=''
fi

# ===== 参数解析 =====
APPLY=false
KEEP_CRON=false
KEEP_CONFIG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        APPLY=true; shift ;;
    --keep-cron)    KEEP_CRON=true; shift ;;
    --keep-config)  KEEP_CONFIG=true; shift ;;
    --help|-h)
      sed -n '2,12p' "$0"; exit 0 ;;
    *)
      echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# ===== 日志 =====
log()   { echo "${C_BLU}[kit]${C_RST} $*"; }
ok()    { echo "${C_GRN}[ ok  ]${C_RST} $*"; }
warn()  { echo "${C_YLW}[warn ]${C_RST} $*"; }
err()   { echo "${C_RED}[error]${C_RST} $*" >&2; }
step()  { echo; echo "${C_BOLD}==> $*${C_RST}"; }

dry() {
  if ! $APPLY; then
    echo "    ${C_DIM}(dry-run) $*${C_RST}"
    return 0
  fi
  return 1
}

# ===== 确认 =====
confirm() {
  local msg="$1"
  read -p "$msg [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

# cron 任务名列表（与 install.sh CRON_JOBS 的 name 字段对应）
CRON_JOB_NAMES=(
  "system-health-check"
  "flywheel-health-report"
  "每日在线学习"
  "知识树k_vector每周兜底维护"
  "每周深度研究-知识树学习"
  "clustering-analysis"
  "知识树维护每日"
  "知识导航评估基线"
  "Skill Eval 评估"
  "memory-cleanup-daily"
  "知识导航 Router 健康巡检"
  "skillopt-nightly-run"
  "dream-daily"
  "cron-periodic-detect"
)

# 组件列表（与 install.sh 相同顺序，逆序卸载）
COMPONENTS=(
  "cron-wrappers"
  "self-evolving"
  "dream-synth"
  "daily-learn"
  "system-health-check"
  "skillopt-sleep"
  "skillopt-runner"
  "memory-cleanup"
  "clustering-analysis-v3"
  "knowledge-tree-builder"
  "knowledge-tree-plugin"
  "knowledge-navigation"
  "cron-common"
)

# ============================================================
# Step 1: 移除 cron 任务
# ============================================================
remove_cron() {
  step "Step 1/4: 移除 cron 任务（${#CRON_JOB_NAMES[@]} 个）"

  if $KEEP_CRON; then
    warn "保留 cron 任务 (--keep-cron)"
    return 0
  fi

  if ! command -v hermes >/dev/null 2>&1; then
    warn "Hermes 未安装，跳过 cron 移除"
    return 0
  fi

  # 构建 name → job_id 映射（hermes cron list 输出格式: "  <hex_id> [active]\n    Name: <name>"）
  declare -A name_to_id=()
  local current_id=""
  while IFS= read -r line; do
    # 匹配 job_id 行: "  98bcaffac0f3 [active]"
    if [[ "$line" =~ ^[[:space:]]*([0-9a-f]{8,})[[:space:]]+\[ ]]; then
      current_id="${BASH_REMATCH[1]}"
    fi
    # 匹配 Name 行: "    Name:      system-health-check"
    if [[ "$line" =~ ^[[:space:]]*Name:[[:space:]]*(.+)$ ]]; then
      local n="${BASH_REMATCH[1]}"
      # 去掉尾部空白
      n="${n%"${n##*[![:space:]]}"}"
      if [[ -n "$current_id" && -n "$n" ]]; then
        name_to_id["$n"]="$current_id"
      fi
      current_id=""
    fi
  done < <(hermes cron list 2>/dev/null || true)

  local i=0
  for job_name in "${CRON_JOB_NAMES[@]}"; do
    i=$((i + 1))
    # 查找 job_id（hermes cron remove 只接受 job_id，不接受 name）
    local job_id="${name_to_id[$job_name]:-}"
    if [[ -z "$job_id" ]]; then
      echo "  $i/${#CRON_JOB_NAMES[@]} $job_name: 不存在（跳过）"
      continue
    fi
    if dry "hermes cron remove $job_id  # $job_name"; then
      continue
    fi
    echo "  $i/${#CRON_JOB_NAMES[@]} 移除 $job_name ($job_id) ..."
    if hermes cron remove "$job_id" 2>&1 | tail -2; then
      ok "$job_name 已移除"
    else
      warn "$job_name 移除失败"
    fi
  done

  ok "cron 任务移除完成"
}

# ============================================================
# Step 2: 移除组件部署
# ============================================================
remove_components() {
  step "Step 2/4: 移除组件部署（${#COMPONENTS[@]} 个）"

  if ! [[ -f "$DEPLOY_SCRIPT" ]]; then
    warn "找不到 deploy 脚本: $DEPLOY_SCRIPT，跳过组件移除"
    return 0
  fi

  local i=0
  for comp in "${COMPONENTS[@]}"; do
    i=$((i + 1))
    if dry "deploy.sh cleanup $comp --uninstall"; then
      continue
    fi
    echo "  $i/${#COMPONENTS[@]} 移除 $comp ..."
    if "$DEPLOY_SCRIPT" cleanup "$comp" --uninstall 2>&1 | tail -3; then
      ok "$comp 已移除"
    else
      warn "$comp 移除失败（可能本来就没装）"
    fi
  done

  ok "组件移除完成"
}

# ============================================================
# Step 3: 移除插件启用配置
# ============================================================
remove_plugins() {
  step "Step 3/4: 移除插件启用配置"

  if ! command -v hermes >/dev/null 2>&1; then
    warn "Hermes 未安装，跳过插件禁用"
    return 0
  fi

  local plugins=("knowledge-navigation" "knowledge-tree-plugin")
  for plugin in "${plugins[@]}"; do
    if dry "禁用插件: $plugin"; then
      continue
    fi
    if hermes plugins list 2>/dev/null | grep -qi "$plugin"; then
      if hermes plugins disable "$plugin" 2>&1 | tail -2; then
        ok "插件 $plugin 已禁用"
      else
        warn "禁用插件 $plugin 失败，请手动执行: hermes plugins disable $plugin"
      fi
    else
      echo "    插件 $plugin 未启用（跳过）"
    fi
  done

  # 移除 .env 中的 HERMES_KIT_ 区块
  local env_file="$HERMES_HOME/.env"
  if [[ -f "$env_file" ]]; then
    if dry "从 $env_file 移除 HERMES_KIT_ 区块"; then
      :
    else
      if grep -q "HERMES_KIT_BEGIN" "$env_file" 2>/dev/null; then
        # 备份
        cp "$env_file" "${env_file}.bak.$(date +%Y%m%d-%H%M%S)"
        # 使用 sed 删除 HERMES_KIT_BEGIN 到 HERMES_KIT_END 之间的内容（含标记行）
        sed -i '/^# ===== HERMES_KIT_BEGIN/,/^# ===== HERMES_KIT_END/d' "$env_file"
        # 清理末尾多余空行
        sed -i -e :a -e '/^\n*$/{$d;N;};/\n$/ba' "$env_file"
        ok "已从 .env 移除 HERMES_KIT_ 区块"
      fi
    fi
  fi
}

# ============================================================
# Step 4: 清理 kit 自身
# ============================================================
remove_kit() {
  step "Step 4/4: 清理 Hermes-Kit 自身"

  if $KEEP_CONFIG; then
    warn "保留配置 (--keep-config): $KIT_HOME"
  else
    if dry "删除 $KIT_HOME"; then
      :
    else
      if [[ -d "$KIT_HOME" ]]; then
        rm -rf "$KIT_HOME"
        ok "已删除 $KIT_HOME"
      else
        echo "    $KIT_HOME 不存在（跳过）"
      fi
    fi
  fi
}

# ============================================================
# 主流程
# ============================================================
main() {
  echo "${C_BOLD}Hermes-Kit 卸载程序 v1.2${C_RST}"
  echo "Kit 目录: $KIT_DIR"
  echo "安装目标: $KIT_HOME"
  echo "Hermes 目录: $HERMES_HOME"
  if ! $APPLY; then
    echo "${C_YLW}模式: dry-run（只展示，不实际执行）${C_RST}"
  fi
  echo
  echo "${C_RED}⚠️  此操作将卸载 Hermes-Kit 的全部组件和 cron 任务。${C_RST}"
  echo "数据（数据库、日志、备份）将被保留。"
  echo

  if ! $APPLY; then
    echo "${C_DIM}（dry-run 模式：展示将执行的操作，不实际删除）${C_RST}"
    echo
  fi

  if $APPLY && ! confirm "确认卸载 Hermes-Kit？此操作不可撤销。"; then
    echo "已取消"
    exit 0
  fi

  remove_cron
  remove_components
  remove_plugins
  remove_kit

  echo
  echo "${C_GRN}✅ Hermes-Kit 卸载完成${C_RST}"
  echo
  echo "保留的数据："
  echo "  - PostgreSQL 数据库（知识树、记忆等）"
  echo "  - Hermes 日志: $HERMES_HOME/logs/"
  echo "  - 部署备份: $HERMES_HOME/backups/"
  echo "  - Hermes 全局配置: $HERMES_HOME/config.yaml（仅移除了 plugins.enabled 中的 kit 插件）"
}

main "$@"
