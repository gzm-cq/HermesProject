"""Self-Evolving A+B 改造的回归测试（2026-08-29）。

覆盖：
  A-1 队列消费（处理完从 failed_tasks 移除，不再每晚原样重跑）
  A-2 全局 task_id 去重（同一 task 挂在多个 skill 下只跑一次）
  A-3 并发执行（所有 item 都被处理）
  B-1 相似度去重（与上次产出高度相似则跳过写回）
  B-2 空变更短路 + 长度护栏（软/硬上限）

运行:
    cd scripts/self-evolving && python -m pytest tests/test_self_evolving_ab_fix.py -q

说明：driver 顶层依赖 hermes_common 与 self_evolving 两个重量级包，
这里用轻量 stub 替代后再从源文件加载 driver，避免测试牵扯 LLM 客户端初始化。
"""
import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── stub 重依赖 ──────────────────────────────────────────────────────────────
def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


_LEDGER_CALLS: list = []

_ledger = _stub_module("hermes_common.ledger",
                       append_ledger_event=lambda *a, **k: _LEDGER_CALLS.append(a))
_stub_module("hermes_common", bootstrap=lambda: None, ledger=_ledger)


class _RevOut:
    def __init__(self, content):
        self.revised_content = content


class _RefOut:
    def __init__(self, content):
        self.refined_content = content


_LLM_CALLS: list = []


def _fake_revise(failed_content, context, config_path=None, **kw):
    _LLM_CALLS.append(("revise", failed_content, context))
    return _RevOut(f"revised::{failed_content}")


def _fake_refine(candidate, config_path=None, **kw):
    _LLM_CALLS.append(("refine", candidate))
    return _RefOut(f"refined::{candidate}")


_stub_module("self_evolving")
_stub_module("self_evolving.operators")
_stub_module("self_evolving.operators.revision", revise=_fake_revise)
_stub_module("self_evolving.operators.refinement", refine=_fake_refine)

_spec = importlib.util.spec_from_file_location(
    "se_driver_under_test", _SCRIPTS / "self_evolving_driver.py")
D = importlib.util.module_from_spec(_spec)
sys.modules["se_driver_under_test"] = D
_spec.loader.exec_module(D)

from skill_patch import patch_skill_md_detailed  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    _LEDGER_CALLS.clear()
    _LLM_CALLS.clear()
    yield


def _load_driver_from_source():
    """重新加载 driver（用于需要独立模块实例的场景）。"""
    spec = importlib.util.spec_from_file_location(
        f"se_driver_{id(object())}", _SCRIPTS / "self_evolving_driver.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_state(path: Path, failed_tasks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"failed_tasks": failed_tasks},
                               ensure_ascii=False, indent=2), encoding="utf-8")


def _make_skill(home: str, name: str = "s", body: str = "# s\nbody\n") -> Path:
    sk = Path(home) / "skills" / name / "SKILL.md"
    sk.parent.mkdir(parents=True, exist_ok=True)
    sk.write_text(f"---\nname: {name}\n---\n{body}", encoding="utf-8")
    return sk


# ── A-2 全局 task_id 去重 ────────────────────────────────────────────────────
class TestGlobalTaskDedup:

    def test_same_task_id_across_skills_kept_once(self, tmp_path):
        """同一 task_id 挂在两个 skill 下 → 只保留首次出现。

        生产实况：task_4ead0e5fd39c 等 4 个 task 同时挂在 devops/kanban-worker
        与 hindsight-memory 下，此前会被 revise→refine 两遍、写进两份 SKILL.md。
        """
        st = tmp_path / "state.json"
        _make_state(st, {
            "devops/kanban-worker": [
                {"id": "t1", "skill": "devops/kanban-worker"},
                {"id": "t2", "skill": "devops/kanban-worker"},
            ],
            "hindsight-memory": [
                {"id": "t1", "skill": "hindsight-memory"},
                {"id": "t3", "skill": "hindsight-memory"},
            ],
        })
        items = D._extract_failed_tasks(str(st))
        ids = [D._task_id_of(t) for t in items]
        assert ids == ["t1", "t2", "t3"]
        assert [t["skill"] for t in items][0] == "devops/kanban-worker"

    def test_distinct_ids_all_kept(self, tmp_path):
        st = tmp_path / "state.json"
        _make_state(st, {"a": [{"id": "x"}, {"id": "y"}]})
        items = D._extract_failed_tasks(str(st))
        assert len(items) == 2

    def test_missing_state_file_returns_empty(self, tmp_path):
        assert D._extract_failed_tasks(str(tmp_path / "nope.json")) == []


# ── A-1 队列消费 ─────────────────────────────────────────────────────────────
class TestQueueConsumption:

    def test_consumed_ids_removed_from_state(self, tmp_path):
        st = tmp_path / "state.json"
        _make_state(st, {
            "a": [{"id": "t1"}, {"id": "t2"}],
            "b": [{"id": "t2"}, {"id": "t3"}],
        })
        assert D._consume_failed_tasks(str(st), {"t1", "t2"}) is True
        ft = json.loads(st.read_text(encoding="utf-8"))["failed_tasks"]
        assert [t["id"] for t in ft["a"]] == []
        assert [t["id"] for t in ft["b"]] == ["t3"]

    def test_empty_consumed_set_is_noop(self, tmp_path):
        st = tmp_path / "state.json"
        _make_state(st, {"a": [{"id": "t1"}]})
        assert D._consume_failed_tasks(str(st), set()) is False
        assert len(json.loads(st.read_text(encoding="utf-8"))["failed_tasks"]["a"]) == 1

    def test_unknown_ids_leaves_state_intact(self, tmp_path):
        st = tmp_path / "state.json"
        _make_state(st, {"a": [{"id": "t1"}]})
        assert D._consume_failed_tasks(str(st), {"nope"}) is False
        assert len(json.loads(st.read_text(encoding="utf-8"))["failed_tasks"]["a"]) == 1

    def test_end_to_end_run_drains_queue(self, tmp_path):
        """回归核心：run() 跑完后队列必须缩短，否则明晚原样重跑。"""
        home = tempfile.mkdtemp()
        _make_skill(home, "devops/kanban-worker")
        st = tmp_path / "state.json"
        _make_state(st, {
            "devops/kanban-worker": [
                {"id": f"t{i}", "skill": "devops/kanban-worker",
                 "failed_content": f"fail {i}"} for i in range(4)
            ],
        })
        D.run(str(st), None, str(tmp_path / "out"), None, False,
              auto_apply=True, home=home, max_items=10, max_workers=2)
        remaining = json.loads(st.read_text(encoding="utf-8"))["failed_tasks"]
        assert remaining["devops/kanban-worker"] == []

    def test_llm_error_keeps_task_for_retry(self, tmp_path, monkeypatch):
        """LLM 异常属临时故障，任务应留在队列里等明晚重试。"""
        home = tempfile.mkdtemp()
        st = tmp_path / "state.json"
        _make_state(st, {"a": [{"id": "boom", "failed_content": "x"}]})

        def _boom(failed_content, context, config_path=None, **kw):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(D, "revise", _boom)
        D.run(str(st), None, str(tmp_path / "out"), None, False,
              auto_apply=True, home=home, max_items=10)
        remaining = json.loads(st.read_text(encoding="utf-8"))["failed_tasks"]["a"]
        assert [t["id"] for t in remaining] == ["boom"]


# ── A-3 并发 ─────────────────────────────────────────────────────────────────
class TestParallelStage:

    def test_all_items_processed(self):
        items = [{"id": f"t{i}", "failed_content": f"c{i}"} for i in range(7)]
        sink = D._run_llm_parallel(items, None, max_workers=3, item_timeout=30)
        assert all(r is not None and r["ok"] for r in sink)
        assert len(_LLM_CALLS) == 14  # 7 × (revise + refine)

    def test_timeout_marks_item_as_none(self, monkeypatch):
        """单项超时后记为未完成（None），同批未超时的任务不受影响。"""
        import time

        orig = D._run_llm_stage

        def _slow(idx, task, sink, config_path):
            if idx == 0:
                time.sleep(5)
                sink[idx] = {"ok": True, "revised": "r", "refined": "r",
                             "rev_out": _RevOut("r"), "ref_out": _RefOut("r")}
            else:
                orig(idx, task, sink, config_path)

        monkeypatch.setattr(D, "_run_llm_stage", _slow)
        items = [{"id": "slow", "failed_content": "a"},
                 {"id": "fast", "failed_content": "b"}]
        sink = D._run_llm_parallel(items, None, max_workers=2, item_timeout=0.5)
        assert sink[0] is None
        assert sink[1] is not None and sink[1]["ok"] is True


# ── B-2 前置过滤（避免为注定被拒的 skill 白烧 LLM）────────────────────────────
class TestOversizedPreFilter:

    def test_oversized_skill_tasks_skipped(self, monkeypatch):
        """SKILL.md 已超硬上限的 task 不应进入 LLM 阶段。"""
        home = tempfile.mkdtemp()
        _make_skill(home, "huge", body="x" * 31000)
        _make_skill(home, "ok")
        monkeypatch.setenv("SE_SKILL_HARD_MAX", "30000")
        # get_char_limits 读环境变量，需重新加载模块以生效
        import skill_patch
        monkeypatch.setattr(D, "get_char_limits", skill_patch.get_char_limits)

        items = [{"id": "a", "skill": "huge", "failed_content": "1"},
                 {"id": "b", "skill": "ok", "failed_content": "2"}]
        kept, skipped = D._filter_oversized(items, home)
        assert [t["id"] for t in kept] == ["b"]
        assert [s["skill"] for s in skipped] == ["huge"]

    def test_oversized_tasks_stay_in_queue(self, tmp_path, monkeypatch):
        """被跳过的 task 不从队列消费 —— 人工整合后应自动恢复。"""
        home = tempfile.mkdtemp()
        _make_skill(home, "huge", body="x" * 31000)
        monkeypatch.setenv("SE_SKILL_HARD_MAX", "30000")
        import skill_patch
        monkeypatch.setattr(D, "get_char_limits", skill_patch.get_char_limits)

        st = tmp_path / "state.json"
        _make_state(st, {"huge": [{"id": "t1", "skill": "huge",
                                   "failed_content": "x"}]})
        D.run(str(st), None, str(tmp_path / "out"), None, False,
              auto_apply=True, home=home, max_items=10)
        remaining = json.loads(st.read_text(encoding="utf-8"))["failed_tasks"]["huge"]
        assert [t["id"] for t in remaining] == ["t1"]
        # 且没有产生任何 LLM 调用
        assert _LLM_CALLS == []

    def test_block_count_exhausted_skill_skipped(self, monkeypatch):
        """块数已达上限的 skill 同样在前置阶段被跳过，不烧 LLM。"""
        home = tempfile.mkdtemp()
        _make_skill(home, "s", body="原始正文\n")
        monkeypatch.setenv("SE_MAX_BLOCK_COUNT", "2")
        patch_skill_md_detailed("s", "a", task_id="t0", home=home)
        patch_skill_md_detailed("s", "b", task_id="t1", home=home)

        items = [{"id": "new", "skill": "s", "failed_content": "x"}]
        kept, skipped = D._filter_oversized(items, home)
        assert kept == []
        assert [s["reason"] for s in skipped] == ["待复核块过多"]

    def test_no_candidates_ledger_event(self, tmp_path, monkeypatch):
        home = tempfile.mkdtemp()
        _make_skill(home, "huge", body="x" * 31000)
        monkeypatch.setenv("SE_SKILL_HARD_MAX", "30000")
        import skill_patch
        monkeypatch.setattr(D, "get_char_limits", skill_patch.get_char_limits)

        st = tmp_path / "state.json"
        _make_state(st, {"huge": [{"id": "t1", "skill": "huge",
                                   "failed_content": "x"}]})
        D.run(str(st), None, str(tmp_path / "out"), None, False,
              auto_apply=True, home=home, max_items=10)
        assert any(a[1].get("status") == "all_oversized"
                   for a in _LEDGER_CALLS if len(a) >= 2)


# ── B-1 相似度去重 ───────────────────────────────────────────────────────────
class TestSimilarityDedup:

    def test_identical_text_is_one(self):
        assert D._similarity("abc def", "abc def") == 1.0

    def test_empty_is_zero(self):
        assert D._similarity("", "abc") == 0.0

    def test_different_text_below_threshold(self):
        assert D._similarity("部署前先备份配置文件",
                             "明天的天气预计是晴天") < 0.5

    def test_minor_rewording_above_threshold(self):
        """LLM 只改几个字时应判定为「无实质改进」。"""
        a = "部署前务必先备份配置文件，并检查端口占用情况。"
        b = "部署前务必先备份配置文件，同时检查端口占用情况。"
        assert D._similarity(a, b) >= 0.9

    def test_second_run_with_similar_output_skips_write(self, tmp_path):
        """第二次跑出几乎相同的内容 → 不写盘、不新增备份。"""
        home = tempfile.mkdtemp()
        sk = _make_skill(home, "s")
        st = tmp_path / "state.json"
        _make_state(st, {"s": [{"id": "t1", "skill": "s", "failed_content": "x"}]})

        def _stable_refine(candidate, config_path=None, **kw):
            return _RefOut("部署前先备份配置文件，并检查端口占用。")

        orig_refine = D.refine
        D.refine = _stable_refine
        try:
            D.run(str(st), None, str(tmp_path / "out"), None, False,
                  auto_apply=True, home=home, max_items=10)
            first = sk.read_text(encoding="utf-8")
            _make_state(st, {"s": [{"id": "t1", "skill": "s", "failed_content": "x"}]})
            D.run(str(st), None, str(tmp_path / "out"), None, False,
                  auto_apply=True, home=home, max_items=10)
            assert sk.read_text(encoding="utf-8") == first
        finally:
            D.refine = orig_refine


# ── B-2 空变更短路 + 长度护栏 ────────────────────────────────────────────────
class TestPatchGuards:

    def test_identical_content_returns_unchanged(self):
        home = tempfile.mkdtemp()
        sk = _make_skill(home, "s")
        r1 = patch_skill_md_detailed("s", "一条修正", task_id="t1", home=home)
        assert (r1.ok, r1.status) == (True, "applied")
        mtime_after_first = sk.stat().st_mtime_ns

        r2 = patch_skill_md_detailed("s", "一条修正", task_id="t1", home=home)
        assert (r2.ok, r2.status) == (True, "unchanged")
        assert sk.stat().st_mtime_ns == mtime_after_first
        assert sk.read_text(encoding="utf-8").count("SE-APPLIED id=t1") == 1

    def test_changed_content_still_applies(self):
        home = tempfile.mkdtemp()
        _make_skill(home, "s")
        patch_skill_md_detailed("s", "第一条", task_id="t1", home=home)
        r = patch_skill_md_detailed("s", "第二条", task_id="t1", home=home)
        assert (r.ok, r.status) == (True, "applied")

    def test_empty_content_rejected(self):
        home = tempfile.mkdtemp()
        _make_skill(home, "s")
        r = patch_skill_md_detailed("s", "   ", task_id="t1", home=home)
        assert (r.ok, r.status) == (False, "rejected")

    def test_hard_limit_rejects(self, monkeypatch):
        """超硬上限（默认 30k）必须拒绝写回 —— 该 skill 需人工整合。"""
        home = tempfile.mkdtemp()
        sk = _make_skill(home, "s", body="x" * 29000)
        monkeypatch.setenv("SE_SKILL_HARD_MAX", "30000")
        monkeypatch.setenv("SE_SKILL_SOFT_MAX", "12000")
        r = patch_skill_md_detailed("s", "y" * 2000, task_id="t1", home=home)
        assert (r.ok, r.status) == (False, "rejected")
        assert "SE-APPLIED" not in sk.read_text(encoding="utf-8")

    def test_soft_limit_warns_but_applies(self, monkeypatch):
        """软上限只告警，仍允许写回。"""
        home = tempfile.mkdtemp()
        _make_skill(home, "s", body="x" * 11000)
        monkeypatch.setenv("SE_SKILL_HARD_MAX", "30000")
        monkeypatch.setenv("SE_SKILL_SOFT_MAX", "12000")
        r = patch_skill_md_detailed("s", "补充要点", task_id="t1", home=home)
        assert (r.ok, r.status) == (True, "applied")

    def test_block_count_limit_rejects(self, monkeypatch):
        """待复核块累积到上限后必须停止追加 —— 没人复核还在加只会加重欠账。"""
        home = tempfile.mkdtemp()
        _make_skill(home, "s", body="原始正文\n")
        monkeypatch.setenv("SE_SKILL_HARD_MAX", "30000")
        monkeypatch.setenv("SE_MAX_BLOCK_COUNT", "3")
        for i in range(3):
            r = patch_skill_md_detailed("s", f"修正 {i}", task_id=f"t{i}",
                                        home=home)
            assert r.status == "applied", f"第 {i} 个块应可写入"
        r = patch_skill_md_detailed("s", "再来一条", task_id="t3", home=home)
        assert (r.ok, r.status) == (False, "rejected")
        assert "待复核块过多" in r.reason

    def test_bool_wrapper_preserved(self):
        """patch_skill_md 必须保持严格 bool（既有测试依赖 is True / is False）。"""
        home = tempfile.mkdtemp()
        _make_skill(home, "s")
        from skill_patch import patch_skill_md
        assert patch_skill_md("s", "内容", task_id="t1", home=home) is True
        assert patch_skill_md("no-such-skill", "x", task_id="g", home=home) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
