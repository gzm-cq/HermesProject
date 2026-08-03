#!/bin/bash
# deploy.sh — 一键式项目级部署 / 回滚（轻量分发器）
#
# 用法:
#   ./deploy/deploy.sh list                              列出可部署项目
#   ./deploy/deploy.sh plan      <project>               展开 manifest，列出将部署/排除的文件（不动文件系统）
#   ./deploy/deploy.sh deploy    <project> [--yes]       部署指定项目（默认会要求确认）
#   ./deploy/deploy.sh rollback  <project> [<timestamp>] 回滚（默认回到最近一次；先删本次部署文件，再还原备份）
#   ./deploy/deploy.sh history   <project>               查看历史部署
#   ./deploy/deploy.sh cleanup   <project> [--keep N]    清理历史备份（默认保留最近 5 次）
#   ./deploy/deploy.sh cleanup   <project> --uninstall   完全卸载：删部署文件 + 删所有备份 + 删旧平铺文件
#
# 本文件为轻量分发器，实际逻辑委托给 deploy/projects/<project>.sh。
# 每个项目的配置（源目录、目标路径、服务、旧文件清理、Skill）在项目脚本中定义。
# 共享函数库位于 deploy/lib/common.sh。
#
# 项目列表:
#   ai-report-system          AI 报告生成系统
#   clustering-analysis-v3    聚类分析
#   drawio-generator          Draw.io/SVG 矢量图生成
#   daily-learn               每日在线学习脚本
#   dream-synth               梦境合成流水线
#   knowledge-navigation      知识导航插件 (重启 hermes-gateway.service)
#   memory-cleanup            记忆清理
#   knowledge-tree-builder     知识树建树管线
#   knowledge-tree-plugin      知识树在线插件 (重启 hermes-gateway.service)
#   p0-benchmark              P0 基准评估工具 (可选)
#   recall-eval               召回评估工具 (可选)
#   self-evolving             自进化飞轮项目
#   skillopt-runner           SkillOpt 技能优化运行器
#   skillopt-sleep            SkillOpt-Sleep 技能优化引擎
#   cron-common               cron 定时任务公共库
#   cron-wrappers              cron 定时任务 wrapper 脚本（统一部署到 /root/.hermes/scripts/）
#   flywheel-health-report    飞轮健康报告+参数自优化调优器
#   system-health-check       系统健康巡检脚本

set -euo pipefail

# ===== 颜色 =====
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_BLU=$'\033[34m'; C_RST=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YLW=''; C_BLU=''; C_RST=''
fi
log()  { echo "${C_BLU}[deploy]${C_RST} $*"; }
ok()   { echo "${C_GRN}[ ok  ]${C_RST} $*"; }
err()  { echo "${C_RED}[error]${C_RST} $*" >&2; }

# ===== 路径 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECTS_DIR="$SCRIPT_DIR/projects"

# ===== 项目信息（仅用于 list） =====
declare -A PROJECT_INFO=(
  [ai-report-system]="AI 报告生成系统"
  [clustering-analysis-v3]="聚类分析"
  [drawio-generator]="Draw.io/SVG 矢量图生成"
  [daily-learn]="每日在线学习脚本"
  [dream-synth]="梦境合成流水线"
  [knowledge-navigation]="知识导航插件 (重启 hermes-gateway.service)"
  [knowledge-tree-builder]="知识树建树管线"
  [knowledge-tree-plugin]="知识树在线插件 (重启 hermes-gateway.service)"
  [memory-cleanup]="记忆清理"
  [p0-benchmark]="P0 基准评估工具 (可选)"
  [recall-eval]="召回评估工具 (可选)"
  [self-evolving]="自进化飞轮项目"
  [skillopt-runner]="SkillOpt 技能优化运行器"
  [skillopt-sleep]="SkillOpt-Sleep 技能优化引擎"
  [cron-common]="cron 定时任务公共库"
  [cron-wrappers]="cron 定时任务 wrapper 脚本"
  [system-health-check]="系统健康巡检脚本"
  [flywheel-health-report]="飞轮健康报告+参数自优化调优器"
)

# ===== 子命令: list =====
cmd_list() {
  printf "%-26s %s\n" "PROJECT" "DESCRIPTION"
  printf "%-26s %s\n" "-------" "-----------"
  for name in "${!PROJECT_INFO[@]}"; do
    printf "%-26s %s\n" "$name" "${PROJECT_INFO[$name]}"
  done | sort
  echo
  log "使用: $0 <plan|deploy|rollback|history|cleanup> <project> [选项]"
}

# ===== 入口 =====
sub="${1:-}"; shift || true
case "$sub" in
  list) cmd_list ;;
  ""|-h|--help|help)
    sed -n '2,12p' "$0" | sed 's/^# //'
    echo
    cmd_list
    ;;
  plan|deploy|rollback|history|cleanup)
    if [[ $# -lt 1 ]]; then
      err "用法: $0 $sub <project> [选项]"
      echo "可用项目: ${!PROJECT_INFO[*]}" >&2
      exit 2
    fi
    project="$1"; shift
    project_script="$PROJECTS_DIR/$project.sh"
    if [[ ! -f "$project_script" ]]; then
      err "未知项目: $project"
      echo "可用项目: ${!PROJECT_INFO[*]}" >&2
      exit 2
    fi
    exec "$project_script" "$sub" "$@"
    ;;
  *) err "未知子命令: $sub"; exit 2 ;;
esac
