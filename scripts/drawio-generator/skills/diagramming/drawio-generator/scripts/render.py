#!/usr/bin/env python3
"""drawio-generator CLI 入口 — 调用核心库生成 .drawio / SVG 矢量图"""
import json
import sys
import os

# 查找 drawio_generator 包：搜索 pyproject.toml（开发环境）或已知部署路径
_script_dir = os.path.dirname(os.path.abspath(__file__))

def _find_src_path():
    """返回 drawio_generator 包所在 src 目录，或 None"""
    # 1. 向上搜索 pyproject.toml（开发环境，搜索 8 层以覆盖技能目录深度）
    d = _script_dir
    for _ in range(8):
        parent = os.path.dirname(d)
        if parent == d:
            break
        if os.path.isfile(os.path.join(d, "pyproject.toml")):
            src = os.path.join(d, "src")
            if os.path.isdir(os.path.join(src, "drawio_generator")):
                return src
        d = parent

    # 2. 已知部署路径（skill → 项目脚本的固定结构）
    deployed = os.path.join(
        os.path.sep, "root", ".hermes", "scripts", "drawio-generator", "src"
    )
    if os.path.isdir(os.path.join(deployed, "drawio_generator")):
        return deployed

    # 3. 开发环境 fallback（从 skill 目录上溯到 HermesProject 根 + scripts/src）
    workspace = os.path.abspath(os.path.join(_script_dir,
        "..", "..", "..", "..", "..", "..", ".."))
    fallback = os.path.join(workspace, "scripts", "drawio-generator", "src")
    if os.path.isdir(os.path.join(fallback, "drawio_generator")):
        return fallback

    return None

_src_path = _find_src_path()
if _src_path is not None:
    sys.path.insert(0, _src_path)

from drawio_generator.render import render as _render


def main():
    if len(sys.argv) < 3:
        print("Usage: render.py <layout_json> <output_path>", file=sys.stderr)
        sys.exit(1)

    layout_path = sys.argv[1]
    output_path = sys.argv[2]

    if not os.path.isfile(layout_path):
        print(f"Error: layout file not found: {layout_path}", file=sys.stderr)
        sys.exit(1)

    with open(layout_path, encoding="utf-8") as f:
        plan = json.load(f)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    _render(plan, output_path)


if __name__ == "__main__":
    main()
