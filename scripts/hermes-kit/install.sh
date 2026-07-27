#!/bin/bash
# install.sh — Hermes-Kit 一键安装脚本
#
# 用法:
#   ./install.sh                    # 交互式安装（需确认）
#   ./install.sh --yes              # 非交互，自动确认
#   ./install.sh --dry-run          # 只展示要做什么，不实际执行
#   ./install.sh --skip-cron        # 跳过 cron 创建（已有环境）
#   ./install.sh --skip-deploy      # 跳过组件部署（已部署过）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="$SCRIPT_DIR"
REPO_ROOT="$(cd "$KIT_DIR/../.." && pwd)"
DEPLOY_SCRIPT="$REPO_ROOT/deploy/deploy.sh"
KIT_HOME="${HERMES_KIT_HOME:-$HOME/.hermes-kit}"
KIT_CONFIG="$KIT_HOME/config.yaml"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_CONFIG="$HERMES_HOME/config.yaml"
STATE_FILE="$KIT_HOME/install.state"

# ===== 颜色 =====
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'
  C_BLU=$'\033[34m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YLW=''; C_BLU=''; C_DIM=''; C_BOLD=''; C_RST=''
fi

# ===== 参数解析 =====
DRY_RUN=false
AUTO_YES=false
SKIP_CRON=false
SKIP_DEPLOY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)    DRY_RUN=true; shift ;;
    --yes|-y)     AUTO_YES=true; shift ;;
    --skip-cron)  SKIP_CRON=true; shift ;;
    --skip-deploy) SKIP_DEPLOY=true; shift ;;
    --help|-h)
      sed -n '2,12p' "$0"; exit 0 ;;
    *)
      echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# ===== 日志函数 =====
log()   { echo "${C_BLU}[kit]${C_RST} $*"; }
ok()    { echo "${C_GRN}[ ok  ]${C_RST} $*"; }
warn()  { echo "${C_YLW}[warn ]${C_RST} $*"; }
err()   { echo "${C_RED}[error]${C_RST} $*" >&2; }
step()  { echo; echo "${C_BOLD}==> $*${C_RST}"; }

dry() {
  if $DRY_RUN; then
    echo "    ${C_DIM}(dry-run) $*${C_RST}"
    return 0
  fi
  return 1
}

# ===== 状态标记 =====
state_get() {
  local key="$1"
  if [[ -f "$STATE_FILE" ]]; then
    grep -E "^${key}=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2-
  fi
}

state_set() {
  local key="$1" val="$2"
  mkdir -p "$(dirname "$STATE_FILE")"
  if [[ -f "$STATE_FILE" ]] && grep -qE "^${key}=" "$STATE_FILE" 2>/dev/null; then
    # 用 sed 替换（仅第一匹配行）
    sed -i "0,/^${key}=.*/ s|^${key}=.*|${key}=${val}|" "$STATE_FILE" 2>/dev/null || echo "${key}=${val}" >> "$STATE_FILE"
  else
    echo "${key}=${val}" >> "$STATE_FILE"
  fi
}

state_done() {
  local key="$1" val
  val=$(state_get "$key")
  [[ "$val" == "done" || "$val" == "skipped" ]]
}

# ===== 确认 =====
confirm() {
  local msg="$1"
  if $AUTO_YES; then
    log "$msg (--yes, 自动确认)"
    return 0
  fi
  read -p "$msg [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

# ============================================================
# Step 1: 环境检查
# ============================================================
check_env() {
  step "Step 1/5: 环境检查"
  local failed=0

  _check_cmd() {
    local name="$1" cmd="$2"
    if command -v "$cmd" >/dev/null 2>&1; then
      local ver
      ver=$("$cmd" --version 2>/dev/null | head -1) || ver="(unknown)"
      ok "$name: $ver"
      return 0
    else
      err "$name: 未找到 ($cmd)"
      return 1
    fi
  }

  _check_cmd "Hermes" "hermes" || failed=$((failed + 1))
  _check_cmd "Python" "python3" || failed=$((failed + 1))

  # Hermes 版本 ≥ 0.19.0
  if command -v hermes >/dev/null 2>&1; then
    local h_ver
    h_ver=$(hermes --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [[ -n "$h_ver" ]]; then
      local major minor
      major=$(echo "$h_ver" | cut -d. -f1)
      minor=$(echo "$h_ver" | cut -d. -f2)
      if [[ "$major" -ge 1 ]] || [[ "$major" -eq 0 && "$minor" -ge 19 ]]; then
        ok "Hermes 版本: $h_ver (≥ 0.19.0)"
      else
        warn "Hermes 版本: $h_ver (< 0.19.0, 可能有兼容性问题)"
      fi
    fi
  fi

  # Python 版本 ≥ 3.11
  if command -v python3 >/dev/null 2>&1; then
    local p_ver
    p_ver=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
    if [[ -n "$p_ver" ]]; then
      local p_major p_minor
      p_major=$(echo "$p_ver" | cut -d. -f1)
      p_minor=$(echo "$p_ver" | cut -d. -f2)
      if [[ "$p_major" -ge 3 && "$p_minor" -ge 11 ]] || [[ "$p_major" -gt 3 ]]; then
        ok "Python 版本: $p_ver (≥ 3.11)"
      else
        warn "Python 版本: $p_ver (< 3.11, 可能有兼容性问题)"
      fi
    fi
  fi

  # Docker 运行中?
  if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
      ok "Docker: 运行中"
    else
      warn "Docker: 未运行（SAG 容器可能不可用）"
      failed=$((failed + 1))
    fi
  else
    warn "Docker: 未安装"
    failed=$((failed + 1))
  fi

  # PostgreSQL 可连? (端口 5434)
  if command -v psql >/dev/null 2>&1; then
    if PGPASSWORD=postgres psql -h 127.0.0.1 -p 5434 -U postgres -c "SELECT 1" >/dev/null 2>&1; then
      ok "PostgreSQL: 可连接 (127.0.0.1:5434)"
    else
      warn "PostgreSQL: 无法连接 (127.0.0.1:5434)（hindsight/knowledge_tree 可能不可用）"
      failed=$((failed + 1))
    fi
  else
    warn "PostgreSQL: psql 未安装，跳过连接检查"
  fi

  # LiteLLM 可连?
  if command -v curl >/dev/null 2>&1; then
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4142/health/liveliness 2>/dev/null | grep -q "200"; then
      ok "LiteLLM: 可连接 (:4142)"
    else
      warn "LiteLLM: 无法连接 (:4142)（模型路由可能不可用）"
      failed=$((failed + 1))
    fi
  fi

  if [[ "$failed" -gt 0 ]]; then
    warn "环境检查有 $failed 项警告，是否继续？"
    if ! confirm "继续安装？"; then
      err "用户取消"
      exit 1
    fi
  else
    ok "环境检查通过"
  fi
  state_set "check_env" "done"
}

# ============================================================
# Step 2: 部署组件
# ============================================================
COMPONENTS=(
  "cron-common"
  "knowledge-navigation"
  "knowledge-tree-plugin"
  "knowledge-tree-builder"
  "clustering-analysis-v3"
  "memory-cleanup"
  "skillopt-runner"
  "skillopt-sleep"
  "system-health-check"
  "daily-learn"
  "dream-synth"
  "self-evolving"
  "cron-wrappers"
)

deploy_components() {
  step "Step 2/5: 部署组件（${#COMPONENTS[@]} 个）"

  if $SKIP_DEPLOY; then
    warn "跳过组件部署 (--skip-deploy)"
    state_set "deploy_components" "skipped"
    return 0
  fi

  if ! [[ -f "$DEPLOY_SCRIPT" ]]; then
    err "找不到 deploy 脚本: $DEPLOY_SCRIPT"
    exit 1
  fi

  local i=0
  for comp in "${COMPONENTS[@]}"; do
    i=$((i + 1))
    local state_key="deploy_${comp//-/_}"
    if state_done "$state_key"; then
      echo "  $i/${#COMPONENTS[@]} $comp: 已完成（跳过）"
      continue
    fi

    echo "  $i/${#COMPONENTS[@]} 部署 $comp ..."
    if dry "deploy/deploy.sh deploy $comp --yes"; then
      state_set "$state_key" "done"
      continue
    fi

    if "$DEPLOY_SCRIPT" deploy "$comp" --yes 2>&1 | tail -5; then
      ok "$comp 部署完成"
      state_set "$state_key" "done"
    else
      err "$comp 部署失败"
      exit 1
    fi
  done

  ok "全部 ${#COMPONENTS[@]} 个组件部署完成"
  state_set "deploy_components" "done"
}

# ============================================================
# Step 3: 配置
# ============================================================
setup_config() {
  step "Step 3/5: 配置"

  # 3.1 创建 ~/.hermes-kit/ 目录和 config.yaml
  if [[ ! -f "$KIT_CONFIG" ]]; then
    if dry "创建 $KIT_CONFIG"; then
      :
    else
      mkdir -p "$KIT_HOME"
      cp "$KIT_DIR/config/default.yaml" "$KIT_CONFIG"
      ok "创建默认配置: $KIT_CONFIG"
    fi
  else
    warn "配置已存在: $KIT_CONFIG（跳过，不覆盖）"
  fi

  # 3.2 追加 .env 配置
  local env_file="$HERMES_HOME/.env"
  if [[ -f "$KIT_DIR/templates/.env.append" ]]; then
    if dry "追加环境变量到 $env_file"; then
      :
    else
      mkdir -p "$HERMES_HOME"
      if [[ -f "$env_file" ]]; then
        # 检查是否已经追加过
        if grep -q "HERMES_KIT_BEGIN" "$env_file" 2>/dev/null; then
          warn ".env 已包含 HERMES_KIT_ 配置（跳过追加）"
        else
          echo "" >> "$env_file"
          echo "# ===== HERMES_KIT_BEGIN =====（由 hermes-kit install.sh 追加）" >> "$env_file"
          cat "$KIT_DIR/templates/.env.append" >> "$env_file"
          echo "# ===== HERMES_KIT_END =====" >> "$env_file"
          ok "追加环境变量到 $env_file"
        fi
      else
        echo "# ===== HERMES_KIT_BEGIN =====" > "$env_file"
        cat "$KIT_DIR/templates/.env.append" >> "$env_file"
        echo "# ===== HERMES_KIT_END =====" >> "$env_file"
        ok "创建 $env_file"
      fi
    fi
  fi

  # 3.3 启用插件（config.yaml plugins.enabled）
  if command -v hermes >/dev/null 2>&1; then
    local plugins=("knowledge-navigation" "knowledge-tree-plugin")
    for plugin in "${plugins[@]}"; do
      if dry "启用插件: $plugin"; then
        continue
      fi
      # 检查插件是否已启用
      if hermes plugins list 2>/dev/null | grep -qi "$plugin"; then
        echo "    插件 $plugin 已启用（跳过）"
      else
        if hermes plugins enable "$plugin" 2>&1 | tail -3; then
          ok "插件 $plugin 已启用"
        else
          warn "启用插件 $plugin 失败，请手动执行: hermes plugins enable $plugin"
        fi
      fi
    done
  else
    warn "Hermes 未安装，跳过插件启用步骤"
  fi

  # 3.4 验证配置
  if [[ -f "$KIT_CONFIG" ]]; then
    ok "配置验证: $KIT_CONFIG 存在"
  fi

  state_set "setup_config" "done"
}

# ============================================================
# Step 4: 创建 cron 任务
# ============================================================

# cron 任务定义: name|config_key|script|no_agent|workdir
# config_key 对应 config.yaml 的 cron.<key> schedule 值
# no_agent=true 表示 --no-agent 模式
# script= 空 表示 agent 任务（prompt 驱动）
CRON_JOBS=(
  "system-health-check|system_health|health-check-cron.sh|true|"
  "flywheel-health-report|health_report|flywheel-health-report.sh|true|"
  "每日在线学习|daily_learn|daily-learn/daily_learn.sh|true|/root/.hermes/scripts/daily-learn"
  "知识树k_vector每周兜底维护|kvector_maintenance|knowledge-tree-builder/scripts/knowledge-tree-kvector-maintenance.sh|true|/root/.hermes/scripts/knowledge-tree-builder"
  "每周深度研究-知识树学习|deep_research||false|"
  "clustering-analysis|clustering|clustering-analysis-v3/scripts/clustering-analysis-cron.sh|true|/root/.hermes/scripts"
  "知识树维护每日|knowledge_tree_consolidate|knowledge-tree-builder/scripts/knowledge-tree-consolidate.sh|true|/root/.hermes/scripts/knowledge-tree-builder"
  "知识导航评估基线|kn_baseline|knowledge-navigation-baseline.sh|true|/root/.hermes/plugins/knowledge-navigation"
  "Skill Eval 评估|skill_eval|run-skill-eval.sh|true|"
  "memory-cleanup-daily|memory_cleanup|memory-cleanup/daily_dryrun.sh|true|/root/.hermes/scripts/memory-cleanup"
  "知识导航 Router 健康巡检|router_health|kn-router-health-check.sh|true|"
  "skillopt-nightly-run|skill_optimization|skillopt-runner/skillopt-nightly-run.sh|true|/root/.hermes/skillopt-runner"
  "dream-daily|dream_daily|dream-synth/scripts/dream-daily.sh|true|/root/.hermes/scripts/dream-synth"
  "cron-periodic-detect|cron_detect|cron-periodic-detect.sh|true|"
)

# cron 调度从 config.yaml 的 cron.<key> 读取
_read_cron_schedule() {
  local key="$1"
  local config_file="${KIT_CONFIG:-$KIT_DIR/config/default.yaml}"
  if [[ ! -f "$config_file" ]]; then
    config_file="$KIT_DIR/config/default.yaml"
  fi
  python3 -c "
import yaml, sys
with open('$config_file') as f:
    cfg = yaml.safe_load(f)
v = cfg.get('cron', {}).get('$key', '')
print(v)
" 2>/dev/null || echo ""
}

setup_cron() {
  step "Step 4/5: 创建 cron 任务（${#CRON_JOBS[@]} 个常驻）"

  if $SKIP_CRON; then
    warn "跳过 cron 创建 (--skip-cron)"
    state_set "setup_cron" "skipped"
    return 0
  fi

  if ! command -v hermes >/dev/null 2>&1; then
    err "Hermes 未安装，无法创建 cron"
    exit 1
  fi

  # 获取现有 cron 列表（提取 Name: 字段的值）
  local existing_names=()
  while IFS= read -r line; do
    local name_val
    name_val=$(echo "$line" | sed -n 's/^[[:space:]]*Name:[[:space:]]*//p')
    [[ -n "$name_val" ]] && existing_names+=("$name_val")
  done < <(hermes cron list 2>/dev/null || true)

  local i=0
  for job_def in "${CRON_JOBS[@]}"; do
    i=$((i + 1))
    IFS='|' read -r j_name j_key j_script j_no_agent j_workdir <<< "$job_def"

    # 从 config.yaml 读取调度时间
    local j_schedule
    j_schedule=$(_read_cron_schedule "$j_key")
    if [[ -z "$j_schedule" ]]; then
      warn "  $i/${#CRON_JOBS[@]} $j_name: cron.$j_key 未在 config.yaml 中定义，跳过"
      continue
    fi

    local state_key="cron_${j_name// /_}"
    state_key="${state_key//[^a-zA-Z0-9_]/_}"

    if state_done "$state_key"; then
      echo "  $i/${#CRON_JOBS[@]} $j_name: 已完成（跳过）"
      continue
    fi

    # 检查是否已存在同名 job
    local exists=false
    for ename in "${existing_names[@]:-}"; do
      if [[ "$ename" == "$j_name" ]]; then
        exists=true
        break
      fi
    done

    if $exists; then
      echo "  $i/${#CRON_JOBS[@]} $j_name: 已存在（跳过，不覆盖）"
      state_set "$state_key" "skipped"
      continue
    fi

    # agent 任务（无 script）需要 prompt 或 skill，kit 无法自动创建
    if [[ -z "$j_script" ]]; then
      echo "  $i/${#CRON_JOBS[@]} $j_name: agent 任务（无 script），需手动创建"
      warn "  请手动执行: hermes cron create --name '$j_name' '$j_schedule' '<prompt>'"
      state_set "$state_key" "skipped"
      continue
    fi

    echo "  $i/${#CRON_JOBS[@]} 创建 $j_name ($j_schedule) ..."

    if dry "hermes cron create --name '$j_name' ..."; then
      state_set "$state_key" "done"
      continue
    fi

    # 构建命令参数
    local cmd_args=()
    if [[ "$j_no_agent" == "true" ]]; then
      cmd_args+=(--script "$j_script" --no-agent)
    else
      cmd_args+=(--script "$j_script")
    fi
    if [[ -n "$j_workdir" ]]; then
      cmd_args+=(--workdir "$j_workdir")
    fi

    # 捕获 hermes cron create 的输出和真实退出码（set -e 下用 &&/|| 分隔）
    local create_output create_rc
    create_output=$(hermes cron create --name "$j_name" "${cmd_args[@]}" "$j_schedule" 2>&1) && create_rc=0 || create_rc=$?

    if [[ $create_rc -eq 0 ]] && echo "$create_output" | grep -qiE "Next run|created|saved"; then
      echo "$create_output" | tail -3
      ok "$j_name 创建成功"
      state_set "$state_key" "done"
    else
      echo "$create_output" | tail -3
      warn "$j_name 创建失败 (rc=$create_rc)，请手动检查: hermes cron list"
      state_set "$state_key" "failed"
    fi
  done

  ok "cron 任务处理完成"
  state_set "setup_cron" "done"
}

# ============================================================
# Step 5: 验证
# ============================================================
verify_install() {
  step "Step 5/5: 安装验证"
  local passed=0 total=0

  _check() {
    total=$((total + 1))
    local label="$1" cmd="$2"
    if $DRY_RUN; then
      ok "$label (dry-run)"
      passed=$((passed + 1))
      return
    fi
    if eval "$cmd" >/dev/null 2>&1; then
      ok "$label"
      passed=$((passed + 1))
    else
      err "$label"
    fi
  }

  _check "配置文件存在" "[[ -f '$KIT_CONFIG' ]]"
  _check "Hermes 可执行" "command -v hermes"

  if command -v hermes >/dev/null 2>&1; then
    _check "cron scheduler 运行中" "hermes cron status 2>&1 | grep -qi running"
    _check "cron 任务 ≥ 10 个" "hermes cron list 2>&1 | grep -c 'Name:' | awk '{ if (\$1 >= 10) exit 0; else exit 1 }'"
  fi

  _check "cron-common 部署" "[[ -f '$HERMES_HOME/lib/cron_common.sh' ]]"
  _check "knowledge-nav 部署" "[[ -d '$HERMES_HOME/plugins/knowledge-navigation' ]]"
  _check "kt-plugin 部署" "[[ -d '$HERMES_HOME/plugins/knowledge-tree-plugin' ]]"
  _check "kt-builder 部署" "[[ -d '$HERMES_HOME/scripts/knowledge-tree-builder' ]]"
  _check "clustering 部署" "[[ -d '$HERMES_HOME/scripts/clustering-analysis-v3' ]]"
  _check "memory-cleanup 部署" "[[ -d '$HERMES_HOME/scripts/memory-cleanup' ]]"
  _check "skillopt 部署" "[[ -d '$HERMES_HOME/skillopt-runner' ]]"

  echo
  if [[ "$passed" -eq "$total" ]]; then
    echo "${C_GRN}✅ 安装验证全部通过 ($passed/$total)${C_RST}"
  else
    echo "${C_YLW}⚠️  安装验证 $passed/$total 通过，请检查上述失败项${C_RST}"
  fi
  echo
  echo "配置文件: $KIT_CONFIG"
  echo "状态文件: $STATE_FILE"
  echo "查看 cron: hermes cron list"
  echo "卸载: $KIT_DIR/uninstall.sh"

  state_set "verify_install" "done"
}

# ============================================================
# 主流程
# ============================================================
main() {
  echo "${C_BOLD}Hermes-Kit 安装程序 v1.2${C_RST}"
  echo "Kit 目录: $KIT_DIR"
  echo "安装目标: $KIT_HOME"
  echo "Hermes 目录: $HERMES_HOME"
  if $DRY_RUN; then
    echo "${C_YLW}模式: dry-run（只展示，不实际执行）${C_RST}"
  fi
  echo

  if ! confirm "开始安装 Hermes-Kit？"; then
    echo "已取消"
    exit 0
  fi

  check_env
  deploy_components
  setup_config
  setup_cron
  verify_install

  echo
  echo "${C_GRN}🎉 Hermes-Kit 安装完成！${C_RST}"
}

main "$@"
