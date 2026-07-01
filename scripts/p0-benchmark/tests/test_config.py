"""配置管理单元测试。"""

from __future__ import annotations

from p0_benchmark.config import AppConfig, load_config


class TestAppConfig:
    """AppConfig 测试。"""

    def test_default_values(self):
        """默认值正确。"""
        cfg = AppConfig()
        assert cfg.output_path == "reports"
        assert cfg.log_level == "INFO"
        assert cfg.seed == 42
        assert cfg.skill_benchmark_queries == 100
        assert cfg.skill_benchmark_prescreen_top_k == 20
        assert cfg.dedup_benchmark_sizes == [1000, 5000, 10000]
        assert cfg.dedup_benchmark_threshold == 0.95
        assert cfg.dedup_benchmark_repeat == 3
        assert cfg.llm_benchmark_article_count == 50

    def test_from_yaml_empty(self):
        """空 YAML 返回默认配置。"""
        cfg = AppConfig.from_yaml({})
        assert cfg.seed == 42
        assert cfg.skill_benchmark_queries == 100

    def test_from_yaml_partial(self):
        """部分配置覆盖默认值。"""
        cfg = AppConfig.from_yaml({
            "seed": 123,
            "skill_benchmark_queries": 200,
        })
        assert cfg.seed == 123
        assert cfg.skill_benchmark_queries == 200
        assert cfg.dedup_benchmark_repeat == 3  # 默认值保留

    def test_from_yaml_list(self):
        """列表类型配置正确加载。"""
        cfg = AppConfig.from_yaml({
            "dedup_benchmark_sizes": [100, 500],
        })
        assert cfg.dedup_benchmark_sizes == [100, 500]


class TestLoadConfig:
    """配置文件加载测试。"""

    def test_missing_file_returns_empty(self):
        """不存在的配置文件返回空 dict。"""
        result = load_config("/nonexistent/path.yaml")
        assert result == {}
