"""记忆质量评分模块测试"""

from unittest.mock import MagicMock, patch

import pytest

from clustering_analysis.core.quality import (
    _clamp_score,
    _parse_llm_json_response,
    batch_score_memories,
    estimate_quality_keywords,
    score_memory_quality,
)


class TestClampScore:
    """测试分数钳制函数"""

    def test_normal_range(self) -> None:
        assert _clamp_score(0.5) == 0.5
        assert _clamp_score(0.0) == 0.0
        assert _clamp_score(1.0) == 1.0

    def test_below_zero(self) -> None:
        assert _clamp_score(-0.1) == 0.0
        assert _clamp_score(-1.0) == 0.0

    def test_above_one(self) -> None:
        assert _clamp_score(1.1) == 1.0
        assert _clamp_score(2.0) == 1.0


class TestParseLlmJsonResponse:
    """测试 LLM JSON 响应解析"""

    def test_direct_json(self) -> None:
        content = '{"informativeness": 0.8, "overall": 0.75}'
        result = _parse_llm_json_response(content)
        assert result is not None
        assert result["informativeness"] == 0.8
        assert result["overall"] == 0.75

    def test_markdown_code_block(self) -> None:
        content = '```json\n{"informativeness": 0.9, "clarity": 0.85}\n```'
        result = _parse_llm_json_response(content)
        assert result is not None
        assert result["informativeness"] == 0.9
        assert result["clarity"] == 0.85

    def test_raw_json_in_text(self) -> None:
        content = '这是一些解释文字 {"overall": 0.7, "timeliness": 0.6} 更多文字'
        result = _parse_llm_json_response(content)
        assert result is not None
        assert result["overall"] == 0.7
        assert result["timeliness"] == 0.6

    def test_empty_content(self) -> None:
        assert _parse_llm_json_response("") is None
        assert _parse_llm_json_response(None) is None  # type: ignore[arg-type]

    def test_invalid_json(self) -> None:
        assert _parse_llm_json_response("不是 json 内容") is None


class TestEstimateQualityKeywords:
    """测试启发式质量估算"""

    def test_empty_string(self) -> None:
        assert estimate_quality_keywords("") == 0.0
        assert estimate_quality_keywords("   ") == 0.0

    def test_very_short_text(self) -> None:
        score = estimate_quality_keywords("hi")
        assert 0.0 <= score <= 0.2

    def test_short_text(self) -> None:
        score = estimate_quality_keywords("这是测试文本")
        assert 0.0 <= score <= 1.0

    def test_medium_quality_text(self) -> None:
        text = "服务器 CPU 使用率在 14:30 达到 85%，持续了约 30 分钟。"
        score = estimate_quality_keywords(text)
        assert 0.3 <= score <= 1.0

    def test_high_quality_text(self) -> None:
        text = (
            "2024 年 1 月 15 日，生产环境服务器 server-01 的 CPU 使用率从 14:00 的 30% "
            "上升到 14:30 的 95%，持续 45 分钟后恢复正常。根因是日志收集进程 log-collector "
            "存在内存泄漏，导致触发频繁 GC。解决方案：升级到 v2.3.1 版本，该版本修复了内存泄漏问题。"
        )
        score = estimate_quality_keywords(text)
        assert 0.5 <= score <= 1.0

    def test_low_quality_patterns(self) -> None:
        assert estimate_quality_keywords("测试") <= 0.2
        assert estimate_quality_keywords("hello") <= 0.2
        assert estimate_quality_keywords("无") <= 0.2
        assert estimate_quality_keywords("暂无") <= 0.2

    def test_score_range(self) -> None:
        texts = [
            "",
            "hi",
            "简短",
            "这是一段中等长度的文本内容，用于测试。",
            "详细的技术文档，包含数字 123 和英文术语 Python、JavaScript、Docker、Kubernetes、Redis 等。"
            "句子一。句子二。句子三。句子四。句子五。",
        ]
        for text in texts:
            score = estimate_quality_keywords(text)
            assert 0.0 <= score <= 1.0


class TestScoreMemoryQuality:
    """测试单条记忆质量评分"""

    def test_empty_text(self) -> None:
        result = score_memory_quality("", use_llm=False)
        assert result["overall"] == 0.0
        assert result["informativeness"] == 0.0
        assert result["clarity"] == 0.0
        assert result["completeness"] == 0.0
        assert result["timeliness"] == 0.0

    def test_heuristic_mode(self) -> None:
        text = "这是一段测试文本，包含数字 123 和英文术语 Python。"
        result = score_memory_quality(text, use_llm=False)
        assert "overall" in result
        assert "informativeness" in result
        assert "clarity" in result
        assert "completeness" in result
        assert "timeliness" in result
        for key in result:
            assert 0.0 <= result[key] <= 1.0

    def test_llm_mode_with_mock(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"informativeness": 0.8, "clarity": 0.75, '
                        '"completeness": 0.7, "timeliness": 0.65, "overall": 0.72}'
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("clustering_analysis.core.quality.requests") as mock_requests:
            mock_requests.post.return_value = mock_response
            result = score_memory_quality(
                "测试文本",
                use_llm=True,
                api_url="http://test:1234/v1/chat/completions",
                api_key="test-key",
                model="test-model",
                retries=1,
            )

        assert result["informativeness"] == 0.8
        assert result["clarity"] == 0.75
        assert result["completeness"] == 0.7
        assert result["timeliness"] == 0.65
        assert result["overall"] == 0.72

    def test_llm_failure_fallback(self) -> None:
        with patch("clustering_analysis.core.quality.requests") as mock_requests:
            mock_requests.post.side_effect = Exception("API error")
            result = score_memory_quality(
                "测试文本",
                use_llm=True,
                retries=1,
            )

        assert 0.0 <= result["overall"] <= 1.0
        assert "informativeness" in result

    def test_llm_invalid_json_fallback(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "无效的 JSON 响应"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("clustering_analysis.core.quality.requests") as mock_requests:
            mock_requests.post.return_value = mock_response
            result = score_memory_quality(
                "测试文本",
                use_llm=True,
                retries=1,
            )

        assert 0.0 <= result["overall"] <= 1.0


class TestBatchScoreMemories:
    """测试批量评分"""

    def test_empty_input(self) -> None:
        result = batch_score_memories([], use_llm=False)
        assert result == []

    def test_single_memory(self) -> None:
        memories = [{"id": "1", "text": "测试文本内容"}]
        result = batch_score_memories(memories, use_llm=False)
        assert len(result) == 1
        assert "quality_score" in result[0]
        assert "quality_details" in result[0]
        assert result[0]["id"] == "1"
        assert 0.0 <= result[0]["quality_score"] <= 1.0

    def test_multiple_memories(self) -> None:
        memories = [
            {"id": str(i), "text": f"记忆文本内容 {i}，包含一些信息。"}
            for i in range(5)
        ]
        result = batch_score_memories(memories, batch_size=2, use_llm=False)
        assert len(result) == 5
        for i, mem in enumerate(result):
            assert mem["id"] == str(i)
            assert "quality_score" in mem
            assert "quality_details" in mem
            assert 0.0 <= mem["quality_score"] <= 1.0

    def test_batch_size_larger_than_input(self) -> None:
        memories = [{"id": str(i), "text": f"文本 {i}"} for i in range(3)]
        result = batch_score_memories(memories, batch_size=10, use_llm=False)
        assert len(result) == 3

    def test_preserves_original_fields(self) -> None:
        memories = [
            {"id": "1", "text": "测试", "extra_field": "value", "created_at": "2024-01-01"}
        ]
        result = batch_score_memories(memories, use_llm=False)
        assert result[0]["extra_field"] == "value"
        assert result[0]["created_at"] == "2024-01-01"
        assert result[0]["id"] == "1"

    def test_with_llm_mock(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"informativeness": 0.85, "clarity": 0.8, '
                        '"completeness": 0.75, "timeliness": 0.7, "overall": 0.78}'
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("clustering_analysis.core.quality.requests") as mock_requests:
            mock_requests.post.return_value = mock_response
            memories = [
                {"id": "1", "text": "文本一"},
                {"id": "2", "text": "文本二"},
            ]
            result = batch_score_memories(
                memories,
                batch_size=2,
                use_llm=True,
                retries=1,
            )

        assert len(result) == 2
        assert result[0]["quality_score"] == 0.78
        assert result[1]["quality_score"] == 0.78


class TestQualityConfig:
    """测试质量评分配置项"""

    def test_default_config_values(self) -> None:
        from clustering_analysis.config import AppConfig

        cfg = AppConfig()
        assert cfg.enable_quality_scoring is False
        assert cfg.quality_score_batch_size == 20
        assert cfg.quality_score_model == "s-deepseek-v4-flash"

    def test_from_dict_overrides(self) -> None:
        from clustering_analysis.config import AppConfig

        cfg = AppConfig.from_dict({
            "enable_quality_scoring": True,
            "quality_score_batch_size": 50,
            "quality_score_model": "test-model",
        })
        assert cfg.enable_quality_scoring is True
        assert cfg.quality_score_batch_size == 50
        assert cfg.quality_score_model == "test-model"

    def test_env_override_bool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clustering_analysis.config import load_config

        monkeypatch.setenv("CLUSTERING_ENABLE_QUALITY_SCORING", "true")
        config = load_config("nonexistent.yaml")
        assert config["enable_quality_scoring"] is True

    def test_env_override_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clustering_analysis.config import load_config

        monkeypatch.setenv("CLUSTERING_QUALITY_SCORE_BATCH_SIZE", "50")
        config = load_config("nonexistent.yaml")
        assert config["quality_score_batch_size"] == 50

    def test_env_override_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clustering_analysis.config import load_config

        monkeypatch.setenv("CLUSTERING_QUALITY_SCORE_MODEL", "custom-model")
        config = load_config("nonexistent.yaml")
        assert config["quality_score_model"] == "custom-model"
