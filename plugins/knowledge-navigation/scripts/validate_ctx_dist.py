#!/usr/bin/env python3
"""validate_ctx_dist.py — A 方案 ctx 维度相似度分布实测。

真实场景模拟：
- 同任务延续：上下文指向同一目标，新 query 是延续（"接着分析利润率"）
- 同主题子任务：上下文同主题但新 query 换子目标
- 跨任务切换：上下文与新 query 完全不同的任务

用本地 bge-m3 (8082) 测 ctx_emb 与 query_emb 的余弦相似度，
决定 A 方案 ctx_threshold / query_threshold 的取值。
"""

from __future__ import annotations

import json
import sys
import time

import httpx
import numpy as np

EMB_URL = "http://127.0.0.1:8082/v1/embeddings"
EMB_MODEL = "bge-m3"

# (context_3rounds, new_query, 期望关系)
CASE = [
    # ── 同任务延续（A 方案要命中的场景）──
    ("用户: 帮我分析这份Excel销售数据\n助手: 好的，我先读取文件\n用户: 重点看各区域销售额",
     "接着分析各区域的利润率", "同任务延续"),
    ("用户: 写一个Python脚本处理CSV去重\n助手: 正在写\n用户: 加个进度条",
     "继续完善这个去重脚本", "同任务延续"),
    ("用户: 检查一下系统里有哪些服务没起来\n助手: 我查一下\n用户: Docker 容器状态",
     "再看看 Nginx 的状态", "同任务延续"),
    ("用户: 数据库查询很慢，帮我看看\n助手: 检查一下索引\n用户: PG 的 HNSW 配置",
     "还查一下 shared_buffers 设置", "同任务延续"),
    # ── 同主题子任务（边缘：ctx 相似但目标略偏）──
    ("用户: 部署一个 Docker 服务\n助手: 写好 docker-compose 了",
     "给这个服务加个健康检查", "同主题子任务"),
    ("用户: 写一段 Python 爬虫\n助手: 已给出代码\n用户: 加代理支持",
     "换成多线程爬取", "同主题子任务"),
    # ── 跨任务切换（A 方案必须拒收）──
    ("用户: 帮我审查这段 Python 代码的安全性",
     "分析Excel里的销售数据", "跨任务"),
    ("用户: 部署 Docker 容器",
     "写一篇项目总结报告", "跨任务"),
    ("用户: 配置飞书机器人消息推送",
     "检查 PostgreSQL 数据库索引", "跨任务"),
]

# query 侧：同 query 字面重复 + 同意图改写 + 不同意图
QUERY_CASES = [
    ("moa配置是什么", "moa配置是什么", "字面重复"),
    ("moa配置是什么", "帮我看看moa的配置", "同意图改写"),
    ("moa配置是什么", "分析Excel销售数据", "不同意图"),
    ("docker怎么部署服务", "docker 部署服务步骤", "同意图改写"),
    ("docker怎么部署服务", "审查Python代码安全", "不同意图"),
]


def embed(text: str) -> np.ndarray:
    for attempt in range(3):
        try:
            resp = httpx.post(
                EMB_URL,
                json={"model": EMB_MODEL, "input": [text]},
                timeout=60,
            )
            resp.raise_for_status()
            return np.array(resp.json()["data"][0]["embedding"], dtype=np.float32)
        except Exception:
            time.sleep(1)
    raise RuntimeError("embedding failed")


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main() -> None:
    print("═══ ctx 维度相似度（context_3rounds vs new_query）═══")
    ctx_same, ctx_cross = [], []
    for ctx, q, rel in CASE:
        sim = cosine(embed(ctx), embed(q))
        print(f"  [{rel}] {sim:.3f}  ctx={ctx[:28]}... → {q[:20]}")
        if rel == "同任务延续":
            ctx_same.append(sim)
        else:
            ctx_cross.append(sim)

    print("\n═══ query 维度相似度 ═══")
    q_same_intent = []
    for a, b, rel in QUERY_CASES:
        sim = cosine(embed(a), embed(b))
        print(f"  [{rel}] {sim:.3f}  {a[:20]} vs {b[:20]}")
        if rel in ("同意图改写",):
            q_same_intent.append(sim)

    print("\n═══ 汇总 ═══")
    if ctx_same:
        print(f"  ctx 同任务延续: {len(ctx_same)} 组, min={min(ctx_same):.3f} mean={np.mean(ctx_same):.3f}")
    if ctx_cross:
        print(f"  ctx 跨任务    : {len(ctx_cross)} 组, max={max(ctx_cross):.3f} mean={np.mean(ctx_cross):.3f}")
    if q_same_intent:
        print(f"  query 同意图改写: mean={np.mean(q_same_intent):.3f}")

    # 建议阈值（安全间隔原则）
    ctx_lo = max(ctx_cross) if ctx_cross else 1.0  # 跨任务最高分（必须拒）
    ctx_hi = min(ctx_same) if ctx_same else 0.0    # 同任务最低分（必须收）
    gap = ctx_hi - ctx_lo
    suggest = (ctx_lo + ctx_hi) / 2
    print(f"\n  ctx 建议阈值区间: 拒<= {ctx_lo:.3f} / 收>= {ctx_hi:.3f}, 间隔 {gap:.3f}")
    if gap > 0.05:
        print(f"  ✓ 可安全取 ctx_threshold = {suggest:.3f}（间隔内）")
    else:
        print("  ⚠ ctx 分布重叠, 单靠 ctx 阈值无法区分, 需 intent 校验兜底")


if __name__ == "__main__":
    main()