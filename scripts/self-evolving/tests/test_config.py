"""配置模块测试"""
import os

from kanban_reflection.config import KanbanReflectionConfig


class TestKanbanReflectionConfig:
    """测试 KanbanReflectionConfig 配置加载"""

    def test_default_values(self) -> None:
        """默认值应正确初始化"""
        config = KanbanReflectionConfig()
        assert config.llm_model == "s-deepseek-v4-flash"
        assert config.llm_timeout == 60
        assert config.max_trace_lines == 5
        assert config.confidence_threshold == 0.6
        assert config.max_retries == 3

    def test_from_env_overrides(self) -> None:
        """环境变量应覆盖默认值"""
        os.environ["KN_REFLECTION_API_URL"] = "http://custom:8080"
        os.environ["KN_REFLECTION_MODEL"] = "custom-model"
        os.environ["KN_REFLECTION_TIMEOUT"] = "15"
        os.environ["KN_REFLECTION_MAX_RETRIES"] = "2"

        try:
            config = KanbanReflectionConfig.from_env()
            assert config.llm_api_url == "http://custom:8080"
            assert config.llm_model == "custom-model"
            assert config.llm_timeout == 15
            assert config.max_retries == 2
        finally:
            for key in ["KN_REFLECTION_API_URL", "KN_REFLECTION_MODEL",
                         "KN_REFLECTION_TIMEOUT", "KN_REFLECTION_MAX_RETRIES"]:
                os.environ.pop(key, None)

    def test_from_env_partial(self) -> None:
        """部分环境变量应只覆盖对应字段"""
        os.environ["KN_REFLECTION_LOG_LEVEL"] = "DEBUG"
        try:
            config = KanbanReflectionConfig.from_env()
            assert config.log_level == "DEBUG"
            # 未设置的环境变量应保持默认
            assert config.llm_timeout == 60
        finally:
            os.environ.pop("KN_REFLECTION_LOG_LEVEL", None)

    def test_from_env_with_overrides(self) -> None:
        """overrides 参数应覆盖环境变量"""
        os.environ["KN_REFLECTION_TIMEOUT"] = "10"
        try:
            config = KanbanReflectionConfig.from_env(overrides={"llm_timeout": 999})
            assert config.llm_timeout == 999  # overrides 优先
        finally:
            os.environ.pop("KN_REFLECTION_TIMEOUT", None)

    def test_failure_types_default(self) -> None:
        """默认应包含 SEAL 6 类"""
        config = KanbanReflectionConfig()
        assert "tool_execution_error" in config.failure_types
        assert "output_mismatch" in config.failure_types
        assert "status_inconsistency" in config.failure_types
        assert "user_correction" in config.failure_types
        assert "kanban_timeout" in config.failure_types
        assert "llm_anomaly" in config.failure_types
