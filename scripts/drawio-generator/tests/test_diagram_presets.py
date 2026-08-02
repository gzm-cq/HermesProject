"""图类型预设和样式预设测试"""
from drawio_generator.diagram_presets import PRESETS, apply_diagram_type
from drawio_generator.style_presets import load_preset, list_presets, BUILT_IN_PRESETS


def test_all_presets_defined():
    """7 种预设全部定义"""
    assert len(PRESETS) == 7
    assert "architecture" in PRESETS
    assert "flowchart" in PRESETS
    assert "ml_model" in PRESETS
    assert "network_topology" in PRESETS
    assert "erd" in PRESETS
    assert "swimlane" in PRESETS
    assert "pipeline" in PRESETS


def test_apply_diagram_type():
    """应用预设后节点获得正确默认形状"""
    plan = {
        "diagram_type": "architecture",
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "edges": [],
    }
    result = apply_diagram_type(plan)
    for n in result["nodes"]:
        assert n["shape"] == "card"


def test_apply_diagram_type_architecture_colors():
    """架构图预设的配色映射"""
    plan = {
        "diagram_type": "architecture",
        "nodes": [{"id": "a", "label": "A", "role": "client"},
                  {"id": "b", "label": "B", "role": "server"}],
        "edges": [],
    }
    result = apply_diagram_type(plan)
    assert result["nodes"][0]["color"] == "node_blue"
    assert result["nodes"][1]["color"] == "node_green"


def test_apply_diagram_type_flowchart_shapes():
    """流程图预设的形状映射"""
    plan = {
        "diagram_type": "flowchart",
        "nodes": [{"id": "s", "label": "Start", "role": "start"},
                  {"id": "p", "label": "Process"},
                  {"id": "d", "label": "Decision", "role": "decision"}],
        "edges": [],
    }
    result = apply_diagram_type(plan)
    assert result["nodes"][0]["shape"] == "card"  # role=start (ellipse->card)


def test_apply_diagram_type_no_change():
    """无 diagram_type 时 plan 不变"""
    plan = {"nodes": [{"id": "a", "label": "A"}], "edges": []}
    result = apply_diagram_type(plan)
    assert result == plan


def test_style_presets_default():
    """默认预设可加载"""
    palette = load_preset("default")
    assert palette is not None
    assert "node_blue" in palette


def test_style_presets_dark():
    """暗色预设可加载"""
    palette = load_preset("dark")
    assert palette is not None
    assert palette["bg"].lower() == "#1a1a2e"
    assert "node_blue" in palette


def test_style_presets_colorblind():
    """色盲友好预设可加载"""
    palette = load_preset("colorblind-safe")
    assert palette is not None
    assert "node_blue" in palette


def test_style_presets_unknown():
    """未知预设返回 None"""
    palette = load_preset("nonexistent")
    assert palette is None


def test_list_presets():
    """列出所有预设"""
    presets = list_presets()
    assert "default" in presets
    assert "dark" in presets
    assert "colorblind-safe" in presets
    assert len(presets) == 3