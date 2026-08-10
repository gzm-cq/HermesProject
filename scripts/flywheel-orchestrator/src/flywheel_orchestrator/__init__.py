"""flywheel-orchestrator — Flywheel 11 cronjobs 统一调度编排层。

入口:
    python3 -m flywheel_orchestrator.cli list
    python3 -m flywheel_orchestrator.cli run <task>
    python3 -m flywheel_orchestrator.cli dry-run <task>

所有任务通过 Task 对象声明：保留各子项目独立 venv 调用入口，
wrapper 层统一调用 + dry-run，不合并代码和依赖。
"""
