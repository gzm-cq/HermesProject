"""
Hermes State Manager — 状态管理器
提供跨会话持久化、进度跟踪和错误恢复
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeAlias

from .base import StatefulComponent
from ..config import get_config
from .exceptions import ReportAgentError, MemoryError

logger = logging.getLogger(__name__)

# 类型别名
TaskID: TypeAlias = str
StateData: TypeAlias = dict[str, Any]


@dataclass
class Checkpoint:
    """检查点"""
    task_id: TaskID
    phase: str
    state: StateData
    timestamp: float
    version: int

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "phase": self.phase,
            "state": self.state,
            "timestamp": self.timestamp,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        """从字典创建"""
        return cls(
            task_id=data["task_id"],
            phase=data["phase"],
            state=data.get("state", {}),
            timestamp=data.get("timestamp", time.time()),
            version=data.get("version", 1),
        )


@dataclass
class ProgressTracker:
    """进度追踪"""
    task_id: str
    total_steps: int
    completed_steps: int = 0
    current_step: str = ""
    started_at: float = 0.0
    completed_at: float | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def progress_pct(self) -> float:
        """进度百分比"""
        if self.total_steps == 0:
            return 0.0
        return min(self.completed_steps / self.total_steps, 1.0)

    @property
    def elapsed_seconds(self) -> float:
        """已耗时间（秒）"""
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def estimated_remaining(self) -> float | None:
        """估计剩余时间（秒）"""
        if self.progress_pct == 0:
            return None
        elapsed = self.elapsed_seconds
        return elapsed / self.progress_pct - elapsed

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "current_step": self.current_step,
            "progress_pct": round(self.progress_pct * 100, 1),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "errors": self.errors[-10:],  # 最多10个错误
            "metadata": self.metadata,
        }


class HermesStateManager(StatefulComponent):
    """
    状态管理器 — 跨会话持久化和错误恢复

    核心功能:
    - 多存储引擎 (SQLite → JSON → 内存)
    - 自动检查点（时间间隔）
    - 进度追踪和报告
    - 错误恢复和上下文保留
    - 任务状态快照

    存储引擎:
    1. SQLite: 主要存储（性能好，支持并发）
    2. JSON文件: 自动回退（SQLite不可用时）
    3. 内存: 极限降级（无文件系统时）

    用法:
        manager = HermesStateManager()
        manager.create_task("report_001", total_steps=5)
        manager.advance_step("report_001", "搜索数据")
        manager.save_state("report_001", {"results": [...]})
        state = manager.load_state("report_001")
    """

    COMPONENT_NAME = "HermesStateManager"
    COMPONENT_VERSION = "1.0.0"
    COMPONENT_DESCRIPTION = "跨会话状态管理、进度追踪和错误恢复"

    def __init__(self, config: Any | None = None) -> None:
        self._engine: str = "memory"
        self._storage_dir: Path | None = None
        self._tasks: dict[str, ProgressTracker] = {}
        self._checkpoints: dict[str, list[Checkpoint]] = {}
        self._checkpoint_interval: float = 60.0  # 秒
        self._last_checkpoint_time: float = 0.0
        super().__init__(config)

    def _initialize_internal(self) -> None:
        """初始化存储引擎"""
        cfg = self._config.system_config
        self._storage_dir = cfg.working_dir / "state"
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        # 尝试选择存储引擎
        self._engine = self._select_engine()
        logger.info(
            "%s 初始化完成, engine=%s, dir=%s",
            self.COMPONENT_NAME, self._engine, self._storage_dir,
        )

    def _select_engine(self) -> str:
        """选择可用存储引擎"""
        # 尝试SQLite
        try:
            import sqlite3
            test_path = self._storage_dir / ".engine_test.db"
            conn = sqlite3.connect(str(test_path))
            conn.execute("CREATE TABLE IF NOT EXISTS test (k TEXT)")
            conn.close()
            test_path.unlink(missing_ok=True)
            logger.debug("SQLite引擎可用")
            return "sqlite"
        except Exception:
            logger.debug("SQLite不可用，回退到JSON引擎")
            return "json"

    # ── 任务管理 ──────────────────────────────────────────

    def create_task(
        self,
        task_id: str,
        total_steps: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> ProgressTracker:
        """
        创建新任务

        Args:
            task_id: 任务标识
            total_steps: 总步骤数
            metadata: 元数据

        Returns:
            进度追踪器

        Raises:
            ReportAgentError: 任务已存在
        """
        if task_id in self._tasks:
            raise ReportAgentError(f"任务已存在: {task_id}")

        tracker = ProgressTracker(
            task_id=task_id,
            total_steps=total_steps,
            started_at=time.time(),
            metadata=metadata or {},
        )
        self._tasks[task_id] = tracker
        logger.info("任务已创建: %s (%d步)", task_id, total_steps)
        return tracker

    def get_task(self, task_id: str) -> ProgressTracker | None:
        """获取任务"""
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        """列出所有任务"""
        return [
            t.to_dict() for t in self._tasks.values()
        ]

    def advance_step(
        self,
        task_id: str,
        step_name: str = "",
    ) -> ProgressTracker:
        """
        前进一个步骤

        Args:
            task_id: 任务标识
            step_name: 步骤名称

        Returns:
            更新后的进度追踪器

        Raises:
            ReportAgentError: 任务不存在或已完成
        """
        tracker = self._tasks.get(task_id)
        if tracker is None:
            raise ReportAgentError(f"任务不存在: {task_id}")

        if tracker.completed_at is not None:
            raise ReportAgentError(f"任务已完成: {task_id}")

        tracker.completed_steps += 1
        tracker.current_step = step_name

        if tracker.completed_steps >= tracker.total_steps:
            tracker.completed_at = time.time()
            logger.info("任务完成: %s (%.1fs)", task_id, tracker.elapsed_seconds)

        logger.debug("任务进度: %s [%d/%d] %s", task_id,
                     tracker.completed_steps, tracker.total_steps, step_name)

        return tracker

    def fail_step(
        self,
        task_id: str,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> ProgressTracker:
        """
        记录步骤失败

        Args:
            task_id: 任务标识
            error: 错误描述
            context: 上下文
        """
        tracker = self._tasks.get(task_id)
        if tracker is None:
            raise ReportAgentError(f"任务不存在: {task_id}")

        tracker.errors.append({
            "error": error,
            "context": context or {},
            "timestamp": time.time(),
            "step": tracker.current_step,
        })

        logger.warning("任务步骤失败: %s [%s] %s", task_id, tracker.current_step, error)
        return tracker

    def delete_task(self, task_id: str) -> None:
        """删除任务"""
        self._tasks.pop(task_id, None)
        self._checkpoints.pop(task_id, None)
        self._delete_state_file(task_id)

    # ── 状态持久化 ──────────────────────────────────────

    def save_state(self, task_id: str, state_data: StateData) -> bool:
        """
        保存任务状态

        Args:
            task_id: 任务标识
            state_data: 状态数据

        Returns:
            是否保存成功
        """
        try:
            # 确保状态数据可序列化
            serialized = json.dumps(state_data, ensure_ascii=False)
            state_data_clean = json.loads(serialized)

            if self._engine == "sqlite":
                self._save_sqlite(task_id, state_data_clean)
            else:
                self._save_json(task_id, state_data_clean)

            # 自动检查点
            self._auto_checkpoint(task_id, state_data_clean)
            logger.debug("状态已保存: %s (%dB)", task_id, len(serialized))
            return True

        except Exception as e:
            logger.error("状态保存失败: %s -> %s", task_id, e)
            # 回退到JSON
            try:
                self._save_json(task_id, state_data)
                return True
            except Exception:
                return False

    def load_state(self, task_id: str) -> StateData | None:
        """
        加载任务状态

        尝试顺序:
        1. 当前引擎
        2. 检查点恢复
        3. JSON回退

        Args:
            task_id: 任务标识

        Returns:
            状态数据，不存在返回None
        """
        try:
            if self._engine == "sqlite":
                data = self._load_sqlite(task_id)
                if data is not None:
                    return data
        except Exception:
            pass

        # 检查点恢复
        checkpoints = self._checkpoints.get(task_id, [])
        if checkpoints:
            latest = max(checkpoints, key=lambda c: c.version)
            logger.info("从检查点恢复: %s (v%d)", task_id, latest.version)
            return latest.state

        # JSON回退
        try:
            return self._load_json(task_id)
        except Exception:
            return None

    # ── 检查点 ────────────────────────────────────────────

    def create_checkpoint(self, task_id: str, state_data: StateData) -> Checkpoint:
        """
        手动创建检查点

        Args:
            task_id: 任务标识
            state_data: 状态数据

        Returns:
            检查点
        """
        checkpoints = self._checkpoints.setdefault(task_id, [])
        version = len(checkpoints) + 1

        tracker = self._tasks.get(task_id)
        current_phase = tracker.current_step if tracker else "unknown"

        checkpoint = Checkpoint(
            task_id=task_id,
            phase=current_phase,
            state=state_data,
            timestamp=time.time(),
            version=version,
        )
        checkpoints.append(checkpoint)

        # 保留最多10个检查点
        if len(checkpoints) > 10:
            self._checkpoints[task_id] = checkpoints[-10:]

        logger.debug("检查点已创建: %s v%d", task_id, version)
        return checkpoint

    def _auto_checkpoint(self, task_id: str, state_data: StateData) -> None:
        """自动检查点（基于时间间隔）"""
        now = time.time()
        if now - self._last_checkpoint_time >= self._checkpoint_interval:
            self.create_checkpoint(task_id, state_data)
            self._last_checkpoint_time = now

    def list_checkpoints(self, task_id: str) -> list[Checkpoint]:
        """列出任务的检查点"""
        return self._checkpoints.get(task_id, [])

    # ── SQLite 存储 ───────────────────────────────────────

    def _save_sqlite(self, task_id: str, data: StateData) -> None:
        """SQLite保存"""
        import sqlite3
        db_path = self._storage_dir / "states.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS states (task_id TEXT PRIMARY KEY, data TEXT, updated_at REAL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO states (task_id, data, updated_at) VALUES (?, ?, ?)",
            (task_id, json.dumps(data, ensure_ascii=False), time.time()),
        )
        conn.commit()
        conn.close()

    def _load_sqlite(self, task_id: str) -> StateData | None:
        """SQLite加载"""
        import sqlite3
        db_path = self._storage_dir / "states.db"
        if not db_path.exists():
            return None

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT data FROM states WHERE task_id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return None
        return json.loads(row[0])

    def _delete_state_file(self, task_id: str) -> None:
        """删除状态文件"""
        file_path = self._storage_dir / f"{task_id}.json"
        if file_path.exists():
            file_path.unlink()

    # ── JSON 存储 ─────────────────────────────────────────

    def _save_json(self, task_id: str, data: StateData) -> None:
        """JSON文件保存"""
        file_path = self._storage_dir / f"{task_id}.json"
        with file_path.open("w", encoding="utf-8") as f:
            json.dump({
                "task_id": task_id,
                "data": data,
                "updated_at": time.time(),
            }, f, indent=2, ensure_ascii=False)

    def _load_json(self, task_id: str) -> StateData | None:
        """JSON文件加载"""
        file_path = self._storage_dir / f"{task_id}.json"
        if not file_path.exists():
            return None

        with file_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw.get("data")

    # ── 通用查询 ──────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取管理器统计"""
        return {
            "engine": self._engine,
            "storage_dir": str(self._storage_dir) if self._storage_dir else None,
            "active_tasks": len(self._tasks),
            "completed_tasks": sum(
                1 for t in self._tasks.values() if t.completed_at is not None
            ),
            "failed_tasks": sum(
                1 for t in self._tasks.values() if len(t.errors) > 0
            ),
            "total_checkpoints": sum(
                len(cps) for cps in self._checkpoints.values()
            ),
        }

    def execute(self) -> dict[str, Any]:
        """执行状态管理器主逻辑"""
        return {
            "status": "ready",
            "stats": self.get_stats(),
            "active_tasks": [t.to_dict() for t in self._tasks.values()],
            "performance": self.get_performance_stats(),
        }
