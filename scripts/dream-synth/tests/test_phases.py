"""phase_patterns / phase_promote / SAG 函数单元测试。"""
import json
import os
from unittest.mock import patch, MagicMock
import pytest


def _make_reflection(sid="s1", title="反思标题", score=5, content=None):
    if content is None:
        content = f"# {title}\n\n## 摘要\n这是关于{title}的摘要内容。\n\n## 关键决策\n测试决策。\n\n## 知识要点\n测试知识点。\n\n## 待办事项\n测试待办。"
    return {
        "session_id": sid,
        "title": title,
        "score": score,
        "content": content,
    }


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
                                        "reason": "有价值的知识"}):
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
                          return_value={"promote": True, "category": "concepts"}):
            result = module.phase_promote(reflections, dry_run=True)

        assert len(result) == 1
        wiki_path = os.path.join(tmp_config["wiki"]["base_path"], "concepts", "dry-run测试.md")
        assert not os.path.exists(wiki_path)

    def test_llm_failure_skips(self, module, tmp_config):
        reflections = [_make_reflection("s1", "出错")]

        with patch.object(module, "call_llm_json", side_effect=Exception("LLM down")):
            result = module.phase_promote(reflections, dry_run=True)
        assert result == []


def _make_mock_session(post_return=None, post_side_effect=None):
    mock_session = MagicMock()
    if post_side_effect is not None:
        mock_session.post.side_effect = post_side_effect
    else:
        mock_session.post.return_value = post_return
    return mock_session


class TestSagIngest:
    def test_dry_run_returns_doc_id(self, module, tmp_config):
        result = module.sag_ingest("测试标题", "内容", {"key": "val"}, dry_run=True)
        assert result is not None
        assert isinstance(result, str)

    def test_successful_ingest_returns_doc_id(self, module, tmp_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"documentId": "doc-123"}
        mock_session = _make_mock_session(post_return=mock_resp)
        with patch.object(module, "_get_sag_session", return_value=mock_session):
            result = module.sag_ingest("标题", "内容", {"source": "test"})
        assert result == "doc-123"

    def test_5xx_retry_then_success(self, module, tmp_config):
        """首次 502，重试后 201 成功。"""
        mock_resp_502 = MagicMock()
        mock_resp_502.status_code = 502
        mock_resp_502.text = "Bad Gateway"

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 201
        mock_resp_ok.json.return_value = {"documentId": "doc-retry"}

        mock_session = MagicMock()
        mock_session.post.side_effect = [mock_resp_502, mock_resp_ok]

        with patch.object(module, "_get_sag_session", return_value=mock_session), \
             patch.object(module.time, "sleep"):  # 跳过重试等待
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result == "doc-retry"
        assert mock_session.post.call_count == 2

    def test_5xx_all_retries_fail(self, module, tmp_config):
        """3 次 500 全失败 → 返回 None。"""
        mock_resp_500 = MagicMock()
        mock_resp_500.status_code = 500
        mock_resp_500.text = "Internal Server Error"

        mock_session = MagicMock()
        mock_session.post.side_effect = [mock_resp_500, mock_resp_500, mock_resp_500]

        with patch.object(module, "_get_sag_session", return_value=mock_session), \
             patch.object(module.time, "sleep"):
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result is None
        assert mock_session.post.call_count == 3

    def test_timeout_retry_then_success(self, module, tmp_config):
        """首次超时，重试后成功。"""
        import requests as req_lib

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 201
        mock_resp_ok.json.return_value = {"documentId": "doc-timeout-retry"}

        mock_session = MagicMock()
        mock_session.post.side_effect = [
            req_lib.exceptions.Timeout("Read timed out"),
            mock_resp_ok,
        ]

        with patch.object(module, "_get_sag_session", return_value=mock_session), \
             patch.object(module.time, "sleep"):
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result == "doc-timeout-retry"
        assert mock_session.post.call_count == 2

    def test_4xx_no_retry(self, module, tmp_config):
        """4xx 错误不重试，直接返回 None。"""
        mock_resp_400 = MagicMock()
        mock_resp_400.status_code = 400
        mock_resp_400.text = "Bad Request"

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp_400

        with patch.object(module, "_get_sag_session", return_value=mock_session):
            result = module.sag_ingest("标题", "内容", {}, max_retries=3, base_delay=0.1)

        assert result is None
        assert mock_session.post.call_count == 1  # 没重试

    def test_failed_ingest_returns_none(self, module, tmp_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_session = _make_mock_session(post_return=mock_resp)
        with patch.object(module, "_get_sag_session", return_value=mock_session), \
             patch.object(module.time, "sleep"):
            result = module.sag_ingest("标题", "内容", {}, max_retries=1, base_delay=0.1)
        assert result is None

    def test_network_error_returns_none(self, module, tmp_config):
        mock_session = _make_mock_session(post_side_effect=Exception("Connection refused"))
        with patch.object(module, "_get_sag_session", return_value=mock_session):
            result = module.sag_ingest("标题", "内容", {})
        assert result is None


class TestSagSearch:
    def test_source_filter_filters_by_metadata_source(self, module, tmp_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "sections": [
                {"title": "A", "metadata": {"source": "dream-synth"}, "content": "内容A"},
                {"title": "B", "metadata": {"source": "other"}, "content": "内容B"},
                {"title": "C", "metadata": {"source": "dream-synth"}, "content": "内容C"},
            ]
        }
        mock_session = _make_mock_session(post_return=mock_resp)
        with patch.object(module, "_get_sag_session", return_value=mock_session):
            result = module.sag_search("测试", top_k=10, source_filter="dream-synth")
        assert len(result) == 2
        assert {s["title"] for s in result} == {"A", "C"}

    def test_no_source_filter_returns_all(self, module, tmp_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "sections": [
                {"title": "A", "metadata": {"source": "x"}},
                {"title": "B", "metadata": {"source": "y"}},
            ]
        }
        mock_session = _make_mock_session(post_return=mock_resp)
        with patch.object(module, "_get_sag_session", return_value=mock_session):
            result = module.sag_search("测试", top_k=10)
        assert len(result) == 2

    def test_network_error_returns_empty(self, module, tmp_config):
        mock_session = _make_mock_session(post_side_effect=Exception("timeout"))
        with patch.object(module, "_get_sag_session", return_value=mock_session):
            result = module.sag_search("测试")
        assert result == []

    def test_non_200_returns_empty(self, module, tmp_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session = _make_mock_session(post_return=mock_resp)
        with patch.object(module, "_get_sag_session", return_value=mock_session):
            result = module.sag_search("测试")
        assert result == []
