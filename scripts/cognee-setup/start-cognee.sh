#!/usr/bin/env bash
# Cognee 服务启动脚本（P0-2，2026-08-23 修订：Python 包路线，无 Docker 镜像依赖）
#
# 用法：
#   bash scripts/cognee-setup/start-cognee.sh up         # 启动 neo4j + 校验 cognee-mcp
#   bash scripts/cognee-setup/start-cognee.sh down       # 停止 neo4j
#   bash scripts/cognee-setup/start-cognee.sh check      # 仅校验 cognee-mcp 命令可用
#   bash scripts/cognee-setup/start-cognee.sh register   # 打印 config.yaml mcp_servers 注册段
#
# cognee-mcp 由 Hermes 核心以 stdio 方式拉起（见本脚本 register 输出），
# 不需本脚本常驻；本脚本只负责 neo4j 与安装校验。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COGNEE_PKG="/root/.hermes/cognee-mcp-pkg"
ACTION="${1:-up}"

COGNEE_MCP_BIN="$COGNEE_PKG/bin/cognee-mcp"

check_cognee() {
  if [ -x "$COGNEE_MCP_BIN" ]; then
    echo "[cognee] cognee-mcp 已安装: $COGNEE_MCP_BIN"
    PYTHONPATH="$COGNEE_PKG" "$COGNEE_MCP_BIN" --help >/dev/null 2>&1 \
      && echo "[cognee] cognee-mcp --help OK" \
      || echo "[cognee] 警告: cognee-mcp 运行自检失败 (可能缺 neo4j / LLM 配置)"
    return 0
  fi
  echo "[cognee] 未找到 $COGNEE_MCP_BIN"
  echo "[cognee] 请先安装: pip3 install --target=$COGNEE_PKG -i https://pypi.tuna.tsinghua.edu.cn/simple --extra-index-url https://download.pytorch.org/whl/cpu cognee-mcp"
  return 1
}

register_block() {
  cat <<'YAML'
  # === Cognee 知识图谱 MCP (P0-2, stdio, 改配置不破约束) ===
  # 追加到 /root/.hermes/config.yaml 的 mcp_servers 段（dict 格式，key=服务名）
  cognee:
    command: /root/.hermes/scripts/cognee-setup/cognee-mcp-run.sh
    args:
      - --transport
      - stdio
    timeout: 120
    connect_timeout: 60
    keepalive_interval: 86400
YAML
}

case "$ACTION" in
  up)
    check_cognee || true
    echo "[cognee] 启动 neo4j (docker.1ms.run/library/neo4j 已验证可拉)..."
    docker compose up -d neo4j
    docker compose ps neo4j
    echo "[cognee] 下一步: 将下方 register 段写入 config.yaml 后重启 Hermes 网关"
    register_block
    ;;
  down)
    echo "[cognee] 停止 neo4j..."
    docker compose down
    ;;
  check)
    check_cognee
    ;;
  register)
    register_block
    ;;
  *)
    echo "用法: $0 [up|down|check|register]"
    exit 1
    ;;
esac
