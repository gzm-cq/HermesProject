"""报告规划 StateGraph 包"""
try:
    from .report_graph import build_report_graph, build_goal_graph, run_planning, run_goal_definition, _optimize_goal as optimize_goal
except ImportError:
    build_report_graph = None  # type: ignore[assignment]
    build_goal_graph = None  # type: ignore[assignment]
    run_planning = None  # type: ignore[assignment]
    run_goal_definition = None  # type: ignore[assignment]
    optimize_goal = None  # type: ignore[assignment]

from .material_service import MaterialService, MaterialPack
from .types import ChapterPrompt, ReportGoal
