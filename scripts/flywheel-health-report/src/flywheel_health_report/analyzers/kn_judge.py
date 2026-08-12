"""kn_judge.py — 知识导航 LLM Judge 质量评估（健康巡检内集成，mask 级）。

封装 collect_baseline.run_judge / collect_all_recalls / _judge_one / bootstrap_ci，
支持：
- since/until 窗口过滤（与飞轮报告数据窗口一致）
- 最小样本量检查（防止小样本噪声）
- 环境变量 / KN_JUDGE_CFG 驱动 LLM 配置
- 失败兜底：用 kn_avg_score + kept 粗估（避免反馈断裂）

v2（mask 级改造 2026-08-10）：
- 单次 LLM 调用返回结构化评分 {overall, hindsight, knowledge_tree, sag}，
  各召回来源单独打分（recalled_summaries 每条已带规范 source）。
- 聚合产出 per-mask 的 relevant_rate / avg_relevance / sample_count，
  供 Auto-Tuner 做「参数 → 其影响的那一路质量」的因果绑定。
- 采样改用滚动窗口（mask_window_days，默认 30 天），解决每日窗口样本不足
  导致 kn_judge 长期为 None 的根因；全局键保留以兼容旧绑定。
"""

from __future__ import annotations

import json
import math
import os
import re
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import KN_JUDGE_CFG

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

# --------------------------------------------------------------------
# collect_baseline 的 run_judge / collect_all_recalls / _judge_one 导入
# （优先 import 安装在 /root/.hermes 下的脚本，否则走 fallback）
# --------------------------------------------------------------------

_COLLECT_BASELINE_IMPORTED = False
_collect_all_recalls = None
_judge_one_legacy = None
_bootstrap_ci = None


def _ensure_collect_baseline_imported(home: Path) -> bool:
    """延迟 import，避免脚本路径在测试环境下不存在时 import 崩溃。"""
    global _COLLECT_BASELINE_IMPORTED, _collect_all_recalls, _judge_one_legacy, _bootstrap_ci
    if _COLLECT_BASELINE_IMPORTED:
        return _collect_all_recalls is not None

    _COLLECT_BASELINE_IMPORTED = True

    candidates: list[str] = [
        str(home / "plugins" / "knowledge-navigation" / "scripts"),
        str(home / "plugins" / "knowledge-navigation" / "src"),
        os.path.join(os.path.dirname(__file__),
                     "..", "..", "..", "..", "..", "plugins",
                     "knowledge-navigation", "scripts"),
    ]
    for p in candidates:
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from collect_baseline import (  # type: ignore[import-not-found]
            collect_all_recalls,
            _judge_one as judge_one_legacy,
            bootstrap_ci,
        )
        _collect_all_recalls = collect_all_recalls
        _judge_one_legacy = judge_one_legacy
        _bootstrap_ci = bootstrap_ci
    except Exception as e:
        sys.stderr.write(f"[kn_judge] collect_baseline import 失败: {type(e).__name__}: {e}\n")
        return False
    return True


def _load_env_file(home: Path) -> None:
    """读取 ~/.hermes/.env，让 LLM API 配置对 judge 子进程可用。"""
    env_file = home / ".env"
    if not env_file.is_file():
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def _load_llm_config(home: Path) -> dict[str, str] | None:
    """构造 judge 的 LLM 配置。优先级：显式 env > .env > 空。"""
    _load_env_file(home)
    url = (
        os.environ.get("LLM_API_URL")
        or os.environ.get("LITELLM_API_URL")
        or "http://127.0.0.1:4142/v1/chat/completions"
    )
    key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("LITELLM_MASTER_KEY")
        or ""
    )
    model = os.environ.get("LLM_MODEL", "s-deepseek-v4-flash")
    if not url or not key:
        sys.stderr.write(
            "[kn_judge] LLM_API_URL/KEY 未配置，无法启动 judge\n"
        )
        return None
    return {"url": url, "key": key, "model": model}


# mask 顺序（注意 key 命名与反馈键后缀一致：h/kt/sag）
_MASK_KEYS = ("hindsight", "knowledge_tree", "sag")
_MASK_SHORT = {"hindsight": "h", "knowledge_tree": "kt", "sag": "sag"}


# --------------------------------------------------------------------
# 单条 recall 的结构化 judge
# --------------------------------------------------------------------

def _format_summaries_by_source(rec: dict) -> str:
    """把 recalled_summaries 按来源分组，拼成可读文本供 judge 逐路评分。"""
    groups: dict[str, list[str]] = {k: [] for k in _MASK_KEYS}
    sags = rec.get("recalled_summaries", []) or []
    label_map = {
        "hindsight": "hindsight（历史回溯笔记）",
        "knowledge_tree": "knowledge_tree（知识树节点）",
        "sag": "sag（会话摘要）",
        "skill": "skill（技能）",
    }
    unnamed = []
    for idx, s in enumerate(sags[:8]):
        if not isinstance(s, dict):
            continue
        src = s.get("source") or "hindsight"
        title = str(s.get("title") or s.get("name") or "")[:80]
        body = str(s.get("text") or s.get("content") or s.get("body") or "")[:150]
        line = f"  [{idx+1}] {title}".rstrip()
        if body:
            line += f" | {body}"
        if src in groups:
            groups[src].append(line)
        else:
            unnamed.append(line)
    parts = []
    for k in _MASK_KEYS:
        items = groups[k]
        if not items:
            continue
        parts.append(f"· {label_map.get(k, k)}:\n" + "\n".join(items))
    if unnamed:
        parts.append("· 其他来源:\n" + "\n".join(unnamed))
    return "\n\n".join(parts) if parts else "（本次召回无内容摘要）"


def _judge_one_masked(rec: dict, config: dict | None) -> tuple[dict | None, Any]:
    """单条 recall 的 mask 级 LLM relevance 评分。

    返回 (score_dict, True) 或 (None, error)。
    score_dict = {
        "overall": float,
        "hindsight": float | None,
        "knowledge_tree": float | None,
        "sag": float | None,
    }
    某来源无召回条目时对应值为 None。
    """
    query = rec.get("query_trunc") or "(空查询)"
    kept = rec.get("kept_results", 0)
    total = rec.get("total_results", 0)
    excluded = rec.get("excluded_marked", 0)
    avg_s = rec.get("avg_score", 0)
    injected = rec.get("injected_count", 0)
    hs_kept = int(rec.get("hs_kept") or 0)
    kt_kept = int(rec.get("kt_kept") or 0)
    sag_kept = int(rec.get("sag_kept") or 0)
    summary_block = _format_summaries_by_source(rec)

    prompt = f"""你是一个 RAG 检索质量评估员。请判断「系统检索到的材料」与「用户查询」的相关程度（不是判断材料数量多少）。

【重要评估原则】
1. 召回条数少不代表质量差——系统有分数阈值和去重机制，会主动过滤低相关内容。只召回 1-2 条但 rerank 分数高，往往比召回 10 条低质内容更有价值。
2. 召回条数=0：可能是用户查询不在知识库（评 0），也可能被阈值截断（按查询语义判断）。
3. 评估核心：查询意图与召回条目主题是否匹配；rerank 分数 >0.7 高相关、0.4~0.7 中等、<0.4 低相关。
4. 评分基准：0.0 完全无关，0.3 部分相关，0.5 中等相关，0.7 比较相关，1.0 完全相关。
5. 逐路评估：请分别评估 hindsight / knowledge_tree / sag 三路召回各自与查询的相关度；若某路无召回条目，该路填 null。

【用户查询】
{query}

【检索统计】
- 初始检索总数：{total} 条
- 被过滤/排除的低相关条目：{excluded} 条
- 最终保留条数（kept）：{kept} 条
- Rerank 平均分数：{avg_s:.4f}

【各路保留情况】（hindsight=历史记忆检索, knowledge_tree=知识树, sag=结构化知识注入）
- hindsight 保留：{hs_kept} 条
- knowledge_tree 保留：{kt_kept} 条
- sag 保留：{sag_kept} 条
（若某路保留为 0，则该路相关性评估为 null）
- 注入 LLM 上下文字符数：{injected} chunks

{summary_block}

请只输出一个严格 JSON 对象（不要任何额外文字、不要代码块标记）：
{{"overall": <0~1 浮点>, "hindsight": <0~1 或 null>, "knowledge_tree": <0~1 或 null>, "sag": <0~1 或 null>}}"""

    headers = {"Content-Type": "application/json"}
    if config.get("key"):
        headers["Authorization"] = f"Bearer {config['key']}"
    body = json.dumps({
        "model": config.get("model", "s-deepseek-v4-flash"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 16384,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    ctx = ssl.create_default_context()
    if os.environ.get("JUDGE_INSECURE", "").lower() in ("1", "true", "yes"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(config["url"], data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            resp_text = resp.read().decode("utf-8")
            resp_data = json.loads(resp_text)
            msg = resp_data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if not content:
                reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
                for line in reversed(reasoning.splitlines()):
                    m = re.search(r"[01](?:\.\d+)?", line.strip().rstrip('.'))
                    if m:
                        content = m.group()
                        break
                if not content:
                    return None, RuntimeError(
                        f"empty content, finish_reason={resp_data['choices'][0].get('finish_reason')}")
            return _parse_judge_response(content), True
    except Exception as e:  # noqa: BLE001
        return None, e


def _parse_judge_response(content: str) -> dict[str, Any] | None:
    """解析 LLM 返回的 judge 评分。

    优先解析 JSON 对象；失败则退回单个 0~1 数字作为 overall（其余 mask 为 None）。
    """
    text = content.strip()
    # 去可能的代码块标记
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            def _clamp(v):
                if v is None:
                    return None
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    return None
                return max(0.0, min(1.0, f))
            result = {
                "overall": _clamp(obj.get("overall")),
                "hindsight": _clamp(obj.get("hindsight")),
                "knowledge_tree": _clamp(obj.get("knowledge_tree")),
                "sag": _clamp(obj.get("sag")),
            }
            if result["overall"] is None:
                # 退化：尝试从任意数值字段取 overall
                for k in ("overall", "score", "relevance"):
                    if _clamp(obj.get(k)) is not None:
                        result["overall"] = _clamp(obj.get(k))
                        break
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    # 退回：单个数字
    m = re.search(r"[01](?:\.\d+)?", text)
    if m:
        try:
            f = max(0.0, min(1.0, float(m.group())))
            return {"overall": f, "hindsight": None, "knowledge_tree": None, "sag": None}
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------
# Judge 执行主函数
# --------------------------------------------------------------------

def run_judge_within_window(
    home: Path,
    since_iso: str,
    until_iso: str = "",
    *,
    force_full_eval: bool = False,
) -> dict[str, Any]:
    """在滚动窗口内执行 mask 级 judge。

    采样窗口：取 max(给定 since, now - mask_window_days) 作为下界，
    保证各路有足够样本（解决每日窗口样本不足 → kn_judge 长期 None 的根因）。

    Returns:
        可直接合并进 daily summary 的 dict：
        全局：kn_judge_relevant_rate / avg_relevance / sample_count / ci_* / fallback / error
        mask 级：kn_judge_relevant_rate_{h,kt,sag} / avg_relevance_{h,kt,sag} / sample_count_{h,kt,sag}
    """
    if not KN_JUDGE_CFG.get("enabled"):
        return {"kn_judge_error": "disabled_by_config"}

    cfg = KN_JUDGE_CFG
    sample_size = int(cfg.get("sample_size", 200))
    min_sample = int(cfg.get("min_sample", 20))
    mask_days = int(cfg.get("mask_window_days", 30))
    mask_min_sample = int(cfg.get("mask_min_sample", 12))
    parallel = int(cfg.get("parallel", 5))
    max_wt = float(cfg.get("max_walltime_sec", 3600))
    fallback_ok = bool(cfg.get("fallback_on_fail", True))

    ok_import = _ensure_collect_baseline_imported(home)
    if not ok_import or _collect_all_recalls is None:
        return {"kn_judge_error": "collect_baseline_import_failed"}

    trace_path = home / "plugins" / "knowledge-navigation" / "trace.log"
    if not trace_path.is_file():
        return {"kn_judge_error": "trace_log_missing"}

    # 滚动窗口：下界 = now - mask_window_days；上界 = now。
    # report.py 传入的 since/until 只是「报告所属 CN 自然日」标签，绝不能用来收窄采样窗口——
    # 否则会把「今天至今」产生的 recall 全部截断（until = CN 今日 00:00 ≈ 8h 前），样本归零，
    # 这正是 kn_judge 长期为 None / sample_count 长期偏低（旧日志仅 8）的旧根因。
    # 因此 judge 一律在 [now - mask_window_days, now] 上聚合，与报告的「日切窗」彻底解耦。
    now = datetime.now(timezone.utc)
    mask_since_dt = now - timedelta(days=mask_days)
    floor_iso = mask_since_dt.strftime("%Y-%m-%dT%H:%M:%S")
    until_iso_eff = now.strftime("%Y-%m-%dT%H:%M:%S")

    all_rec = _collect_all_recalls(str(trace_path))
    windowed = [
        r for r in all_rec
        if r.get("timestamp", "") >= floor_iso
        and r.get("timestamp", "") < until_iso_eff
    ]

    total_windowed = len(windowed)
    if total_windowed < mask_min_sample and not force_full_eval:
        return {
            "kn_judge_sample_count": total_windowed,
            "kn_judge_error": f"sample_below_min:{total_windowed}<{mask_min_sample}",
        }

    llm_cfg = _load_llm_config(home)
    if llm_cfg is None:
        if fallback_ok:
            fb = _kn_judge_fallback(all_rec, floor_iso, until_iso_eff)
            fb["kn_judge_error"] = "llm_config_missing"
            return fb
        return {"kn_judge_sample_count": total_windowed, "kn_judge_error": "llm_config_missing"}

    sample = windowed[-sample_size:] if len(windowed) >= sample_size else windowed

    _old_judge_insecure = os.environ.get("JUDGE_INSECURE")
    _old_judge_parallel = os.environ.get("JUDGE_PARALLEL")
    os.environ["JUDGE_INSECURE"] = "1"
    os.environ["JUDGE_PARALLEL"] = str(parallel)

    results: list[dict] = []
    try:
        start = time.monotonic()
        judged = 0
        errors = 0
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(_judge_one_masked, rec, llm_cfg): i for i, rec in enumerate(sample)}
            for future in as_completed(futures):
                if time.monotonic() - start > max_wt:
                    break
                try:
                    result = future.result(timeout=30)
                except Exception:  # noqa: BLE001
                    errors += 1
                    continue
                if result is None or result[0] is None:
                    errors += 1
                    continue
                score_dict, _ = result
                results.append(score_dict)
                judged += 1
    finally:
        if _old_judge_insecure is not None:
            os.environ["JUDGE_INSECURE"] = _old_judge_insecure
        else:
            os.environ.pop("JUDGE_INSECURE", None)
        if _old_judge_parallel is not None:
            os.environ["JUDGE_PARALLEL"] = _old_judge_parallel
        else:
            os.environ.pop("JUDGE_PARALLEL", None)

    if judged < min(min_sample // 2, 20):
        if fallback_ok:
            fb = _kn_judge_fallback(all_rec, floor_iso, until_iso_eff)
            fb["kn_judge_error"] = f"too_few_judged:{judged}"
            return fb
        return {
            "kn_judge_sample_count": judged,
            "kn_judge_error": f"too_few_judged:{judged}",
        }

    # ---- 聚合 ----
    def _agg(scores: list[float]):
        if not scores:
            return 0.0, 0.0, (0.0, 0.0, 0.0)
        avg = sum(scores) / len(scores)
        rel = sum(1 for s in scores if s >= 0.5) / len(scores)
        ci = _bootstrap_ci(scores) if _bootstrap_ci and len(scores) > 1 else (avg, avg, avg)
        return round(rel, 4), round(avg, 4), ci

    overall_scores = [r["overall"] for r in results if r.get("overall") is not None]
    mask_scores: dict[str, list[float]] = {k: [] for k in _MASK_KEYS}
    for r in results:
        for k in _MASK_KEYS:
            if r.get(k) is not None:
                mask_scores[k].append(r[k])

    rel_o, avg_o, ci_o = _agg(overall_scores)

    out: dict[str, Any] = {
        "kn_judge_sample_count": judged,
        "kn_judge_relevant_rate": rel_o,
        "kn_judge_avg_relevance": avg_o,
        "kn_judge_ci_lo": round(ci_o[1], 4),
        "kn_judge_ci_hi": round(ci_o[2], 4),
        "kn_judge_fallback": False,
    }
    for k in _MASK_KEYS:
        short = _MASK_SHORT[k]
        cnt = len(mask_scores[k])
        out[f"kn_judge_sample_count_{short}"] = cnt
        if cnt >= mask_min_sample:
            rel_m, avg_m, _ = _agg(mask_scores[k])
            out[f"kn_judge_relevant_rate_{short}"] = rel_m
            out[f"kn_judge_avg_relevance_{short}"] = avg_m
        else:
            # 样本不足：只写计数，不虚构 rate/avg（tuner 按 count 拒绝，显示侧显 N/A）
            out[f"kn_judge_relevant_rate_{short}"] = None
            out[f"kn_judge_avg_relevance_{short}"] = None
    # F-1 统一反馈账本：记录各路 mask 指标，供跨循环关联（"改写后 neg 是否真的降"等）
    _mask_rates = {k: out.get(f"kn_judge_relevant_rate_{k}") for k in ("h", "kt", "sag")}
    if any(v is not None for v in _mask_rates.values()):
        append_ledger_event("kn_judge", {
            "relevant_rate_h": _mask_rates["h"],
            "relevant_rate_kt": _mask_rates["kt"],
            "relevant_rate_sag": _mask_rates["sag"],
            "sample_count_h": out.get("kn_judge_sample_count_h"),
            "sample_count_kt": out.get("kn_judge_sample_count_kt"),
            "sample_count_sag": out.get("kn_judge_sample_count_sag"),
        })
    return out


# --------------------------------------------------------------------
# 兜底：judge 跑不起来时，用 kn_avg_score + kept 粗估，防止反馈断裂
# --------------------------------------------------------------------

def _kn_judge_fallback(all_records: list[dict], since: str, until: str) -> dict[str, Any]:
    """用 kept + avg_score 粗估 judge 评分（含 mask 级）。"""
    if not all_records:
        return {
            "kn_judge_sample_count": 0, "kn_judge_relevant_rate": 0,
            "kn_judge_avg_relevance": 0, "kn_judge_fallback": True,
            "kn_judge_sample_count_h": 0, "kn_judge_relevant_rate_h": 0.0,
            "kn_judge_avg_relevance_h": 0.0,
            "kn_judge_sample_count_kt": 0, "kn_judge_relevant_rate_kt": 0.0,
            "kn_judge_avg_relevance_kt": 0.0,
            "kn_judge_sample_count_sag": 0, "kn_judge_relevant_rate_sag": 0.0,
            "kn_judge_avg_relevance_sag": 0.0,
        }
    windowed = [
        r for r in all_records
        if r.get("timestamp", "") >= since
        and (not until or r.get("timestamp", "") < until)
    ]
    windowed = windowed[-200:] or all_records[-200:]
    if not windowed:
        return _kn_judge_fallback([], since, until)
    scores = [float(r.get("avg_score") or 0) for r in windowed]
    kepts = [int(r.get("kept_results") or 0) for r in windowed]
    avg_scr = sum(scores) / len(scores) if scores else 0
    clipped = max(0.40, min(0.65, avg_scr))
    est_avg = 0.40 + (clipped - 0.40) / 0.25 * 0.35
    non_empty_ratio = sum(1 for k in kepts if k > 0) / len(kepts) if kepts else 0
    est_rel_rate = max(0.1, min(0.95, est_avg * (0.85 + 0.3 * non_empty_ratio)))

    # mask 级：仅当该路在窗口内有命中时才给估计，否则 None（样本不足不虚构）
    def _mask_present(field: str) -> bool:
        return any(int(r.get(field, 0) > 0) for r in windowed)

    out: dict[str, Any] = {
        "kn_judge_sample_count": len(windowed),
        "kn_judge_relevant_rate": round(est_rel_rate, 4),
        "kn_judge_avg_relevance": round(est_avg, 4),
        "kn_judge_fallback": True,
    }
    mask_map = [("h", "hs_kept"), ("kt", "kt_kept"), ("sag", "sag_kept")]
    for short, field in mask_map:
        present = _mask_present(field)
        out[f"kn_judge_sample_count_{short}"] = sum(1 for r in windowed if int(r.get(field, 0) > 0))
        out[f"kn_judge_relevant_rate_{short}"] = round(est_rel_rate, 4) if present else 0.0
        out[f"kn_judge_avg_relevance_{short}"] = round(est_avg, 4) if present else 0.0
    return out


# --------------------------------------------------------------------
# CLI 入口用的简单 judge（collect_baseline --judge 调用）
# --------------------------------------------------------------------

def run_judge(log_file: str, config: dict | None = None) -> dict[str, Any]:
    """用 LLM 评估所有 recall 的 relevance（mask 级，CLI 用）。"""
    records = _collect_all_recalls(log_file) if _collect_all_recalls else []
    if not records:
        print("⚠️  未找到 recall_success 记录。")
        return {}
    print(f"📡 评估 {len(records)} 次 recall 的 relevance（并行 {KN_JUDGE_CFG.get('parallel', 5)} 路）...", file=sys.stderr)
    sample = records[-min(200, len(records)):]
    if not config:
        return {}

    overall: list[float] = []
    mask_scores: dict[str, list[float]] = {k: [] for k in _MASK_KEYS}
    judged = 0
    with ThreadPoolExecutor(max_workers=KN_JUDGE_CFG.get("parallel", 5)) as pool:
        futures = {pool.submit(_judge_one_masked, rec, config): i for i, rec in enumerate(sample)}
        for future in as_completed(futures):
            result = future.result()
            if result is None or result[0] is None:
                continue
            sd = result[0]
            if sd.get("overall") is not None:
                overall.append(sd["overall"])
            for k in _MASK_KEYS:
                if sd.get(k) is not None:
                    mask_scores[k].append(sd[k])
            judged += 1
            if judged % 20 == 0:
                print(f"   已评 {judged}/{len(sample)} 条...", file=sys.stderr)

    def _r(scores):
        return round(sum(scores) / len(scores), 4) if scores else 0.0

    return {
        "total_records": len(records),
        "judged": judged,
        "relevant_rate": _r([s for s in overall if s >= 0.5]),
        "avg_relevance": _r(overall),
        "relevant_rate_h": _r([s for s in mask_scores["hindsight"] if s >= 0.5]),
        "avg_relevance_h": _r(mask_scores["hindsight"]),
        "relevant_rate_kt": _r([s for s in mask_scores["knowledge_tree"] if s >= 0.5]),
        "avg_relevance_kt": _r(mask_scores["knowledge_tree"]),
        "relevant_rate_sag": _r([s for s in mask_scores["sag"] if s >= 0.5]),
        "avg_relevance_sag": _r(mask_scores["sag"]),
    }


# --------------------------------------------------------------------
# 参数优化现状分析：读取 auto-tuner-log.jsonl 生成摘要，供健康巡检报告
# --------------------------------------------------------------------

def summarize_param_tuning(log_file: Path, state_file: Path) -> dict[str, Any]:
    """读取 auto-tuner 日志 + state，输出参数优化轨迹摘要（与其他参数优化一致）。

    返回结构：
    {
      "params": { "KN_MIN_SCORE": { "current", "initial", "history": [...],
                                     "locked"/"suspended"/"converged", "improvement" },
                   "sag_max_inject": {...} },
      "last_tune": { ... },
      "any_pending_restart": bool,
      "history_count": int,
    }
    """
    from ..config import PARAM_DEFS, STATE_FILE, LOG_FILE, HERMES_HOME  # local

    if not str(log_file):
        log_file = Path(LOG_FILE)
    if not str(state_file):
        state_file = Path(STATE_FILE)

    state: dict[str, Any] = {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    per_param_history: dict[str, list[dict]] = {}
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = rec.get("parameter", "")
                if not p or rec.get("dry_run"):
                    continue
                per_param_history.setdefault(p, []).append(rec)
    except FileNotFoundError:
        pass

    env_file = Path(HERMES_HOME) / ".env"
    env_map: dict[str, str] = {}
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                env_map[k.strip()] = v.strip()
    except FileNotFoundError:
        pass

    params: dict[str, Any] = {}
    any_pending = False
    for pdef in PARAM_DEFS:
        name, default, pmin, pmax, step, _ = pdef
        pst = state.get(name) or {}

        current = None
        if name in env_map:
            try:
                current = float(env_map[name])
            except (TypeError, ValueError):
                current = None
        if current is None and name in per_param_history:
            last = per_param_history[name][-1]
            if last.get("status", "") == "applied":
                current = float(last.get("new_value") or default)
        if current is None:
            current = float(default)

        history: list[dict] = []
        for rec in per_param_history.get(name, [])[-5:]:
            history.append({
                "date": rec.get("date", ""),
                "old": rec.get("old_value"),
                "new": rec.get("new_value"),
                "direction": rec.get("direction", ""),
                "status": rec.get("status", ""),
                "reason": rec.get("reason", "")[:60],
            })

        initial = pst.get("initial_value")
        try:
            improvement: float | None = (
                round(current - float(initial), 4)
                if initial is not None else None
            )
        except (TypeError, ValueError):
            improvement = None

        locked = bool(pst.get("locked"))
        suspended = bool(pst.get("suspended"))
        converged = locked or suspended or (
            int(pst.get("no_change_count", 0)) >= 3
        )

        last_status = ""
        if per_param_history.get(name):
            last_status = per_param_history[name][-1].get("status", "")
            if last_status == "pending_restart":
                any_pending = True

        params[name] = {
            "current": round(current, 4),
            "default": float(default),
            "range": [float(pmin), float(pmax)],
            "step": float(step),
            "initial_value": initial,
            "improvement_since_initial": improvement,
            "history": history,
            "locked": locked,
            "suspended": suspended,
            "converged": converged,
            "last_status": last_status,
            "state_counts": {
                "no_change_count": int(pst.get("no_change_count", 0)),
                "degradation_count": int(pst.get("degradation_count", 0)),
                "consecutive_degradation_count": int(pst.get("consecutive_degradation_count", 0)),
            },
        }

    total_hist = sum(len(v) for v in per_param_history.values())

    last_tune: dict | None = None
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("dry_run"):
                    continue
                last_tune = rec
    except FileNotFoundError:
        pass

    return {
        "params": params,
        "any_pending_restart": any_pending,
        "history_count": total_hist,
        "last_tune": {
            "date": last_tune.get("date", "") if last_tune else "",
            "parameter": last_tune.get("parameter", "") if last_tune else "",
            "old": last_tune.get("old_value") if last_tune else None,
            "new": last_tune.get("new_value") if last_tune else None,
            "status": last_tune.get("status", "") if last_tune else "",
        } if last_tune else {},
    }
