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
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
def call_llm(prompt: str, model: str, max_tokens: int = 4096) -> str:
    """通过 LiteLLM 网关调用 LLM"""
    base_url = CFG["llm"]["base_url"]
    # 从环境变量获取 API key
    api_key = os.environ.get("LITELLM_MASTER_KEY", "sk-litellm-default")
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def call_llm_json(prompt: str, model: str) -> dict:
    """调用 LLM 并解析 JSON 输出"""
    raw = call_llm(prompt, model, max_tokens=1024)
    # 提取 JSON
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {}


def load_prompt(name: str) -> str:
    with open(PROMPTS_DIR / f"{name}.txt", encoding="utf-8") as f:
        return f.read().strip()

# ── Session 提取 ─────────────────────────────────────
def read_sessions(since_ts: float, dry_run: bool = False) -> list[dict]:
    """从 state.db 读当天新增的 session"""
    db_path = CFG["session"]["db_path"]
    min_msgs = CFG["session"]["min_messages"]
    min_tokens = CFG["session"]["min_tokens"]

    conn = sqlite3.connect(db_path)
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

# ── SAG 写入 ──────────────────────────────────────────
def sag_ingest(title: str, content: str, metadata: dict, dry_run: bool = False) -> bool:
    """通过 SAG MCP 写入文档"""
    if dry_run:
        print(f"  [DRY-RUN] SAG ingest: {title[:50]}")
        return True

    url = CFG["sag"]["ingest_url"]
    # SAG MCP 使用 SSE，需要先建立 session
    # 直接用内部 API 更简单
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "sag_ingest_document",
            "arguments": {
                "title": title,
                "content": content,
                "metadata": metadata,
                "waitForCompletion": False,
            },
        },
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            return True
        print(f"  SAG ingest failed: {resp.status_code} {resp.text[:100]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  SAG ingest error: {e}", file=sys.stderr)
        return False


def sag_search(query: str, top_k: int = 50) -> list[dict]:
    """从 SAG 搜索文档"""
    url = CFG["sag"]["search_url"]
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "sag_search",
            "arguments": {
                "query": query,
                "topK": top_k,
                "searchMode": "fast",
            },
        },
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            # 解析 SSE 或 JSON
            raw = resp.text
            for line in raw.split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if "result" in data:
                        result = data["result"]
                        if isinstance(result, str):
                            result = json.loads(result)
                        return result.get("sections", [])
            # 尝试直接 JSON
            data = resp.json()
            if "result" in data:
                result = data["result"]
                if isinstance(result, str):
                    result = json.loads(result)
                return result.get("sections", [])
    except Exception as e:
        print(f"  SAG search error: {e}", file=sys.stderr)
    return []

# ── Phase 1: synthesize ──────────────────────────────
def phase_synthesize(sessions: list[dict], dry_run: bool = False) -> list[dict]:
    """提炼 session → 反思笔记 → 写入 SAG"""
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

        # 幂等：检查 verdict 缓存
        verdict_file = os.path.join(verdict_dir, f"{sid}.json")
        if os.path.exists(verdict_file):
            with open(verdict_file) as f:
                verdict = json.load(f)
            print(f"  [{i+1}/{len(sessions)}] CACHE {title[:40]} → score={verdict.get('score','?')}")
        else:
            # significanceFilter — 截断到 4000 字避免超时
            prompt = sig_prompt + "\n\n对话：\n" + text[:4000]
            try:
                verdict = call_llm_json(prompt, cheap_model)
            except Exception as e:
                print(f"  [{i+1}/{len(sessions)}] FILTER FAIL {title[:40]}: {e}")
                verdict = {"score": 0, "reason": str(e)}
            verdict["session_id"] = sid
            with open(verdict_file, "w", encoding="utf-8") as f:
                json.dump(verdict, f, ensure_ascii=False, indent=2)
            score = verdict.get("score", 0)
            print(f"  [{i+1}/{len(sessions)}] FILTER {title[:40]} → score={score}")

        score = verdict.get("score", 0)
        if score < 3:
            continue

        # synthesize
        prompt = syn_prompt.replace("{session_text}", text[:8000])
        try:
            md_content = call_llm(prompt, smart_model)
        except Exception as e:
            print(f"  synthesize 失败: {e}", file=sys.stderr)
            continue

        # 提取标题
        title_match = re.search(r"^#\s+(.+)$", md_content, re.MULTILINE)
        refl_title = title_match.group(1).strip() if title_match else f"反思-{title[:30]}"

        # 写入 SAG
        metadata = {
            "source": "dream-synth",
            "session_id": sid,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "score": score,
        }
        ok = sag_ingest(refl_title, md_content, metadata, dry_run=dry_run)
        if ok:
            reflections.append({
                "title": refl_title,
                "content": md_content,
                "session_id": sid,
                "score": score,
            })
            print(f"  [{i+1}/{len(sessions)}] SYNTH → {refl_title[:50]}")

        time.sleep(0.5)  # 避免限流

    return reflections

# ── Phase 2: patterns ────────────────────────────────
def phase_patterns(reflections: list[dict], dry_run: bool = False) -> list[dict]:
    """跨 session 主题发现"""
    if len(reflections) < 2:
        print("  patterns: 反思笔记不足 2 篇，跳过")
        return []

    smart_model = CFG["llm"]["smart"]
    prompt_tmpl = load_prompt("pattern-discovery")

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
        wiki_path = os.path.join(wiki_base, category, f"{r['title'][:60]}.md")

        if dry_run:
            print(f"  [DRY-RUN] WIKI → {category}/{r['title'][:40]}")
            promoted.append(r)
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
        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write(frontmatter + r["content"])

        # 记入去重日志
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

    # 按 score 排序取 top-5
    unsorted.sort(key=lambda r: r.get("score", 0), reverse=True)
    top5 = unsorted[:5]

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
        summary = summary_match.group(1).strip()[:100] if summary_match else ""
        lines.append(f"   {summary}")
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
        result = os.system(f"lark-cli im +messages-send --chat-id {chat_id} --markdown '{msg}' --as bot 2>&1")
        if result == 0:
            print("  feishu: 推送成功")
        else:
            print(f"  feishu: 推送失败 (exit={result})", file=sys.stderr)
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
        # 如果只跑 patterns，从 SAG 查近期反思
        if not reflections:
            sections = sag_search("dream-synth", top_k=50)
            reflections = [{"title": s.get("title", ""),
                            "content": s.get("content", "")[:500],
                            "session_id": s.get("documentId", ""),
                            "score": 3} for s in sections]
        phase_patterns(reflections, dry_run=args.dry_run)

    # Phase 3: promote
    promoted = []
    if not args.phase or args.phase == "promote":
        print("\n── Phase 3: promote ──")
        promoted = phase_promote(reflections, dry_run=args.dry_run)
        print(f"  归档 {len(promoted)} 篇到 Wiki")

    # Phase 4: feishu
    if not args.phase or args.phase == "feishu":
        print("\n── Phase 4: feishu push ──")
        phase_feishu(reflections, promoted, dry_run=args.dry_run)

    # 更新时间戳
    if not args.dry_run:
        save_last_run_ts(now_ts)

    print(f"\n✅ 梦境流水线完成 — {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()