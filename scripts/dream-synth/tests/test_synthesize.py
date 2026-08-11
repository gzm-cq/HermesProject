"""phase_synthesize 幂等性测试 — 缓存、重试逻辑。"""
import json
import os
from unittest.mock import patch, MagicMock

from tests._helpers import make_session as _make_session


def _mock_sag_ok():
    """Mock SAG health check to return True."""
    return patch.object(__import__("sys").modules["dream_daily"], "sag_health_check", return_value=True)


class TestPhaseSynthesize:
    def test_empty_sessions_returns_empty(self, module, tmp_config):
        result = module.phase_synthesize([], dry_run=True)
        assert result == []

    def test_score_below_threshold_skipped(self, module, tmp_config):
        sessions = [_make_session("s1")]
        with patch.object(module, "call_llm_json", return_value={"score": 1, "reason": "不相关"}):
            result = module.phase_synthesize(sessions, dry_run=False)

        assert result == []
        # dry_run=False → cache 应该写入
        verdict_file = os.path.join(tmp_config["cache"]["verdict_dir"], "s1.json")
        assert os.path.exists(verdict_file)
        with open(verdict_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["score"] == 1
        assert "synthesized" not in data

    def test_score_above_threshold_synthesizes_and_ingests(self, module, tmp_config):
        sessions = [_make_session("s1")]
        llm_calls = []
        ingest_calls = []

        def fake_json(prompt, model, **kw):
            return {"score": 5, "reason": "有价值"}

        def fake_llm(prompt, model, **kw):
            llm_calls.append(prompt)
            return "# 反思标题\n\n## 摘要\n测试摘要\n\n## 关键决策\n测试决策\n\n## 知识要点\n测试知识\n\n## 待办事项\n测试待办"

        def fake_ingest(title, content, metadata, dry_run=False):
            ingest_calls.append({"title": title, "content": content, "metadata": metadata})
            return "doc-id-123"

        with patch.object(module, "call_llm_json", side_effect=fake_json), \
             patch.object(module, "call_llm", side_effect=fake_llm), \
             patch.object(module, "sag_ingest", side_effect=fake_ingest), \
             patch.object(module, "sag_health_check", return_value=True):
            result = module.phase_synthesize(sessions, dry_run=False)

        assert len(result) == 1
        assert result[0]["title"] == "反思标题"
        assert result[0]["session_id"] == "s1"
        assert result[0]["score"] == 5
        assert result[0]["document_id"] == "doc-id-123"
        assert len(llm_calls) == 1
        assert len(ingest_calls) == 1
        assert ingest_calls[0]["metadata"]["source"] == "dream-synth"
        assert ingest_calls[0]["metadata"]["session_id"] == "s1"

    def test_dry_run_does_not_call_sag_ingest(self, module, tmp_config):
        """dry_run 模式下不应调用 sag_ingest，不写 ingested=True 到 cache。"""
        sessions = [_make_session("s1")]
        ingest_calls = []

        def fake_ingest(*args, **kw):
            ingest_calls.append(True)
            return "doc-id"

        with patch.object(module, "call_llm_json", return_value={"score": 5}), \
             patch.object(module, "call_llm", return_value="# 标题\n内容"), \
             patch.object(module, "sag_ingest", side_effect=fake_ingest):
            result = module.phase_synthesize(sessions, dry_run=True)

        assert len(result) == 1
        assert len(ingest_calls) == 0
        # dry_run 不写 cache
        verdict_file = os.path.join(tmp_config["cache"]["verdict_dir"], "s1.json")
        assert not os.path.exists(verdict_file)

    def test_dry_run_does_not_write_cache(self, module, tmp_config):
        """dry_run 模式下不写 verdict cache 到磁盘。"""
        sessions = [_make_session("s1")]

        with patch.object(module, "call_llm_json", return_value={"score": 5}), \
             patch.object(module, "call_llm", return_value="# 标题\n内容"):
            result = module.phase_synthesize(sessions, dry_run=True)

        assert len(result) == 1
        verdict_file = os.path.join(tmp_config["cache"]["verdict_dir"], "s1.json")
        assert not os.path.exists(verdict_file)

    def test_verdict_cache_hit_skips_filter(self, module, tmp_config):
        sessions = [_make_session("s1")]

        verdict_dir = tmp_config["cache"]["verdict_dir"]
        os.makedirs(verdict_dir, exist_ok=True)
        with open(os.path.join(verdict_dir, "s1.json"), "w", encoding="utf-8") as f:
            json.dump({"score": 5, "reason": "有价值", "session_id": "s1"}, f, ensure_ascii=False)

        llm_json_calls = []

        def fake_json(prompt, model, **kw):
            llm_json_calls.append(prompt)
            return {"score": 5, "reason": "有价值"}

        def fake_llm(prompt, model, **kw):
            return "# 标题\n\n内容"

        def fake_ingest(*args, **kw):
            return "doc-id"

        with patch.object(module, "call_llm_json", side_effect=fake_json), \
             patch.object(module, "call_llm", side_effect=fake_llm), \
             patch.object(module, "sag_ingest", side_effect=fake_ingest), \
             patch.object(module, "sag_health_check", return_value=True):
            result = module.phase_synthesize(sessions, dry_run=False)

        assert len(result) == 1
        assert len(llm_json_calls) == 0

    def test_synthesis_cache_hit_skips_synthesis(self, module, tmp_config):
        sessions = [_make_session("s1")]

        verdict_dir = tmp_config["cache"]["verdict_dir"]
        os.makedirs(verdict_dir, exist_ok=True)
        cache_data = {
            "score": 5,
            "reason": "有价值",
            "session_id": "s1",
            "synthesized": True,
            "reflection_title": "缓存标题",
            "reflection_content": "# 缓存标题\n\n## 摘要\n缓存内容",
        }
        with open(os.path.join(verdict_dir, "s1.json"), "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)

        llm_calls = []

        def fake_llm(prompt, model, **kw):
            llm_calls.append(prompt)
            return "# 新标题\n\n内容"

        def fake_ingest(*args, **kw):
            return "doc-id"

        with patch.object(module, "call_llm", side_effect=fake_llm), \
             patch.object(module, "sag_ingest", side_effect=fake_ingest), \
             patch.object(module, "sag_health_check", return_value=True):
            result = module.phase_synthesize(sessions, dry_run=False)

        assert len(result) == 1
        assert result[0]["title"] == "缓存标题"
        assert len(llm_calls) == 0

    def test_ingested_cache_hit_skips_ingest(self, module, tmp_config):
        sessions = [_make_session("s1")]

        verdict_dir = tmp_config["cache"]["verdict_dir"]
        os.makedirs(verdict_dir, exist_ok=True)
        cache_data = {
            "score": 5,
            "session_id": "s1",
            "synthesized": True,
            "reflection_title": "标题",
            "reflection_content": "# 标题\n内容",
            "ingested": True,
            "document_id": "existing-doc-id",
        }
        with open(os.path.join(verdict_dir, "s1.json"), "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)

        ingest_calls = []

        def fake_ingest(*args, **kw):
            ingest_calls.append(True)
            return "doc-id"

        with patch.object(module, "sag_ingest", side_effect=fake_ingest):
            result = module.phase_synthesize(sessions, dry_run=False)

        assert len(result) == 1
        assert len(ingest_calls) == 0

    def test_sag_ingest_failure_not_marked_ingested(self, module, tmp_config):
        sessions = [_make_session("s1")]

        with patch.object(module, "call_llm_json", return_value={"score": 5}), \
             patch.object(module, "call_llm", return_value="# 标题\n内容"), \
             patch.object(module, "sag_ingest", return_value=None), \
             patch.object(module, "sag_health_check", return_value=True):
            result = module.phase_synthesize(sessions, dry_run=False)

        assert result == []
        verdict_file = os.path.join(tmp_config["cache"]["verdict_dir"], "s1.json")
        with open(verdict_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("ingested") is not True
        assert data.get("synthesized") is True
        assert data.get("ingest_attempts", 0) >= 1

    def test_multiple_sessions_mixed_scores(self, module, tmp_config):
        sessions = [
            _make_session("s1", "高分对话"),
            _make_session("s2", "低分对话"),
            _make_session("s3", "中高分对话"),
        ]
        score_map = {"高分对话": 5, "低分对话": 1, "中高分对话": 3}

        def fake_json(prompt, model, **kw):
            for name, sc in score_map.items():
                if name in prompt:
                    return {"score": sc, "reason": f"分数{sc}"}
            return {"score": 0, "reason": "未知"}

        def fake_llm(prompt, model, **kw):
            return "# 反思\n内容"

        with patch.object(module, "call_llm_json", side_effect=fake_json), \
             patch.object(module, "call_llm", side_effect=fake_llm), \
             patch.object(module, "sag_ingest", return_value="doc-id"), \
             patch.object(module, "sag_health_check", return_value=True):
            result = module.phase_synthesize(sessions, dry_run=False)

        assert len(result) == 2
        sids = {r["session_id"] for r in result}
        assert "s1" in sids
        assert "s3" in sids
        assert "s2" not in sids

    def test_sag_unavailable_skips_ingest(self, module, tmp_config):
        """SAG 不可达时跳过 ingest，但合成结果仍加入 reflections 供下游使用。"""
        sessions = [_make_session("s1")]

        with patch.object(module, "call_llm_json", return_value={"score": 5}), \
             patch.object(module, "call_llm", return_value="# 标题\n内容"), \
             patch.object(module, "sag_health_check", return_value=False):
            result = module.phase_synthesize(sessions, dry_run=False)

        # SAG 不可达 → 不投 ingest，但合成结果仍应在 reflections 中（供下游 feishu push）
        assert len(result) == 1
        assert result[0]["session_id"] == "s1"
        assert result[0]["title"] == "标题"
        assert result[0]["document_id"] == ""
        # LLM 合成结果应该写入 cache
        verdict_file = os.path.join(tmp_config["cache"]["verdict_dir"], "s1.json")
        assert os.path.exists(verdict_file)
        with open(verdict_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("synthesized") is True
        assert data.get("ingested") is not True
