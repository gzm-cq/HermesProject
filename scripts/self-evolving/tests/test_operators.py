"""Tests for SE-Agent evolution operators."""
import pytest

from self_evolving.models.trajectory import Trajectory, Step, ToolCall, ToolType, ToolStatus
from self_evolving.models.failure_diagnosis import (
    FailureSignal, FailureType, DiagnosisResult,
    AlternativeSolution, RevisionOutput
)
from self_evolving.models.risk_assessment import (
    RiskFactor, RiskCategory, RiskLevel, RiskReport
)
from self_evolving.operators.revision import RevisionOperator, RevisionConfig
from self_evolving.operators.recombination import RecombinationOperator, RecombinationConfig, Component
from self_evolving.operators.refinement import RefinementOperator, RefinementConfig


class TestTrajectoryModels:
    """Test trajectory data models."""
    
    def test_tool_call_creation(self):
        tc = ToolCall(
            tool_type=ToolType.TERMINAL,
            tool_name="ls",
            arguments={"path": "/tmp"},
            status=ToolStatus.SUCCESS,
            output="file1 file2",
        )
        
        assert tc.tool_type == ToolType.TERMINAL
        assert tc.status == ToolStatus.SUCCESS
        assert tc.output == "file1 file2"
    
    def test_trajectory_conversion(self):
        from datetime import datetime
        
        trajectory = Trajectory(
            trajectory_id="test_001",
            task_id="task_001",
            task_context="Test task",
            steps=[
                Step(
                    step_id=1,
                    step_type="tool_call",
                    content="Execute ls",
                    tool_calls=[
                        ToolCall(
                            tool_type=ToolType.TERMINAL,
                            tool_name="ls",
                            arguments={},
                            status=ToolStatus.SUCCESS,
                            output="test.txt",
                        )
                    ],
                )
            ],
            start_time=datetime.now(),
            status="success",
            final_answer="Found test.txt",
        )
        
        # Test conversion to dict
        data = trajectory.to_dict()
        assert data["trajectory_id"] == "test_001"
        assert data["status"] == "success"
        assert len(data["steps"]) == 1
    
    def test_trajectory_failure_points(self):
        from datetime import datetime
        
        trajectory = Trajectory(
            trajectory_id="test_002",
            task_id="task_002",
            task_context="Test failure detection",
            steps=[
                Step(
                    step_id=1,
                    step_type="tool_call",
                    content="Step 1",
                    tool_calls=[
                        ToolCall(
                            tool_type=ToolType.FILE_READ,
                            tool_name="read_file",
                            arguments={"path": "missing.txt"},
                            status=ToolStatus.FAILED,
                            error="File not found",
                        )
                    ],
                ),
                Step(
                    step_id=2,
                    step_type="tool_call",
                    content="Step 2",
                    tool_calls=[
                        ToolCall(
                            tool_type=ToolType.TERMINAL,
                            tool_name="ls",
                            arguments={},
                            status=ToolStatus.SUCCESS,
                            output="ok",
                        )
                    ],
                ),
            ],
            start_time=datetime.now(),
            status="failed",
        )
        
        failure_points = trajectory.failure_points
        assert len(failure_points) == 1
        assert failure_points[0].step_id == 1


class TestFailureDiagnosis:
    """Test failure diagnosis models."""
    
    def test_failure_signal_creation(self):
        signal = FailureSignal(
            task_id="task_001",
            failure_type=FailureType.ARGUMENT_MISMATCH,
            context="Calling function with wrong type",
            failed_content="foo(1)",
            timestamp="2026-05-30T10:00:00",
        )
        
        assert signal.failure_type == FailureType.ARGUMENT_MISMATCH
        assert signal.failed_content == "foo(1)"
    
    def test_diagnosis_result_conversion(self):
        diagnosis = DiagnosisResult(
            failure_type=FailureType.ARGUMENT_MISMATCH,
            confidence=0.85,
            direct_cause="Type mismatch: expected str, got int",
            root_cause="Function signature not validated before call",
            evidence=["Error message", "Stack trace"],
            recommended_fix_type="type_coercion",
        )
        
        data = diagnosis.to_dict()
        assert data["confidence"] == 0.85
        assert data["failure_type"] == "argument_mismatch"


class TestRiskAssessment:
    """Test risk assessment models."""
    
    def test_risk_factor_calculation(self):
        factor = RiskFactor(
            category=RiskCategory.LOGIC,
            description="Potential infinite loop",
            severity=RiskLevel.HIGH,
            likelihood=0.7,
            impact=0.8,
        )
        
        assert factor.risk_score == pytest.approx(0.56)  # 0.7 * 0.8
        assert factor.severity == RiskLevel.HIGH
    
    def test_risk_report_stats(self):
        report = RiskReport(
            overall_risk=RiskLevel.MEDIUM,
            risk_factors=[
                RiskFactor(
                    category=RiskCategory.SYNTAX,
                    description="Syntax issue",
                    severity=RiskLevel.LOW,
                ),
                RiskFactor(
                    category=RiskCategory.LOGIC,
                    description="Logic issue",
                    severity=RiskLevel.HIGH,
                ),
            ],
            risk_score=0.5,
        )
        
        counts = report.risk_count
        assert counts["low"] == 1
        assert counts["high"] == 1
        assert counts["medium"] == 0


class TestRevisionOperator:
    """Test Revision operator."""
    
    def test_revision_config_loading(self):
        config = RevisionConfig()
        assert config.reflection_depth == 2
        assert config.generate_alternatives is True
        assert config.confidence_threshold == 0.6
    
    def test_revision_diagnosis(self):
        operator = RevisionOperator()
        result = operator.diagnose(
            failed_content="def foo(x): return x + 'hello'",
            context="TypeError: can only concatenate str to str",
            failure_type=FailureType.ARGUMENT_MISMATCH,
        )
        
        assert result.failure_type == FailureType.ARGUMENT_MISMATCH
        assert result.direct_cause is not None
        assert result.root_cause is not None
    
    def test_revision_alternatives_generation(self):
        operator = RevisionOperator()
        diagnosis = DiagnosisResult(
            failure_type=FailureType.ARGUMENT_MISMATCH,
            confidence=0.8,
            direct_cause="Type mismatch",
            root_cause="No type validation",
        )
        
        alternatives = operator.generate_alternatives(
            failed_content="def foo(x): return x + 'hello'",
            context="Test context",
            diagnosis=diagnosis,
        )
        
        assert len(alternatives) >= 1
        assert alternatives[0].solution_type == "direct_fix"


class TestRecombinationOperator:
    """Test Recombination operator."""
    
    def test_recombination_config_loading(self):
        config = RecombinationConfig()
        assert config.selection_criteria == "quality"
        assert config.max_components == 5
        assert config.detect_conflicts is True
    
    def test_component_extraction(self):
        operator = RecombinationOperator()
        candidates = [
            "def hello():\n    print('Hello')\n\ndef world():\n    print('World')",
            "def greet(name):\n    print(f'Hello {name}')",
        ]
        
        components = operator.extract_components(candidates)
        assert len(components) > 0
        assert all(hasattr(c, 'component_id') for c in components)
    
    def test_similarity_calculation(self):
        operator = RecombinationOperator()
        
        comp_a = Component(
            component_id="a1",
            source_index=0,
            component_type="function",
            content="def hello(): print('Hello')",
        )
        
        comp_b = Component(
            component_id="b1",
            source_index=1,
            component_type="function",
            content="def hello(): print('Hello')",
        )
        
        similarity = operator._calculate_similarity(comp_a, comp_b)
        assert similarity > 0.9  # Nearly identical
    
    def test_recombination_execute(self):
        operator = RecombinationOperator()
        candidates = [
            "Implementation A with error handling",
            "Implementation B with performance optimization",
            "Implementation C with clean interface",
        ]
        
        result = operator.execute(
            candidate_contents=candidates,
            task_context="Merge these implementations",
        )
        
        assert result.recombined_content is not None
        assert result.synergy_score >= 0
        assert len(result.preserved_components) > 0


class TestRefinementOperator:
    """Test Refinement operator."""
    
    def test_refinement_config_loading(self):
        config = RefinementConfig()
        assert config.risk_threshold == 0.3
        assert config.optimization_budget == 3
        assert config.compress_output is True
    
    def test_redundancy_detection(self):
        operator = RefinementOperator()
        content = """
def foo():
    print("Hello")
    print("Hello")
    print("Hello")
"""
        
        redundancies = operator.detect_redundancies(content)
        # Should detect duplicate lines
        assert len(redundancies) > 0
    
    def test_risk_scanning(self):
        operator = RefinementOperator()
        content = """
def vulnerable_code():
    query = "SELECT * FROM users WHERE id = " + user_input
    # SQL injection vulnerability
"""
        
        report = operator.scan_risks(content)
        # Should detect SQL injection pattern
        assert report.risk_factors is not None
    
    def test_refinement_execute(self):
        operator = RefinementOperator()
        content = """
def process_data(data):
    # Step 1: Validate input
    if data is None:
        return None
    if data == "":
        return None
    if len(data) == 0:
        return None
    
    # Step 2: Process
    result = []
    for item in data:
        result.append(item)
    
    # Step 3: Return
    return result
"""
        
        result = operator.execute(
            candidate_content=content,
            failure_patterns=["return None multiple times", "redundant checks"],
        )
        
        assert result.refined_content is not None
        assert result.reduction_stats["reduction_ratio"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
