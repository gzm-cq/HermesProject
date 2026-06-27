# common.sh — 部署共享函数库
#
# 本文件无 shebang，不可直接执行。
# 由 deploy/projects/*.sh 通过 source 加载。
# 期望调用方已设置以下变量:
#   PROJECT_NAME, PROJECT_SRC_REL, PROJECT_TGT, PROJECT_SVC
#   LEGACY_FILES (bash 数组, 可选)
#   SKILLS_SRC, SKILLS_TGT (可选)

set -euo pipefail
shopt -s globstar nullglob extglob

# ===== 颜色 =====
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_BLU=$'\033[34m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YLW=''; C_BLU=''; C_DIM=''; C_RST=''
fi
log()  { echo "${C_BLU}[deploy]${C_RST} $*"; }
ok()   { echo "${C_GRN}[ ok  ]${C_RST} $*"; }
warn() { echo "${C_YLW}[warn ]${C_RST} $*"; }
err()  { echo "${C_RED}[error]${C_RST} $*" >&2; }

# ===== 路径 =====
# BASH_SOURCE[0] 指向 lib/common.sh，因此 ..
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST_DIR="$SCRIPT_DIR/manifests"
BACKUP_ROOT="/root/.hermes/backups"

# ===== 校验项目配置 =====
validate_project() {
  PROJECT_TGT="${PROJECT_TGT%/}"
  PROJECT_SRC_ABS="$PROJECT_ROOT/$PROJECT_SRC_REL"
  PROJECT_MANIFEST="$MANIFEST_DIR/${PROJECT_NAME}.manifest"
  if [[ ! -d "$PROJECT_SRC_ABS" ]]; then err "源目录不存在: $PROJECT_SRC_ABS"; exit 1; fi
  if [[ ! -f "$PROJECT_MANIFEST" ]]; then err "清单不存在: $PROJECT_MANIFEST"; exit 1; fi
}

# ===== 解析 manifest → 文件列表 =====
# 输出: 每行一条相对项目源根的文件路径
expand_manifest() {
  local manifest="$1" src_root="$2"
  local includes=() excludes=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"; line="${line##+([[:space:]])}"; line="${line%%+([[:space:]])}"
    [[ -z "$line" ]] && continue
    if [[ "$line" == !* ]]; then excludes+=("${line:1}"); else includes+=("$line"); fi
  done < "$manifest"

  local matched=()
  pushd "$src_root" >/dev/null
  for pat in "${includes[@]}"; do
    for f in $pat; do [[ -f "$f" ]] && matched+=("$f"); done
  done
  popd >/dev/null

  # 去重
  local unique=()
  declare -A seen=()
  for f in "${matched[@]}"; do
    [[ -z "${seen[$f]:-}" ]] && unique+=("$f") && seen[$f]=1
  done

  # 按排除模式过滤（用 case 以支持 ** globstar 匹配）
  for f in "${unique[@]}"; do
    local skip=0
    for ex in "${excludes[@]}"; do
      # shellcheck disable=SC2254
      case "$f" in
        $ex) skip=1; break ;;
      esac
    done
    [[ $skip -eq 0 ]] && echo "$f"
  done | sort
}

# ===== 子命令: plan =====
cmd_plan() {
  log "项目: ${C_GRN}$PROJECT_NAME${C_RST}"
  log "源  : $PROJECT_SRC_ABS"
  log "标  : $PROJECT_TGT"
  log "重启: ${PROJECT_SVC:-<none>}"
  log "清单: $PROJECT_MANIFEST"
  echo
  log "==> 展开后将部署的文件清单:"
  local files; files=$(expand_manifest "$PROJECT_MANIFEST" "$PROJECT_SRC_ABS")
  if [[ -z "$files" ]]; then warn "无匹配文件"; return; fi
  local n=0
  while IFS= read -r f; do echo "  + $f"; n=$((n+1)); done <<<"$files"
  echo
  ok "共 $n 个文件待部署"

  # Skill 预览
  if [[ -n "${SKILLS_SRC:-}" && -n "${SKILLS_TGT:-}" ]]; then
    local skill_src_abs="$PROJECT_ROOT/$SKILLS_SRC"
    if [[ -d "$skill_src_abs" ]]; then
      local skill_files; skill_files=$(find "$skill_src_abs" -type f | sort)
      local sn=0
      echo
      log "==> 将部署的 Skill 文件 (→ $SKILLS_TGT):"
      if [[ -n "$skill_files" ]]; then
        while IFS= read -r sf; do
          local rel="${sf#$skill_src_abs/}"
          echo "  + skills/$rel"
          sn=$((sn+1))
        done <<<"$skill_files"
      fi
      echo
      ok "共 $sn 个 Skill 文件待部署"
    fi
  fi
}

# ===== 子命令: deploy =====
cmd_deploy() {
  local auto_yes="${1:-no}"

  local files; files=$(expand_manifest "$PROJECT_MANIFEST" "$PROJECT_SRC_ABS")
  [[ -z "$files" ]] && { err "清单展开为空，拒绝部署"; exit 1; }
  local total; total=$(echo "$files" | wc -l)

  log "项目: ${C_GRN}$PROJECT_NAME${C_RST}  (文件数: $total)"
  log "源  : $PROJECT_SRC_ABS"
  log "标  : $PROJECT_TGT"
  log "重启: ${PROJECT_SVC:-<none>}"

  if [[ "$auto_yes" != "--yes" ]]; then
    read -r -p "确认部署？(yes/N) " ans
    [[ "$ans" == "yes" ]] || { warn "已取消"; exit 0; }
  fi

  local ts; ts=$(date +%Y%m%d-%H%M%S)
  local backup_dir="$BACKUP_ROOT/$PROJECT_NAME/$ts"
  local latest_link="$BACKUP_ROOT/$PROJECT_NAME/latest"
  sudo mkdir -p "$backup_dir" "$PROJECT_TGT"
  sudo touch "$backup_dir/.deployed-files" "$backup_dir/.backed-up-files"

  log "==> 文件级备份当前 target 中将被覆盖的文件 → $backup_dir"
  while IFS= read -r rel; do
    local tgt_abs="$PROJECT_TGT/$rel"
    if [[ -f "$tgt_abs" ]]; then
      sudo mkdir -p "$(dirname "$backup_dir/$rel")"
      sudo cp -p "$tgt_abs" "$backup_dir/$rel"
      echo "$tgt_abs" | sudo tee -a "$backup_dir/.backed-up-files" >/dev/null
    fi
  done <<<"$files"

  log "==> 文件级写入新版本 → $PROJECT_TGT"
  while IFS= read -r rel; do
    local src_abs="$PROJECT_SRC_ABS/$rel" tgt_abs="$PROJECT_TGT/$rel"
    sudo mkdir -p "$(dirname "$tgt_abs")"
    sudo cp -p "$src_abs" "$tgt_abs"
    echo "$tgt_abs" | sudo tee -a "$backup_dir/.deployed-files" >/dev/null
  done <<<"$files"

  # 元信息
  {
    echo "project=$PROJECT_NAME"
    echo "timestamp=$ts"
    echo "source=$PROJECT_SRC_ABS"
    echo "target=$PROJECT_TGT"
    echo "restart_service=${PROJECT_SVC:-}"
    echo "file_count=$total"
  } | sudo tee "$backup_dir/.meta" >/dev/null

  # 防残留：清理上次部署中本次未覆盖的旧文件
  if [[ -L "$latest_link" || -d "$latest_link" ]]; then
    local prev_list="$latest_link/.deployed-files"
    if [[ -f "$prev_list" ]]; then
      log "==> 防残留扫描（对比上一次部署清单）"
      local cur_set; cur_set=$(sudo sort -u "$backup_dir/.deployed-files" | sed 's|//|/|g')
      local stale_count=0
      while IFS= read -r old_path; do
        [[ -z "$old_path" ]] && continue
        old_path="${old_path//\/\//\/}"
        if ! grep -Fxq "$old_path" <<<"$cur_set"; then
          if [[ -f "$old_path" ]]; then
            local rel="${old_path#$PROJECT_TGT/}"
            sudo mkdir -p "$(dirname "$backup_dir/$rel")"
            [[ -f "$backup_dir/$rel" ]] || sudo cp -p "$old_path" "$backup_dir/$rel"
            sudo rm -f "$old_path"
            echo "$old_path" | sudo tee -a "$backup_dir/.backed-up-files" >/dev/null
            warn "已删除残留: $old_path"
            stale_count=$((stale_count+1))
          fi
        fi
      done < <(sudo cat "$prev_list")
      [[ $stale_count -eq 0 ]] && ok "无残留文件"
    fi
  else
    # 首次部署（latest 不存在，可能从旧部署系统迁移）
    # 先清理旧系统平铺文件
    if [[ -n "${LEGACY_FILES[*]:-}" ]]; then
      log "==> 清理旧系统平铺文件（${C_DIM}sync-scripts.sh 遗留${C_RST}）"
      for leg_pattern in "${LEGACY_FILES[@]}"; do
        if [[ "$leg_pattern" == *\** ]]; then
          # glob 展开（在目标父目录）
          for leg_file in $leg_pattern; do
            [[ -f "$leg_file" ]] || continue
            local leg_rel="${leg_file##*/}"
            sudo mkdir -p "$(dirname "$backup_dir/legacy/$leg_rel")"
            sudo cp -p "$leg_file" "$backup_dir/legacy/$leg_rel"
            echo "$leg_file" | sudo tee -a "$backup_dir/.backed-up-files" >/dev/null
            sudo rm -f "$leg_file"
            warn "已删除旧文件: $leg_file"
          done
        else
          [[ -f "$leg_pattern" ]] || continue
          local leg_rel="${leg_pattern##*/}"
          sudo mkdir -p "$(dirname "$backup_dir/legacy/$leg_rel")"
          sudo cp -p "$leg_pattern" "$backup_dir/legacy/$leg_rel"
          echo "$leg_pattern" | sudo tee -a "$backup_dir/.backed-up-files" >/dev/null
          sudo rm -f "$leg_pattern"
          warn "已删除旧文件: $leg_pattern"
        fi
      done
    fi

    # 全量对比目标目录. Some projects deploy into a shared target directory
    # (for example /root/.hermes/scripts). Those projects must opt out, or the
    # first deployment would remove unrelated projects as "stale" files.
    if [[ "${FIRST_DEPLOY_CLEANUP:-true}" == "false" ]]; then
      warn "跳过首次部署全量防残留扫描（共享目标目录）"
    else
      log "==> 防残留扫描（首次部署，全量对比目标目录）"
      local cur_set; cur_set=$(sudo sort -u "$backup_dir/.deployed-files" | sed 's|//|/|g')
      local stale_count=0
      while IFS= read -r exist_path; do
        [[ -z "$exist_path" ]] && continue
        if ! grep -Fxq "$exist_path" <<<"$cur_set"; then
          local rel="${exist_path#$PROJECT_TGT/}"
          sudo mkdir -p "$(dirname "$backup_dir/$rel")"
          [[ -f "$backup_dir/$rel" ]] || sudo cp -p "$exist_path" "$backup_dir/$rel"
          sudo rm -f "$exist_path"
          echo "$exist_path" | sudo tee -a "$backup_dir/.backed-up-files" >/dev/null
          warn "已删除残留: $exist_path"
          stale_count=$((stale_count+1))
        fi
      done < <(sudo find "$PROJECT_TGT" -type f 2>/dev/null)
      [[ $stale_count -eq 0 ]] && ok "无残留文件"
    fi
  fi

  # 更新 latest
  sudo rm -f "$latest_link"
  sudo ln -s "$ts" "$latest_link"

  # 修复属主
  sudo chown -R root:root "$PROJECT_TGT"

  # ===== Skill 部署 =====
  if [[ -n "${SKILLS_SRC:-}" && -n "${SKILLS_TGT:-}" ]]; then
    local skill_src_abs="$PROJECT_ROOT/$SKILLS_SRC"
    if [[ -d "$skill_src_abs" ]]; then
      log "==> 部署 Skill: $skill_src_abs → $SKILLS_TGT"
      sudo mkdir -p "$SKILLS_TGT"
      local skill_count=0
      while IFS= read -r -d '' sf; do
        local rel="${sf#$skill_src_abs/}"
        local install_path="$SKILLS_TGT/$rel"
        # 备份已有 skill 文件
        if [[ -f "$install_path" ]]; then
          sudo mkdir -p "$(dirname "$backup_dir/skills/$rel")"
          sudo cp -p "$install_path" "$backup_dir/skills/$rel"
          echo "$install_path" | sudo tee -a "$backup_dir/.backed-up-files" >/dev/null
        fi
        sudo mkdir -p "$(dirname "$install_path")"
        sudo cp -p "$sf" "$install_path"
        echo "$install_path" | sudo tee -a "$backup_dir/.deployed-files" >/dev/null
        skill_count=$((skill_count+1))
      done < <(find "$skill_src_abs" -type f -print0)
      sudo chown -R root:root "$SKILLS_TGT"
      ok "Skill 部署完成: $skill_count 个文件"
    else
      warn "Skill 源码目录不存在: $skill_src_abs"
    fi
  fi

  # 重启服务（如需）
  if [[ -n "$PROJECT_SVC" ]]; then
    log "==> 重启服务: $PROJECT_SVC"
    sudo systemctl restart "$PROJECT_SVC"
    if sudo systemctl is-active --quiet "$PROJECT_SVC"; then
      ok "$PROJECT_SVC 运行正常"
    else
      err "$PROJECT_SVC 启动失败，请用以下命令查看："
      err "  sudo systemctl status $PROJECT_SVC"
      exit 1
    fi
  fi

  ok "部署完成: $PROJECT_NAME ($total 文件)"
  ok "备份目录: $backup_dir"
  ok "回滚命令: $SCRIPT_DIR/deploy.sh rollback $PROJECT_NAME $ts"
}

# ===== 子命令: rollback =====
cmd_rollback() {
  local ts="${1:-latest}"

  local backup_dir="$BACKUP_ROOT/$PROJECT_NAME/$ts"
  [[ ! -d "$backup_dir" ]] && { err "备份不存在: $backup_dir"; exit 1; }

  log "项目: ${C_GRN}$PROJECT_NAME${C_RST}"
  log "回滚至: $backup_dir"
  if [[ -f "$backup_dir/.meta" ]]; then
    sudo sed 's/^/  /' "$backup_dir/.meta"
  fi

  read -r -p "确认回滚？(yes/N) " ans
  [[ "$ans" == "yes" ]] || { warn "已取消"; exit 0; }

  local deployed="$backup_dir/.deployed-files"
  local backedup="$backup_dir/.backed-up-files"

  # 第一步：删除本次部署写入的所有文件 → 确保无残留
  if [[ -f "$deployed" ]]; then
    log "==> 删除本次部署写入的所有文件（防残留）"
    while IFS= read -r path; do
      [[ -z "$path" ]] && continue
      if [[ -f "$path" ]]; then sudo rm -f "$path"; fi
    done < <(sudo cat "$deployed")
    ok "已清空本次部署写入"
  else
    warn ".deployed-files 缺失，跳过删除步骤"
  fi

  # 第二步：还原备份（按文件逐个还原）
  if [[ -f "$backedup" ]]; then
    log "==> 还原备份文件"
    while IFS= read -r path; do
      [[ -z "$path" ]] && continue
      local rel="${path#$PROJECT_TGT/}"
      # 旧系统平铺文件不在 PROJECT_TGT 下，用 basename 查找备份
      [[ "$rel" == "$path" ]] && rel="${path##*/}"
      local bak="$backup_dir/$rel"
      # Skill 备份：如果 basename 路径不存在，且该项目有 SKILLS 映射，
      # 尝试从 skills/ 子目录（按技能目标路径结构）查找
      if [[ ! -f "$bak" && -n "${SKILLS_TGT:-}" ]]; then
        local skill_rel="${path#$SKILLS_TGT/}"
        [[ "$skill_rel" != "$path" ]] && bak="$backup_dir/skills/$skill_rel"
      fi
      if [[ -f "$bak" ]]; then
        sudo mkdir -p "$(dirname "$path")"
        sudo cp -p "$bak" "$path"
      fi
    done < <(sudo cat "$backedup")
    ok "已还原 $(sudo cat "$backedup" | wc -l) 个文件"
  else
    warn ".backed-up-files 缺失，跳过还原步骤"
  fi

  # 修复属主
  sudo chown -R root:root "$PROJECT_TGT"

  # 重启服务（如需）
  if [[ -n "$PROJECT_SVC" ]]; then
    log "==> 重启服务: $PROJECT_SVC"
    sudo systemctl restart "$PROJECT_SVC"
    sudo systemctl is-active --quiet "$PROJECT_SVC" && ok "$PROJECT_SVC 运行正常" || { err "$PROJECT_SVC 启动失败"; exit 1; }
  fi

  # 回滚后更新 latest 链接
  if [[ "$ts" != "latest" ]]; then
    sudo rm -f "$BACKUP_ROOT/$PROJECT_NAME/latest"
    sudo ln -s "$ts" "$BACKUP_ROOT/$PROJECT_NAME/latest"
  fi

  ok "回滚完成"
}

# ===== 子命令: history =====
cmd_history() {
  local dir="$BACKUP_ROOT/$PROJECT_NAME"
  [[ ! -d "$dir" ]] && { warn "尚无部署历史"; return; }
  log "项目 $PROJECT_NAME 部署历史 (新→旧):"
  printf "  %-22s %-8s %s\n" "TIMESTAMP" "FILES" "META"
  while IFS= read -r d; do
    [[ "$d" == "latest" ]] && continue
    local meta="$dir/$d/.meta" cnt="?"
    [[ -f "$dir/$d/.deployed-files" ]] && cnt=$(sudo cat "$dir/$d/.deployed-files" | wc -l)
    local marker=""
    [[ "$(readlink "$dir/latest" 2>/dev/null || true)" == "$d" ]] && marker=" ${C_GRN}<- latest${C_RST}"
    printf "  %-22s %-8s %s%s\n" "$d" "$cnt" "$([[ -f $meta ]] && echo OK || echo NO_META)" "$marker"
  done < <(ls -1t "$dir" 2>/dev/null)
}

# ===== 子命令: cleanup =====
cmd_cleanup() {
  local keep=5 uninstall=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --keep) keep="$2"; shift 2 ;;
      --uninstall) uninstall=true; shift ;;
      *) err "未知选项: $1"; exit 2 ;;
    esac
  done

  local dir="$BACKUP_ROOT/$PROJECT_NAME"
  [[ ! -d "$dir" ]] && { warn "$PROJECT_NAME 尚无备份记录"; return; }

  if $uninstall; then
    # --- 完全卸载 ---
    log "项目: ${C_GRN}$PROJECT_NAME${C_RST}  (${C_RED}完全卸载${C_RST})"
    local deployed="$dir/latest/.deployed-files"
    if [[ -f "$deployed" ]]; then
      log "==> 删除本次部署写入的所有文件"
      while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        [[ -f "$path" ]] && sudo rm -f "$path"
      done < <(sudo cat "$deployed")
    fi

    # 清理旧平铺文件
    if [[ -n "${LEGACY_FILES[*]:-}" ]]; then
      log "==> 删除旧系统平铺文件"
      for leg_pattern in "${LEGACY_FILES[@]}"; do
        for f in $leg_pattern; do [[ -f "$f" ]] && sudo rm -f "$f" && warn "已删除: $f"; done
      done
    fi

    # 清理 Skill 部署目录
    if [[ -n "${SKILLS_TGT:-}" ]]; then
      local skill_parent="$(dirname "$SKILLS_TGT")"
      if [[ -d "$skill_parent" ]]; then
        find "$SKILLS_TGT" -type f -name "*.md" 2>/dev/null -exec sudo rm -f {} \; 2>/dev/null || true
        rmdir "$SKILLS_TGT" 2>/dev/null && warn "已删除 skill 目录: $SKILLS_TGT" || true
      fi
    fi

    log "==> 删除备份目录"
    sudo rm -rf "$dir"

    # 清理空父目录
    local parent="$(dirname "$PROJECT_TGT")"
    if [[ -d "$PROJECT_TGT" ]]; then
      rmdir "$PROJECT_TGT" 2>/dev/null && warn "已删除空目标目录: $PROJECT_TGT" || true
    fi

    if [[ -n "$PROJECT_SVC" ]]; then
      warn "注意：服务 $PROJECT_SVC 未停止，如不再需要请手动处理"
    fi

    ok "已完全卸载: $PROJECT_NAME"
    return
  fi

  # --- 清理历史备份（保留最近 N 次） ---
  local all_snapshots=()
  while IFS= read -r d; do
    [[ "$d" != "latest" ]] && all_snapshots+=("$d")
  done < <(ls -1t "$dir" 2>/dev/null)

  local total="${#all_snapshots[@]}"
  if [[ $total -le $keep ]]; then
    ok "历史备份共 $total 次，未超过保留上限 $keep，无需清理"
    return
  fi

  local to_delete=$((total - keep))
  log "历史备份: $total 次，保留最近 $keep 次，删除 $to_delete 次"
  read -r -p "确认删除 $to_delete 个旧备份？(yes/N) " ans
  [[ "$ans" == "yes" ]] || { warn "已取消"; exit 0; }

  local deleted=0
  for ((i=keep; i<total; i++)); do
    local snap="${all_snapshots[$i]}"
    if sudo rm -rf "$dir/$snap"; then
      warn "已删除: $snap"
      deleted=$((deleted+1))
    fi
  done
  ok "清理完成，共删除 $deleted 个旧备份"
}

# ===== 自动 dispatch（被 source 时 $@ 已传入）=====
validate_project
sub="${1:-}"; shift || true
case "$sub" in
  plan)     cmd_plan "$@" ;;
  deploy)   cmd_deploy "$@" ;;
  rollback) cmd_rollback "$@" ;;
  history)  cmd_history "$@" ;;
  cleanup)  cmd_cleanup "$@" ;;
  *)        err "未知子命令: $sub"; exit 2 ;;
esac
