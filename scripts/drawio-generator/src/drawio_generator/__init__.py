"""drawio-generator: 根据自然语言描述生成 draw.io/SVG 矢量图"""

from .render import generate_svg, render, PALETTES, DEFAULT_PALETTE, VERSION
from .layout import layout_plan  # noqa: F401
from .templates import (
    TEMPLATES,
    apply_template,
    microservices_template,
    network_topology_template,
    dataflow_template,
    er_diagram_template,
)
from .shape_library import search_shape, get_shape, list_shapes  # noqa: F401
from .aiicons import search_icon, get_icon, list_icons  # noqa: F401
from .legend import build_legend  # noqa: F401
from .validator import (  # noqa: F401
    validate_plan,
    score_layout,
    check_edge_crossings,
    check_edge_through_vertex,
)
from .style_presets import (  # noqa: F401
    BUILT_IN_PRESETS as STYLE_PRESETS,
    load_preset,
    list_presets,
)
from .diagram_presets import (  # noqa: F401
    PRESETS as DIAGRAM_PRESETS,
    list_diagram_types,
    apply_diagram_type,
)
from .edge_styles import (  # noqa: F401
    get_base_edge_style,
    apply_flow_animation,
    distribute_ports,
    check_arrowhead_gap,
)

__version__ = VERSION

__all__ = [
    "generate_svg", "render", "layout_plan",
    "PALETTES", "DEFAULT_PALETTE",
    "TEMPLATES", "apply_template",
    "microservices_template", "network_topology_template",
    "dataflow_template", "er_diagram_template",
    "search_shape", "get_shape", "list_shapes",
    "search_icon", "get_icon", "list_icons",
    "build_legend",
    "validate_plan", "score_layout", "check_edge_crossings", "check_edge_through_vertex",
    "STYLE_PRESETS", "load_preset", "list_presets",
    "DIAGRAM_PRESETS", "list_diagram_types", "apply_diagram_type",
    "get_base_edge_style", "apply_flow_animation", "distribute_ports", "check_arrowhead_gap",
]
