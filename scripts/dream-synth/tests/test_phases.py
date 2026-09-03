"""phase_patterns / phase_promote / SAG 函数单元测试。"""
import json
import os
from unittest.mock import patch, MagicMock
import pytest

from tests._helpers import make_reflection as _make_reflection


class TestPhaseRepairIngest:
    def _write_verdict(self, dirpath, sid, **overrides):
        d = {
            "score": 5,
            "session_id": sid,
            "synthesized": True,
            "reflection_title": f"反思-{sid}",
            "reflection_content": f"# 反思-{sid}\n\n内容",
        }
        d.update(overrides)
        with open(os.path.join(dirpath, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def test_repairs_orphan_reflections(self, module, tmp_path):
        """扫描 cache：synthesized 未 ingest 的补写入，已 ingest 的跳过。"""
        d = tmp_path / "verdicts"
        d.mkdir()
        # 孤儿（synthesized=True, 未 ingest）
        self._write_verdict(d, "s_orphan1")
        self._write_verdict(d, "s_orphan2")
        # 已 ingest（跳过）
        self._write_verdict(d, "s_done", ingested=True, document_id="doc-ok")
        # 未合成（跳过）
        self._write_verdict(d, "s_unsyn", synthesized=False)

        with patch.object(module, "sag_health_check", return_value=True), \
             patch.object(module, "sag_ingest", return_value="doc-repair") as mock_ingest:
            repaired = module.phase_repair_ingest(dry_run=False, verdict_dir=str(d))

        assert repaired == 2
        assert mock_ingest.call_count == 2
        # 孤儿已标记 ingested
        with open(d / "s_orphan1.json", encoding="utf-8") as f:
            assert json.load(f)["ingested"] is True

    def test_dry_run_no_write(self, module, tmp_path):
        """dry-run：只报告不写 cache。"""
        d = tmp_path / "verdicts"
        d.mkdir()
        self._write_verdict(d, "s_orphan1")

        with patch.object(module, "sag_ingest") as mock_ingest:
            would = module.phase_repair_ingest(dry_run=True, verdict_dir=str(d))

        assert would == 1
        mock_ingest.assert_not_called()
        with open(d / "s_orphan1.json", encoding="utf-8") as f:
            assert json.load(f).get("ingested") is None

    def test_skips_attempts_exhausted(self, module, tmp_path):
        """ingest_attempts >= 3 的跳过（上限保护）。"""
        d = tmp_path / "verdicts"
        d.mkdir()
        self._write_verdict(d, "s_exhausted", ingest_attempts=3, last_ingest_error="x")

        with patch.object(module, "sag_health_check", return_value=True), \
             patch.object(module, "sag_ingest") as mock_ingest:
            repaired = module.phase_repair_ingest(dry_run=False, verdict_dir=str(d))

        assert repaired == 0
        mock_ingest.assert_not_called()

    def test_sag_down_returns_zero(self, module, tmp_path):
        """SAG 不可达时不尝试 ingest。"""
        d = tmp_path / "verdicts"
        d.mkdir()
        self._write_verdict(d, "s_orphan1")

        with patch.object(module, "sag_health_check", return_value=False), \
             patch.object(module, "sag_ingest") as mock_ingest:
            repaired = module.phase_repair_ingest(dry_run=False, verdict_dir=str(d))

        assert repaired == 0
        mock_ingest.assert_not_called()


class TestPhasePatterns:
    def test_less_than_two_reflections_skips(self, module, tmp_config):
        result = module.phase_patterns([_make_reflection()], dry_run=True)
        assert result == []

    def test_empty_reflections_skips(self, module, tmp_config):
        result = module.phase_patterns([], dry_run=True)
        assert result == []

    def test_discovers_patterns(self, module, tmp_config):
        reflections = [
            _make_reflection("s1", "缓存架构设计"),
            _make_reflection("s2", "缓存性能优化"),
            _make_reflection("s3", "数据库缓存策略"),
        ]
        patterns_result = {
            "patterns": [
                {
                    "topic": "缓存系统设计",
                    "evidence_count": 3,
                    "evidence_ids": ["缓存架构设计", "缓存性能优化", "数据库缓存策略"],
                    "summary": "多个 session 讨论了缓存系统的设计与优化。",
                }
            ]
        }
        ingest_calls = []

        def fake_ingest(title, content, metadata, dry_run=False):
            ingest_calls.append({"title": title, "content": content, "metadata": metadata})
            return True

        with patch.object(module, "call_llm_json", return_value=patterns_result), \
             patch.object(module, "sag_ingest", side_effect=fake_ingest):
            result = module.phase_patterns(reflections, dry_run=True)

        assert len(result) == 1
        assert result[0]["topic"] == "缓存系统设计"
        assert len(ingest_calls) == 1
        assert ingest_calls[0]["metadata"]["source"] == "dream-pattern"
        assert "模式：缓存系统设计" in ingest_calls[0]["content"]

    def test_no_patterns_found(self, module, tmp_config):
        reflections = [
            _make_reflection("s1", "主题A"),
            _make_reflection("s2", "主题B"),
        ]
        with patch.object(module, "call_llm_json", return_value={"patterns": []}):
            result = module.phase_patterns(reflections, dry_run=True)
        assert result == []

    def test_llm_failure_returns_empty(self, module, tmp_config):
        reflections = [
            _make_reflection("s1", "主题A"),
            _make_reflection("s2", "主题B"),
        ]
        with patch.object(module, "call_llm_json", side_effect=Exception("LLM error")):
            result = module.phase_patterns(reflections, dry_run=True)
        assert result == []

    def test_empty_topic_skipped(self, module, tmp_config):
        reflections = [
            _make_reflection("s1", "主题A"),
            _make_reflection("s2", "主题B"),
        ]
        bad_result = {
            "patterns": [
                {"topic": "", "evidence_count": 2},
                {"topic": "有效主题", "evidence_count": 2},
            ]
        }
        with patch.object(module, "call_llm_json", return_value=bad_result), \
             patch.object(module, "sag_ingest", return_value=True):
            result = module.phase_patterns(reflections, dry_run=True)
        assert len(result) == 1
        assert result[0]["topic"] == "有效主题"


class TestPhasePromote:
    def test_empty_reflections_skips(self, module, tmp_config):
        result = module.phase_promote([], dry_run=True)
        assert result == []

    def test_promote_true_writes_wiki(self, module, tmp_config):
        reflections = [_make_reflection("s1", "测试归档")]

        with patch.object(module, "call_llm_json",
                          return_value={"promote": True, "category": "concepts",
                                        "reason": "有价值的知识", "score": 0.9}):
            result = module.phase_promote(reflections, dry_run=False)

        assert len(result) == 1
        wiki_path = os.path.join(tmp_config["wiki"]["base_path"], "concepts", "测试归档.md")
        assert os.path.exists(wiki_path)
        with open(wiki_path, encoding="utf-8") as f:
            content = f.read()
        assert "title: 测试归档" in content
        assert "type: concepts" in content
        assert "tags: [dream-synth, dream-promote]" in content

    def test_promote_false_skips(self, module, tmp_config):
        reflections = [_make_reflection("s1", "不归档")]

        with patch.object(module, "call_llm_json",
                          return_value={"promote": False, "reason": "临时讨论"}):
            result = module.phase_promote(reflections, dry_run=False)

        assert result == []

    def test_promote_log_dedup(self, module, tmp_config):
        reflections = [_make_reflection("s1", "已归档")]

        promote_log = tmp_config["cache"]["promote_log"]
        os.makedirs(os.path.dirname(promote_log), exist_ok=True)
        with open(promote_log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": "s1", "title": "已归档",
                                "category": "concepts", "path": "/tmp/test.md"},
                               ensure_ascii=False) + "\n")

        with patch.object(module, "call_llm_json",
                          return_value={"promote": True, "category": "concepts"}):
            result = module.phase_promote(reflections, dry_run=False)

        assert result == []

    def test_dry_run_does_not_write_wiki(self, module, tmp_config):
        reflections = [_make_reflection("s1", "dry-run测试")]

        with patch.object(module, "call_llm_json",
                          return_value={"promote": True, "category": "concepts", "score": 0.9}):
            result = module.phase_promote(reflections, dry_run=True)

        assert len(result) == 1
        wiki_path = os.path.join(tmp_config["wiki"]["base_path"], "concepts", "dry-run测试.md")
        assert not os.path.exists(wiki_path)

    def test_llm_failure_skips(self, module, tmp_config):
        reflections = [_make_reflection("s1", "出错")]

        with patch.object(module, "call_llm_json", side_effect=Exception("LLM down")):
            result = module.phase_promote(reflections, dry_run=True)
        assert result == []
class TestSagIngest:
    def test_dry_run_returns_doc_id(self, module, tmp_config):
        result = module.sag_ingest("测试标题", "内容", {"key": "val"}, dry_run=True)
        assert result is not None
        assert isinstance(result, str)

    def test_successful_ingest_returns_doc_id(self, module, tmp_config):
        mock_client = MagicMock()
        mock_client.ingest.return_value = "doc-123"
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_ingest("标题", "内容", {"source": "test"})
        assert result == "doc-123"
        mock_client.ingest.assert_called_once()

    def test_5xx_retry_then_success(self, module, tmp_config):
        """SagClient 内部 5xx 重试后成功 → wrapper 透传 doc_id。

        重试逻辑在 SagClient.ingest 内部（5xx/超时指数退避），wrapper 只委托，
        因此这里模拟 client 重试成功后的最终返回值。
        """
        mock_client = MagicMock()
        mock_client.ingest.return_value = "doc-retry"
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result == "doc-retry"
        # wrapper 只调用一次；重试发生在 client 内部
        mock_client.ingest.assert_called_once()
        # 重试参数正确传给 client
        _, kwargs = mock_client.ingest.call_args
        assert kwargs["max_retries"] == 3
        assert kwargs["base_delay"] == 0.1

    def test_5xx_all_retries_fail(self, module, tmp_config):
        """SagClient 内部 3 次 5xx 全失败 → 返回 None，wrapper 透传 None。"""
        mock_client = MagicMock()
        mock_client.ingest.return_value = None
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result is None
        mock_client.ingest.assert_called_once()

    def test_timeout_retry_then_success(self, module, tmp_config):
        """SagClient 内部超时重试后成功 → wrapper 透传 doc_id。"""
        mock_client = MagicMock()
        mock_client.ingest.return_value = "doc-timeout-retry"
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result == "doc-timeout-retry"
        mock_client.ingest.assert_called_once()

    def test_4xx_no_retry(self, module, tmp_config):
        """4xx 不重试（client 返回 None），wrapper 透传 None。"""
        mock_client = MagicMock()
        mock_client.ingest.return_value = None
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result is None
        mock_client.ingest.assert_called_once()

    def test_failed_ingest_returns_none(self, module, tmp_config):
        mock_client = MagicMock()
        mock_client.ingest.return_value = None
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_ingest("标题", "内容", {}, max_retries=1, base_delay=0.1)
        assert result is None

    def test_network_error_returns_none(self, module, tmp_config):
        """网络错误由 SagClient.ingest 内部捕获返回 None，wrapper 透传 None。"""
        mock_client = MagicMock()
        mock_client.ingest.return_value = None
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_ingest("标题", "内容", {})
        assert result is None


class TestSagSearch:
    def test_source_filter_filters_by_metadata_source(self, module, tmp_config):
        mock_client = MagicMock()
        mock_client.search.return_value = [
            {"title": "A", "metadata": {"source": "dream-synth"}, "content": "内容A"},
            {"title": "B", "metadata": {"source": "other"}, "content": "内容B"},
            {"title": "C", "metadata": {"source": "dream-synth"}, "content": "内容C"},
        ]
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_search("测试", top_k=10, source_filter="dream-synth")
        assert len(result) == 2
        assert {s["title"] for s in result} == {"A", "C"}

    def test_no_source_filter_returns_all(self, module, tmp_config):
        mock_client = MagicMock()
        mock_client.search.return_value = [
            {"title": "A", "metadata": {"source": "x"}},
            {"title": "B", "metadata": {"source": "y"}},
        ]
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_search("测试", top_k=10)
        assert len(result) == 2

    def test_network_error_returns_empty(self, module, tmp_config):
        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("timeout")
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_search("测试")
        assert result == []

    def test_non_200_returns_empty(self, module, tmp_config):
        mock_client = MagicMock()
        mock_client.search.return_value = []
        with patch.object(module, "_get_sag_client", return_value=mock_client):
            result = module.sag_search("测试")
        assert result == []
