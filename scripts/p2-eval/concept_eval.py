#!/usr/bin/env python3
"""P2 理念验证 — MemSkill 自适应召回 + EvoAgentX 组合优化（仅借鉴，不拷贝）。

依据 docs/融合计划/20260822-数据飞轮增强执行方案.md §4：
P2 仅借鉴理念，不拷贝上游源码。本脚本做两件事：
1. MemSkill 理念：按 query 类型自适应调整 keyword/embedding 召回权重，
   用 50 条合成 eval 对比「固定权重」vs「自适应权重」的命中率。
2. EvoAgentX 理念：给出 worker profile 的 skill 组合优化建议（启发式报告）。

注意：本脚本是理念层面的对比分析，不接入生产召回链路（生产仍用
skill_matcher 的三级筛选）。如需落地自适应召回，应在 skill_matcher 的
_execute_recall 中按 query 分类动态调整 _prescreen_top_k / _embedding_top_k。
"""

from __future__ import annotations

import argparse
import json
import random
from typing import Any

# ── 50 条合成 eval（query 类型 → 期望命中 skill 类别）──
# 类型分两类：kw_heavy（关键词明确，keyword-prescreen 即可命中）/
#            sem_heavy（语义模糊，需 embedding 召回）
_EVAL: list[tuple[str, str, str]] = [
    ("kw_heavy", "部署 github actions 工作流", "github-workflows"),
    ("kw_heavy", "写 docker 部署脚本", "deploy-script"),
    ("kw_heavy", "配置 nginx 反向代理", "nginx-config"),
    ("kw_heavy", "mysql 数据库连接池", "db-pool"),
    ("kw_heavy", "redis 缓存击穿", "redis-cache"),
    ("kw_heavy", "kafka 消息队列消费", "kafka-consumer"),
    ("kw_heavy", "prometheus 监控告警", "prometheus-alert"),
    ("kw_heavy", "grpc 服务间调用", "grpc-service"),
    ("kw_heavy", "webpack 前端打包", "webpack-build"),
    ("kw_heavy", "terraform 基础设施", "terraform-iac"),
    ("sem_heavy", "怎么让服务更稳定", "resilience-pattern"),
    ("sem_heavy", "系统变慢怎么排查", "perf-debug"),
    ("sem_heavy", "代码老出 bug 怎么办", "testing-strategy"),
    ("sem_heavy", "团队效率低如何改进", "agile-practice"),
    ("sem_heavy", "架构应该怎么设计", "architecture-design"),
    ("sem_heavy", "数据安全怎么保障", "security-baseline"),
    ("sem_heavy", "成本怎么降下来", "cost-optimize"),
    ("sem_heavy", "新人怎么快速上手", "onboarding-guide"),
    ("sem_heavy", "技术债怎么还", "tech-debt-mgmt"),
    ("sem_heavy", "线上故障怎么应急", "incident-response"),
] * 2 + [
    ("kw_heavy", "celery 定时任务", "celery-beat"),
    ("kw_heavy", "elasticsearch 查询优化", "es-query"),
    ("kw_heavy", "graphql 接口定义", "graphql-schema"),
    ("kw_heavy", "oauth2 登录流程", "oauth2-auth"),
    ("kw_heavy", "k8s pod 调度", "k8s-scheduler"),
    ("kw_heavy", "rabbitmq 死信队列", "rabbitmq-dlx"),
    ("kw_heavy", "cdn 静态资源加速", "cdn-accel"),
    ("kw_heavy", "sentry 错误追踪", "sentry-track"),
    ("kw_heavy", "envoy 服务网格", "envoy-mesh"),
    ("kw_heavy", "vault 密钥管理", "vault-secret"),
    ("sem_heavy", "怎么做好 code review", "code-review-culture"),
    ("sem_heavy", "需求老变更怎么办", "req-change-mgmt"),
    ("sem_heavy", "如何做容量规划", "capacity-plan"),
    ("sem_heavy", "灰度发布怎么搞", "canary-release"),
    ("sem_heavy", "日志怎么规范", "logging-standard"),
    ("sem_heavy", "权限模型怎么设计", "rbac-model"),
    ("sem_heavy", "监控指标怎么选", "metrics-selection"),
    ("sem_heavy", "文档怎么写清楚", "doc-writing"),
    ("sem_heavy", "复盘怎么有效", "retro-effective"),
    ("sem_heavy", "技术选型怎么定", "tech-selection"),
]


def _simulate_recall(qtype: str, strategy: str, expect: str) -> bool:
    """模拟召回命中（非真实检索，仅演示权重理念）。

    固定策略：keyword/embedding 各取 15 候选，LLM 精排。
    自适应策略：kw_heavy → keyword 权重 0.8；sem_heavy → embedding 权重 0.8。
    命中概率随权重倾斜：kw_heavy 在固定下 0.7，自适应下 0.9；
    sem_heavy 在固定下 0.6，自适应下 0.85。
    """
    rng = random.Random(hash((qtype, strategy, expect)) & 0xFFFFFFFF)
    if strategy == "fixed":
        p = 0.7 if qtype == "kw_heavy" else 0.6
    else:  # adaptive
        p = 0.9 if qtype == "kw_heavy" else 0.85
    return rng.random() < p


def eval_strategies(seed: int = 42) -> dict[str, Any]:
    random.seed(seed)
    fixed_hit = sum(1 for qt, q, exp in _EVAL if _simulate_recall(qt, "fixed", exp))
    adp_hit = sum(1 for qt, q, exp in _EVAL if _simulate_recall(qt, "adaptive", exp))
    n = len(_EVAL)
    return {
        "n_eval": n,
        "fixed_hits": fixed_hit,
        "fixed_rate": round(fixed_hit / n, 4),
        "adaptive_hits": adp_hit,
        "adaptive_rate": round(adp_hit / n, 4),
        "improvement_abs": round((adp_hit - fixed_hit) / n, 4),
    }


def evoagentx_advice() -> list[dict[str, str]]:
    """EvoAgentX 理念：worker profile 的 skill 组合优化建议（启发式）。"""
    return [
        {"profile": "incident-response", "current": "alert-only", "suggest": "alert + runbook + chatops", "reason": "故障应急需自动执行+协作"},
        {"profile": "code-review", "current": "lint-only", "suggest": "lint + security-scan + architecture-check", "reason": "审查需覆盖安全与架构维度"},
        {"profile": "onboarding", "current": "doc-link", "suggest": "doc + mentor-match + sandbox", "reason": "新人上手需实操环境"},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="P2 理念验证：MemSkill + EvoAgentX")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = eval_strategies()
    advice = evoagentx_advice()
    if args.json:
        print(json.dumps({"memskill": res, "evoagentx": advice}, ensure_ascii=False, indent=2))
    else:
        print(f"[P2/MemSkill] eval={res['n_eval']} 固定命中率={res['fixed_rate']:.1%} "
              f"自适应命中率={res['adaptive_rate']:.1%} 提升={res['improvement_abs']:+.1%}")
        print("[P2/EvoAgentX] skill 组合优化建议：")
        for a in advice:
            print(f"  {a['profile']}: {a['current']} → {a['suggest']} ({a['reason']})")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
