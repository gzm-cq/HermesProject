"""Failure diagnosis models for SE-Agent evolution operators."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional


class FailureType(Enum):
    """Six predefined failure types"""
    INVALID_TOOL_CALL = "invalid_tool_call"      # Tool call format/name error
    ARGUMENT_MISMATCH = "argument_mismatch"      # Parameter type/format mismatch
    STATE_MISMATCH = "state_mismatch"            # State inconsistent with expected
    RECOVERY_FAILURE = "recovery_failure"        # Error recovery failure
    MISSING_TOOL_CALL = "missing_tool_call"      # Missing necessary tool call
    RESPONSE_MISMATCH = "response_mismatch"      # Output mismatched with expectation
    UNKNOWN = "unknown"                          # Unclassified failure


FAILURE_TYPE_DESCRIPTIONS = {
    FailureType.INVALID_TOOL_CALL: "Tool call format/name error, e.g., typo in tool name, parameter format doesn't match schema",
    FailureType.ARGUMENT_MISMATCH: "Parameter type/format mismatch, e.g., string passed instead of integer",
    FailureType.STATE_MISMATCH: "State inconsistent with expected, e.g., file not found but trying to read, context lost",
    FailureType.RECOVERY_FAILURE: "Error recovery failure, e.g., retry mechanism didn't work, exception handling has defects",
    FailureType.MISSING_TOOL_CALL: "Missing necessary tool call, e.g., need to search but directly giving answer",
    FailureType.RESPONSE_MISMATCH: "Output mismatched with expectation, e.g., empty result, format error, incomplete content",
    FailureType.UNKNOWN: "Unclassified failure, needs further analysis",
}


@dataclass
class FailureSignal:
    """Failure signal - minimal unit to trigger evolution operators"""
    task_id: str
    failure_type: FailureType
    context: str  # Task context when failure occurred
    failed_content: str  # Specific content that caused failure
    timestamp: str
    trajectory_id: Optional[str] = None
    step_id: Optional[int] = None
    tool_call_name: Optional[str] = None
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "failure_type": self.failure_type.value,
            "context": self.context,
            "failed_content": self.failed_content,
            "timestamp": self.timestamp,
            "trajectory_id": self.trajectory_id,
            "step_id": self.step_id,
            "tool_call_name": self.tool_call_name,
            "error_message": self.error_message,
        }


@dataclass
class DiagnosisResult:
    """Failure diagnosis result"""
    failure_type: FailureType
    confidence: float  # Diagnosis confidence (0-1)
    direct_cause: str  # Direct cause (1st level reflection)
    root_cause: str  # Root cause (2nd level reflection)
    deep_analysis: Optional[str] = None  # Deep tracing (3rd level reflection, optional)
    evidence: List[str] = field(default_factory=list)  # Diagnostic evidence
    recommended_fix_type: str = ""  # Recommended fix type
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_type": self.failure_type.value,
            "confidence": self.confidence,
            "direct_cause": self.direct_cause,
            "root_cause": self.root_cause,
            "deep_analysis": self.deep_analysis,
            "evidence": self.evidence,
            "recommended_fix_type": self.recommended_fix_type,
        }


@dataclass
class AlternativeSolution:
    """Alternative solution"""
    solution_id: str
    solution_type: str  # "direct_fix", "orthogonal_fix", "conservative_fix"
    description: str  # Solution description
    content: str  # Corrected content
    confidence: float  # Confidence
    risk_level: str = "low"  # "low", "medium", "high"
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "solution_type": self.solution_type,
            "description": self.description,
            "content": self.content,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "pros": self.pros,
            "cons": self.cons,
        }


@dataclass
class RevisionOutput:
    """Revision operator output"""
    revised_content: str
    diagnosis: DiagnosisResult
    alternatives: List[AlternativeSolution]
    confidence_score: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "revised_content": self.revised_content,
            "diagnosis": self.diagnosis.to_dict(),
            "alternatives": [a.to_dict() for a in self.alternatives],
            "confidence_score": self.confidence_score,
        }
