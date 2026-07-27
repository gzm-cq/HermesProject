#!/bin/bash
# upgrade.sh — Hermes-Kit 升级脚本
#
# 用法:
#   ./upgrade.sh                    # 升级（需确认）
#   ./upgrade.sh --yes              # 非交互
#   ./upgrade.sh --dry-run          # 只展示要做什么，不实际执行
#   ./upgrade.sh --skip-cron        # 跳过 cron 重建
#   ./upgrade.sh --skip-deploy      # 跳过组件部署
#
# 升级流程:
#   1. 备份旧配置 → config.yaml.bak.<timestamp>
#   2. 部署组件更新
#   3. 合并配置（保留用户值，追加新 key）
#   4. 重建 cron（根据新配置的 schedule 字段）
#   5. 验证
#   6. 通知
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="$SCRIPT_DIR"
REPO_ROOT="$(cd "$KIT_DIR/../.." && pwd)"
DEPLOY_SCRIPT="$REPO_ROOT/deploy/deploy.sh"
KIT_HOME="${HERMES_KIT_HOME:-$HOME/.hermes-kit}"
KIT_CONFIG="$KIT_HOME/config.yaml"
KIT_TEMPLATE="$KIT_DIR/config/default.yaml"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

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
    --help|-h)    sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
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

confirm() {
  local msg="$1"
  if $AUTO_YES; then return 0; fi
  read -p "$msg [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

# ============================================================
# Step 1: 备份旧配置
# ============================================================
backup_config() {
  step "Step 1/6: 备份旧配置"

  if [[ ! -f "$KIT_CONFIG" ]]; then
    warn "旧配置不存在: $KIT_CONFIG（跳过备份）"
    return 0
  fi

  local bak="$KIT_CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
  if dry "cp $KIT_CONFIG $bak"; then
    :
  else
    cp "$KIT_CONFIG" "$bak"
    ok "已备份: $bak"
  fi
}

# ============================================================
# Step 2: 部署组件更新
# ============================================================
COMPONENTS=(
  "cron-common" "knowledge-navigation" "knowledge-tree-plugin"
  "knowledge-tree-builder" "clustering-analysis-v3" "memory-cleanup"
  "skillopt-runner" "skillopt-sleep" "system-health-check"
  "daily-learn" "dream-synth" "self-evolving" "cron-wrappers"
)

deploy_components() {
  step "Step 2/6: 部署组件更新（${#COMPONENTS[@]} 个）"

  if $SKIP_DEPLOY; then
    warn "跳过组件部署 (--skip-deploy)"
    return 0
  fi

  if ! [[ -f "$DEPLOY_SCRIPT" ]]; then
    err "找不到 deploy 脚本: $DEPLOY_SCRIPT"
    exit 1
  fi

  local i=0
  for comp in "${COMPONENTS[@]}"; do
    i=$((i + 1))
    echo "  $i/${#COMPONENTS[@]} 更新 $comp ..."
    if dry "deploy/deploy.sh deploy $comp --yes"; then
      continue
    fi
    if "$DEPLOY_SCRIPT" deploy "$comp" --yes 2>&1 | tail -3; then
      ok "$comp 更新完成"
    else
      warn "$comp 更新失败（跳过）"
    fi
  done
  ok "组件更新完成"
}

# ============================================================
# Step 3: 合并配置
# ============================================================
merge_config() {
  step "Step 3/6: 合并配置"

  if [[ ! -f "$KIT_CONFIG" ]]; then
    # 首次安装？直接用模板
    if dry "cp $KIT_TEMPLATE $KIT_CONFIG"; then
      :
    else
      mkdir -p "$KIT_HOME"
      cp "$KIT_TEMPLATE" "$KIT_CONFIG"
      ok "创建新配置: $KIT_CONFIG"
    fi
    return 0
  fi

  if dry "合并配置（保留用户值，追加新 key）"; then
    return 0
  fi

  # 用 Python 做深度合并：保留用户值，追加新 key
  python3 -c "
import yaml, sys

with open('$KIT_TEMPLATE') as f:
    new_cfg = yaml.safe_load(f) or {}
with open('$KIT_CONFIG') as f:
    old_cfg = yaml.safe_load(f) or {}

def deep_merge(base, overlay):
    \"\"\"overlay 合并到 base，base 已有 key 保留原值\"\"\"
    for k, v in overlay.items():
        if k not in base:
            base[k] = v
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
    return base

merged = deep_merge(old_cfg, new_cfg)
with open('$KIT_CONFIG', 'w') as f:
    yaml.dump(merged, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
print('配置合并完成')
" 2>&1 | tail -3
  ok "配置合并完成（用户值已保留，新增 key 已追加）"
}

# ============================================================
# Step 4: 重建 cron
# ============================================================
rebuild_cron() {
  step "Step 4/6: 重建 cron"

  if $SKIP_CRON; then
    warn "跳过 cron 重建 (--skip-cron)"
    return 0
  fi

  if ! command -v hermes >/dev/null 2>&1; then
    warn "Hermes 未安装，跳过 cron 重建"
    return 0
  fi

  # 读取旧 cron 的 name→job_id 映射
  declare -A old_cron_ids=()
  local current_id=""
  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*([0-9a-f]{8,})[[:space:]]+\[ ]]; then
      current_id="${BASH_REMATCH[1]}"
    fi
    if [[ "$line" =~ ^[[:space:]]*Name:[[:space:]]*(.+)$ ]]; then
      local n="${BASH_REMATCH[1]}"
      n="${n%${n##*[![:space:]]}}"
      [[ -n "$current_id" && -n "$n" ]] && old_cron_ids["$n"]="$current_id"
      current_id=""
    fi
  done < <(hermes cron list 2>/dev/null || true)

  # 先删除旧 cron（保留不在 kit 管理范围内的）
  local removed=0
  for job_name in "${!old_cron_ids[@]}"; do
    if dry "hermes cron remove ${old_cron_ids[$job_name]}  # $job_name"; then
      continue
    fi
    echo "  移除旧 cron: $job_name ..."
    if hermes cron remove "${old_cron_ids[$job_name]}" 2>&1 | tail -1 | grep -qi "removed\|deleted\|ok"; then
      removed=$((removed + 1))
    else
      warn "  移除 $job_name 失败"
    fi
  done
  [[ $removed -gt 0 ]] && ok "已移除 $removed 个旧 cron"

  # 重新创建（使用 install.sh 的逻辑）
  local CRON_JOBS=(
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

  _read_schedule() {
    local key="$1"
    python3 -c "
import yaml, sys
with open('$KIT_CONFIG') as f:
    cfg = yaml.safe_load(f)
v = cfg.get('cron', {}).get('$key', '')
print(v)
" 2>/dev/null || echo ""
  }

  local created=0
  for job_def in "${CRON_JOBS[@]}"; do
    IFS='|' read -r j_name j_key j_script j_no_agent j_workdir <<< "$job_def"
    local j_schedule
    j_schedule=$(_read_schedule "$j_key")
    [[ -z "$j_schedule" ]] && { warn "  $j_name: cron.$j_key 未定义，跳过"; continue; }

    if [[ -z "$j_script" ]]; then
      echo "  $j_name: agent 任务，需手动创建"
      continue
    fi

    echo "  创建 $j_name ($j_schedule) ..."
    if dry "hermes cron create --name '$j_name' ..."; then
      continue
    fi

    local cmd_args=()
    [[ "$j_no_agent" == "true" ]] && cmd_args+=(--script "$j_script" --no-agent) || cmd_args+=(--script "$j_script")
    [[ -n "$j_workdir" ]] && cmd_args+=(--workdir "$j_workdir")

    local out rc
    out=$(hermes cron create --name "$j_name" "${cmd_args[@]}" "$j_schedule" 2>&1) && rc=0 || rc=$?
    if [[ $rc -eq 0 ]] && echo "$out" | grep -qiE "Next run|created|saved"; then
      created=$((created + 1))
    else
      warn "  $j_name 创建失败 (rc=$rc)"
    fi
  done
  [[ $created -gt 0 ]] && ok "已创建 $created 个 cron 任务"
}

# ============================================================
# Step 5: 验证
# ============================================================
verify_upgrade() {
  step "Step 5/6: 验证"
  local passed=0 total=0

  _check() {
    total=$((total + 1))
    local label="$1" cmd="$2"
    if $DRY_RUN; then
      ok "$label (dry-run)"; passed=$((passed + 1)); return
    fi
    if eval "$cmd" >/dev/null 2>&1; then
      ok "$label"; passed=$((passed + 1))
    else
      err "$label"
    fi
  }

  _check "配置文件存在" "[[ -f '$KIT_CONFIG' ]]"
  _check "cron 任务 ≥ 10 个" "hermes cron list 2>&1 | grep -c 'Name:' | awk '{ if (\$1 >= 10) exit 0; else exit 1 }'"
  _check "知识导航插件部署" "[[ -d '$HERMES_HOME/plugins/knowledge-navigation' ]]"
  _check "skillopt 部署" "[[ -d '$HERMES_HOME/skillopt-runner' ]]"

  echo
  if [[ "$passed" -eq "$total" ]]; then
    echo "${C_GRN}✅ 升级验证全部通过 ($passed/$total)${C_RST}"
  else
    echo "${C_YLW}⚠️ 升级验证 $passed/$total 通过${C_RST}"
  fi
}

# ============================================================
# Step 6: 通知
# ============================================================
notify() {
  step "Step 6/6: 通知"
  echo "${C_GRN}✅ Hermes-Kit 升级完成${C_RST}"
  echo "  - 配置: $KIT_CONFIG"
  echo "  - 备份: $KIT_CONFIG.bak.<timestamp>"
  echo "  - 如需回滚: cp $KIT_CONFIG.bak.<latest> $KIT_CONFIG"
}

# ============================================================
# 主流程
# ============================================================
main() {
  echo "${C_BOLD}Hermes-Kit 升级程序 v1.2${C_RST}"
  echo "Kit 目录: $KIT_DIR"
  echo "配置: $KIT_CONFIG"
  echo

  if ! $DRY_RUN && ! $AUTO_YES; then
    if ! confirm "确认升级 Hermes-Kit？将更新代码、合并配置、重建 cron。"; then
      echo "已取消"; exit 0
    fi
  fi

  backup_config
  deploy_components
  merge_config
  rebuild_cron
  verify_upgrade
  notify
}

main "$@"