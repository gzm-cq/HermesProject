#!/usr/bin/env python3
"""图类型预设 — 7 种预设定义，应用于 plan 的前处理"""


PRESETS = {
    "architecture": {
        "description": "分层架构图",
        "layout": "TB",
        "default_shape": "card",
        "colors": {"client": "node_blue", "server": "node_green",
                    "data": "node_orange", "external": "node_red"},
        "node_w": 150, "node_h": 55,
    },
    "flowchart": {
        "description": "流程图",
        "layout": "TB",
        "shapes": {"start": "card", "process": "card",
                    "decision": "rhombus", "io": "parallelogram"},
        "colors": {"start": "node_green", "process": "node_blue",
                    "decision": "node_yellow", "error": "node_red"},
        "node_w": 140, "node_h": 50,
    },
    "ml_model": {
        "description": "ML/DL 模型架构图",
        "layout": "TB",
        "colors": {"input": "node_green", "conv": "node_blue",
                    "attention": "node_purple", "rnn": "node_yellow",
                    "fc": "node_orange", "loss": "node_red"},
        "node_w": 130, "node_h": 45,
    },
    "network_topology": {
        "description": "网络拓扑图",
        "layout": "TB",
        "edge_style": "orthogonal",
    },
    "erd": {
        "description": "ER 数据模型图",
        "layout": "TB",
        "node_w": 180, "node_h": 120,
    },
    "swimlane": {
        "description": "泳道流程图",
        "layout": "LR",
        "node_w": 120, "node_h": 50,
    },
    "pipeline": {
        "description": "数据管线图",
        "layout": "LR",
        "flow_animation": True,
        "node_w": 130, "node_h": 55,
    },
}


def list_diagram_types():
    """返回所有可用图类型及其说明列表：[(key, description), ...]"""
    return [(k, v.get("description", "")) for k, v in PRESETS.items()]


def apply_diagram_type(plan_dict):
    """根据 plan_dict['diagram_type'] 应用预设，返回修改后的 plan_dict。"""
    dtype = plan_dict.get("diagram_type", "")
    if not dtype or dtype not in PRESETS:
        return plan_dict

    preset = PRESETS[dtype]
    plan = dict(plan_dict)
    # 深拷贝节点列表，避免修改调用方原 dict
    if "nodes" in plan:
        plan["nodes"] = [dict(n) for n in plan["nodes"]]
    if "edges" in plan:
        plan["edges"] = [dict(e) for e in plan["edges"]]
    plan.setdefault("layout_direction", preset.get("layout", "TB"))

    if ("default_shape" in preset or preset.get("shapes")) and not plan.get("diagram_type_shape_applied"):
        shapes = preset.get("shapes", {})
        default = preset.get("default_shape")
        for n in plan.get("nodes", []):
            if "shape" not in n:
                role = n.get("role", "")
                if role in shapes:
                    n["shape"] = shapes[role]
                elif default:
                    n["shape"] = default
        plan["diagram_type_shape_applied"] = True

    for n in plan.get("nodes", []):
        if "color" not in n:
            role = n.get("role", "")
            colors = preset.get("colors", {})
            if role in colors:
                n["color"] = colors[role]

    if preset.get("flow_animation") and "flow_animation" not in plan:
        plan["flow_animation"] = True

    if "edge_style" in preset and "edge_style" not in plan:
        plan["edge_style"] = preset["edge_style"]

    # 可根据预设覆盖节点尺寸
    nw = preset.get("node_w")
    nh = preset.get("node_h")
    if nw or nh:
        for n in plan.get("nodes", []):
            if nw and "w" not in n:
                n["w"] = nw
            if nh and "h" not in n:
                n["h"] = nh

    return plan