"""Risk assessment models for SE-Agent evolution operators."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional


class RiskLevel(Enum):
    """Risk levels"""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_LEVEL_SCORES = {
    RiskLevel.NONE: 0.0,
    RiskLevel.LOW: 0.2,
    RiskLevel.MEDIUM: 0.5,
    RiskLevel.HIGH: 0.8,
    RiskLevel.CRITICAL: 1.0,
}


class RiskCategory(Enum):
    """Risk categories"""
    SYNTAX = "syntax"
    LOGIC = "logic"
    DATA = "data"
    SECURITY = "security"
    PERFORMANCE = "performance"
    COMPATIBILITY = "compatibility"
    UNKNOWN = "unknown"


@dataclass
class RiskFactor:
    """Single risk factor"""
    category: RiskCategory
    description: str
    severity: RiskLevel
    location: Optional[str] = None  # Risk location (line number, paragraph, etc.)
    likelihood: float = 0.5  # Probability of occurrence (0-1)
    impact: float = 0.5  # Impact severity (0-1)
    
    @property
    def risk_score(self) -> float:
        """Calculate risk score = probability * impact"""
        return self.likelihood * self.impact
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "description": self.description,
            "severity": self.severity.value,
            "location": self.location,
            "likelihood": self.likelihood,
            "impact": self.impact,
            "risk_score": self.risk_score,
        }


@dataclass
class RiskReport:
    """Risk assessment report"""
    overall_risk: RiskLevel
    risk_factors: List[RiskFactor]
    risk_score: float  # Composite risk score (0-1)
    recommendations: List[str] = field(default_factory=list)
    mitigated_risks: List[str] = field(default_factory=list)
    remaining_risks: List[str] = field(default_factory=list)
    
    @property
    def risk_count(self) -> Dict[str, int]:
        """Count by risk level"""
        counts = {level.value: 0 for level in RiskLevel}
        for factor in self.risk_factors:
            counts[factor.severity.value] += 1
        return counts
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_risk": self.overall_risk.value,
            "risk_factors": [f.to_dict() for f in self.risk_factors],
            "risk_score": self.risk_score,
            "recommendations": self.recommendations,
            "mitigated_risks": self.mitigated_risks,
            "remaining_risks": self.remaining_risks,
            "risk_count": self.risk_count,
        }


@dataclass
class RefinementOutput:
    """Refinement operator output"""
    refined_content: str
    reduction_stats: Dict[str, Any]  # Reduction statistics
    risk_assessment: RiskReport
    removed_redundancies: List[str]
    replaced_risky_parts: List[str]
    optimization_log: List[Dict[str, Any]]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "refined_content": self.refined_content,
            "reduction_stats": self.reduction_stats,
            "risk_assessment": self.risk_assessment.to_dict(),
            "removed_redundancies": self.removed_redundancies,
            "replaced_risky_parts": self.replaced_risky_parts,
            "optimization_log": self.optimization_log,
        }
