"""测试辅助工具 — 共享的 fixture、工厂函数和常量。

集中管理跨测试文件复用的工具，避免重复定义。
"""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


# ---- drawio 测试数据工厂 ----

def create_drawio_file(filepath, nodes, edges, *, title="Test", width=500, height=300):
    """创建一个包含指定节点和边的 .drawio 文件。

    此工厂函数在 test_buildup.py 和 test_svgflow.py 中均有重复定义，
    统一迁移到此处。
    """
    mxfile = ET.Element("mxfile")
    ET.SubElement(mxfile, "diagram", id="1", name=title)
    model = ET.SubElement(mxfile.find("diagram"), "mxGraphModel")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    nid = 100
    node_ids = {}
    for node in nodes:
        cid = str(nid)
        nid += 1
        node_ids[node["id"]] = cid
        style = node.get("style", "rounded=1;whiteSpace=wrap;html=1;")
        cell = ET.SubElement(
            root, "mxCell",
            id=cid, parent="1",
            value=node.get("label", ""),
            vertex="1",
            style=style,
        )
        ET.SubElement(
            cell, "mxGeometry",
            x=str(node.get("x", 0)),
            y=str(node.get("y", 0)),
            width=str(node.get("w", 100)),
            height=str(node.get("h", 50)),
            **{"as": "geometry"},
        )

    for edge in edges:
        src = node_ids.get(edge["from"], "")
        tgt = node_ids.get(edge["to"], "")
        cid = str(nid)
        nid += 1
        edge_style = edge.get("style", "edgeStyle=orthogonalEdgeStyle;")
        cell = ET.SubElement(
            root, "mxCell",
            id=cid, parent="1",
            source=src, target=tgt,
            edge="1",
            style=edge_style,
        )
        ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})

    tree = ET.ElementTree(mxfile)
    tree.write(filepath, encoding="utf-8", xml_declaration=True)


# ---- 渲染测试计划工厂 ----

def make_plan(**overrides):
    """创建最小可用的 plan dict，可通过 kwargs 覆盖字段。"""
    base = {
        "title": "Test",
        "width": 600,
        "height": 400,
        "nodes": [],
        "edges": [],
        "format": "svg",
    }
    base.update(overrides)
    return base


def make_node(**overrides):
    """创建最小可用的 node dict。"""
    base = {"id": "n1", "label": "Node", "x": 50, "y": 50, "w": 100, "h": 50}
    base.update(overrides)
    return base


def make_edge(**overrides):
    """创建最小可用的 edge dict。"""
    base = {"from": "a", "to": "b"}
    base.update(overrides)
    return base


# ---- 输出文件管理 ----

@pytest.fixture
def tmp_output_dir(tmp_path):
    """创建临时输出目录，返回路径字符串。"""
    out_dir = tmp_path / "output"
    out_dir.mkdir(exist_ok=True)
    return str(out_dir)


def write_temp_plan(plan, suffix=".json"):
    """将 plan dict 写入临时 JSON 文件，返回 (path, plan)。"""
    import json
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plan, f)
    return path


def render_to_temp(plan, suffix=".svg"):
    """将 plan 渲染到临时文件，返回 (path, plan)。"""
    from drawio_generator.render import render
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    render(plan, path)
    return path
