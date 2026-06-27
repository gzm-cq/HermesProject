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
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Set, Tuple

import yaml
import subprocess
import shutil
from collections import defaultdict

# SkillOpt-Sleep 克隆在本地，必须在 import 前插入 sys.path
SKILLOPT_HOME = pathlib.Path('/root/.hermes/skillopt-runner')
_SKILLOPT_SLEEP_PATH = str(SKILLOPT_HOME.parent / 'skillopt-sleep')
if _SKILLOPT_SLEEP_PATH not in sys.path:
    sys.path.insert(0, _SKILLOPT_SLEEP_PATH)

from skillopt_sleep.types import SessionDigest, TaskRecord
from skillopt_sleep.mine import mine
from skillopt_sleep.config import load_config, SleepConfig
from skillopt_sleep.cycle import run_sleep_cycle

HERMES_HOME = pathlib.Path(os.environ.get('HERMES_HOME', '/root/.hermes'))


USAGE_FILE = HERMES_HOME / 'skills' / '.usage.json'
BACKUP_DIR = SKILLOPT_HOME / 'backups'
CONFIG_PATH = SKILLOPT_HOME / 'config.yaml'
STATE_FILE = SKILLOPT_HOME / 'state.json'


def load_state() -> dict:
    """加载状态：迭代计数器、负反馈累积数据"""
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            s = json.load(f)
        # 兼容旧格式：迁移 last_run_iso → skill_last_run
        if 'last_run_iso' in s and 'skill_last_run' not in s:
            s['skill_last_run'] = {}
        return s
    return {
        'skill_last_run': {},             # skill_name -> 上次优化完成时间
        'skill_neg_feedback': {},         # skill_name -> 累积负反馈次数
        'skill_total_mentions': {},       # skill_name -> 累积被提及次数
    }


def save_state(state: dict):
    """保存状态到 state.json"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f'状态已保存: 累积 {len(state.get("skill_neg_feedback", {}))} 个技能的负反馈数据')

def load_usage() -> Dict[str, Dict]:
    if not USAGE_FILE.exists():
        return {}
    with open(USAGE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def filter_eligible_skills(usage: Dict[str, Dict], denylist_patterns: List[str]) -> List[Tuple[str, Dict]]:
    """Filter eligible skills for SkillOpt optimization.
    不需要 allowlist — 完全数据驱动，由 curator 生命周期 + denylist 决定。"""
    eligible: List[Tuple[str, Dict]] = []
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


def harvest_hermes_sessions(since_iso: Optional[str], max_sessions: int = 0) -> List[SessionDigest]:
    """Harvest Hermes session JSONs -> SkillOpt-Sleep SessionDigest list.
    If since_iso is provided, only harvest sessions that ended after since_iso (incremental mode).
    
    Storage format (双格式兼容):
    - *.jsonl → 旧格式，每行一条消息，每条有 role/content/timestamp/tool_name
    - session_*.json → 新格式，单个 JSON 含 messages 数组 + session_start/last_updated
    """
    digests: List[SessionDigest] = _harvest_state_db_sessions(since_iso)
    session_dir = HERMES_HOME / 'sessions'

    def _make_digest(session_id: str, project: str,
                     started_at: str, ended_at: str,
                     user_prompts: List[str], assistant_finals: List[str],
                     tools_used: List[str], feedback: List[str],
                     n_user: int, n_assistant: int, raw_path: str) -> Optional[SessionDigest]:
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
        messages: List[Dict] = []
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
        user_prompts: List[str] = []
        assistant_finals: List[str] = []
        tools_used: List[str] = []
        feedback: List[str] = []

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

def _detect_feedback(text: str) -> List[str]:
    """Detect positive/negative feedback from Hermes user message text, inherits SkillOpt-Sleep's detector + adds Chinese keywords."""
    lower = text.lower()
    sigs: List[str] = []
    # negative feedback: SkillOpt's original list
    _NEGATIVE = (
        "still broken", "still not", "still wrong", "doesn't work", "does not work", "that's wrong", "thats wrong", "incorrect", "wrong",
        "no,", "nope", "fix it", "didn't", "did not", "broken", "error again", "still failing", "still fails", "not fixed",
    )
    _POSITIVE = (
        "thanks", "thank you", "perfect", "great", "works now", "fixed", "that works", "lgtm", "looks good", "nice", "correct",
    )
    for ph in _NEGATIVE:
        if ph in lower:
            sigs.append(f'neg:{ph}')
    for ph in _POSITIVE:
        if ph in lower:
            sigs.append(f'pos:{ph}')
    # Chinese additional
    CN_NEGATIVE = (
        '不对', '错了', '还不对', '还错', '还是错', '没改好', '没修好', '改不对', '仍然不对', '不正确', '还是不行', '错误',
        '不行', '不好', '不可以',
        'bug', '缺陷', '失败',
    )
    for ph in CN_NEGATIVE:
        if ph in lower:
            sigs.append(f'neg:{ph}')
    # Chinese positive — 注意排除「不可以」「不对了」等负反馈包含正反馈词的情况
    CN_POSITIVE = (
        '可以', '好了', '对了', '正确', '通过', '行了', '改好了', '修好了', '搞定', '没问题', '可用',
    )
    for ph in CN_POSITIVE:
        if ph in lower:
            # 排除：'不可以' 含 '可以'，'不对了' 含 '对了'
            if ph == '可以' and '不可以' in lower:
                continue
            if ph == '对了' and '不对' in lower:
                continue
            if ph == '好了' and '不好' in lower:
                continue
            sigs.append(f'pos:{ph}')
    return sigs

def get_skill_path(skill_name: str) -> Optional[pathlib.Path]:
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
    eligible: List[Tuple[str, Dict]],
    digests: List[SessionDigest],
    state: dict,
    top_k: int = 5,
) -> Tuple[List[Tuple[str, Dict, float, int, int]], dict, Dict[str, list]]:
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


def patch_skill_hermes(skill_name: str, new_content: str, state: dict) -> bool:
    """Patch skill via Hermes skill_manage tool — merge edit into existing SKILL.md.
    安全特性：保留 frontmatter + append 到正文 + atomic write + security scan + 回滚。
    部署成功 → 自动清零该技能的累积负反馈。"""
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

    # backup
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H-%M-%S')
    backup_path = BACKUP_DIR / f'{skill_name}_{ts}.md.bak'
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, backup_path)
    print(f'BACKUP saved to {backup_path}')

    # 直接调用 Hermes skill_manage 工具
    hermes_agent_path = str(HERMES_HOME / 'hermes-agent')
    if hermes_agent_path not in sys.path:
        sys.path.insert(0, hermes_agent_path)
    try:
        from tools.skill_manager_tool import skill_manage
    except ImportError as e:
        print(f'ERROR: cannot import skill_manage from Hermes: {e}')
        return False

    import json as _json

    result = skill_manage(
        action='edit',
        name=skill_name,
        content=merged,
    )
    # skill_manage 工具返回 JSON 字符串，需要反序列化
    if isinstance(result, str):
        try:
            result = _json.loads(result)
        except _json.JSONDecodeError:
            pass
    if not isinstance(result, dict) or not result.get('success'):
        err = result.get('error', 'unknown error') if isinstance(result, dict) else str(result)
        print(f'ERROR: skill_manage edit failed: {err}')
        shutil.copy2(backup_path, p)  # revert
        return False

    print(f'SUCCESS: patch applied via Hermes skill_manage')
    # 部署成功 → 清零该技能负反馈
    if skill_name in state.get('skill_neg_feedback', {}):
        old_val = state['skill_neg_feedback'][skill_name]
        state['skill_neg_feedback'][skill_name] = 0
        save_state(state)
        print(f'  → 负反馈清零: {skill_name} ({old_val} → 0)')
    return True

def filter_digests_by_since(
    digests: List[SessionDigest], since_iso: str | None
) -> List[SessionDigest]:
    """Filter sessions to only those ending after ``since_iso``.

    Args:
        digests: Full harvest of session digests.
        since_iso: ISO timestamp bound. ``None`` means no filter (full harvest).

    Returns:
        Filtered session digests.
    """
    if not since_iso:
        return digests  # full harvest (first time for this skill)
    result = [d for d in digests if d.ended_at and d.ended_at >= since_iso]
    print(f'  after {since_iso}: {len(result)} sessions in window')
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
    sincetimes = [v for v in skill_last_run.values() if v]
    if sincetimes:
        harvest_since = min(sincetimes)
        print(f'Phase 1: 增量 harvest（自 {harvest_since} 之后的新 session）')
    else:
        harvest_since = None
        print('Phase 1: 全量 harvest（首次运行）')
    max_sessions = runner_cfg.get('max_sessions', 0)
    digests = harvest_hermes_sessions(harvest_since, max_sessions=max_sessions)
    print(f'Harvested {len(digests)} total sessions')
    return digests


def _phase_rank(
    eligible: list,
    all_digests: list[SessionDigest],
    state: dict,
    runner_cfg: dict,
) -> Tuple[list, Dict[str, list]]:
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
    skill_last_run: dict[str, str],
    state: dict,
    batches: list[list],
    sleep_cfg: dict,
    *,
    dry_run: bool,
) -> tuple[str, bool]:
    """Run one skill through all batches. Returns (skill_name, optimized).

    Failed task retry pool (state.failed_tasks):
        If a batch's gate rejects, its tasks are appended to the retry pool
        keyed by skill_name (with dedup + max_retries cap). Next run, failed
        tasks are appended after fresh batches so the updated skill gets a
        chance to fix them. Transient API errors don't permanently lose work.
    """
    # ── Load failed tasks from retry pool ──
    failed_tasks = state.setdefault('failed_tasks', {})
    saved = failed_tasks.pop(skill_name, [])
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
        return skill_name, False

    p = get_skill_path(skill_name)
    if not p:
        print(f'  [{skill_name}] 找不到 SKILL.md，跳过')
        return skill_name, False

    per_cfg = dict(sleep_cfg)
    per_cfg['projects'] = [str(p.parent)]

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
            MAX_RETRY_POOL = 200
            merged = merged[-MAX_RETRY_POOL:]
            # 淘汰超限重试
            merged = [t for t in merged if t.get('retry_count', 0) <= 3]
            failed_tasks[skill_name] = merged
            save_state(state)
            print(f'  [{skill_name}] → Gate 拒绝，重试池当前 {len(merged)} 个 task')
            continue

        # ── Gate accept — clear retry pool ──
        failed_tasks.pop(skill_name, None)
        save_state(state)

        if dry_run:
            print(f'  [{skill_name}] → Dry-run: {len(result.report.edits)} edits accepted')
            # Dry-run 模式下继续跑剩余 batch（验证全流程）
            continue

        # Apply edits（不 return，继续跑下一个 batch，skill 已更新）
        print(f'  [{skill_name}] ✅ Batch {batch_idx} passed!')
        for edit in result.report.edits:
            ok = patch_skill_hermes(skill_name, edit.content, state)
            if ok:
                print(f'  [{skill_name}] ✅ Applied: {edit.target}/{edit.op}')
                skill_last_run[skill_name] = datetime.now(timezone.utc).isoformat()
                break
            else:
                print(f'  [{skill_name}] ❌ Apply failed: {edit.target}/{edit.op}')

    return skill_name, True if skill_last_run.get(skill_name) else False


def _phase_optimize(
    top_scored: list,
    skill_last_run: dict[str, str],
    state: dict,
    skill_sessions: Dict[str, list],
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

            fut = ex.submit(
                _optimize_one_skill,
                skill_name, skill_last_run, state,
                batches, sleep_cfg, dry_run=dry_run,
            )
            futures[fut] = skill_name

        for fut in as_completed(futures):
            name = futures[fut]
            try:
                _, ok = fut.result()
                if ok:
                    optimized += 1
                    print(f'  ✅ [{name}] 优化成功')
                else:
                    print(f'  ﹣ [{name}] 未优化')
            except Exception as e:
                print(f'  ❌ [{name}] 异常: {e}')

    save_state(state)
    print(f'\n{"="*65}')
    print(f'All done: {optimized}/{len(top_scored)} skills optimized')
    return optimized


def main() -> int:
    """SkillOpt-Runner 主入口：三段流水线 → harvest → rank → per-skill optimize."""
    parser = argparse.ArgumentParser(
        description='SkillOpt-Runner: run SkillOpt-Sleep after Hermes curator')
    parser.add_argument('--dry-run', action='store_true',
                        help='Dry run: do not apply any changes')
    args = parser.parse_args()

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

    # ── Phase 3: per-skill optimize ──
    _phase_optimize(
        top_scored, skill_last_run, state, skill_sessions,
        sleep_cfg, runner_cfg, dry_run=args.dry_run,
    )
    return 0

if __name__ == '__main__':
    sys.exit(main())
