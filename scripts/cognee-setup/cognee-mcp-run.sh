#!/usr/bin/env bash
# cognee-mcp 启动包装脚本（P0-2）
# 由 Hermes 核心以 stdio 方式拉起（config.yaml mcp_servers.cognee.command 指向本脚本）
#
# 需要设置 PYTHONPATH 指向 cognee-mcp-pkg（pip --target 安装目录），
# 否则无法 import cognee_mcp 模块。
set -euo pipefail

COGNEE_PKG="/root/.hermes/cognee-mcp-pkg"
PYTHONPATH="$COGNEE_PKG${PYTHONPATH:+:$PYTHONPATH}" \
exec "$COGNEE_PKG/bin/cognee-mcp" "$@"