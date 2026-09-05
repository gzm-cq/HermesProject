"""validate_cache_hitrate.py — 意图联合键缓存命中率验证（接入生产前）。

目标：回答"接入缓存后，真实使用中能省多少 LLM 精排调用"。
本次重写（2026-09-04 第二轮）：
1. 正确分离 ctx/query 两个维度（上次脚本 bug：store 用 query-only、lookup 用带 ctx 键 → 维度不匹配必然 0%）
2. 输出真实余弦相似度分布——验证 0.92 阈值对"同意图改写表达"是否合理
3. 区分三类场景：完全相同 / 同意图改写 / 不同意图

用法：python3 scripts/validate_cache_hitrate.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from knowledge_navigation.core.skill_match_cache import SkillMatchCache  # noqa: E402

EMBED_MODEL = os.environ.get("KN_SKILL_EMBEDDING_MODEL", "BAAI/bge-m3")
EMBED_URL = os.environ.get("KN_SKILL_EMBEDDING_URL", "http://127.0.0.1:8082/v1")
EMBED_API_KEY = os.environ.get("KN_SKILL_EMBEDDING_API_KEY", "")

# ── 同一会话的稳定上下文（用户进入"Excel 分析"任务后，后续轮次共享的 goal 上下文）──
CTX_EXCEL = "user: 帮我分析这份 Excel 销售报表的季度趋势和利润率\nassistant: 好的，我已读取报表，开始分析\n"
CTX_CODE = "user: 审查一下这段 Python 代码的安全性\nassistant: 正在审查代码\n"
CTX_SYS = "user: 检查系统健康状态\nassistant: 开始巡检\n"
CTX_DOC = "user: 把讨论整理成报告\nassistant: 正在整理\n"
CTX_DB = "user: 查询数据库表数据\nassistant: 正在连接数据库\n"
CTX_CACHE = "user: 技能缓存命中问题\nassistant: 正在分析缓存\n"
CTX_MODEL = "user: 配置智谱模型接入\nassistant: 正在查看配置\n"
CTX_DEPLOY = "user: 部署服务到生产\nassistant: 部署流程已确认\n"
CTX_LOG = "user: 分析 gateway 日志错误\nassistant: 正在读取日志\n"
CTX_PERF = "user: 系统响应变慢\nassistant: 正在做性能分析\n"
CTX_FEISHU = "user: 查飞书日历\nassistant: 正在查询日历\n"
CTX_KNOW = "user: SAG 搜索原理\nassistant: 正在检索知识库\n"

# (意图, 上下文, 表达1, 表达2)
INTENT_PAIRS = [
    ("Excel数据分析", CTX_EXCEL, "帮我分析这份Excel销售报表的季度趋势", "接着分析利润率变化"),
    ("代码审查", CTX_CODE, "审查一下这段代码有没有安全问题", "检查这个函数的注入风险"),
    ("系统健康检查", CTX_SYS, "检查一下系统当前的健康状态", "看看所有服务是否正常"),
    ("文档生成", CTX_DOC, "把这次讨论整理成一份报告", "生成会议纪要文档"),
    ("数据库操作", CTX_DB, "查询一下数据库里这个表的数据", "查一下PG里memory_units的统计"),
    ("缓存排障", CTX_CACHE, "技能缓存命不中怎么办", "skill缓存一直miss怎么排查"),
    ("模型配置", CTX_MODEL, "怎么配置智谱的模型接入", "GLM模型的Bifrost接入参数"),
    ("部署运维", CTX_DEPLOY, "把这个服务部署到生产环境", "上线新版本到服务器"),
    ("日志分析", CTX_LOG, "看看gateway日志里有什么错误", "分析systemd日志的报错"),
    ("性能优化", CTX_PERF, "系统响应变慢了怎么优化", "首字延迟高如何调优"),
    ("飞书操作", CTX_FEISHU, "帮我查一下飞书日历的日程", "看看我明天有什么会议"),
    ("知识查询", CTX_KNOW, "SAG搜索的原理是什么", "解释一下SAG的多跳检索"),
]

# 不同意图（ctx 不同且 query 不同 → 不应命中）
DIFFERENT_INTENTS = [
    (CTX_EXCEL, "帮我分析这份Excel销售报表的季度趋势", CTX_CODE, "审查这段Python代码的依赖注入"),
    (CTX_SYS, "检查一下系统当前的健康状态", CTX_DOC, "生成一份项目周报文档"),
]


def embed(text: str) -> np.ndarray:
    resp = httpx.post(
        f"{EMBED_URL.rstrip('/')}/embeddings",
        json={"model": EMBED_MODEL, "input": text[:1000]},
        headers={"Authorization": f"Bearer {EMBED_API_KEY}"} if EMBED_API_KEY else {},
        timeout=10,
    )
    resp.raise_for_status()
    return np.array(resp.json()["data"][0]["embedding"], dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


def main() -> int:
    cache = SkillMatchCache(cache_path="/tmp/validate_skill_cache.json")
    results: list[dict] = []

    print("=" * 70)
    print("意图联合键缓存 — 命中率验证（第二轮：正确维度 + 相似度分布）")
    print("=" * 70)
    print(f"{'意图':<10} {'query余弦':>9} {'ctx余弦':>9} {'命中':>4}")
    print("-" * 70)

    # Phase 1: 同一 ctx，表达1 store → 表达2 lookup
    hits = 0
    for intent, ctx_text, expr1, expr2 in INTENT_PAIRS:
        ctx_emb = embed(ctx_text)
        q1 = embed(expr1)
        q2 = embed(expr2)
        cache.store(ctx_emb, q1, [f"skill-{intent}"])
        result = cache.lookup(ctx_emb, q2)  # 同一 ctx，不同 query 表达
        hit = result is not None
        hits += 1 if hit else 0
        results.append({
            "intent": intent, "hit": hit,
            "q_sim": round(cosine(q1, q2), 3), "ctx_sim": round(cosine(ctx_emb, ctx_emb), 3),
        })
        mark = "✅" if hit else "❌"
        print(f"{intent:<10} {cosine(q1, q2):>9.3f} {1.000:>9.3f} {mark:>4}")

    # Phase 2: 不同意图（ctx 不同）→ 不应命中
    false_hits = 0
    for ctx1, expr1, ctx2, expr2 in DIFFERENT_INTENTS:
        c1, c2 = embed(ctx1), embed(ctx2)
        q1, q2 = embed(expr1), embed(expr2)
        cache.store(c1, q1, ["skill-ctx1"])
        if cache.lookup(c2, q2) is not None:
            false_hits += 1
        print(f"\n不同意图: ctx_sim={cosine(c1, c2):.3f} q_sim={cosine(q1, q2):.3f} "
              f"→ {'❌误命中!' if false_hits else '✅正确miss'}")

    hit_rate = hits / len(INTENT_PAIRS) * 100
    sims = [r["q_sim"] for r in results]
    print("-" * 70)
    print(f"同意图命中率: {hits}/{len(INTENT_PAIRS)} = {hit_rate:.1f}%  "
          f"(query 余弦范围 {min(sims):.3f}~{max(sims):.3f}, 均值 {sum(sims)/len(sims):.3f})")
    print(f"不同意图误命中: {false_hits}/{len(DIFFERENT_INTENTS)} (应为 0)")
    print("=" * 70)

    # 结论判定：真实相似度分布决定阈值合理性
    below = sum(1 for s in sims if s < 0.92)
    print(f"\n分析: {below}/{len(sims)} 组改写表达的 query 余弦 < 0.92 阈值")
    if below > 0:
        print("  → 0.92 阈值对'同意图改写'过严，缓存只命中'近似重复'而非'同意图'")
        print("  → 需决策：A) 降阈值（风险：误命中上升） B) 接受缓存仅服务重复查询")
    ok = hit_rate >= 80.0 and false_hits == 0
    print(f"\n验收(原标准 命中率≥80% 误命中=0): {'✅ PASS' if ok else '❌ FAIL'} — 见上方分析")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
