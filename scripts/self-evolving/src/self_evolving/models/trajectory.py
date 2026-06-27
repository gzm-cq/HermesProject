"""Trajectory data models for SE-Agent evolution framework."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime


class ToolType(Enum):
    """Tool call types"""
    WEB_SEARCH = "web_search"
    WEB_EXTRACT = "web_extract"
    TERMINAL = "terminal"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    CODE_EXECUTE = "code_execute"
    BROWSER = "browser"
    CUSTOM = "custom"


class ToolStatus(Enum):
    """Tool call status"""
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class ToolCall:
    """Single tool call"""
    tool_type: ToolType
    tool_name: str
    arguments: Dict[str, Any]
    status: ToolStatus
    output: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_type": self.tool_type.value,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(
            tool_type=ToolType(data["tool_type"]),
            tool_name=data["tool_name"],
            arguments=data.get("arguments", {}),
            status=ToolStatus(data["status"]),
            output=data.get("output"),
            error=data.get("error"),
            duration_ms=data.get("duration_ms"),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
        )


@dataclass
class Step:
    """A step in the trajectory"""
    step_id: int
    step_type: str  # "thought", "tool_call", "observation", "final_answer"
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    duration_ms: Optional[int] = None
    confidence: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "content": self.content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "duration_ms": self.duration_ms,
            "confidence": self.confidence,
        }


@dataclass
class Trajectory:
    """Complete reasoning trajectory"""
    trajectory_id: str
    task_id: str
    task_context: str
    steps: List[Step]
    start_time: datetime
    end_time: Optional[datetime] = None
    status: str = "running"  # "running", "success", "failed", "partial"
    final_answer: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def total_duration_ms(self) -> Optional[int]:
        if self.end_time and self.start_time:
            return int((self.end_time - self.start_time).total_seconds() * 1000)
        return None
    
    @property
    def tool_call_count(self) -> int:
        return sum(len(step.tool_calls) for step in self.steps)
    
    @property
    def failure_points(self) -> List[Step]:
        """Return steps containing failed tool calls"""
        failed_steps = []
        for step in self.steps:
            for tc in step.tool_calls:
                if tc.status in (ToolStatus.FAILED, ToolStatus.PARTIAL, ToolStatus.TIMEOUT):
                    failed_steps.append(step)
                    break
        return failed_steps
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "task_id": self.task_id,
            "task_context": self.task_context,
            "steps": [s.to_dict() for s in self.steps],
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "status": self.status,
            "final_answer": self.final_answer,
            "metadata": self.metadata,
            "total_duration_ms": self.total_duration_ms,
            "tool_call_count": self.tool_call_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trajectory":
        steps = [Step(
            step_id=s["step_id"],
            step_type=s["step_type"],
            content=s["content"],
            tool_calls=[ToolCall.from_dict(tc) for tc in s.get("tool_calls", [])],
            duration_ms=s.get("duration_ms"),
            confidence=s.get("confidence"),
        ) for s in data.get("steps", [])]
        
        return cls(
            trajectory_id=data["trajectory_id"],
            task_id=data["task_id"],
            task_context=data["task_context"],
            steps=steps,
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            status=data.get("status", "running"),
            final_answer=data.get("final_answer"),
            metadata=data.get("metadata", {}),
        )
