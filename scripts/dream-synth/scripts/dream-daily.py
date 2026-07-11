#!/usr/bin/env python3
"""
dream-daily.py — 每日梦境流水线

4 阶段串行：
  1. synthesize:  session → 反思笔记 → SAG
  2. patterns:    跨 session 主题发现 → SAG
  3. promote:     精选 → axiom-wiki
  4. feishu push:  top-5 未归档反思 → 飞书

Usage:
  python3 dream-daily.py                    # 完整流水线
  python3 dream-daily.py --dry-run          # 只打印不写入
  python3 dream-daily.py --phase synthesize # 只跑某一阶段
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

# ── 路径 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
PROMPTS_DIR = PROJECT_DIR / "prompts"
CONFIG_PATH = PROJECT_DIR / "config.yaml"

# ── 配置 ──────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)

CFG = load_config()

# ── LLM 调用 ─────────────────────────────────────────
def call_llm(prompt: str, model: str, max_tokens: int = 4096, temperature: float = 0.3) -> str:
    """通过 LiteLLM 网关调用 LLM"""
    base_url = CFG["llm"]["base_url"]
    api_key = os.environ.get("LITELLM_MASTER_KEY", "")
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def call_llm_json(prompt: str, model: str, max_retries: int = 2) -> dict:
    """调用 LLM 并解析 JSON 输出（temperature=0，带重试）"""
    for attempt in range(max_retries + 1):
        raw = call_llm(prompt, model, max_tokens=1024, temperature=0.0)
        # 先尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 再用正则提取第一个 JSON 对象（非贪婪）
        m = re.search(r"\{[\s\S]*?\}", raw)
        if not m:
            if attempt < max_retries:
                continue
            return {}
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            # 尝试贪婪匹配（处理嵌套对象）
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            if attempt < max_retries:
                continue
            return {}
    return {}


def load_prompt(name: str) -> str:
    with open(PROMPTS_DIR / f"{name}.txt", encoding="utf-8") as f:
        return f.read().strip()

# ── Session 提取 ─────────────────────────────────────

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\uFF1A\uFF0F\uFF1C\uFF1E\uFF1F\uFF0A]')


def _sanitize_filename(name: str, max_len: int = 60) -> str:
    """清洗文件名中的非法字符（Windows/Unix 兼容，含全角符号）。"""
    name = _INVALID_FILENAME_CHARS.sub("_", name).strip().rstrip(".")
    return name[:max_len] if name else "untitled"


def _safe_int(val, default: int = 0) -> int:
    """安全转 int，失败返回 default。"""
    try:
        return int(val)
    except (TypeError, ValueError):
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return default


def read_sessions(since_ts: float, dry_run: bool = False) -> list[dict]:
    """从 state.db 读当天新增的 session"""
    db_path = CFG["session"]["db_path"]
    min_msgs = CFG["session"]["min_messages"]
    min_tokens = CFG["session"]["min_tokens"]

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            """SELECT id, title, message_count, input_tokens, started_at
               FROM sessions
               WHERE started_at >= ? AND message_count >= ? AND input_tokens >= ?
                 AND archived = 0
               ORDER BY started_at""",
            (since_ts, min_msgs, min_tokens),
        )
        sessions = [dict(r) for r in cur.fetchall()]

        for s in sessions:
            cur.execute(
                """SELECT role, content, timestamp
                   FROM messages WHERE session_id = ? ORDER BY id""",
                (s["id"],),
            )
            msgs = cur.fetchall()
            text_parts = []
            for m in msgs:
                role = m["role"]
                content = m["content"] or ""
                if role == "user":
                    text_parts.append(f"[用户] {content[:2000]}")
                elif role == "assistant":
                    text_parts.append(f"[助手] {content[:3000]}")
                elif role == "tool":
                    # 只保留工具名，不保留结果
                    pass
            s["text"] = "\n\n".join(text_parts)
            s["text_len"] = len(s["text"])
    finally:
        conn.close()

    # 过滤太短的
    sessions = [s for s in sessions if s["text_len"] >= 2000]
    return sessions


def get_last_run_ts() -> float:
    """读上次运行时间戳"""
    verdict_dir = CFG["cache"]["verdict_dir"]
    ts_file = os.path.join(verdict_dir, "last_run.txt")
    if os.path.exists(ts_file):
        try:
            with open(ts_file) as f:
                return float(f.read().strip())
        except (ValueError, IOError):
            pass
    # 默认取 24 小时前
    return (datetime.now().timestamp() - 86400)


def save_last_run_ts(ts: float):
    verdict_dir = CFG["cache"]["verdict_dir"]
    os.makedirs(verdict_dir, exist_ok=True)
    ts_file = os.path.join(verdict_dir, "last_run.txt")
    with open(ts_file, "w") as f:
        f.write(str(ts))

# ── SAG ─────────────────────────────────────────────
def sag_ingest(title: str, content: str, metadata: dict, dry_run: bool = False) -> bool:
    """通过 SAG REST API 写入文档（/api/documents/upload）"""
    if dry_run:
        print(f"  [DRY-RUN] SAG ingest: {title[:50]}")
        return True

    base_url = CFG["sag"]["base_url"]
    payload = {
        "title": title,
        "content": content,
        "metadata": metadata,
    }
    try:
        resp = requests.post(f"{base_url}/api/documents/upload", json=payload, timeout=60)
        if resp.status_code in (200, 201):
            return True
        print(f"  SAG ingest failed: {resp.status_code} {resp.text[:100]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  SAG ingest error: {e}", file=sys.stderr)
        return False


def sag_search(query: str, top_k: int = 50, source_filter: str | None = None) -> list[dict]:
    """从 SAG 搜索文档（REST API /search），可选按 metadata.source 过滤"""
    base_url = CFG["sag"]["base_url"]
    payload = {
        "query": query,
        "topK": top_k,
        "searchMode": "fast",
    }
    try:
        resp = requests.post(f"{base_url}/search", json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            sections = data.get("sections", [])
            if source_filter:
                filtered = []
                for s in sections:
                    meta = s.get("metadata") or {}
                    if meta.get("source") == source_filter or s.get("source") == source_filter:
                        filtered.append(s)
                sections = filtered
            return sections
    except Exception as e:
        print(f"  SAG search error: {e}", file=sys.stderr)
    return []

# ── Phase 1: synthesize ──────────────────────────────
def _save_verdict(verdict_file: str, data: dict):
    os.makedirs(os.path.dirname(verdict_file), exist_ok=True)
    with open(verdict_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def phase_synthesize(sessions: list[dict], dry_run: bool = False) -> list[dict]:
    """提炼 session → 反思笔记 → 写入 SAG（带完整幂等）"""
    if not sessions:
        print("  synthesize: 无新 session，跳过")
        return []

    cheap_model = CFG["llm"]["cheap"]
    smart_model = CFG["llm"]["smart"]
    sig_prompt = load_prompt("significance-filter")
    syn_prompt = load_prompt("synthesis")

    verdict_dir = CFG["cache"]["verdict_dir"]
    os.makedirs(verdict_dir, exist_ok=True)

    reflections = []
    for i, s in enumerate(sessions):
        sid = s["id"]
        title = s.get("title", "untitled")
        text = s["text"]

        verdict_file = os.path.join(verdict_dir, f"{sid}.json")

        # 1. 读取缓存（包含 verdict / synthesis / ingested 状态）
        cache = None
        if os.path.exists(verdict_file):
            try:
                with open(verdict_file, encoding="utf-8") as f:
                    cache = json.load(f)
            except (json.JSONDecodeError, IOError):
                cache = None

        # 2. significanceFilter
        if cache and "score" in cache:
            score = cache.get("score", 0)
            print(f"  [{i+1}/{len(sessions)}] CACHE {title[:40]} → score={score}")
        else:
            prompt = sig_prompt + "\n\n对话：\n" + text[:4000]
            try:
                verdict = call_llm_json(prompt, cheap_model)
            except Exception as e:
                print(f"  [{i+1}/{len(sessions)}] FILTER FAIL {title[:40]}: {e}（不缓存，下次重试）")
                continue
            verdict["session_id"] = sid
            cache = verdict
            _save_verdict(verdict_file, cache)
            score = verdict.get("score", 0)
            print(f"  [{i+1}/{len(sessions)}] FILTER {title[:40]} → score={score}")

        if score < 3:
            continue

        # 3. synthesize（有缓存且成功过则跳过）
        if cache and cache.get("synthesized") and cache.get("reflection_content"):
            md_content = cache["reflection_content"]
            refl_title = cache.get("reflection_title", f"反思-{title[:30]}")
            print(f"  [{i+1}/{len(sessions)}] SYNTH-CACHE → {refl_title[:40]}")
        else:
            prompt = syn_prompt.replace("{session_text}", text[:8000])
            try:
                md_content = call_llm(prompt, smart_model, temperature=0.3)
            except Exception as e:
                print(f"  synthesize 失败: {e}", file=sys.stderr)
                continue

            title_match = re.search(r"^#\s+(.+)$", md_content, re.MULTILINE)
            refl_title = title_match.group(1).strip() if title_match else f"反思-{title[:30]}"

            cache["synthesized"] = True
            cache["reflection_title"] = refl_title
            cache["reflection_content"] = md_content
            _save_verdict(verdict_file, cache)

        # 4. 写入 SAG（已成功写入过则跳过）
        if cache.get("ingested"):
            print(f"  [{i+1}/{len(sessions)}] INGEST-CACHE → {refl_title[:40]}")
            reflections.append({
                "title": refl_title,
                "content": md_content,
                "session_id": sid,
                "score": score,
            })
            continue

        metadata = {
            "source": "dream-synth",
            "session_id": sid,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "score": score,
        }
        ok = sag_ingest(refl_title, md_content, metadata, dry_run=dry_run)
        if ok:
            cache["ingested"] = True
            _save_verdict(verdict_file, cache)
            reflections.append({
                "title": refl_title,
                "content": md_content,
                "session_id": sid,
                "score": score,
            })
            print(f"  [{i+1}/{len(sessions)}] SYNTH → {refl_title[:50]}")

        time.sleep(0.5)

    return reflections

# ── Phase 2: patterns ────────────────────────────────
def phase_patterns(reflections: list[dict], dry_run: bool = False) -> list[dict]:
    """跨 session 主题发现"""
    if len(reflections) < 2:
        print("  patterns: 反思笔记不足 2 篇，跳过")
        return []

    smart_model = CFG["llm"]["smart"]
    prompt_tmpl = load_prompt("pattern-discovery")
    pattern_log = CFG["cache"].get("pattern_log", os.path.join(os.path.dirname(CFG["cache"]["verdict_dir"]), "pattern-log.json"))

    # 读已写入的 pattern topic 清单（幂等）
    written_topics: set[str] = set()
    if os.path.exists(pattern_log):
        with open(pattern_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    written_topics.add(entry.get("topic", ""))
                except json.JSONDecodeError:
                    pass

    # 拼接反思摘要
    refl_parts = []
    for r in reflections:
        # 只取摘要部分
        summary = r["content"][:500]
        refl_parts.append(f"### {r['title']}\n{summary}")
    refl_text = "\n\n---\n\n".join(refl_parts)

    prompt = prompt_tmpl.replace("{reflections_text}", refl_text[:12000])
    try:
        result = call_llm_json(prompt, smart_model)
    except Exception as e:
        print(f"  patterns 失败: {e}", file=sys.stderr)
        return []

    patterns = result.get("patterns", [])
    if not patterns:
        print("  patterns: 未发现重复主题")
        return []

    written = []
    for p in patterns:
        topic = p.get("topic", "")
        if not topic:
            continue
        # 幂等：同 topic 已写入过则跳过
        if topic in written_topics:
            print(f"  PATTERN SKIP (已写入): {topic}")
            continue

        content = f"## 模式：{topic}\n\n{p.get('summary','')}\n\n"
        content += f"**出现次数**: {p.get('evidence_count', 0)}\n\n"
        evidence = p.get("evidence_ids", [])
        content += "**相关反思**:\n" + "\n".join(f"- {e}" for e in evidence) + "\n"

        metadata = {
            "source": "dream-pattern",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "topic": topic,
        }
        ok = sag_ingest(f"模式发现：{topic}", content, metadata, dry_run=dry_run)
        if ok:
            written.append(p)
            written_topics.add(topic)
            # 记入幂等日志
            if not dry_run:
                os.makedirs(os.path.dirname(pattern_log), exist_ok=True)
                with open(pattern_log, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"topic": topic, "date": metadata["date"]},
                                        ensure_ascii=False) + "\n")
            print(f"  PATTERN → {topic} ({p.get('evidence_count',0)} 次)")

    return written

# ── Phase 3: promote ──────────────────────────────────
def phase_promote(reflections: list[dict], dry_run: bool = False) -> list[dict]:
    """精选反思笔记 → axiom-wiki"""
    if not reflections:
        print("  promote: 无反思笔记，跳过")
        return []

    smart_model = CFG["llm"]["smart"]
    prompt_tmpl = load_prompt("promote-judge")
    promote_log = CFG["cache"]["promote_log"]

    # 读已归档清单
    promoted_ids = set()
    if os.path.exists(promote_log):
        with open(promote_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    promoted_ids.add(entry.get("session_id", ""))
                except json.JSONDecodeError:
                    pass

    wiki_base = CFG["wiki"]["base_path"]
    promoted = []
    for r in reflections:
        sid = r["session_id"]
        if sid in promoted_ids:
            continue

        # LLM 判断
        prompt = prompt_tmpl + "\n\n反思笔记：\n" + r["content"][:3000]
        try:
            verdict = call_llm_json(prompt, smart_model)
        except Exception as e:
            print(f"  promote 判断失败: {e}", file=sys.stderr)
            continue

        if not verdict.get("promote", False):
            print(f"  PROMOTE SKIP: {r['title'][:40]} → {verdict.get('reason','')}")
            continue

        category = verdict.get("category", "concepts")
        safe_title = _sanitize_filename(r["title"], max_len=60)
        wiki_path = os.path.join(wiki_base, category, f"{safe_title}.md")

        if dry_run:
            print(f"  [DRY-RUN] WIKI → {category}/{r['title'][:40]}")
            promoted.append(r)
            continue

        # 幂等：如果 Wiki 文件已存在，只补日志
        if os.path.exists(wiki_path):
            with open(promote_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"session_id": sid, "title": r["title"],
                                     "category": category, "path": wiki_path},
                                    ensure_ascii=False) + "\n")
            promoted.append(r)
            print(f"  PROMOTE EXISTS → {category}/{r['title'][:50]}")
            continue

        # 写入 wiki
        os.makedirs(os.path.dirname(wiki_path), exist_ok=True)
        frontmatter = f"""---
title: {r['title']}
type: {category}
tags: [dream-synth, dream-promote]
source: dream-synth
session_id: {sid}
date: {datetime.now().strftime('%Y-%m-%d')}
---

"""
        try:
            with open(wiki_path, "w", encoding="utf-8") as f:
                f.write(frontmatter + r["content"])
        except OSError as e:
            print(f"  PROMOTE 写入失败: {e}", file=sys.stderr)
            continue

        # 记入去重日志（文件写入成功后立即记录）
        with open(promote_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": sid, "title": r["title"],
                                 "category": category, "path": wiki_path},
                                ensure_ascii=False) + "\n")

        promoted.append(r)
        print(f"  PROMOTE → {category}/{r['title'][:50]}")

    return promoted

# ── Phase 4: 飞书推送 ────────────────────────────────
def phase_feishu(reflections: list[dict], promoted: list[dict], dry_run: bool = False):
    """推送 top-5 未归档反思到飞书"""
    promoted_sids = {r["session_id"] for r in promoted}
    unsorted = [r for r in reflections if r["session_id"] not in promoted_sids]

    if not unsorted:
        print("  feishu: 无未归档反思，跳过")
        return

    # 幂等：检查当天是否已推送过
    feishu_log = CFG["cache"].get("feishu_log", os.path.join(os.path.dirname(CFG["cache"]["verdict_dir"]), "feishu-log.json"))
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(feishu_log):
        with open(feishu_log, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("date") == today:
                        print(f"  feishu: 今天已推送过（{today}），跳过")
                        return
                except json.JSONDecodeError:
                    pass

    # 按 score 排序取 top-5
    feishu_top_n = CFG.get("feishu", {}).get("top_n", 5)
    unsorted.sort(key=lambda r: r.get("score", 0), reverse=True)
    top5 = unsorted[:feishu_top_n]

    # 格式化飞书消息
    lines = [f"# 🌙 梦境流水线 — {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append(f"今日提炼 {len(reflections)} 篇反思，归档 {len(promoted)} 篇，未归档 {len(unsorted)} 篇")
    lines.append("")
    lines.append("## Top-5 未归档反思")
    for i, r in enumerate(top5):
        lines.append(f"{i+1}. **{r['title'][:50]}**")
        # 取摘要第一段
        content = r["content"]
        summary_match = re.search(r"## 摘要\s*\n(.+?)(?:\n##|\Z)", content, re.DOTALL)
        summary = summary_match.group(1).strip()[:100] if summary_match else content[:100]
        lines.append(f"   {summary}")
        # 未归档原因
        score = r.get("score", 0)
        if score < 4:
            reason = "分数不够高"
        else:
            reason = "内容偏临时"
        lines.append(f"   _未归档原因：{reason}_")
        lines.append("")

    lines.append("---")
    lines.append("_如需归档，告诉 axiom 即可_")

    msg = "\n".join(lines)

    if dry_run:
        print(f"\n  [DRY-RUN] 飞书消息:\n{msg[:500]}")
        return

    # 推送飞书
    chat_id = CFG["feishu"]["chat_id"]
    try:
        proc = subprocess.run(
            ["lark-cli", "im", "+messages-send",
             "--chat-id", chat_id,
             "--markdown", msg,
             "--as", "bot"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            print("  feishu: 推送成功")
            # 记入幂等日志
            os.makedirs(os.path.dirname(feishu_log), exist_ok=True)
            with open(feishu_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"date": today, "time": datetime.now().strftime("%H:%M")},
                                    ensure_ascii=False) + "\n")
        else:
            print(f"  feishu: 推送失败 (exit={proc.returncode}) {proc.stderr[:200]}",
                  file=sys.stderr)
    except Exception as e:
        print(f"  feishu: 推送异常: {e}", file=sys.stderr)

# ── 主入口 ────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="dream-daily — 每日梦境流水线")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    parser.add_argument("--phase", choices=["synthesize", "patterns", "promote", "feishu"],
                        help="只跑某一阶段")
    args = parser.parse_args()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"🌙 梦境流水线启动 — {now_str}")

    try:
        _run_pipeline(args)
    except Exception as e:
        print(f"\n❌ 流水线异常中断: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print(f"\n✅ 梦境流水线完成 — {datetime.now().strftime('%H:%M:%S')}")


def _run_pipeline(args):
    """执行流水线各阶段。"""
    last_ts = get_last_run_ts()
    now_ts = datetime.now().timestamp()

    # Phase 1: synthesize
    reflections = []
    if not args.phase or args.phase == "synthesize":
        print("\n── Phase 1: synthesize ──")
        sessions = read_sessions(last_ts, dry_run=args.dry_run)
        print(f"  发现 {len(sessions)} 个新 session")
        reflections = phase_synthesize(sessions, dry_run=args.dry_run)
        print(f"  产出 {len(reflections)} 篇反思笔记")

    # Phase 2: patterns
    if not args.phase or args.phase == "patterns":
        print("\n── Phase 2: patterns ──")
        # 如果只跑 patterns，从 SAG 查近期 dream-synth 文档
        if not reflections:
            sections = sag_search("知识 反思 决策", top_k=100, source_filter="dream-synth")
            reflections = [{"title": s.get("title", ""),
                            "content": s.get("content", ""),
                            "session_id": (s.get("metadata") or {}).get("session_id", s.get("documentId", "")),
                            "score": _safe_int((s.get("metadata") or {}).get("score", 3), 3)} for s in sections]
        phase_patterns(reflections, dry_run=args.dry_run)

    # Phase 3: promote
    promoted = []
    if not args.phase or args.phase == "promote":
        print("\n── Phase 3: promote ──")
        # 如果只跑 promote，从 SAG 查近期 dream-synth 反思
        if not reflections:
            sections = sag_search("反思 笔记", top_k=50, source_filter="dream-synth")
            reflections = [{"title": s.get("title", ""),
                            "content": s.get("content", ""),
                            "session_id": (s.get("metadata") or {}).get("session_id", s.get("documentId", "")),
                            "score": _safe_int((s.get("metadata") or {}).get("score", 3), 3)} for s in sections]
        promoted = phase_promote(reflections, dry_run=args.dry_run)
        print(f"  归档 {len(promoted)} 篇到 Wiki")

    # Phase 4: feishu
    if not args.phase or args.phase == "feishu":
        print("\n── Phase 4: feishu push ──")
        # 如果只跑 feishu 且无 reflections，从 SAG 查
        if not reflections:
            sections = sag_search("反思 笔记", top_k=50, source_filter="dream-synth")
            reflections = [{"title": s.get("title", ""),
                            "content": s.get("content", ""),
                            "session_id": (s.get("metadata") or {}).get("session_id", s.get("documentId", "")),
                            "score": _safe_int((s.get("metadata") or {}).get("score", 3), 3)} for s in sections]
        phase_feishu(reflections, promoted, dry_run=args.dry_run)

    # 更新时间戳：仅在完整流水线或 synthesize 阶段运行后更新
    # 单独运行 patterns/promote/feishu 时不更新，避免跳过未处理的 session
    if not args.dry_run and (not args.phase or args.phase == "synthesize"):
        save_last_run_ts(now_ts)


if __name__ == "__main__":
    main()