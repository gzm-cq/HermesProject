#!/usr/bin/env python3
from __future__ import annotations

"""
SkillOpt-Runner for Hermes — post-curator nightly runner.
Runs after hermes curator, optimizes user/agent-created skills via SkillOpt-Sleep, doesn't change Hermes core, uses curator's existing usage stats.
"""

import os
import sys
import json
import argparse
import pathlib
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml
import subprocess
import shutil
from collections import defaultdict

_STATE_LOCK = threading.Lock()
MAX_RETRY_POOL = 200

# SkillOpt-Sleep 克隆在本地，必须在 import 前插入 sys.path
# SKILLOPT_HOME 与 _SKILLOPT_SLEEP_PATH 保持一致：都基于 HERMES_HOME 计算，
# 可通过环境变量 HERMES_HOME 覆盖，避免硬编码路径与运行环境不一致。
HERMES_HOME = pathlib.Path(os.environ.get('HERMES_HOME', '/root/.hermes'))
SKILLOPT_HOME = pathlib.Path(os.environ.get('SKILLOPT_HOME', str(HERMES_HOME / 'skillopt-runner')))
_SKILLOPT_SLEEP_PATH = str(SKILLOPT_HOME.parent / 'skillopt-sleep')
if _SKILLOPT_SLEEP_PATH not in sys.path:
    sys.path.insert(0, _SKILLOPT_SLEEP_PATH)

from skillopt_sleep.types import SessionDigest, TaskRecord
from skillopt_sleep.mine import mine
from skillopt_sleep.config import load_config, SleepConfig
from skillopt_sleep.cycle import run_sleep_cycle

# ── 统一反馈账本（F-1）：跨飞轮事件追加 ──────────────
try:
    from hermes_common import bootstrap  # noqa: F401
except ImportError:
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path
    _parent = _os.environ.get("HERMES_COMMON_SRC") or ""
    if not _parent:
        _d = _Path(__file__).resolve().parent
        for _ in range(12):
            _cand = _d / "libs" / "hermes_common"
            if (_cand / "hermes_common" / "__init__.py").is_file():
                _parent = str(_cand)
                break
            if _d.parent == _d:
                break
            _d = _d.parent
    if not _parent:
        _prod = "/root/.hermes/lib"
        if _os.path.isfile(_os.path.join(_prod, "hermes_common", "__init__.py")):
            _parent = _prod
    if _parent and _parent not in _sys.path:
        _sys.path.insert(0, _parent)
    from hermes_common import bootstrap  # noqa: F401
bootstrap()
from hermes_common.ledger import append_ledger_event


USAGE_FILE = HERMES_HOME / 'skills' / '.usage.json'
BACKUP_DIR = SKILLOPT_HOME / 'backups'
CONFIG_PATH = SKILLOPT_HOME / 'config.yaml'
STATE_FILE = SKILLOPT_HOME / 'state.json'


def load_state() -> dict:
    """加载状态：迭代计数器、负反馈累积数据

    容错处理：
    - 文件不存在：返回默认空状态
    - JSON 损坏：备份损坏文件后返回默认空状态（避免每次都崩）
    - 旧格式字段：自动迁移到新格式
    """
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                s = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            # 备份损坏的 state 文件，重置为空（不阻断运行）
            try:
                backup_path = STATE_FILE.with_suffix('.corrupted')
                shutil.copy2(STATE_FILE, backup_path)
                print(f'Warning: state.json 损坏已备份到 {backup_path}，原因: {e}', file=sys.stderr)
            except Exception:
                pass
            return {
                'skill_last_run': {},
                'skill_neg_feedback': {},
                'skill_total_mentions': {},
                'last_harvest_iso': None,    # F-4: harvest 窗口锚点，避免负反馈重复计数
            }
        # 兼容旧格式：迁移 last_run_iso → skill_last_run
        if 'last_run_iso' in s and 'skill_last_run' not in s:
            s['skill_last_run'] = {}
        return s
    return {
        'skill_last_run': {},             # skill_name -> 上次优化完成时间
        'skill_neg_feedback': {},         # skill_name -> 累积负反馈次数
        'skill_total_mentions': {},       # skill_name -> 累积被提及次数
        'last_harvest_iso': None,        # F-4: harvest 窗口锚点，避免负反馈重复计数
    }


def save_state(state: dict):
    """保存状态到 state.json（线程安全）。

    Note: 当前锁仅保护文件写入，state 字典的修改在单线程上下文中安全。
    若未来引入并行 worker，需将 state 的 read-modify-write 也纳入锁保护。
    """
    with _STATE_LOCK:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f'状态已保存: 累积 {len(state.get("skill_neg_feedback", {}))} 个技能的负反馈数据')

def load_usage() -> dict[str, dict]:
    """加载使用统计。损坏时备份并返回空 dict。"""
    if not USAGE_FILE.exists():
        return {}
    try:
        with open(USAGE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        try:
            backup_path = USAGE_FILE.with_suffix('.corrupted')
            shutil.copy2(USAGE_FILE, backup_path)
            print(f'Warning: usage.json 损坏已备份到 {backup_path}，原因: {e}', file=sys.stderr)
        except Exception:
            pass
        return {}

def filter_eligible_skills(usage: dict[str, dict], denylist_patterns: list[str]) -> list[tuple[str, dict]]:
    """Filter eligible skills for SkillOpt optimization.
    不需要 allowlist — 完全数据驱动，由 curator 生命周期 + denylist 决定。"""
    eligible: list[tuple[str, dict]] = []
    for name, rec in usage.items():
        # Deny: pinned, archived, stale — reuse curator lifecycle judgment
        if rec.get('pinned', False):
            continue
        state = rec.get('state', 'active')
        if state != 'active':
            continue
        # Deny: third-party collections (lark/sensenova/gstack) — prefix match
        if any(name.startswith(pattern) for pattern in denylist_patterns):
            continue
        # Allow: agent-created OR local with >0 activity
        created_by = rec.get('created_by')
        if created_by == 'agent':
            eligible.append((name, rec))
            continue
        # local manually-created skill: must have activity to be interesting
        activity_count = rec.get('use_count', 0) + rec.get('view_count', 0) + rec.get('patch_count', 0)
        if activity_count >= 1:
            eligible.append((name, rec))
    return eligible







def _parse_iso_to_timestamp(value: str | None) -> float | None:
    """Parse ISO timestamp string to Unix seconds.

    Args:
        value: ISO timestamp string. ``None`` or empty returns ``None``.

    Returns:
        Unix timestamp seconds, or ``None`` when parsing fails.
    """
    if not value:
        return None
    normalized = value.replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _timestamp_to_iso(value: float | int | None) -> str:
    """Convert Unix seconds to UTC ISO timestamp string."""
    if value is None:
        return ''
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _build_session_digest(
    session_id: str,
    project: str,
    started_at: str,
    ended_at: str,
    user_prompts: list[str],
    assistant_finals: list[str],
    tools_used: list[str],
    feedback: list[str],
    raw_path: str,
) -> SessionDigest | None:
    """Build a SkillOpt SessionDigest, skipping sessions without user turns."""
    if not user_prompts:
        return None
    return SessionDigest(
        session_id=session_id,
        project=project,
        started_at=started_at,
        ended_at=ended_at,
        user_prompts=user_prompts,
        assistant_finals=assistant_finals,
        tools_used=tools_used,
        files_touched=[],
        feedback_signals=feedback,
        n_user_turns=len(user_prompts),
        n_assistant_turns=len(assistant_finals),
        raw_path=raw_path,
    )


def _dedupe_digests(digests: list[SessionDigest]) -> list[SessionDigest]:
    """Deduplicate digests by session_id, preserving first occurrence."""
    seen: set[str] = set()
    unique: list[SessionDigest] = []
    for digest in digests:
        if digest.session_id in seen:
            continue
        seen.add(digest.session_id)
        unique.append(digest)
    return unique


def _harvest_state_db_sessions(since_iso: str | None) -> list[SessionDigest]:
    """Harvest current Hermes sessions/messages from state.db.

    Args:
        since_iso: Optional incremental lower bound.

    Returns:
        Session digests built from SQLite session history.
    """
    db_path = HERMES_HOME / 'state.db'
    if not db_path.exists():
        return []
    since_ts = _parse_iso_to_timestamp(since_iso)
    digests: list[SessionDigest] = []
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT s.id, s.source, s.started_at, s.ended_at, s.title,
                       COALESCE(s.ended_at, MAX(m.timestamp), s.started_at) AS last_activity
                FROM sessions AS s
                LEFT JOIN messages AS m ON m.session_id = s.id
                GROUP BY s.id
                HAVING last_activity >= COALESCE(?, 0)
                ORDER BY last_activity DESC
                """,
                (since_ts,),
            ).fetchall()
            for row in rows:
                msg_rows = con.execute(
                    """
                    SELECT role, content, tool_name, timestamp, finish_reason
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id ASC
                    """,
                    (row['id'],),
                ).fetchall()
                user_prompts: list[str] = []
                assistant_finals: list[str] = []
                tools_used: list[str] = []
                feedback: list[str] = []
                last_ts = row['ended_at'] or row['started_at']
                for msg in msg_rows:
                    content = msg['content'] or ''
                    if not content:
                        continue
                    last_ts = msg['timestamp'] or last_ts
                    role = msg['role']
                    if role == 'user':
                        user_prompts.append(content)
                        feedback.extend(_detect_feedback(content))
                    elif role == 'assistant':
                        assistant_finals.append(content)
                    elif role == 'tool' and msg['tool_name']:
                        tools_used.append(msg['tool_name'])
                digest = _build_session_digest(
                    session_id=row['id'],
                    project=row['source'] or 'hermes-cli',
                    started_at=_timestamp_to_iso(row['started_at']),
                    ended_at=_timestamp_to_iso(last_ts),
                    user_prompts=user_prompts,
                    assistant_finals=assistant_finals,
                    tools_used=tools_used,
                    feedback=feedback,
                    raw_path=str(db_path),
                )
                if digest:
                    digests.append(digest)
    except sqlite3.Error as e:
        print(f'Warning: failed to harvest state.db sessions: {e}')
    return digests


def harvest_hermes_sessions(since_iso: str | None, max_sessions: int = 0) -> list[SessionDigest]:
    """Harvest Hermes session JSONs -> SkillOpt-Sleep SessionDigest list.
    If since_iso is provided, only harvest sessions that ended after since_iso (incremental mode).
    
    Storage format (双格式兼容):
    - *.jsonl → 旧格式，每行一条消息，每条有 role/content/timestamp/tool_name
    - session_*.json → 新格式，单个 JSON 含 messages 数组 + session_start/last_updated
    """
    digests: list[SessionDigest] = _harvest_state_db_sessions(since_iso)
    session_dir = HERMES_HOME / 'sessions'

    def _make_digest(session_id: str, project: str,
                     started_at: str, ended_at: str,
                     user_prompts: list[str], assistant_finals: list[str],
                     tools_used: list[str], feedback: list[str],
                     n_user: int, n_assistant: int, raw_path: str) -> SessionDigest | None:
        if n_user == 0:
            return None
        return SessionDigest(
            session_id=session_id,
            project=project,
            started_at=started_at,
            ended_at=ended_at,
            user_prompts=user_prompts,
            assistant_finals=assistant_finals,
            tools_used=tools_used,
            files_touched=[],
            feedback_signals=feedback,
            n_user_turns=n_user,
            n_assistant_turns=n_assistant,
            raw_path=raw_path,
        )

    # ── 格式1: *.jsonl（旧格式，每行一条 JSON） ──
    for p in session_dir.glob('*.jsonl'):
        session_id = p.stem
        messages: list[dict] = []
        try:
            with open(p, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception as e:
            print(f'Warning: failed to parse {p}: {e}, skipping')
            continue

        if len(messages) == 0:
            continue

        first = messages[0]
        started_at = first.get('timestamp', '')
        last_msg = messages[-1]
        ended_at = last_msg.get('timestamp', started_at)

        if since_iso and ended_at and ended_at < since_iso:
            continue

        project = first.get('platform') or 'hermes-cli'
        user_prompts: list[str] = []
        assistant_finals: list[str] = []
        tools_used: list[str] = []
        feedback: list[str] = []

        for msg in messages:
            role = msg.get('role')
            content = msg.get('content', '')
            if not content:
                continue
            if role == 'user':
                user_prompts.append(content)
                feedback.extend(_detect_feedback(content))
            elif role == 'assistant':
                finish_reason = msg.get('finish_reason')
                if finish_reason is not None or len(assistant_finals) == len(user_prompts):
                    assistant_finals.append(content)
            elif role == 'tool' and msg.get('tool_name'):
                tools_used.append(msg['tool_name'])

        d = _make_digest(session_id, project, started_at, ended_at,
                         user_prompts, assistant_finals, tools_used, feedback,
                         len(user_prompts), len(assistant_finals), str(p))
        if d:
            digests.append(d)

    # ── 格式2: session_*.json（新格式，单 JSON 含 messages 数组） ──
    for p in session_dir.glob('session_*.json'):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            continue

        session_id = data.get('session_id', p.stem)
        started_at = data.get('session_start', '')
        ended_at = data.get('last_updated', started_at)

        # 增量过滤
        if since_iso and ended_at and ended_at < since_iso:
            continue

        project = data.get('platform') or 'hermes-cli'
        msgs = data.get('messages', [])
        if not msgs:
            continue

        user_prompts = []
        assistant_finals = []
        tools_used = []
        feedback = []

        for msg in msgs:
            role = msg.get('role')
            content = msg.get('content', '')
            if not content:
                continue
            if role == 'user':
                user_prompts.append(content)
                feedback.extend(_detect_feedback(content))
            elif role == 'assistant':
                assistant_finals.append(content)
            elif role == 'tool':
                tn = msg.get('name', '') or msg.get('tool_name', '')
                if tn:
                    tools_used.append(tn)

        d = _make_digest(session_id, project, started_at, ended_at,
                         user_prompts, assistant_finals, tools_used, feedback,
                         len(user_prompts), len(assistant_finals), str(p))
        if d:
            digests.append(d)

    # Sort by newest first
    digests = _dedupe_digests(digests)
    digests.sort(key=lambda d: d.ended_at, reverse=True)
    # 限制处理量（默认 0 = 无限制）
    if max_sessions and len(digests) > max_sessions:
        print(f'  限制: 剪裁 {len(digests)} → {max_sessions} 个 session (--max-sessions)')
        digests = digests[:max_sessions]
    print(f'Processed {len(digests)} complete sessions from Hermes')
    if since_iso:
        print(f'  (incremental: only sessions ending after {since_iso})')
    return digests

def _detect_feedback(text: str) -> list[str]:
    """Detect positive/negative feedback from Hermes user message text.

    使用词边界匹配（英文）+ 否定前缀排除（中文），减少子串误报：
    - 英文用 re.search(rf'\\b{ph}\\b', ...) 匹配单词边界
    - 中文短语在匹配后检查前缀是否包含否定词（不/没/未 等）
    """
    import re as _re
    lower = text.lower()
    sigs: list[str] = []

    # 中文否定前缀：出现在关键词前时反转情感
    _CN_NEGATION_PREFIX = ('不', '没', '未', '别', '莫', '无')

    def _cn_has_negation_before(phrase: str, source: str) -> bool:
        """检查 phrase 在 source 中出现时，前一个字符是否为否定词。"""
        idx = source.find(phrase)
        while idx >= 0:
            if idx == 0 or source[idx - 1] not in _CN_NEGATION_PREFIX:
                return False  # 至少有一次出现无否定前缀 → 视为原语义
            idx = source.find(phrase, idx + 1)
        return True  # 所有出现都被否定前缀修饰

    # negative feedback: SkillOpt's original list — 用词边界避免 "wrongly"/"nope-fully" 之类误配
    _NEGATIVE = (
        "still broken", "still not", "still wrong", "doesn't work", "does not work",
        "that's wrong", "thats wrong", "incorrect", "wrong",
        "no,", "nope", "fix it", "didn't", "did not", "broken",
        "error again", "still failing", "still fails", "not fixed",
    )
    _POSITIVE = (
        "thanks", "thank you", "perfect", "great", "works now", "fixed",
        "that works", "lgtm", "looks good", "nice", "correct",
    )
    for ph in _NEGATIVE:
        # 多词短语用普通 in，单个英文单词用 word boundary
        if ' ' in ph or ',' in ph or '.' in ph or "'" in ph:
            if ph in lower:
                sigs.append(f'neg:{ph}')
        else:
            if _re.search(rf'\b{_re.escape(ph)}\b', lower):
                sigs.append(f'neg:{ph}')
    for ph in _POSITIVE:
        if ' ' in ph:
            if ph in lower:
                sigs.append(f'pos:{ph}')
        else:
            if _re.search(rf'\b{_re.escape(ph)}\b', lower):
                sigs.append(f'pos:{ph}')

    # Chinese negative — 大多本身含否定语义，直接匹配
    CN_NEGATIVE = (
        '不对', '错了', '还不对', '还错', '还是错', '没改好', '没修好', '改不对',
        '仍然不对', '不正确', '还是不行', '错误',
        '不行', '不好', '不可以',
        'bug', '缺陷', '失败',
    )
    for ph in CN_NEGATIVE:
        if ph in lower:
            # 排除："没有错误" / "没有失败" — "错误"/"失败" 被否定前缀修饰后是正向语义
            if ph in ('错误', '失败', 'bug', '缺陷') and _cn_has_negation_before(ph, lower):
                continue
            sigs.append(f'neg:{ph}')

    # Chinese positive — 排除否定前缀出现的情况（不可以/没修好/未通过 等）
    CN_POSITIVE = (
        '可以', '好了', '对了', '正确', '通过', '行了', '改好了', '修好了', '搞定',
        '没问题', '可用',
    )
    for ph in CN_POSITIVE:
        if ph not in lower:
            continue
        # 特殊短语：'没问题' 本身以'没'开头，是正向语义，跳过否定检查
        if ph == '没问题':
            sigs.append(f'pos:{ph}')
            continue
        # 检查是否所有出现都被否定前缀修饰
        if _cn_has_negation_before(ph, lower):
            continue
        sigs.append(f'pos:{ph}')
    return sigs

def get_skill_path(skill_name: str) -> pathlib.Path | None:
    """Find the SKILL.md path for a skill name, handles category nesting."""
    base = HERMES_HOME / 'skills'
    for candidate in base.rglob(f'{skill_name}/SKILL.md'):
        return candidate
    return None


RUNNER_CONFIG_KEYS = {
    'top_k',
    'denylist_patterns',
    'max_sessions',
    'max_tasks_per_night',
    'sleep_workers',
    'batch_size',
}

_SHORT_AMBIGUOUS_TOKENS = {
    'ai', 'ml', 'llm', 'rag', 'api', 'agent', 'system', 'dev', 'ops', 'data',
}


def split_config(raw: dict) -> tuple[dict, dict]:
    """Split SkillOpt-Runner private config from SkillOpt-Sleep config."""
    runner_cfg: dict = {}
    sleep_cfg: dict = {}
    for key, value in raw.items():
        if key in RUNNER_CONFIG_KEYS:
            runner_cfg[key] = value
        else:
            sleep_cfg[key] = value
    return runner_cfg, sleep_cfg


def _skill_match_tokens(skill_name: str) -> list[str]:
    """Return meaningful tokens from a skill name for mention matching."""
    leaf = skill_name.rsplit('/', 1)[-1].lower()
    tokens = [t for t in re.split(r'[^a-z0-9]+', leaf) if t]
    return [t for t in tokens if t not in _SHORT_AMBIGUOUS_TOKENS and len(t) >= 3]


def message_mentions_skill(message: str, skill_name: str) -> bool:
    """Return whether a user message explicitly mentions a skill."""
    lower = message.lower()
    leaf = skill_name.rsplit('/', 1)[-1].lower()
    phrase_pattern = r'(?<![a-z0-9])' + re.escape(leaf) + r'(?![a-z0-9])'
    if re.search(phrase_pattern, lower):
        return True
    tokens = _skill_match_tokens(skill_name)
    if len(tokens) < 2:
        return False
    return all(re.search(r'(?<![a-z0-9])' + re.escape(t) + r'(?![a-z0-9])', lower) for t in tokens)


def rank_skills(
    eligible: list[tuple[str, dict]],
    digests: list[SessionDigest],
    state: dict,
    top_k: int = 5,
) -> tuple[list[tuple[str, dict, float, int, int]], dict, dict[str, list]]:
    """
    精排（增量）：从所有 eligible 技能中选出最值得优化的 top-K。
    只扫描增量 digests 的负反馈，合并到累积 state 中。
    
    评分因子（权重可调）：
      - 负反馈次数 × 3.0（反映用户痛点，最高权重）
      - 活跃度 × 0.5（使用频率，中等权重）
      - agent-created 加成 × 2.0（核心业务技能优先）

    返回：(top_K_skills, updated_state)
    """

    # 从累积状态中加载历史负反馈数据
    skill_neg = defaultdict(int, state.get('skill_neg_feedback', {}))
    skill_total = defaultdict(int, state.get('skill_total_mentions', {}))

    eligible_names = [name for name, _ in eligible]
    # ★ 收集每个 skill 关联的负反馈 session（用于后续 per-skill mine）
    skill_sessions: dict[str, list[SessionDigest]] = {n: [] for n in eligible_names}

    # ★ 负反馈匹配（message 级，非 session 级）
    #   逐条检测用户消息中的负反馈，仅当某条消息实际包含负反馈时，
    #   该消息中提及的技能才计为负反馈。避免尾端正反馈稀释整 session 标签。
    new_neg = 0
    for d in digests:
        # 预处理：用户消息逐条检测负反馈
        prompt_feedback = []
        for prompt in d.user_prompts:
            if not prompt:
                prompt_feedback.append(('', False))
                continue
            sigs = _detect_feedback(prompt)
            has_neg = any(s.startswith('neg:') for s in sigs)
            prompt_feedback.append((prompt, has_neg))

        lower_tools = [t.lower() for t in d.tools_used if t]
        # 工具到消息映射已丢失，采用会话级保守策略
        session_has_neg = any(neg for _, neg in prompt_feedback)

        for name in eligible_names:
            # 用户消息：逐条检测，只在实际负反馈消息中计扣分
            for prompt, has_neg in prompt_feedback:
                if message_mentions_skill(prompt, name):
                    skill_total[name] += 1
                    if has_neg:
                        skill_neg[name] += 1
                        new_neg += 1
                        # ── 只收集有负反馈的 session ──
                        if (not skill_sessions[name] or
                            skill_sessions[name][-1].session_id != d.session_id):
                            skill_sessions[name].append(d)

            # 工具：保守用 session 级
            tool_count = sum(1 for t in lower_tools if message_mentions_skill(t, name))
            if tool_count:
                skill_total[name] += tool_count
                if session_has_neg:
                    skill_neg[name] += tool_count
                    new_neg += tool_count
                    # ── 工具路径也要收集 session（与文本路径对称）──
                    if (not skill_sessions[name] or
                        skill_sessions[name][-1].session_id != d.session_id):
                        skill_sessions[name].append(d)

    print(f'本轮新增负反馈: {new_neg} 次 | 累积负反馈技能数: {len(skill_neg)}')

    # 更新状态
    state['skill_neg_feedback'] = dict(skill_neg)
    state['skill_total_mentions'] = dict(skill_total)

    # 评分（使用累积数据）
    scored = []
    for name, rec in eligible:
        neg = skill_neg.get(name, 0)
        activity = (
            (rec.get('use_count') or 0) +
            (rec.get('view_count') or 0) +
            (rec.get('patch_count') or 0)
        )
        bonus = 2.0 if rec.get('created_by') == 'agent' else 1.0
        score = neg * 3.0 + activity * 0.5 * bonus
        scored.append((name, rec, round(score, 1), neg, activity))

    scored.sort(key=lambda x: x[2], reverse=True)
    top = scored[:top_k]

    # 打印精排结果
    print(f'\n{"="*65}')
    print(f'  精排 TOP {top_k} ｜ 评分 = 负反馈×3 + 活跃度×0.5 + agent 加成')
    print(f'  (累积负反馈数据, 共 {sum(skill_neg.values())} 次负反馈)')
    print(f'{"="*65}')
    print(f'{"#":>3s} {"Skill":42s} {"Score":>8s} {"负反馈":>6s} {"活跃度":>6s}')
    print('-'*65)
    for i, (name, _, sc, neg, act) in enumerate(top, 1):
        marker = ' ★' if neg > 0 else '  '
        print(f'{i:>3d}{marker} {name:42s} {sc:>8.1f} {neg:>6d} {act:>6d}')
    print(f'{"-"*65}')
    print(f'  (淘汰 {len(scored) - top_k} 个技能, 共 {len(scored)} 个候选)')
    print()

    return top, state, skill_sessions


def _security_scan(content: str) -> tuple[bool, str]:
    """语义安全护栏：区分「高置信危险」与「需代码语境才危险」两类模式。

    - 高置信危险（始终拦截）：rm -rf /、curl|bash、wget|bash、AWS key、prompt injection。
      这些在 SKILL.md 正文里无论出现在 prose 还是代码都不可接受。
    - 语境相关（仅在代码/命令语境拦截）：sudo / eval( / exec(。
      避免误杀 prose 中「用 eval 评估」「请勿 sudo」之类的正向说明文字。

    Returns:
        (is_safe, reason) - is_safe=True 表示通过；reason 为失败原因
    """
    import re as _re
    # 高置信危险：无论上下文，始终拦截
    HARD_BLOCK: list[tuple[_re.Pattern[str], str]] = [
        (_re.compile(r'rm\s+-rf\s+/', _re.IGNORECASE), '危险的 rm -rf 路径'),
        (_re.compile(r'curl\s+[^|]*\|\s*bash', _re.IGNORECASE), 'curl | bash 远程执行'),
        (_re.compile(r'wget\s+[^|]*\|\s*bash', _re.IGNORECASE), 'wget | bash 远程执行'),
        (_re.compile(r'AKIA[0-9A-Z]{16}'), '疑似 AWS access key'),
        (_re.compile(r'(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions?'),
         '疑似 prompt injection: ignore instructions'),
        (_re.compile(r'(?i)you\s+are\s+now\s+(a|an)\s+'),
         '疑似 prompt injection: 角色重定义'),
        (_re.compile(r'(?i)disregard\s+(all\s+)?(safety|security|ethical)'),
         '疑似 prompt injection: 绕过安全'),
    ]
    for pattern, reason in HARD_BLOCK:
        if pattern.search(content):
            return False, f'安全扫描失败: {reason}'

    # 语境相关：仅在「代码块 / shell 行」中拦截 sudo/eval(/exec(，降低 prose 误杀
    has_code_fence = '```' in content or '~~~' in content
    has_shell = bool(_re.search(r'(?m)^\s*[\$>]\s*\S', content)) or bool(
        _re.search(r'(?m)^\s*(sudo|rm\b|curl|wget|bash|sh|python3?|eval|exec)\b', content))
    if has_code_fence or has_shell:
        CODE_CONTEXT: list[tuple[_re.Pattern[str], str]] = [
            (_re.compile(r'(?m)^\s*(sudo|eval|exec)\b', _re.IGNORECASE),
             '代码/命令语境中的 sudo/eval/exec'),
        ]
        for pattern, reason in CODE_CONTEXT:
            if pattern.search(content):
                return False, f'安全扫描失败: {reason}'
    return True, 'OK'


def validate_patched_skill(content: str) -> tuple[bool, str]:
    """写回后自动验证（P2-3）：SKILL.md 结构完整性检查。

    在写入磁盘前对 merged 内容做防御性校验，防止 LLM edit 内容
    破坏 frontmatter / YAML 结构，导致 Hermes 技能加载失败。

    验证点：
      1. frontmatter 存在且恰好一对 --- 分隔符
      2. frontmatter YAML 可解析且为 mapping（防 YAML 语法错误/列表注入）
      3. 必含 name + description（Hermes skill 加载的最低要求）
      4. body 非空

    Returns:
        (is_valid, reason) — is_valid=True 表示结构完整；reason 为失败原因
    """
    stripped = content.lstrip()
    if not stripped.startswith('---'):
        return False, '缺少 frontmatter 起始分隔符'
    first_end = stripped.find('---', 3)
    if first_end <= 0:
        return False, 'frontmatter 未闭合（缺少第二个 ---）'
    fm_text = stripped[3:first_end]
    body = stripped[first_end + 3:]
    try:
        fm = yaml.safe_load(fm_text)
    except Exception as e:  # noqa: BLE001 — 任何 YAML 错误都拦截
        return False, f'frontmatter YAML 解析失败: {e}'
    if not isinstance(fm, dict):
        return False, f'frontmatter 必须是 mapping，实际是 {type(fm).__name__}'
    if not fm.get('name'):
        return False, 'frontmatter 缺少 name 字段'
    if not fm.get('description'):
        return False, 'frontmatter 缺少 description 字段'
    if not body.strip():
        return False, 'body 为空'
    return True, 'OK'


def patch_skill_hermes(skill_name: str, new_content: str, state: dict | None = None) -> bool:
    """Patch skill via Hermes skill_manage tool — merge edit into existing SKILL.md.
    安全特性：保留 frontmatter + append 到正文 + atomic write + security scan + 回滚。
    部署成功 → 自动清零该技能的累积负反馈。"""
    # 0. 安全扫描（先于文件操作，避免先备份后拒绝）
    is_safe, reason = _security_scan(new_content)
    if not is_safe:
        print(f'SECURITY: 拒绝写入 {skill_name}: {reason}')
        return False

    p = get_skill_path(skill_name)
    if not p:
        print(f'ERROR: cannot find SKILL.md for skill {skill_name}')
        return False

    # 读取现有 SKILL.md
    existing = p.read_text(encoding='utf-8')

    # 解析 frontmatter
    fm_end = None
    if existing.lstrip().startswith('---'):
        # 找到第二个 ---
        stripped = existing.lstrip()
        first_end = stripped.find('---', 3)
        if first_end > 0:
            fm_end = len(existing) - len(stripped) + first_end + 3
    if fm_end is None:
        print(f'ERROR: cannot parse frontmatter from {skill_name} SKILL.md')
        return False

    frontmatter = existing[:fm_end]
    body = existing[fm_end:]

    # 将 edit 内容追加到正文（作为新规则段落）
    body_clean = body.lstrip('\n').rstrip('\n')
    merged = frontmatter + '\n\n' + body_clean + '\n\n' + new_content.strip() + '\n'

    # ── P2-3 写回前自动验证：结构完整性检查，失败不写、不产生审计产物 ──
    is_valid, v_reason = validate_patched_skill(merged)
    if not is_valid:
        print(f'VALIDATE: 拒绝写入 {skill_name}: {v_reason}')
        return False

    # backup + diff 审计
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H-%M-%S')
    safe_name = skill_name.replace('/', '-')
    backup_path = BACKUP_DIR / f'{safe_name}_{ts}.md.bak'
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, backup_path)
    print(f'BACKUP saved to {backup_path}')

    # 生成 unified diff 供事后审查
    import difflib
    diff_lines = list(difflib.unified_diff(
        existing.splitlines(keepends=True),
        merged.splitlines(keepends=True),
        fromfile=f'{skill_name}/SKILL.md (before)',
        tofile=f'{skill_name}/SKILL.md (after)',
        n=3,
    ))
    diff_text = ''.join(diff_lines) if diff_lines else '(no changes)\n'
    patch_id = f'{safe_name}_{ts}'
    diff_path = BACKUP_DIR / f'{patch_id}.diff'
    try:
        diff_path.write_text(diff_text, encoding='utf-8')
        print(f'DIFF saved to {diff_path}')
    except Exception:
        pass  # diff 写失败不阻断主流程

    # 直接写文件（绕过 skill_manage 工具，no_agent cron 无 review turn）
    try:
        p.write_text(merged, encoding='utf-8')
    except Exception as e:
        print(f'ERROR: write SKILL.md failed: {e}')
        shutil.copy2(backup_path, p)  # revert
        return False

    # ── P2-3 写回后验证：磁盘内容与 merged 必须一致，不一致回滚 ──
    try:
        written = p.read_text(encoding='utf-8')
    except Exception as e:
        print(f'ERROR: read-back verify failed: {e}')
        shutil.copy2(backup_path, p)  # revert
        return False
    if written != merged:
        print(f'ERROR: 写回验证失败 {skill_name}: 磁盘内容与期望不一致 '
              f'({len(written)} vs {len(merged)} chars)')
        shutil.copy2(backup_path, p)  # revert
        return False

    print(f'SUCCESS: patch applied to {p}')
    # F-1 统一反馈账本：记录 SkillOpt 改写事件（跨循环关联 SAG 生产/消费质量）
    _neg_before = None
    if isinstance(state, dict):
        _neg_before = state.get('skill_neg_feedback', {}).get(skill_name)
    append_ledger_event('skillopt_patch', {
        'skill': skill_name,
        'neg_before': _neg_before,
        'neg_after': 0 if isinstance(state, dict) else None,
        'dry_run': False,
    })
    # 部署成功 → 清零该技能负反馈（仅当传入 state 时；线程安全路径下由主线程统一合并，见 F-7）
    if state is not None and skill_name in state.get('skill_neg_feedback', {}):
        old_val = state['skill_neg_feedback'][skill_name]
        state['skill_neg_feedback'][skill_name] = 0
        save_state(state)
        print(f'  → 负反馈清零: {skill_name} ({old_val} → 0)')
    return True

def filter_digests_by_since(
    digests: list[SessionDigest], since_iso: str | None
) -> list[SessionDigest]:
    """Filter sessions to only those ending after ``since_iso``.

    Args:
        digests: Full harvest of session digests.
        since_iso: ISO timestamp bound. ``None`` means no filter (full harvest).

    Returns:
        Filtered session digests.
    """
    if not since_iso:
        return digests  # full harvest (first time for this skill)

    since_ts = _parse_iso_to_timestamp(since_iso) or 0
    result = []
    for d in digests:
        # ended_at 可能 None（未结束的 session），fallback 到 started_at
        end_ts = _parse_iso_to_timestamp(d.ended_at) or _parse_iso_to_timestamp(d.started_at) or 0
        if end_ts >= since_ts:
            result.append(d)
    print(f'  after {since_iso}: {len(result)} sessions in window (ended_at=None fallback to started_at)')
    return result


def _phase_harvest(
    state: dict,
    skill_last_run: dict[str, str],
    runner_cfg: dict,
) -> list[SessionDigest]:
    """Phase 1: 确定 harvest 窗口并拉取 session。

    默认全量。如果所有 skill 都已优化过，从最早的 skill_last_run 开始增量。

    Returns:
        Harvested session digests。
    """
    # F-4 fix: 用单一 last_harvest_iso 推进窗口，替代 min(skill_last_run)。
    # 旧逻辑用 min(skill_last_run) 作下界——一旦某 skill 长期未被再选，窗口锚定在
    # 最早的优化时刻，每次运行都重新 harvest 全部旧 session，导致 rank_skills 对
    # 同一条历史负反馈逐日 +1 重复计数，污染 score = neg*3 + ...。
    # 改为每个 session 仅计一次：harvest 后立刻推进锚点，下次自此刻起。
    last_harvest = state.get('last_harvest_iso')
    if last_harvest:
        harvest_since = last_harvest
        print(f'Phase 1: 增量 harvest（自 {harvest_since} 之后的新 session）')
    else:
        harvest_since = None
        print('Phase 1: 全量 harvest（首次运行）')
    max_sessions = runner_cfg.get('max_sessions', 0)
    digests = harvest_hermes_sessions(harvest_since, max_sessions=max_sessions)
    print(f'Harvested {len(digests)} total sessions')
    # 立即推进 harvest 锚点（边界闭于本次，下次开窗口严格大于，保证不重复计数）
    state['last_harvest_iso'] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return digests


def _phase_rank(
    eligible: list,
    all_digests: list[SessionDigest],
    state: dict,
    runner_cfg: dict,
) -> tuple[list, dict[str, list]]:
    """Phase 2: 精排 — 用新 harvest 的会话更新负反馈，选出 top-K。

    Returns:
        (top_scored, skill_sessions) 元组.
    """
    top_k = runner_cfg.get('top_k', 5)
    top_scored, state, skill_sessions = rank_skills(eligible, all_digests, state, top_k)
    save_state(state)
    if not top_scored:
        print('精排后无技能入选，退出')
        return [], {}
    return top_scored, skill_sessions


def _optimize_one_skill(
    skill_name: str,
    skill_last_run_ts: str | None,
    failed_tasks_in: list,
    batches: list[list],
    sleep_cfg: dict,
    *,
    dry_run: bool,
) -> dict:
    """Run one skill through all batches. Thread-safe (F-7): 只操作线程本地数据，
    不修改共享 state / skill_last_run，返回 delta 供主线程合并，消除并发竞态。

    Returns:
        {
          "skill_name": str,
          "optimized": bool,
          "skill_last_run_ts": str | None,
          "failed_tasks": list,
          "neg_cleared": bool,
        }
    """
    # ── Load failed tasks from retry pool（本地副本，避免共享 mutate） ──
    failed_tasks: dict[str, list] = {skill_name: list(failed_tasks_in or [])}
    saved = failed_tasks[skill_name]
    retry_map: dict[str, int] = {}
    has_retry = bool(saved)
    if saved:
        retry_map = {t['id']: t.get('retry_count', 0)
                     for t in saved if isinstance(t, dict)}
        saved_ids = set(retry_map.keys())
        seen = set(saved_ids)
        deduped = []
        for batch in batches:
            fresh = [t for t in batch if t.id not in seen]
            seen.update(t.id for t in fresh)
            if fresh:
                deduped.append(fresh)
        restored = [TaskRecord.from_dict(t) for t in saved]
        # ── 重试池放最后（fresh tasks 先跑，skill 更新后胜率更高） ──
        deduped.append(restored)
        batches = deduped
        print(f'  [{skill_name}] 恢复 {len(restored)} 重试 task '
              f'(放最后)，共 {sum(len(b) for b in batches)} 个 task')

    # ── 无重试池 且 无 batch → skip ──
    if not has_retry and not batches:
        print(f'  [{skill_name}] 无 session 数据 + 无重试池，跳过')
        return {"skill_name": skill_name, "optimized": False,
                "skill_last_run_ts": None, "failed_tasks": saved, "neg_cleared": False}

    p = get_skill_path(skill_name)
    if not p:
        print(f'  [{skill_name}] 找不到 SKILL.md，跳过')
        return {"skill_name": skill_name, "optimized": False,
                "skill_last_run_ts": None, "failed_tasks": saved, "neg_cleared": False}

    per_cfg = dict(sleep_cfg)
    per_cfg['projects'] = [str(p.parent)]

    neg_cleared = False
    last_run_ts: str | None = None

    for batch_idx, batch_tasks in enumerate(batches, 1):
        if not batch_tasks:
            continue
        cfg = load_config(**per_cfg)
        result = run_sleep_cycle(cfg=cfg, seed_tasks=batch_tasks, dry_run=dry_run)

        print(f'  [{skill_name}] Batch {batch_idx}/{len(batches)}: '
              f'replayed={result.report.n_replayed} '
              f'baseline={result.report.baseline_score:.3f} '
              f'candidate={result.report.candidate_score:.3f} '
              f'gate={result.report.gate_action}')

        if not result.report.accepted:
            # ── Gate reject — append（含去重+cap+retry_count 递增） ──
            new_tasks = [t.to_dict() for t in batch_tasks if hasattr(t, 'to_dict')]
            existing = failed_tasks.get(skill_name, [])
            existing_ids = {t.get('id') for t in existing if isinstance(t, dict)}
            merged = list(existing)
            for nt in new_tasks:
                nt_id = nt.get('id')
                if nt_id not in existing_ids:
                    base = retry_map.get(nt_id, 0)  # fresh→0, retry→过往次数
                    nt['retry_count'] = base + 1
                    merged.append(nt)
            # cap
            merged = merged[-MAX_RETRY_POOL:]
            # 淘汰超限重试
            merged = [t for t in merged if t.get('retry_count', 0) <= 3]
            failed_tasks[skill_name] = merged
            print(f'  [{skill_name}] → Gate 拒绝，重试池当前 {len(merged)} 个 task')
            continue

        # ── Gate accept — clear retry pool ──
        failed_tasks.pop(skill_name, None)

        if dry_run:
            print(f'  [{skill_name}] → Dry-run: {len(result.report.edits)} edits accepted')
            # Dry-run 模式下继续跑剩余 batch（验证全流程）
            continue

        # F-6: 应用单个 batch 内全部通过的 edit（不再只取首个），逐条应用；
        # 不传 state → 不在子线程内共享 mutate，负反馈清零由主线程统一合并（F-7）。
        print(f'  [{skill_name}] ✅ Batch {batch_idx} passed!')
        applied = 0
        for edit in result.report.edits:
            ok = patch_skill_hermes(skill_name, edit.content)
            if ok:
                print(f'  [{skill_name}] ✅ Applied: {edit.target}/{edit.op}')
                applied += 1
            else:
                print(f'  [{skill_name}] ❌ Apply failed: {edit.target}/{edit.op}')
        if applied:
            last_run_ts = datetime.now(timezone.utc).isoformat()
            neg_cleared = True  # 部署成功后主线程统一清零负反馈
            print(f'  [{skill_name}] ✅ Batch {batch_idx} 共应用 {applied} 个 edit')

    return {
        "skill_name": skill_name,
        "optimized": bool(last_run_ts),
        "skill_last_run_ts": last_run_ts,
        "failed_tasks": failed_tasks.get(skill_name, []),
        "neg_cleared": neg_cleared,
    }


def _phase_optimize(
    top_scored: list,
    skill_last_run: dict[str, str],
    state: dict,
    skill_sessions: dict[str, list],
    sleep_cfg: dict,
    runner_cfg: dict,
    *,
    dry_run: bool,
) -> int:
    """Phase 3: 5 个 skill 并行优化 — 每个 skill 从自己的 session 提 task。

    各 skill 按 skill_sessions 获取关联的负反馈 session，
    独立 mine → batch → sleep_cycle，数据互不重叠。
    """
    print(f'\nPhase 3: 并行优化 — {len(top_scored)} 个 skill 各自跑自己的数据')
    print(f'{"="*65}')

    workers = runner_cfg.get('sleep_workers', 3)
    batch_size = runner_cfg.get('batch_size', 100)
    os.environ['SKILLOPT_SLEEP_WORKERS'] = str(workers)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    optimized = 0
    with ThreadPoolExecutor(max_workers=len(top_scored)) as ex:
        futures = {}
        for rank, (skill_name, *_) in enumerate(top_scored, 1):
            # ── 从自己的 session 中 mine ──
            digests = skill_sessions.get(skill_name, [])
            since = skill_last_run.get(skill_name)
            digests = filter_digests_by_since(digests, since)

            all_tasks = mine(digests, max_tasks=len(digests))
            batches = [all_tasks[i:i + batch_size]
                       for i in range(0, len(all_tasks), batch_size)]

            print(f'  [{skill_name}] #{rank}: {len(digests)} sessions → '
                  f'{len(all_tasks)} tasks → {len(batches)} batches')

            # F-7: 传入线程本地所需的「该 skill 的 since / 重试池副本」，
            #       _optimize_one_skill 不再接触共享 state/skill_last_run。
            failed_in = state.get('failed_tasks', {}).get(skill_name, [])
            fut = ex.submit(
                _optimize_one_skill,
                skill_name, since, failed_in,
                batches, sleep_cfg, dry_run=dry_run,
            )
            futures[fut] = skill_name

        for fut in as_completed(futures):
            name = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                print(f'  ❌ [{name}] 异常: {e}')
                continue
            if res.get('optimized'):
                optimized += 1
                print(f'  ✅ [{name}] 优化成功')
            else:
                print(f'  ﹣ [{name}] 未优化')

            # F-7: 增量合并 delta 到共享 state 并落盘（顺序、无并发写）
            with _STATE_LOCK:
                if res.get('skill_last_run_ts'):
                    skill_last_run[name] = res['skill_last_run_ts']
                if res.get('neg_cleared'):
                    state.setdefault('skill_neg_feedback', {})[name] = 0
                ft = res.get('failed_tasks')
                if ft is not None:
                    state.setdefault('failed_tasks', {})[name] = ft
                state['skill_last_run'] = skill_last_run
                save_state(state)

    print(f'\n{"="*65}')
    print(f'All done: {optimized}/{len(top_scored)} skills optimized')
    return optimized


# ── F-2: Skill 路控制环（auto-tuner 经 .env 下发执行参数） ──────────────
def _load_skillopt_env_overrides() -> dict:
    """读取 HERMES_HOME/.env 中的 SKILLOPT_* 覆盖（与 auto-tuner 写 .env 对应）。

    这些 actuator 由 auto-tuner 基于 skill_used_count 反馈自动调优，经 .env 下发到这里，
    使「数据飞轮能测量 skill 健康度」真正闭环到「能驱动 SkillOpt 执行参数」。
    """
    out: dict = {}
    env_file = HERMES_HOME / '.env'
    try:
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#') or '=' not in s:
                    continue
                k, v = s.split('=', 1)
                k = k.strip()
                if k.startswith('SKILLOPT_'):
                    out[k] = v.strip()
    except (OSError, FileNotFoundError):
        pass
    return out


def _apply_skillopt_controls(top_scored: list, skill_last_run: dict, runner_cfg: dict) -> list:
    """应用 auto-tuner 经 .env 下发的 SkillOpt 控制参数（F-2）。

    返回经过过滤的 top_scored 列表：
      - SKILLOPT_ENABLED=0 → 整轮跳过（master switch）
      - SKILLOPT_MAX_PER_NIGHT > 0 → 限制每夜优化技能数（覆盖 top_k）
      - SKILLOPT_COOLDOWN_DAYS > 0 → 跳过冷却期内（近期已优化）的技能
    """
    overrides = _load_skillopt_env_overrides()
    enabled = int(overrides.get('SKILLOPT_ENABLED', '1') or '1')
    if not enabled:
        print('[SkillCtrl] SKILLOPT_ENABLED=0，跳过本轮 SkillOpt 优化')
        return []

    max_per_night = int(overrides.get('SKILLOPT_MAX_PER_NIGHT',
                                     str(runner_cfg.get('top_k', 5))) or 0)
    if max_per_night and max_per_night > 0:
        if len(top_scored) > max_per_night:
            print(f'[SkillCtrl] SKILLOPT_MAX_PER_NIGHT={max_per_night}，'
                  f'截断 {len(top_scored)} → {max_per_night}')
            top_scored = top_scored[:max_per_night]

    cooldown_days = int(overrides.get('SKILLOPT_COOLDOWN_DAYS', '0') or 0)
    if cooldown_days and cooldown_days > 0:
        now = datetime.now(timezone.utc).timestamp()
        filtered = []
        for entry in top_scored:
            name = entry[0]
            ts = _parse_iso_to_timestamp(skill_last_run.get(name))
            if ts and (now - ts) < cooldown_days * 86400:
                print(f'  [SkillCtrl] {name} 在冷却期（{cooldown_days}天），跳过')
                continue
            filtered.append(entry)
        top_scored = filtered
    return top_scored


def main() -> int:
    """SkillOpt-Runner 主入口：三段流水线 → harvest → rank → per-skill optimize."""
    parser = argparse.ArgumentParser(
        description='SkillOpt-Runner: run SkillOpt-Sleep after Hermes curator')
    parser.add_argument('--dry-run', action='store_true',
                        help='Dry run: do not apply any changes')
    args = parser.parse_args()

    state: dict = {}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            our_cfg = yaml.safe_load(f) or {}
        runner_cfg, sleep_cfg = split_config(our_cfg)
        denylist_patterns = runner_cfg.get('denylist_patterns', [
            'lark-', 'sn-', 'gstack-',
        ])

        state = load_state()
        skill_last_run = state.setdefault('skill_last_run', {})

        # ── Phase 1: harvest ──
        all_digests = _phase_harvest(state, skill_last_run, runner_cfg)

        # ── Phase 2: rank ──
        usage = load_usage()
        eligible = filter_eligible_skills(usage, denylist_patterns)
        print(f'Found {len(eligible)} eligible skills for optimization')
        if not eligible:
            save_state(state)
            print('No eligible skills found, exiting')
            return 0

        top_scored, skill_sessions = _phase_rank(eligible, all_digests, state, runner_cfg)
        if not top_scored:
            return 0

        # ── F-2: Skill 路控制环（auto-tuner 经 .env 下发执行参数） ──
        top_scored = _apply_skillopt_controls(top_scored, skill_last_run, runner_cfg)
        if not top_scored:
            save_state(state)
            print('Skill 控制环过滤后无候选，退出')
            return 0

        # ── Phase 3: per-skill optimize ──
        _phase_optimize(
            top_scored, skill_last_run, state, skill_sessions,
            sleep_cfg, runner_cfg, dry_run=args.dry_run,
        )
        return 0
    except Exception as e:
        # 全局兜底：未捕获异常时尝试保存当前 state，避免下次从头开始
        print(f'FATAL: 未捕获异常 {type(e).__name__}: {e}', file=__import__('sys').stderr)
        if state:
            try:
                save_state(state)
                print('已保存当前 state 以便下次恢复')
            except Exception:
                pass
        return 1

if __name__ == '__main__':
    sys.exit(main())
