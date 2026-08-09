"""recommendations.py — 基于当前指标生成可执行的优化建议。

从 flywheel-health-report.py L1986-2150 搬入。
"""

from __future__ import annotations

import re

from .config import TH, REC_TH


def generate_recommendations(
    router_m: dict, skill_m: dict, kn_m: dict,
    kt_m: dict, cluster_m: dict,
    issues: list[dict], trends: dict[str, str],
    credibility_warnings: list[str], zombie_files: list[str],
    token_m: dict | None = None,
    sag_contr_m: dict | None = None,
    skill_usage_m: dict | None = None,
    error_m: dict | None = None,
    memory_m: dict | None = None,
) -> list[dict]:
    """Generate actionable optimization recommendations based on current metrics."""
    recs: list[dict] = []

    # --- Router ---
    if router_m.get("status") != "no_data":
        n = router_m.get("real_total", 0)
        if n > 0 and n < TH["min_sample_size"]:
            recs.append({"flywheel": "Router", "desc": f"样本量不足（真实消息 {n} 次 < {TH['min_sample_size']}），建议增加日常路由量或降低最小样本阈值"})
        full_off = router_m.get("full_off_pct", 0)
        if full_off > REC_TH["router_full_off_high_pct"]:
            recs.append({"flywheel": "Router", "desc": f"全关率 {full_off}% 偏高，建议检查 Router prompt 是否过度保守或模型超时频发"})
        empty = router_m.get("empty_rate", 0)
        if empty > REC_TH["router_empty_high_pct"]:
            recs.append({"flywheel": "Router", "desc": f"空结果率 {empty}% 偏高，建议检查 Hindsight/知识树召回链路或降低 min_score 阈值"})
        avg_lat = router_m.get("avg_latency_ms", 0)
        if avg_lat > REC_TH["router_latency_high_ms"]:
            recs.append({"flywheel": "Router", "desc": f"平均延迟 {avg_lat}ms 偏高，建议排查 Hindsight daemon 连接池或 Reranker 超时"})
        avg_score = router_m.get("avg_score", 0)
        if 0 < avg_score < REC_TH["router_score_low"]:
            recs.append({"flywheel": "Router", "desc": f"平均得分 {avg_score} 偏低，召回结果相关性不足，建议调整 embedding 或 reranker 模型"})
        err_rate = router_m.get("error_rate", 0)
        if err_rate > TH["error_rate_high_pct"]:
            recs.append({"flywheel": "Router", "desc": f"路由错误率 {err_rate}% 偏高（阈值 {TH['error_rate_high_pct']}%），建议检查 Router LLM 调用稳定性或外部召回服务健康状态"})

        sag_on = router_m.get("sag_on_pct", 0)
        if sag_on < REC_TH["sag_on_low_pct"] and n > 0:
            recs.append({"flywheel": "SAG", "desc": f"SAG 开启率仅 {sag_on}%，Router 极少触发 SAG 召回，建议检查 Router prompt 或 SAG 触发条件"})
        sag_kept = router_m.get("sag_total_kept", 0)
        if sag_on > REC_TH["sag_on_high_pct"] and sag_kept == 0:
            recs.append({"flywheel": "SAG", "desc": f"SAG 开启率 {sag_on}% 但召回量为 0，可能 SAG 服务异常或索引为空，建议排查 SAG 健康状态"})
        sag_lat = router_m.get("sag_avg_latency_ms", 0)
        if sag_lat > REC_TH["sag_latency_high_ms"]:
            recs.append({"flywheel": "SAG", "desc": f"SAG 平均延迟 {sag_lat}ms 偏高，建议排查 SAG 服务性能或网络连接"})

        # SAG 贡献度
        if sag_contr_m and sag_contr_m.get("status") != "no_data":
            merge_zero = sag_contr_m.get("merge_zero_pct", 0)
            recall_success_count = sag_contr_m.get("recall_success_count", 0)
            recall_error_count = sag_contr_m.get("recall_error_count", 0)
            recall_zero = sag_contr_m.get("recall_zero", 0)
            # 全部成功召回为 0 section 的极端情况（排除 error 场景）
            if recall_success_count > 0 and recall_zero == recall_success_count:
                recs.append({"flywheel": "SAG", "desc": f"SAG 全部 {recall_success_count} 次成功召回均为 0 section，索引可能为空或搜索条件过严，建议检查 SAG 索引完整性和 query 构造逻辑"})
            elif merge_zero > REC_TH["sag_merge_zero_high_pct"] and sag_on > REC_TH["sag_on_low_pct"]:
                recs.append({"flywheel": "SAG", "desc": f"SAG 合并零结果率 {merge_zero}%，召回内容未通过去重/打分，建议降低 SAG 阈值或优化 SAG 索引质量"})
            if recall_error_count > 0:
                recs.append({"flywheel": "SAG", "desc": f"SAG 召回异常 {recall_error_count} 次，建议检查 SAG 服务健康状态和熔断器日志"})

    # --- Token 预算 ---
    if token_m and token_m.get("status") != "no_data":
        exhaust = token_m.get("exhaust_pct", 0)
        if exhaust > TH["token_budget_exhaust_pct"]:
            recs.append({"flywheel": "Token", "desc": f"Token 预算耗尽率 {exhaust}%，可能导致召回截断，建议增加 total_budget 或优化各源 token 占用"})
        total_avg = token_m.get("total_stats", {}).get("avg", 0)
        budget = token_m.get("total_budget", 4000)
        if budget and total_avg / budget > REC_TH["token_avg_usage_high_ratio"]:
            recs.append({"flywheel": "Token", "desc": f"Token 平均使用率 {total_avg/budget*100:.0f}% 偏高，建议关注高峰期耗尽风险"})

    # --- Skill ---
    if skill_m.get("status") != "no_data":
        f1 = skill_m.get("avg_f1", 0)
        if 0 < f1 < TH["skill_f1_low"]:
            recs.append({"flywheel": "Skill", "desc": f"F1={f1} 低于阈值，建议检查 skillopt-nightly-run 训练数据质量或调整评估基准"})
        elif 0 < f1 < REC_TH["skill_f1_moderate"]:
            recs.append({"flywheel": "Skill", "desc": f"F1={f1} 有提升空间，建议关注 Precision/Recall 差异，优化 skill_matcher 关键词扩展"})
        precision = skill_m.get("avg_precision", 0)
        recall = skill_m.get("avg_recall", 0)
        if precision > 0 and recall > 0:
            if recall < precision * REC_TH["skill_pr_imbalance_ratio"]:
                recs.append({"flywheel": "Skill", "desc": f"Recall ({recall}) 远低于 Precision ({precision})，建议扩充同义词库或增加中英文双向匹配"})
            elif precision < recall * REC_TH["skill_pr_imbalance_ratio"]:
                recs.append({"flywheel": "Skill", "desc": f"Precision ({precision}) 远低于 Recall ({recall})，建议收紧匹配规则或增加负样本"})

    # --- KN Baseline ---
    if kn_m.get("status") != "no_data":
        unknown_pct = kn_m.get("unknown_dim_pct", 0)
        if unknown_pct > REC_TH["kn_unknown_dim_high_pct"]:
            recs.append({"flywheel": "KN", "desc": f"unknown 维度占比 {unknown_pct}%，建议优化维度分类器或扩充基线查询覆盖"})
        dim_summary = kn_m.get("dim_summary", {})
        # 收集所有均分偏低的维度，合并为一条推荐（避免 entity/debug 各出 1 条重复）
        low_score_dims: list[tuple[str, dict]] = []
        for dim, s in dim_summary.items():
            if dim == "unknown" or s.get("count", 0) < REC_TH["kn_dim_min_sample"]:
                continue
            if s.get("avg_score", 1) < TH["kn_avg_score_low"]:
                low_score_dims.append((dim, s))
        if len(low_score_dims) == 1:
            dim, s = low_score_dims[0]
            recs.append({"flywheel": "KN",
                         "desc": f"dimension={dim} 均分 {s['avg_score']}（{s['count']} 条查询），建议针对性增加该维度召回源或调整权重"})
        elif len(low_score_dims) > 1:
            parts = []
            small_dims = []
            for dim, s in low_score_dims:
                parts.append(f"{dim}(均分{s['avg_score']}, {s['count']}条)")
                if s.get("count", 0) < 20:
                    small_dims.append(dim)
            note = f"（其中{'/'.join(small_dims)}样本量偏小，建议关注）" if small_dims else ""
            recs.append({"flywheel": "KN",
                         "desc": f"部分维度均分偏低：{'；'.join(parts)}{note}，建议针对性增加召回源或调整权重"})

    # --- 知识树 ---
    if kt_m.get("status") != "no_data":
        orphan = kt_m.get("orphan_pct", 0)
        if orphan > REC_TH["kt_orphan_high_pct"]:
            recs.append({"flywheel": "知识树", "desc": f"孤立知识点 {orphan}%，建议运行 consolidate 补齐 knowledge_tree_edges 或检查 k_vector 兜底"})
        frag = kt_m.get("fragment_domains", 0)
        if frag > REC_TH["kt_fragment_high_count"]:
            recs.append({"flywheel": "知识树", "desc": f"碎片域 {frag} 个，建议合并相似域或调整 HDBSCAN min_cluster_size"})
        conf = kt_m.get("avg_confidence", 0)
        if 0 < conf < REC_TH["kt_confidence_low"]:
            recs.append({"flywheel": "知识树", "desc": f"平均置信度 {conf} 偏低，建议检查知识点提取 prompt 或增加准入校验"})

    # --- 聚类 ---
    if cluster_m.get("status") != "no_data":
        noise = cluster_m.get("noise_rate", 0)
        if noise > REC_TH["cluster_noise_high_pct"]:
            recs.append({"flywheel": "聚类", "desc": f"噪声率 {noise}% 偏高，建议调整 HDBSCAN min_cluster_size 或增加 min_llm_size"})
        n_clusters = cluster_m.get("cluster_count", 0)
        if n_clusters > 0 and n_clusters < REC_TH["cluster_min_count"]:
            recs.append({"flywheel": "聚类", "desc": f"聚类数仅 {n_clusters}，可能过粗，建议降低 min_cluster_size 或增加样本量"})
        links = cluster_m.get("memory_links", 0)
        if 0 < links < REC_TH["cluster_links_min_count"]:
            recs.append({"flywheel": "聚类", "desc": f"Memory Links 仅 {links}，聚类间关联稀疏，建议检查 memory_links 写入逻辑"})

    # --- 记忆清理 ---
    if memory_m and memory_m.get("status") != "no_data":
        mem_usage = memory_m.get("memory_usage_pct", 0)
        user_usage = memory_m.get("user_usage_pct", 0)
        if mem_usage > REC_TH["memory_usage_high_pct"]:
            recs.append({"flywheel": "记忆", "desc": f"MEMORY.md 占用 {mem_usage}%，接近上限，建议增加清理力度或提高 compress/hindsight 迁移比例"})
        if user_usage > REC_TH["memory_usage_high_pct"]:
            recs.append({"flywheel": "记忆", "desc": f"USER.md 占用 {user_usage}%，接近上限，建议精简用户偏好和个人信息"})
        hindsight = memory_m.get("total_hindsight", 0)
        compress = memory_m.get("total_compress", 0)
        if hindsight == 0 and compress == 0 and mem_usage > REC_TH["memory_no_output_usage_pct"]:
            recs.append({"flywheel": "记忆", "desc": f"连续无 hindsight/compress 产出（占用 {mem_usage}%），建议检查分类 prompt 是否过于保守"})

    # --- 趋势恶化 ---
    for key, val in trends.items():
        if "→" in val and "(" in val:
            try:
                m = re.search(r"\(([+-]?\d+\.?\d*)", val)
                if not m:
                    continue
                delta = float(m.group(1))
                if delta > 0 and any(k in key for k in ["全关率", "空结果率", "噪声率", "孤立率", "unknown", "MEMORY占用率", "USER占用率"]):
                    recs.append({"flywheel": "趋势", "desc": f"{key} 恶化 ({val})，建议关注并排查根因"})
                elif delta < 0 and any(k in key for k in ["F1", "得分", "成功率"]):
                    recs.append({"flywheel": "趋势", "desc": f"{key} 下降 ({val})，建议关注并排查根因"})
            except (ValueError, IndexError):
                pass

    # --- 全局错误 ---
    if error_m and error_m.get("status") != "no_data":
        err_count = error_m.get("error_count", 0)
        if err_count > REC_TH["error_high_count"]:
            recs.append({"flywheel": "系统", "desc": f"当日 ERROR 日志 {err_count} 条偏多，建议排查 top 错误模块"})
        top_mods = error_m.get("top_modules", [])
        if top_mods:
            top1 = top_mods[0]
            total = error_m.get("date_logs", 1)
            if total and top1["count"] / total > REC_TH["error_concentration_ratio"]:
                recs.append({"flywheel": "系统", "desc": f"错误集中在 {top1['module']} ({top1['count']}/{total}, {top1['count']/total*100:.0f}%)，建议优先排查"})

    # --- 僵尸文件 ---
    if zombie_files:
        recs.append({"flywheel": "维护", "desc": f"发现 {len(zombie_files)} 个非飞轮 state 文件 ({', '.join(zombie_files[:3])})，建议清理以减少噪音"})

    return recs
