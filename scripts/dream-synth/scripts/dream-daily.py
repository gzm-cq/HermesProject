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
import random
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
import yaml

try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda iterable, desc=None, total=None: iterable

_shutdown_event = threading.Event()

def _handle_shutdown(signum, frame):
    _shutdown_event.set()
    print("\n⚠️ 收到中断信号，停止提交新任务，等待已提交任务完成...", file=sys.stderr)

signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)

_sag_session: requests.Session | None = None
_sag_session_lock = threading.Lock()

def _get_sag_session() -> requests.Session:
    """获取全局 SAG requests Session（连接池复用，线程安全）"""
    global _sag_session
    if _sag_session is None:
        with _sag_session_lock:
            if _sag_session is None:
                _sag_session = requests.Session()
    return _sag_session

@dataclass
class IngestStats:
    """SAG ingest 运行指标统计"""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0

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

# 环境变量覆盖（继承链：DREAM_SYNTH_* → LLM_MODEL_LIGHT → LLM_MODEL_MAIN）
_env_light = os.environ.get("LLM_MODEL_LIGHT", "")
_env_main = os.environ.get("LLM_MODEL_MAIN", "")
_env_cheap = os.environ.get("DREAM_SYNTH_CHEAP_MODEL") or \
             os.environ.get("DREAM_SYNTH_LLM_MODEL") or \
             _env_light or _env_main
_env_smart = os.environ.get("DREAM_SYNTH_SMART_MODEL") or \
             os.environ.get("DREAM_SYNTH_LLM_MODEL") or \
             _env_main or _env_light
if _env_cheap or _env_smart:
    if "llm" not in CFG:
        CFG["llm"] = {}
    if _env_cheap:
        CFG["llm"]["cheap"] = _env_cheap
    if _env_smart:
        CFG["llm"]["smart"] = _env_smart

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


# ── SAG 生产端闭环（F-3）：晋升阈值取自 .env（auto-tuner 调优），兜底 config.yaml ──
def _load_promote_threshold() -> float:
    """读取 DREAM_PROMOTE_THRESHOLD（.env > config.yaml > 0.6）。"""
    default = float(CFG.get("promote", {}).get("threshold", 0.6))
    home = os.environ.get("HERMES_HOME") or "/root/.hermes"
    envf = os.path.join(home, ".env")
    try:
        with open(envf, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() == "DREAM_PROMOTE_THRESHOLD":
                    return float(v.strip())
    except (OSError, ValueError):
        pass
    return default


def _read_latest_relevant_rate_sag() -> float | None:
    """从 daily-summary-history.jsonl 读取 kn_judge_relevant_rate_sag 的稳健聚合值。

    鲁棒化（修复 SAG 数据稀疏导致的误收紧）：
      - 早期实现只取「最近一条非 None」，但当该字段在历史上大多为 None、仅零星出现
        低值（如 0.36）时，会把单个低值当作权威信号去收紧 promote 门控，误杀本该晋升的笔记。
      - 现改为：取最近 200 行中所有非 None 值，计算其中位数；并引入「稀疏度」判定——
        若有效样本占比过低（< MIN_SAMPLE_RATIO），视为信号不可靠，返回 None（不收紧）。
      - 仅当数据足够密集且偏低时，才保留 F-3 的「消费者相关性低→收紧晋升」语义。
    """
    home = os.environ.get("HERMES_HOME") or "/root/.hermes"
    hist = os.path.join(home, "data", "flywheel", "daily-summary-history.jsonl")
    if not os.path.isfile(hist):
        return None
    try:
        with open(hist, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    window = lines[-200:]
    values: list[float] = []
    for raw in window:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        v = rec.get("kn_judge_relevant_rate_sag")
        if v is None:
            continue
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    # 稀疏度判定：有效样本占比不足 → 信号不可靠，不收紧（避免单个低值误杀）
    MIN_SAMPLE_RATIO = 0.3
    if not values:
        return None
    if len(values) < max(3, len(window) * MIN_SAMPLE_RATIO):
        return None
    # 中位数聚合：对单个离群低值不敏感
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return median


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
    payload["extra_body"] = {"thinking": {"type": "disabled"}}
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"]
    # 推理模型（s-deepseek-v4-flash）返回 reasoning 而非 content
    return msg.get("content") or msg.get("reasoning", "")


def call_llm_json(prompt: str, model: str, max_retries: int = 2) -> dict:
    """调用 LLM 并解析 JSON 输出（temperature=0，带重试）"""
    for attempt in range(max_retries + 1):
        raw = call_llm(prompt, model, max_tokens=16384, temperature=0.0)  # 16384 for s-deepseek-v4-flash reasoning model output
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


def _safe_int(value, default: int = 0) -> int:
    """安全转换为整数，失败返回默认值。"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        pass
    try:
        return int(float(value))
    except (ValueError, TypeError):
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
            if s.get("title") is None:
                s["title"] = "untitled"
            s["title"] = s["title"][:80]  # safe truncate
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
# SAG sourceId（与 knowledge-navigation 插件共用同一个源）
SAG_SOURCE_ID = "89a9a04d295c4206b35706a09ffb43e8"

# SAG Bearer token（从文件读取，避免硬编码）
_SAG_TOKEN_PATH = "/root/.hermes/.sag_token"

def _get_sag_token() -> str:
    """读取 SAG Bearer token"""
    try:
        with open(_SAG_TOKEN_PATH) as f:
            return f.read().strip()
    except IOError:
        return ""

def _sag_auth_headers() -> dict:
    """SAG 认证 headers"""
    token = _get_sag_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def sag_ingest(title: str, content: str, metadata: dict, dry_run: bool = False,
               max_retries: int = 3, base_delay: float = 5.0) -> str | None:
    """写入 SAG，返回 documentId（成功）或 None（失败）。

    Args:
        max_retries: 5xx 错误和超时的重试次数
        base_delay: 重试基础延迟（指数退避，单位秒）
    """
    if dry_run:
        return "dry-run-doc-id"

    base_url = CFG["sag"]["base_url"]
    payload = {
        "title": title,
        "text": content,
        "metadata": metadata,
        "chunking": {
            "maxTokens": 8192,
        },
    }

    session = _get_sag_session()
    last_error = ""

    for attempt in range(max_retries):
        t0 = time.time()
        try:
            resp = session.post(f"{base_url}/api/v1/sources/{SAG_SOURCE_ID}/documents/ingest",
                                json=payload, headers=_sag_auth_headers(), timeout=180)
            elapsed_ms = (time.time() - t0) * 1000
            if resp.status_code in (200, 201):
                doc_id = resp.json().get("documentId") or resp.json().get("id", "")
                return doc_id if doc_id else None
            if 500 <= resp.status_code < 600:
                last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"  SAG ingest 5xx ({resp.status_code}), {elapsed_ms:.0f}ms, 重试 {attempt+1}/{max_retries} ({delay:.1f}s)...", file=sys.stderr)
                    time.sleep(delay)
                    continue
                print(f"  SAG ingest 最终失败: {last_error}", file=sys.stderr)
                return None
            last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
            print(f"  SAG ingest failed: {last_error}", file=sys.stderr)
            return None
        except requests.exceptions.Timeout as e:
            last_error = f"Timeout: {e}"
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"  SAG ingest 超时, 重试 {attempt+1}/{max_retries} ({delay:.1f}s)...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"  SAG ingest 最终超时", file=sys.stderr)
            return None
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"  SAG ingest error: {last_error}", file=sys.stderr)
            return None
    return None


def sag_health_check(timeout: float = 5.0) -> bool:
    """SAG 服务健康检查（预热）。"""
    base_url = CFG.get("sag", {}).get("base_url", "")
    if not base_url:
        return False
    session = _get_sag_session()
    try:
        resp = session.get(f"{base_url}/api/v1/system/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        pass
    try:
        resp = session.get(f"{base_url}/", timeout=timeout)
        return resp.status_code < 500
    except Exception:
        return False


def sag_search(query: str, top_k: int = 50, source_filter: str | None = None) -> list[dict]:
    """从 SAG 搜索文档（REST API /search），返回 sections 列表。

    Args:
        source_filter: 按 metadata.source 过滤，None 返回全部
    """
    base_url = CFG["sag"]["base_url"]
    payload = {
        "query": query,
        "topK": top_k,
        "searchMode": "fast",
        "sourceIds": [SAG_SOURCE_ID],
    }
    try:
        session = _get_sag_session()
        resp = session.post(f"{base_url}/api/v1/search", json=payload, headers=_sag_auth_headers(), timeout=15)
        if resp.status_code == 200:
            sections = resp.json().get("sections", [])
            if source_filter:
                return [s for s in sections if s.get("metadata", {}).get("source") == source_filter]
            return sections
    except Exception as e:
        print(f"  SAG search error: {e}", file=sys.stderr)
    return []


def _load_cached_reflections() -> list[dict]:
    """从 verdict cache 和 SAG 读取反思笔记。

    供独立阶段（--phase patterns/promote/feishu）使用。
    优先从 verdict cache 读取，如无则从 SAG 搜索。
    """
    verdict_dir = CFG["cache"]["verdict_dir"]
    reflections = []

    if os.path.isdir(verdict_dir):
        for fname in os.listdir(verdict_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(verdict_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    cache = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
            if not cache.get("reflection_content"):
                continue
            reflections.append({
                "title": cache.get("reflection_title", ""),
                "content": cache["reflection_content"],
                "session_id": cache.get("session_id", fname.replace(".json", "")),
                "score": cache.get("score", 0),
                "document_id": cache.get("document_id", ""),
            })

    if not reflections:
        sag_sections = sag_search("dream-synth", top_k=100, source_filter="dream-synth")
        for s in sag_sections:
            reflections.append({
                "title": s.get("title", ""),
                "content": s.get("content", ""),
                "session_id": s.get("metadata", {}).get("session_id", ""),
                "score": s.get("metadata", {}).get("score", 0),
            })

    return reflections

# ── Phase 1: synthesize ──────────────────────────────
def _save_verdict(verdict_file: str, data: dict):
    os.makedirs(os.path.dirname(verdict_file), exist_ok=True)
    with open(verdict_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _should_skip_ingest(cache: dict) -> bool:
    """判断是否应该跳过 ingest 尝试。

    返回 True 如果：
    - 已成功 ingest（ingested=True）
    - 失败次数已达上限（ingest_attempts >= 3）
    """
    if cache.get("ingested"):
        return True
    if cache.get("ingest_attempts", 0) >= 3:
        return True
    return False


def phase_synthesize(sessions: list[dict], dry_run: bool = False) -> list[dict]:
    """提炼 session → 反思笔记 → 写入 SAG（带完整幂等，Producer-Consumer 并发）

    特性：
    - SAG 健康检查预热
    - LLM 合成（producer）+ SAG ingest（consumer）并发
    - 可配置并发数（sag.ingest_workers，默认 3）
    - 指数退避 + 抖动重试
    - 连接池复用（requests.Session）
    - 失败次数上限（默认 3 次后跳过）
    - 保持输入顺序输出
    - 完整指标统计
    """
    if not sessions:
        print("  synthesize: 无新 session，跳过")
        return []

    _sag_available = False if dry_run else sag_health_check()
    if not _sag_available and not dry_run:
        print("  ⚠️ SAG 服务不可达，跳过 ingest 阶段（保留 LLM 合成结果到 cache）", file=sys.stderr)

    cheap_model = CFG["llm"]["cheap"]
    smart_model = CFG["llm"]["smart"]
    sig_prompt = load_prompt("significance-filter")
    syn_prompt = load_prompt("synthesis")
    llm_throttle_s = CFG.get("llm", {}).get("throttle_seconds", 0.0)

    verdict_dir = CFG["cache"]["verdict_dir"]
    os.makedirs(verdict_dir, exist_ok=True)

    result_map: dict[str, dict] = {}
    order: list[str] = []
    future_to_sid: dict[object, str] = {}
    skipped_ingest_count = 0
    stats = IngestStats()

    max_workers = CFG.get("sag", {}).get("ingest_workers", 3)

    t0_total = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i, s in enumerate(tqdm(sessions, desc="  LLM 合成", total=len(sessions))):
            if _shutdown_event.is_set():
                print(f"\n⚠️ 已停止提交新任务，当前进度: {i}/{len(sessions)}", file=sys.stderr)
                break

            sid = s["id"]
            title = s.get("title", "untitled")
            text = s["text"]

            verdict_file = os.path.join(verdict_dir, f"{sid}.json")

            cache = None
            if os.path.exists(verdict_file):
                try:
                    with open(verdict_file, encoding="utf-8") as f:
                        cache = json.load(f)
                except (json.JSONDecodeError, IOError):
                    cache = None

            if cache and "score" in cache:
                score = cache.get("score", 0)
            else:
                prompt = sig_prompt + "\n\n对话：\n" + text[:4000]
                try:
                    verdict = call_llm_json(prompt, cheap_model)
                except Exception as e:
                    print(f"  FILTER FAIL {title[:40]}: {e}（不缓存，下次重试）")
                    if llm_throttle_s > 0:
                        time.sleep(llm_throttle_s)
                    continue
                verdict["session_id"] = sid
                cache = verdict
                if not dry_run:
                    _save_verdict(verdict_file, cache)
                score = verdict.get("score", 0)

            if score < 3:
                if llm_throttle_s > 0:
                    time.sleep(llm_throttle_s)
                continue

            if cache and cache.get("synthesized") and cache.get("reflection_content"):
                md_content = cache["reflection_content"]
                refl_title = cache.get("reflection_title", f"反思-{title[:30]}")
            else:
                prompt = syn_prompt.replace("{session_text}", text[:8000])
                try:
                    md_content = call_llm(prompt, smart_model, temperature=0.3)
                except Exception as e:
                    print(f"  SYNTH FAIL {title[:40]}: {e}", file=sys.stderr)
                    if llm_throttle_s > 0:
                        time.sleep(llm_throttle_s)
                    continue

                title_match = re.search(r"^#\s+(.+)$", md_content, re.MULTILINE)
                refl_title = title_match.group(1).strip() if title_match else f"反思-{title[:30]}"

                cache["synthesized"] = True
                cache["reflection_title"] = refl_title
                cache["reflection_content"] = md_content
                if not dry_run:
                    _save_verdict(verdict_file, cache)

            order.append(sid)

            if _should_skip_ingest(cache):
                if cache.get("ingested"):
                    result_map[sid] = {
                        "title": refl_title,
                        "content": md_content,
                        "session_id": sid,
                        "score": score,
                        "document_id": cache.get("document_id", ""),
                    }
                    stats.skipped += 1
                    stats.success += 1
                else:
                    stats.skipped += 1
                    stats.failed += 1
                skipped_ingest_count += 1
                if llm_throttle_s > 0:
                    time.sleep(llm_throttle_s)
                continue

            metadata = {
                "source": "dream-synth",
                "session_id": sid,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "score": score,
            }

            # dry_run 模式：不投 ingest，不写 ingested=True 到 cache
            if dry_run:
                result_map[sid] = {
                    "title": refl_title,
                    "content": md_content,
                    "session_id": sid,
                    "score": score,
                    "document_id": "",
                }
                stats.skipped += 1
                stats.success += 1
                if llm_throttle_s > 0:
                    time.sleep(llm_throttle_s)
                continue

            # SAG 不可达时：不投 ingest，但仍加入 result_map 供下游使用
            if not _sag_available:
                stats.skipped += 1
                result_map[sid] = {
                    "title": refl_title,
                    "content": md_content,
                    "session_id": sid,
                    "score": score,
                    "document_id": "",
                }
                if llm_throttle_s > 0:
                    time.sleep(llm_throttle_s)
                continue

            future = executor.submit(
                sag_ingest, refl_title, md_content, metadata, dry_run=dry_run
            )
            future_to_sid[future] = sid
            stats.total += 1

            result_map[sid] = {
                "refl_title": refl_title,
                "md_content": md_content,
                "score": score,
                "verdict_file": verdict_file,
                "cache": cache,
            }

            if llm_throttle_s > 0:
                time.sleep(llm_throttle_s)

        failed_sessions: list[str] = []
        for future in tqdm(as_completed(future_to_sid.keys()),
                         desc="  SAG ingest", total=len(future_to_sid)):
            sid = future_to_sid[future]
            info = result_map[sid]
            doc_id = future.result()
            if doc_id:
                info["cache"]["ingested"] = True
                info["cache"]["document_id"] = doc_id
                info["cache"].pop("ingest_attempts", None)
                info["cache"].pop("last_ingest_error", None)
                _save_verdict(info["verdict_file"], info["cache"])
                result_map[sid] = {
                    "title": info["refl_title"],
                    "content": info["md_content"],
                    "session_id": sid,
                    "score": info["score"],
                    "document_id": doc_id,
                }
                stats.success += 1
            else:
                info["cache"]["ingested"] = False
                info["cache"]["ingest_attempts"] = info["cache"].get("ingest_attempts", 0) + 1
                info["cache"]["last_ingest_error"] = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (attempt {info['cache']['ingest_attempts']})"
                _save_verdict(info["verdict_file"], info["cache"])
                failed_sessions.append(info["refl_title"])
                stats.failed += 1

        total_ms = (time.time() - t0_total) * 1000
        print(f"  📊 合成完成: 总 {stats.total + stats.skipped} 个 | 成功 {stats.success} | 失败 {stats.failed} | 跳过 {stats.skipped} | 耗时 {total_ms/1000:.1f}s")

        if failed_sessions:
            print(f"\n  ⚠️ {len(failed_sessions)} 个 session ingest 失败:", file=sys.stderr)
            for t in failed_sessions[:5]:
                print(f"    - {t[:50]}", file=sys.stderr)
            if len(failed_sessions) > 5:
                print(f"    ... 还有 {len(failed_sessions) - 5} 个", file=sys.stderr)

    reflections = [result_map[sid] for sid in order if sid in result_map and "title" in result_map[sid]]
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
    """精选反思笔记 → axiom-wiki（支持 LLM 判断并发）

    通过 llm.promote_workers 配置并发数（默认 1，即串行）。
    """
    if not reflections:
        print("  promote: 无反思笔记，跳过")
        return []

    # F-3 SAG 生产端闭环：晋升阈值来自 .env（auto-tuner 调优），消费侧质量差则收紧
    threshold = _load_promote_threshold()
    sag_rate = _read_latest_relevant_rate_sag()
    sag_str = f"{sag_rate:.2f}" if sag_rate is not None else "N/A"
    print(f"  promote 控制环: threshold={threshold:.2f}, relevant_rate_sag={sag_str}")

    smart_model = CFG["llm"]["smart"]
    prompt_tmpl = load_prompt("promote-judge")
    # 注入控制环上下文：当前晋升阈值 + 消费侧相关性，约束 LLM 的 promote/score 判定
    promote_ctx = (
        f"\n\n[晋升控制环]\n"
        f"当前晋升阈值 threshold = {threshold:.2f}（score >= threshold 才允许晋升）。\n"
        f"消费者相关性 relevant_rate_sag = {sag_str}（0~1，越低表示 SAG 内容在检索侧越不相关，"
        f"越应收紧晋升、只留高置信笔记）。\n"
        f"若你认为该笔记值得晋升，请在 score 字段给出 0~1 置信度；"
        f"promote=true 时 score 必须 >= {threshold:.2f}。"
    )
    promote_log = CFG["cache"]["promote_log"]
    max_workers = CFG.get("llm", {}).get("promote_workers", 1)
    llm_throttle_s = CFG.get("llm", {}).get("throttle_seconds", 0.0)

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
    candidates = [r for r in reflections if r["session_id"] not in promoted_ids]

    if not candidates:
        print("  promote: 全部已归档，跳过")
        return []

    t0 = time.time()

    if max_workers <= 1:
        verdicts = []
        for r in tqdm(candidates, desc="  Promote 判断", total=len(candidates)):
            prompt = prompt_tmpl + promote_ctx + "\n\n反思笔记：\n" + r["content"][:3000]
            try:
                verdict = call_llm_json(prompt, smart_model)
                verdicts.append((r, verdict))
            except Exception as e:
                print(f"  promote 判断失败: {e}", file=sys.stderr)
                verdicts.append((r, None))
            if llm_throttle_s > 0:
                time.sleep(llm_throttle_s)
    else:
        def _judge_one(r):
            prompt = prompt_tmpl + promote_ctx + "\n\n反思笔记：\n" + r["content"][:3000]
            try:
                verdict = call_llm_json(prompt, smart_model)
                return (r, verdict)
            except Exception as e:
                print(f"  promote 判断失败: {e}", file=sys.stderr)
                return (r, None)

        verdicts = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_r = {executor.submit(_judge_one, r): r for r in candidates}
            for future in tqdm(as_completed(future_to_r.keys()),
                             desc="  Promote 判断", total=len(candidates)):
                verdicts.append(future.result())

        sid_order = {r["session_id"]: i for i, r in enumerate(candidates)}
        verdicts.sort(key=lambda x: sid_order.get(x[0]["session_id"], 0))

    promoted = []
    promote_log_lines = []

    for r, verdict in verdicts:
        if verdict is None:
            continue
        # F-3 双门控：LLM 判定 promote=true 且 score 达到动态阈值才晋升
        score = verdict.get("score")
        try:
            score_f = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_f = 0.0
        if not verdict.get("promote", False):
            print(f"  PROMOTE SKIP: {r['title'][:40]} → {verdict.get('reason','')}（promote=false）")
            continue
        if score_f < threshold:
            print(f"  PROMOTE SKIP: {r['title'][:40]} → score {score_f:.2f} < 阈值 {threshold:.2f}（{verdict.get('reason','')}）")
            continue

        sid = r["session_id"]
        category = verdict.get("category", "concepts")
        safe_title = _sanitize_filename(r["title"], max_len=60)
        wiki_path = os.path.join(wiki_base, category, f"{safe_title}.md")

        if dry_run:
            print(f"  [DRY-RUN] WIKI → {category}/{r['title'][:40]}")
            promoted.append(r)
            continue

        if os.path.exists(wiki_path):
            line = json.dumps({"session_id": sid, "title": r["title"],
                               "category": category, "path": wiki_path},
                              ensure_ascii=False)
            promote_log_lines.append(line)
            promoted.append(r)
            print(f"  PROMOTE EXISTS → {category}/{r['title'][:50]}")
            continue

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

        line = json.dumps({"session_id": sid, "title": r["title"],
                           "category": category, "path": wiki_path},
                          ensure_ascii=False)
        promote_log_lines.append(line)
        promoted.append(r)
        print(f"  PROMOTE → {category}/{r['title'][:50]}")

    if promote_log_lines and not dry_run:
        os.makedirs(os.path.dirname(promote_log), exist_ok=True)
        with open(promote_log, "a", encoding="utf-8") as f:
            for line in promote_log_lines:
                f.write(line + "\n")

    # F-1 统一反馈账本：记录本次晋升控制环结果（跨循环关联 SAG 生产/消费质量）
    append_ledger_event("dream_promote", {
        "count": len(promoted),
        "candidates": len(candidates),
        "threshold": round(threshold, 2),
        "sag_rate": sag_rate,
        "dry_run": dry_run,
    })

    elapsed = time.time() - t0
    print(f"  📊 归档完成: {len(promoted)}/{len(candidates)} 晋升 | 耗时 {elapsed:.1f}s")

    return promoted

# ── Phase 4: 飞书推送 ────────────────────────────────
def _load_pushed_session_ids(feishu_log: str) -> set[str]:
    """从 feishu-log.json 读取所有已推送过的 session_id"""
    pushed: set[str] = set()
    if not os.path.exists(feishu_log):
        return pushed
    with open(feishu_log, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                sids = entry.get("session_ids", [])
                pushed.update(sids)
            except json.JSONDecodeError:
                pass
    return pushed


def _mark_verdicts_feishu_pushed(verdict_dir: str, session_ids: set[str]):
    """在 verdict cache 中标记已推送到飞书"""
    for fname in os.listdir(verdict_dir):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(verdict_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        sid = cache.get("session_id", "")
        if sid in session_ids and not cache.get("feishu_pushed"):
            cache["feishu_pushed"] = True
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            print(f"  feishu: 标记 {sid[:20]}... 已推送")


def phase_feishu(reflections: list[dict], promoted: list[dict], dry_run: bool = False,
                fresh_count: int | None = None):
    """推送 top-5 未推送反思到飞书（已推的不重复推送）

    Args:
        fresh_count: 今日新提炼的反思篇数（来自 synthesize 阶段）。None 表示独立运行阶段。
    """
    promoted_sids = {r["session_id"] for r in promoted}
    # 独立运行时从 promote-log 补充已归档 session_id
    if not promoted_sids:
        promote_log = CFG["cache"].get("promote_log", os.path.join(
            os.path.dirname(CFG["cache"]["verdict_dir"]), "promote-log.json"))
        if os.path.exists(promote_log):
            with open(promote_log, encoding="utf-8") as f:
                for line in f:
                    try:
                        promoted_sids.add(json.loads(line).get("session_id", ""))
                    except json.JSONDecodeError:
                        pass
    promoted_count = len(promoted_sids)
    # 同时排除已归档和已推送过的
    feishu_log = CFG["cache"].get("feishu_log", os.path.join(
        os.path.dirname(CFG["cache"]["verdict_dir"]), "feishu-log.json"))
    pushed_sids = _load_pushed_session_ids(feishu_log)
    skip_sids = promoted_sids | pushed_sids

    unsorted = [r for r in reflections if r["session_id"] not in skip_sids]

    if not unsorted:
        print("  feishu: 无未推送反思，跳过")
        return

    # 按 score 排序取 top-5
    feishu_top_n = CFG.get("feishu", {}).get("top_n", 5)
    unsorted.sort(key=lambda r: r.get("score", 0), reverse=True)
    top5 = unsorted[:feishu_top_n]

    # 格式化飞书消息
    lines = [f"# 🌙 梦境流水线 — {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    total_count = len(reflections)
    if fresh_count and fresh_count > 0:
        lines.append(f"今日提炼 **{fresh_count}** 篇新反思（累计 **{total_count}** 篇），归档 **{promoted_count}** 篇，剩余未推送 **{len(unsorted)}** 篇")
    else:
        lines.append(f"累计 **{total_count}** 篇反思，归档 **{promoted_count}** 篇，剩余未推送 **{len(unsorted)}** 篇")
    lines.append("")
    lines.append("## Top-5 未推送反思")
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
    lines.append(f"_已推送 {len(pushed_sids)} 篇，仍有 {len(unsorted) - len(top5)} 篇待推送_")
    lines.append("_如需归档，告诉 axiom 即可_")

    msg = "\n".join(lines)

    if dry_run:
        print(f"\n  [DRY-RUN] 飞书消息:\n{msg[:500]}")
        return

    # 推送飞书
    chat_id = os.environ.get("FEISHU_CHAT_ID") or CFG.get("feishu", {}).get("chat_id", "")
    if not chat_id:
        print("  feishu: 未配置 FEISHU_CHAT_ID，跳过推送")
        return
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
            # 记录本次推送的 session_ids 到幂等日志
            pushed_sids_this_run = {r["session_id"] for r in top5}
            os.makedirs(os.path.dirname(feishu_log), exist_ok=True)
            with open(feishu_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "time": datetime.now().strftime("%H:%M"),
                    "count": len(top5),
                    "session_ids": list(pushed_sids_this_run),
                    "titles": [r["title"][:50] for r in top5],
                }, ensure_ascii=False) + "\n")
            # 更新 verdict cache 标记
            verdict_dir = CFG["cache"]["verdict_dir"]
            _mark_verdicts_feishu_pushed(verdict_dir, pushed_sids_this_run)
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
        # 独立运行时从 verdict cache 恢复反思笔记（不依赖 SAG 搜索）
        if not reflections:
            reflections = _load_cached_reflections()
            print(f"  从 cache 加载 {len(reflections)} 篇反思笔记")
        phase_patterns(reflections, dry_run=args.dry_run)

    # Phase 3: promote
    promoted = []
    if not args.phase or args.phase == "promote":
        print("\n── Phase 3: promote ──")
        # 独立运行时从 verdict cache 恢复反思笔记
        if not reflections:
            reflections = _load_cached_reflections()
            print(f"  从 cache 加载 {len(reflections)} 篇反思笔记")
        promoted = phase_promote(reflections, dry_run=args.dry_run)
        print(f"  归档 {len(promoted)} 篇到 Wiki")

    # Phase 4: feishu
    if not args.phase or args.phase == "feishu":
        print("\n── Phase 4: feishu push ──")
        # 独立运行时从 verdict cache 恢复反思笔记
        feishu_fresh = None
        if not reflections:
            reflections = _load_cached_reflections()
            print(f"  从 cache 加载 {len(reflections)} 篇反思笔记")
        else:
            # 完整流水线：reflections 是本轮 synthesize 的新反思
            fresh_synth_count = len(reflections)
            if fresh_synth_count > 0:
                feishu_fresh = fresh_synth_count
        phase_feishu(reflections, promoted, dry_run=args.dry_run, fresh_count=feishu_fresh)

    # 更新时间戳：仅在完整流水线或 synthesize 阶段运行后更新
    # 单独运行 patterns/promote/feishu 时不更新，避免跳过未处理的 session
    if not args.dry_run and (not args.phase or args.phase == "synthesize"):
        save_last_run_ts(now_ts)


if __name__ == "__main__":
    main()