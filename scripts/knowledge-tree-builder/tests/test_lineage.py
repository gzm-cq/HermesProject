"""数据血缘模块单元测试"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from knowledge_tree_builder.core.lineage import LineageRecord, LineageTracker
from knowledge_tree_builder.config import AppConfig, load_config


class TestLineageRecord:
    """LineageRecord 数据类测试"""

    def test_create_record_defaults(self) -> None:
        record = LineageRecord(
            node_id="test_001",
            source_article="test_article.md",
        )
        assert record.node_id == "test_001"
        assert record.source_article == "test_article.md"
        assert record.source_text == ""
        assert record.extraction_method == "llm_extract"
        assert record.processing_steps == []
        assert record.version == 1
        assert record.metadata == {}
        assert record.created_at
        assert record.updated_at

    def test_create_record_full(self) -> None:
        record = LineageRecord(
            node_id="test_002",
            source_article="article2.md",
            source_text="这是原文片段",
            extraction_method="manual",
            processing_steps=["analyze", "split"],
            version=2,
        )
        assert record.node_id == "test_002"
        assert record.source_article == "article2.md"
        assert record.source_text == "这是原文片段"
        assert record.extraction_method == "manual"
        assert record.processing_steps == ["analyze", "split"]
        assert record.version == 2

    def test_add_step(self) -> None:
        record = LineageRecord(node_id="test", source_article="test.md")
        record.add_step("analyze")
        assert "analyze" in record.processing_steps
        assert len(record.processing_steps) == 1

    def test_add_step_no_duplicates(self) -> None:
        record = LineageRecord(node_id="test", source_article="test.md")
        record.add_step("analyze")
        record.add_step("analyze")
        assert record.processing_steps.count("analyze") == 1

    def test_add_step_with_detail(self) -> None:
        record = LineageRecord(node_id="test", source_article="test.md")
        record.add_step("admit", {"result": "passed"})
        assert "admit" in record.processing_steps
        assert "step_admit_detail" in record.metadata
        assert record.metadata["step_admit_detail"] == [{"result": "passed"}]

    def test_increment_version(self) -> None:
        record = LineageRecord(node_id="test", source_article="test.md")
        assert record.version == 1
        old_updated = record.updated_at
        record.increment_version()
        assert record.version == 2
        assert record.updated_at >= old_updated

    def test_to_dict_basic(self) -> None:
        record = LineageRecord(
            node_id="test",
            source_article="test.md",
            source_text="secret text",
        )
        data = record.to_dict(detail_level="basic")
        assert data["node_id"] == "test"
        assert data["source_article"] == "test.md"
        assert "source_text" not in data

    def test_to_dict_full(self) -> None:
        record = LineageRecord(
            node_id="test",
            source_article="test.md",
            source_text="full text",
        )
        data = record.to_dict(detail_level="full")
        assert data["node_id"] == "test"
        assert data["source_text"] == "full text"

    def test_from_dict(self) -> None:
        data = {
            "node_id": "from_dict_test",
            "source_article": "article.md",
            "source_text": "hello",
            "extraction_method": "import",
            "processing_steps": ["analyze"],
            "version": 3,
            "metadata": {"key": "value"},
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-02T00:00:00+00:00",
            "extra_field": "should be ignored",
        }
        record = LineageRecord.from_dict(data)
        assert record.node_id == "from_dict_test"
        assert record.source_article == "article.md"
        assert record.source_text == "hello"
        assert record.extraction_method == "import"
        assert record.processing_steps == ["analyze"]
        assert record.version == 3
        assert record.metadata == {"key": "value"}
        assert not hasattr(record, "extra_field")


class TestLineageTracker:
    """LineageTracker 测试"""

    def test_create_tracker_default(self) -> None:
        tracker = LineageTracker()
        assert tracker.detail_level == "basic"
        assert tracker.count() == (0, 0)

    def test_create_tracker_full(self) -> None:
        tracker = LineageTracker(detail_level="full")
        assert tracker.detail_level == "full"

    def test_create_record_basic(self) -> None:
        tracker = LineageTracker(detail_level="basic")
        record = tracker.create_record(
            node_id="node1",
            source_article="article.md",
            source_text="should not be stored",
        )
        assert record.node_id == "node1"
        assert record.source_article == "article.md"
        assert record.source_text == ""
        assert tracker.count() == (1, 1)

    def test_create_record_full(self) -> None:
        tracker = LineageTracker(detail_level="full")
        record = tracker.create_record(
            node_id="node1",
            source_article="article.md",
            source_text="should be stored",
        )
        assert record.source_text == "should be stored"
        assert tracker.count() == (1, 1)

    def test_get_record_existing(self) -> None:
        tracker = LineageTracker()
        tracker.create_record(node_id="node1", source_article="a.md")
        record = tracker.get_record("node1")
        assert record is not None
        assert record.node_id == "node1"

    def test_get_record_not_found(self) -> None:
        tracker = LineageTracker()
        assert tracker.get_record("nonexistent") is None

    def test_add_step_existing(self) -> None:
        tracker = LineageTracker()
        tracker.create_record(node_id="node1", source_article="a.md")
        result = tracker.add_step("node1", "analyze")
        assert result is True
        record = tracker.get_record("node1")
        assert "analyze" in record.processing_steps

    def test_add_step_nonexistent(self) -> None:
        tracker = LineageTracker()
        result = tracker.add_step("nonexistent", "analyze")
        assert result is False

    def test_all_records(self) -> None:
        tracker = LineageTracker()
        tracker.create_record(node_id="n1", source_article="a.md")
        tracker.create_record(node_id="n2", source_article="b.md")
        records = tracker.all_records()
        assert len(records) == 2
        assert {r.node_id for r in records} == {"n1", "n2"}

    def test_to_json_basic(self) -> None:
        tracker = LineageTracker(detail_level="basic")
        tracker.create_record(
            node_id="n1",
            source_article="a.md",
            source_text="secret",
        )
        json_str = tracker.to_json()
        data = json.loads(json_str)
        assert len(data) == 1
        assert data[0]["node_id"] == "n1"
        assert "source_text" not in data[0]

    def test_to_json_full(self) -> None:
        tracker = LineageTracker(detail_level="full")
        tracker.create_record(
            node_id="n1",
            source_article="a.md",
            source_text="visible",
        )
        json_str = tracker.to_json()
        data = json.loads(json_str)
        assert len(data) == 1
        assert data[0]["source_text"] == "visible"

    def test_save_and_load_file(self) -> None:
        tracker = LineageTracker(detail_level="full")
        tracker.create_record(
            node_id="n1",
            source_article="test.md",
            source_text="hello world",
            extraction_method="llm_extract",
        )
        tracker.add_step("n1", "analyze")
        tracker.add_step("n1", "split")

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "lineage.json"
            tracker.save_to_file(str(filepath))
            assert filepath.exists()

            loaded = LineageTracker.load_from_file(str(filepath), detail_level="full")
            assert loaded.count() == (1, 1)
            record = loaded.get_record("n1")
            assert record is not None
            assert record.source_article == "test.md"
            assert record.source_text == "hello world"
            assert "analyze" in record.processing_steps
            assert "split" in record.processing_steps

    def test_load_nonexistent_file(self) -> None:
        tracker = LineageTracker.load_from_file("/nonexistent/path.json")
        assert tracker.count() == (0, 0)

    def test_from_json_valid(self) -> None:
        json_str = json.dumps([
            {"node_id": "n1", "source_article": "a.md", "processing_steps": ["analyze"]},
            {"node_id": "n2", "source_article": "b.md"},
        ])
        tracker = LineageTracker.from_json(json_str)
        assert tracker.count() == (2, 2)
        assert tracker.get_record("n1") is not None
        assert tracker.get_record("n2") is not None
        assert "analyze" in tracker.get_record("n1").processing_steps

    def test_from_json_invalid(self) -> None:
        tracker = LineageTracker.from_json("not valid json{{{")
        assert tracker.count() == (0, 0)

    def test_from_json_empty_list(self) -> None:
        tracker = LineageTracker.from_json("[]")
        assert tracker.count() == (0, 0)


class TestLineageDetailLevels:
    """basic/full 级别差异测试"""

    def test_basic_no_source_text(self) -> None:
        tracker = LineageTracker(detail_level="basic")
        record = tracker.create_record(
            node_id="n1",
            source_article="a.md",
            source_text="very long source text",
        )
        assert record.source_text == ""
        data = json.loads(tracker.to_json())
        assert "source_text" not in data[0]

    def test_full_has_source_text(self) -> None:
        tracker = LineageTracker(detail_level="full")
        record = tracker.create_record(
            node_id="n1",
            source_article="a.md",
            source_text="very long source text",
        )
        assert record.source_text == "very long source text"
        data = json.loads(tracker.to_json())
        assert data[0]["source_text"] == "very long source text"

    def test_basic_record_to_dict_strips_text(self) -> None:
        record = LineageRecord(
            node_id="n1",
            source_article="a.md",
            source_text="some text",
        )
        basic_dict = record.to_dict("basic")
        assert "source_text" not in basic_dict
        full_dict = record.to_dict("full")
        assert full_dict["source_text"] == "some text"


class TestConfigItems:
    """配置项测试"""

    def test_default_config_values(self) -> None:
        config = AppConfig()
        assert config.enable_data_lineage is False
        assert config.lineage_detail_level == "basic"

    def test_config_from_dict(self) -> None:
        data = {
            "enable_data_lineage": True,
            "lineage_detail_level": "full",
            "unknown_field": "should be ignored",
        }
        config = AppConfig.from_dict(data)
        assert config.enable_data_lineage is True
        assert config.lineage_detail_level == "full"

    def test_env_var_enable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KT_ENABLE_DATA_LINEAGE", "true")
        config_dict = load_config("nonexistent.yaml")
        assert config_dict.get("enable_data_lineage") is True

    def test_env_var_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KT_ENABLE_DATA_LINEAGE", "false")
        config_dict = load_config("nonexistent.yaml")
        assert config_dict.get("enable_data_lineage") is False

    def test_env_var_detail_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KT_LINEAGE_DETAIL_LEVEL", "full")
        config_dict = load_config("nonexistent.yaml")
        assert config_dict.get("lineage_detail_level") == "full"

    def test_env_var_enable_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KT_ENABLE_DATA_LINEAGE", "1")
        config_dict = load_config("nonexistent.yaml")
        assert config_dict.get("enable_data_lineage") is True

    def test_env_var_enable_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KT_ENABLE_DATA_LINEAGE", "yes")
        config_dict = load_config("nonexistent.yaml")
        assert config_dict.get("enable_data_lineage") is True
