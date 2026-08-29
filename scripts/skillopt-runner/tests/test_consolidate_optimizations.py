"""consolidate 行为验证（pytest）。

覆盖：
  1. evolve_memory=False 时跳过 memory 阶段（不产生第二次 reflect）
  2. 末尾不重复跑 VAL replay（复用 _gate_last_hard）
  3. A方案⑤ 基线有效性校验：baseline=0（val 上 baseline 全错 / replay 全失败）
     时**禁止** accept，避免「没有对照组的改进」被推上生产
  4. 基线有效时的真实改进仍然放行（不能误杀）

历史说明：本文件原为模块级 print 脚本，被 pytest 以 test_ 前缀收集后，
在收集阶段就执行 consolidate 并因 reflect 返回 dict（而非 EditRecord）崩溃。
已改写为真正的用例。
"""
import os
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from skillopt_sleep.types import TaskRecord, EditRecord  # noqa: E402
from skillopt_sleep.consolidate import consolidate  # noqa: E402


# ── mock backend：统计调用次数，judge 分数可配 ──────────────────────────────
class MockBackend:
    """judge_score 控制 replay 得分：1.0 = 全对，0.0 = 全错（用于构造无效基线）。"""

    def __init__(self, judge_score: float = 1.0):
        self._tokens = 0
        self.judge_score = judge_score
        self.calls: list[str] = []

    name = "mock"

    def tokens_used(self):
        return self._tokens

    def attempt(self, task, skill, memory, sample_id=0):
        self.calls.append("attempt")
        return f"response to {task.intent[:20]}"

    def attempt_with_tools(self, task, skill, memory, tools):
        self.calls.append("attempt_tools")
        return f"response to {task.intent[:20]}", []

    def judge(self, task, response):
        self.calls.append("judge")
        return self.judge_score, self.judge_score, "local-ok"

    def reflect(self, failures, successes, skill, memory, **kw):
        self.calls.append("reflect")
        return [EditRecord(
            target="skill", op="add",
            content="test rule: always be helpful",
            rationale="test",
        )]

    def _call(self, prompt, **kw):
        return '[{"op": "add", "content": "test rule", "rationale": "test"}]'


def _make_tasks(n_train: int = 4, n_val: int = 2) -> list:
    tasks = []
    for i in range(n_train + n_val):
        tasks.append(TaskRecord(
            id=f"task_{i}", project="test", intent=f"do thing {i}",
            context_excerpt="", reference_kind="none", reference="",
            judge={}, system="", tags=[],
            split="train" if i < n_train else "val",
        ))
    return tasks


def _run(judge_score: float = 1.0, **kw):
    backend = MockBackend(judge_score=judge_score)
    params = dict(
        edit_budget=2, evolve_skill=True, evolve_memory=False,
    )
    params.update(kw)
    result = consolidate(backend, _make_tasks(), "test_skill", "test_memory",
                         **params)
    return backend, result


# ── 1 & 2: 原有两项优化 ────────────────────────────────────────────────────
def test_evolve_memory_false_skips_memory_phase():
    """evolve_memory=False → 只 reflect 一次（skill），不进 memory 阶段。"""
    backend, result = _run()
    assert backend.calls.count("reflect") == 1


def test_no_redundant_final_val_replay():
    """末尾不再重复跑一遍 VAL replay。

    4 train + 2 val、evolve_memory=False 时的调用账目（每次 replay_batch
    对每 task 各产生 1 次 attempt + 1 次 judge）：
        baseline VAL replay     2 tasks × 2 =  4
        train replay            4 tasks × 2 =  8
        gate 试用 VAL replay    2 tasks × 2 =  4
        合计 16

    若 final decision 再跑一次 VAL，会变成 20。这里用 16 作上界，
    既能捕获冗余回归，又不会在 replay 策略微调时误报。

    （原脚本写的是 12 —— 它假设 train replay 跑的是全部 6 个 task，
      与 consolidate 实际传 train_tasks 不符，阈值本身没有依据。）
    """
    backend, _result = _run()
    replay_calls = (backend.calls.count("attempt")
                    + backend.calls.count("judge"))
    assert replay_calls <= 16, (
        f"疑似冗余 replay: {replay_calls} 次（预期 16，冗余时 20+）")


# ── 3 & 4: A方案⑤ 基线有效性校验 ───────────────────────────────────────────
def test_zero_baseline_is_rejected():
    """回归：judge 全返 0 → baseline=0，即使 candidate>0 也不得 accept。

    对应生产日志中的 `baseline=0.000 candidate=1.000 gate=accept` ——
    没有对照组的「改进」被直接推上生产。
    """
    _backend, result = _run(judge_score=0.0)
    assert result.baseline_score == 0.0
    assert result.baseline_valid is False
    assert result.accepted is False
    assert result.gate_action == "reject_invalid_baseline"
    # 已应用的 edit 必须回退到 rejected，绝不能流出到 applied_edits
    assert result.applied_edits == []
    assert len(result.rejected_edits) >= 1


def test_valid_baseline_uses_normal_gate():
    """基线有效时走常规 gate 判定，不被基线校验误杀。"""
    _backend, result = _run(judge_score=1.0)
    assert result.baseline_score > 0.0
    assert result.baseline_valid is True
    assert result.gate_action != "reject_invalid_baseline"
