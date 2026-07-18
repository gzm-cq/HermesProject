"""聚类算法核心 — 纯业务逻辑，无外部 I/O"""

import re
import time
import warnings
from collections import Counter, defaultdict

import numpy as np

# Optional GPU deps
TORCH_AVAILABLE = False
try:
    import torch

    TORCH_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore[assignment]


# HDBSCAN (sklearn 1.3+)
try:
    from sklearn.cluster import HDBSCAN as HDBSCANCluster

    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCANCluster = None  # type: ignore[assignment]
    HDBSCAN_AVAILABLE = False

from clustering_analysis.core.embeddings import call_llm_for_entity_with_causal

# ========== Noise words ==========
# 因果连词（来源文本至少包含其中一个才应提取因果对）
CAUSAL_TRIGGERS = frozenset({
    "因为", "所以", "因此", "导致", "由于", "于是", "从而",
    "引发", "造成", "引起", "触发", "使得", "根因", "原因",
    "产生", "促使", "衍生", "诱发", "致使", "酿成", "影响",
    "衍化", "促成", "造就", "带来", "推动", "滋生", "诱致",
})

NOISE_WORDS = frozenset({
    "系统状态",
    "系统",
    "系统日志",
    "日志",
    "错误",
    "报错",
    "失败",
    "提取",
    "提取失败",
    "提取全部",
    "测试",
    "验证",
    "执行",
    "运行",
    "完成",
    "处理",
    "处理完成",
    "处理失败",
    "状态",
    "记录",
    "2026",
    "2025",
    "2024",
    "2023",
    "2022",
    "2021",
    "第",
    "号",
    "条",
    "个",
    "次",
    "分",
    "秒",
    "时",
    "caused_by",
    "causes",
    "enables",
    "prevents",
    "LLM",
    "API",
    "URL",
    "HTTP",
    "JSON",
    "SQL",
    "数据库",
    "表",
    "字段",
    "索引",
    "查询",
    "写入",
    "读取",
})


# ========== Causal detection ==========

def detect_causal_pairs(text: str) -> list[tuple[str, str, float, str]]:
    """Detect causal patterns in text."""
    pairs: list[tuple[str, str, float, str]] = []

    def is_noise_word(word: str) -> bool:
        return word in NOISE_WORDS or word.lower() in NOISE_WORDS

    for m in re.finditer(
        r"([一-鿿\w]{2,20})(?:导致|引发|造成|引起|触发|带来)([一-鿿\w]{2,20})",
        text,
    ):
        subj, obj = m.group(1), m.group(2)
        if not is_noise_word(subj) and not is_noise_word(obj):
            pairs.append((subj, obj, 0.9, "causes"))

    for m in re.finditer(r"([一-鿿\w]{2,15})(?:失败|报错|崩溃|超时|卡死)", text):
        subj = m.group(1)
        if not is_noise_word(subj):
            pairs.append((subj, "系统异常", 0.6, "caused_by"))

    for m in re.finditer(
        r"因为[\s]*([一-鿿\w]{2,15})[，,].*?(?:才|就|所以|因此|从而)[\s]*([一-鿿\w]{2,20})",
        text,
    ):
        subj, obj = m.group(1), m.group(2)
        if not is_noise_word(subj) and not is_noise_word(obj):
            pairs.append((subj, obj, 0.8, "causes"))

    for m in re.finditer(r"(?:根因|根源)(?:是|为)[\s]*([一-鿿\w]{2,20})", text):
        subj = m.group(1)
        if not is_noise_word(subj):
            pairs.append((subj, "问题根因", 0.85, "causes"))

    return pairs


def enrich_text(to_text: str, from_text: str, causal_words: list[str]) -> str:
    """Enrich target text with causal context.

    质量守卫（P0级）：
    - cause == effect → 自杀链，跳过
    - effect 为空 → 无信息量，跳过
    - 因果词为停用词（"频繁""大量"等）→ 跳过
    - 长度不足 → 跳过
    """
    if not causal_words:
        return to_text

    # 停用词：因果动词和泛义高频词
    _CAUSAL_STOP = frozenset({
        "导致", "引发", "造成", "引起", "触发", "使得",
        "频繁", "大量", "多次", "持续", "反复", "连续",
        "发生", "出现", "存在", "进入", "开始", "停止",
    })

    filtered = [w for w in causal_words if w not in NOISE_WORDS and w not in _CAUSAL_STOP]
    if not filtered:
        return to_text

    # 已包含富化标记则跳过，防止反复追加
    if "[因果来源：" in to_text:
        return to_text

    cause_word = filtered[0]
    effect_word = filtered[1] if len(filtered) > 1 else ""

    # P0: 因果相同（自杀链）或效果为空 → 跳过
    if not effect_word or cause_word == effect_word:
        return to_text
    # P0: 长度不足（至少 2 个中文字符或 3 个字母）
    if len(cause_word) < 2 or len(effect_word) < 2:
        return to_text

    structured = f"[因果来源：{cause_word}] [因果结果：{effect_word}]"
    natural = f"与 {cause_word} 相关"

    return f"{to_text}。{structured} {natural}。"


def _detect_causal_in_group(
    group_label: str,
    members: list[int],
    unit_ids: list,
    unit_texts: list[str],
    seen_pairs: set,
    group_prefix: str = "",
) -> list[dict]:
    """Detect causal links within a group."""
    links: list[dict] = []
    _cached_pairs: dict[int, list] = {}

    for i_idx in range(len(members)):
        for j_idx in range(i_idx + 1, len(members)):
            mem_i, mem_j = members[i_idx], members[j_idx]

            for idx in (mem_i, mem_j):
                if idx not in _cached_pairs:
                    _cached_pairs[idx] = detect_causal_pairs(unit_texts[idx])

            for cause_subj, effect_subj, confidence, link_type in _cached_pairs[mem_i]:
                key = (unit_ids[mem_i], unit_ids[mem_j], link_type)
                if key not in seen_pairs and confidence >= 0.6:
                    seen_pairs.add(key)
                    links.append(
                        {
                            "from_id": unit_ids[mem_i],
                            "to_id": unit_ids[mem_j],
                            "link_type": link_type,
                            "weight": min(1.0, confidence),
                            "confidence": confidence,
                            "reason": f"[因果聚类] {group_prefix}组{group_label}内因果词匹配: {cause_subj} → {effect_subj}",
                            "enriched_to_text": enrich_text(
                                unit_texts[mem_j],
                                unit_texts[mem_i],
                                [cause_subj, effect_subj],
                            ),
                        }
                    )

            for cause_subj, effect_subj, confidence, link_type in _cached_pairs[mem_j]:
                key = (unit_ids[mem_j], unit_ids[mem_i], link_type)
                if key not in seen_pairs and confidence >= 0.6:
                    seen_pairs.add(key)
                    links.append(
                        {
                            "from_id": unit_ids[mem_j],
                            "to_id": unit_ids[mem_i],
                            "link_type": link_type,
                            "weight": min(1.0, confidence),
                            "confidence": confidence,
                            "reason": f"[因果聚类] {group_prefix}组{group_label}内因果词匹配: {cause_subj} → {effect_subj}",
                            "enriched_to_text": enrich_text(
                                unit_texts[mem_i],
                                unit_texts[mem_j],
                                [cause_subj, effect_subj],
                            ),
                        }
                    )
    return links


def convert_llm_causal_pairs(
    causal_pairs: list[dict],
    members: list[int],
    unit_ids: list,
    unit_texts: list[str],
    seen_pairs: set,
    group_label: str,
    label_prefix: str = "",
) -> list[dict]:
    """将 LLM 返回的因果对转换为 memory_link 格式。"""
    links: list[dict] = []
    for pair in causal_pairs:
        if not isinstance(pair, dict):
            continue
        cause_idx = pair.get("cause_idx")
        effect_idx = pair.get("effect_idx")
        if not isinstance(cause_idx, int) or not isinstance(effect_idx, int):
            continue
        if cause_idx < 0 or cause_idx >= len(members) or effect_idx < 0 or effect_idx >= len(members):
            continue
        if cause_idx == effect_idx:
            continue
        from_member = members[cause_idx]
        to_member = members[effect_idx]
        key = (str(unit_ids[from_member]), str(unit_ids[to_member]), "causes")
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        # P2: 置信度过滤（LLM 可能返回 low/medium/high 或数值）
        raw_conf = pair.get("confidence", pair.get("confidence_level", "medium"))
        if isinstance(raw_conf, str):
            if raw_conf.lower() == "low":
                continue
            conf_value = 0.85 if raw_conf.lower() == "high" else 0.7
        elif isinstance(raw_conf, (int, float)):
            if raw_conf < 0.5:
                continue
            conf_value = float(raw_conf)
        else:
            conf_value = 0.7

        reason = pair.get("reason", "")
        # P1: 来源文本无因果连词时跳过（LLM 被逼着从无关文本里捏造因果关系）
        if not any(trig in reason for trig in CAUSAL_TRIGGERS):
            if not reason or not any(
                re.search(rf"({trig})", unit_texts[from_member])
                for trig in CAUSAL_TRIGGERS
            ):
                continue
        # 使用 LLM 的 reason 字段作为富化因果词（比原文截取更精准）
        # 尝试在"导致/引发/造成"等因果词处分段，使 [因果来源] 和 [因果结果] 都有内容
        if reason:
            causal_match = re.search(r"(.+?)(?:导致|引发|造成|引起|触发|使得)(.+)", reason)
            if causal_match:
                causal_words = [causal_match.group(1).strip()[:20], causal_match.group(2).strip()[:20]]
            else:
                causal_words = [reason[:30]]
        else:
            causal_words = [unit_texts[from_member][:20]]
        links.append({
            "from_id": str(unit_ids[from_member]),
            "to_id": str(unit_ids[to_member]),
            "link_type": "causes",
            "weight": conf_value,
            "confidence": conf_value,
            "reason": f"[因果LLM] {label_prefix}组{group_label}: {reason}",
            "enriched_to_text": enrich_text(
                unit_texts[to_member],
                unit_texts[from_member],
                causal_words,
            ),
        })
    return links


# ========== Signal computation (torch or numpy) ==========


def compute_semantic_similarity(embeddings: np.ndarray, use_gpu: bool = False) -> np.ndarray:
    """计算语义相似度（余弦相似度）— GPU 加速或 CPU numpy"""
    warnings.warn("compute_semantic_similarity is deprecated and will be removed in a future version.", DeprecationWarning, stacklevel=2)
    n = len(embeddings)
    print("   [1/3] 语义相似度...")
    t0 = time.time()

    if use_gpu and TORCH_AVAILABLE:
        emb_t = torch.from_numpy(embeddings).cuda()  # type: ignore[union-attr]
        norm_emb = emb_t / torch.norm(emb_t, dim=1, keepdim=True)  # type: ignore[union-attr]
        semantic_sim = (norm_emb @ norm_emb.T).cpu().numpy()
        del norm_emb
        torch.cuda.empty_cache()  # type: ignore[union-attr]
    else:
        if use_gpu and not TORCH_AVAILABLE:
            print("     GPU 请求但 torch 不可用，回退到 CPU numpy")
        norm = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / norm
        semantic_sim = normalized @ normalized.T

    t1 = time.time()
    print(f"         计算耗时：{t1 - t0:.2f} 秒")
    print(
        f"         范围：[{semantic_sim[np.triu_indices(n, 1)].min():.4f}, "
        f"{semantic_sim[np.triu_indices(n, 1)].max():.4f}]"
    )
    print(
        f"         均值：{semantic_sim[np.triu_indices(n, 1)].mean():.4f}, "
        f"标准差：{semantic_sim[np.triu_indices(n, 1)].std():.4f}"
    )
    return semantic_sim


def compute_entity_similarity(unit_entity_sets: list[set[str]], use_gpu: bool = False) -> np.ndarray:
    """计算实体重叠度（Jaccard）— 倒排索引 + co-occurrence 避免大矩阵"""
    warnings.warn("compute_entity_similarity is deprecated and will be removed in a future version.", DeprecationWarning, stacklevel=2)
    print("   [2/3] 实体重叠度...")
    t0 = time.time()

    n = len(unit_entity_sets)

    # 倒排索引：entity → list of unit indices
    entity_to_units: dict[str, list[int]] = defaultdict(list)
    for i, entities in enumerate(unit_entity_sets):
        for eid in entities:
            entity_to_units[eid].append(i)

    num_entities = len(entity_to_units)
    print(f"         实体总数：{num_entities}")

    # 每个 unit 的实体基数
    unit_card = np.array([len(s) for s in unit_entity_sets], dtype=np.int32)

    # 统计 co-occurrence（只存非零对）
    pair_intersection: dict[tuple[int, int], int] = {}
    for eid, units in entity_to_units.items():
        if len(units) < 2:
            continue
        for i_idx in range(len(units)):
            for j_idx in range(i_idx + 1, len(units)):
                i, j = units[i_idx], units[j_idx]
                if i < j:
                    key = (i, j)
                else:
                    key = (j, i)
                pair_intersection[key] = pair_intersection.get(key, 0) + 1

    # 构造相似度矩阵
    entity_sim = np.zeros((n, n), dtype=np.float32)
    np.fill_diagonal(entity_sim, 1.0)
    for (i, j), inter in pair_intersection.items():
        union = int(unit_card[i]) + int(unit_card[j]) - inter
        sim = inter / union if union > 0 else 0.0
        entity_sim[i, j] = sim
        entity_sim[j, i] = sim

    t1 = time.time()
    has_common = len(pair_intersection)
    total_pairs = n * (n - 1) // 2
    print(f"         计算耗时：{t1 - t0:.2f} 秒")
    print(
        f"         有共同实体的对：{has_common}/{total_pairs} "
        f"({has_common / total_pairs * 100:.4f}%)"
    )
    if pair_intersection:
        values = list(pair_intersection.values())
        card_i = np.array([unit_card[i] for i, _ in pair_intersection], dtype=np.float32)
        card_j = np.array([unit_card[j] for _, j in pair_intersection], dtype=np.float32)
        inter_val = np.array(values, dtype=np.float32)
        unions = card_i + card_j - inter_val
        sims = np.where(unions > 0, inter_val / unions, 0.0)
        print(f"         非零均值：{sims.mean():.4f}")
    return entity_sim


def compute_info_density_similarity(unit_texts: list[str]) -> np.ndarray:
    """计算信息密度相似度（IDF 加权）— 只计算统计量，不构造 n×n 矩阵"""
    warnings.warn("compute_info_density_similarity is deprecated and will be removed in a future version.", DeprecationWarning, stacklevel=2)
    print("   [3/3] 信息密度...")
    n = len(unit_texts)
    all_words: Counter = Counter()
    for text in unit_texts:
        words = re.findall(r"[一-鿿\w]+|[a-zA-Z]+|\d+", text)
        all_words.update(words)

    total_docs = n
    idf = {w: np.log(total_docs / (1 + c)) for w, c in all_words.items()}

    info_density = np.zeros(n, dtype=np.float32)
    for i, text in enumerate(unit_texts):
        words = re.findall(r"[一-鿿\w]+|[a-zA-Z]+|\d+", text)
        info_density[i] = np.mean([idf.get(w, 0) for w in words]) if words else 0.0

    # 归一化到 [0,1]
    dmin, dmax = info_density.min(), info_density.max()
    if dmax > dmin:
        info_density = (info_density - dmin) / (dmax - dmin)

    # 从排序后 1D 向量计算 info_sim 的 min/max/mean，避免构造 n×n 矩阵
    sorted_d = np.sort(info_density)
    # min(info_sim) = 1 - max_pairwise_diff/2
    max_diff = sorted_d[-1] - sorted_d[0]
    info_sim_min = 1.0 - max_diff / 2.0
    # max(info_sim) = 1 - min_positive_pairwise_diff/2
    diffs = np.diff(sorted_d)
    pos_diffs = diffs[diffs > 1e-10]
    if len(pos_diffs) > 0:
        info_sim_max = 1.0 - float(pos_diffs.min()) / 2.0
    else:
        info_sim_max = 1.0
    # mean(info_sim) 通过前缀和求所有 |d_i - d_j| 均值
    prefix_sum = np.cumsum(sorted_d, dtype=np.float64)
    total_sum = float(prefix_sum[-1])
    total_abs_diff = 0.0
    for i in range(n - 1):
        left_count = n - 1 - i
        # sum_{j>i} (d_j - d_i) = (sum_{j>i} d_j) - left_count * d_i
        right_sum = total_sum - prefix_sum[i]
        total_abs_diff += right_sum - left_count * float(sorted_d[i])
    mean_abs_diff = total_abs_diff / (n * (n - 1) / 2)
    mean_info_sim = 1.0 - mean_abs_diff / 2.0

    print(f"         范围：[{info_sim_min:.4f}, {info_sim_max:.4f}]")
    print(f"         均值：{mean_info_sim:.4f}")
    # 构造相似度矩阵：sim[i,j] = 1 - |d_i - d_j| / 2
    diff = np.abs(info_density[:, None] - info_density[None, :])
    info_sim = 1.0 - diff / 2.0
    return info_sim


# ========== Clustering ==========

def adaptive_hdbscan_params(
    n_samples: int,
    min_samples_min: int = 2,
    min_samples_max: int = 10,
) -> tuple[int, int]:
    """根据数据点数量自适应计算 HDBSCAN 参数。

    Args:
        n_samples: 数据点数量
        min_samples_min: min_samples 最小值限制
        min_samples_max: min_samples 最大值限制

    Returns:
        (min_cluster_size, min_samples)
    """
    if n_samples < 20:
        min_cluster_size = 2
        min_samples = 2
    elif n_samples < 100:
        min_cluster_size = 3
        min_samples = 3
    elif n_samples < 500:
        min_cluster_size = 5
        min_samples = 4
    elif n_samples < 2000:
        min_cluster_size = 8
        min_samples = 6
    else:
        min_cluster_size = 15
        min_samples = 10

    min_samples = max(min_samples_min, min(min_samples_max, min_samples))
    min_cluster_size = max(min_samples_min, min_cluster_size)

    return min_cluster_size, min_samples


def run_hdbscan_clustering(
    embeddings: np.ndarray,
    min_cluster_size: int = 5,
    min_samples: int | None = None,
    cluster_selection_method: str = "eom",
) -> tuple[np.ndarray, np.ndarray, float | None]:
    """单次 HDBSCAN 聚类，替代 DBSCAN 多 eps 扫描。

    HDBSCAN 不需要 eps 参数，自动根据数据密度确定聚类。

    Args:
        embeddings: embedding 矩阵 (n, dim)
        min_cluster_size: 最小簇大小
        min_samples: 构建 core distance 时的邻域样本数（None 则用 sklearn 默认值）
        cluster_selection_method: 'eom' (Excess of Mass) 或 'leaf'

    Returns:
        (labels, probabilities, silhouette) — labels: 聚类标签 (-1 为噪声), silhouette: Silhouette 评分或 None
    """
    print("\n[HDBSCAN] 聚类...")
    t0 = time.time()

    if not HDBSCAN_AVAILABLE:
        raise ImportError("HDBSCAN is not available. Please install scikit-learn >= 1.3.")

    hdb_kwargs = dict(
        min_cluster_size=min_cluster_size,
        cluster_selection_method=cluster_selection_method,
        metric="cosine",
        copy=False,
    )
    if min_samples is not None:
        hdb_kwargs["min_samples"] = min_samples

    # 抑制 sklearn 1.10+ 的 copy 默认值变更警告
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='The default value of `copy`')
        hdb = HDBSCANCluster(**hdb_kwargs)
    labels = hdb.fit_predict(embeddings)
    probabilities = hdb.probabilities_

    n = len(embeddings)
    n_clusters = len(set(labels) - {-1})
    n_noise = (labels == -1).sum()
    noise_ratio = n_noise / n if n > 0 else 0.0

    t1 = time.time()
    print(f"   HDBSCAN: {n_clusters} \u7bc7, {n_noise} \u566a\u58f0 ({noise_ratio:.2%})")
    print(f"   \u8017\u65f6: {t1 - t0:.2f} \u79d2")

    # Silhouette 评分（至少 2 簇且不全是噪声）
    silhouette_val: float | None = None
    if 2 <= n_clusters < n:
        try:
            from sklearn.metrics import silhouette_score

            mask = labels != -1
            if mask.sum() >= 2:
                sil = silhouette_score(embeddings[mask], labels[mask])
            else:
                sil = 0.0
            print(f"   Silhouette: {sil:.4f}")
            silhouette_val = sil
        except Exception as exc:
            print(f"   [WARN] silhouette 计算失败: {exc}")

    return labels, probabilities, silhouette_val


def process_clusters(
    labels: np.ndarray,
    unit_ids: list,
    unit_texts: list[str],
    unit_entity_sets: list[set[str]],
    *,
    skip_entity: bool = False,
    llm_api_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    min_llm_size: int = 10,
    max_group_size: int = 20,
    label_prefix: str = "",
) -> tuple[list[dict], list[dict], list[dict], dict[str, list[str]]]:
    """处理聚类结果，生成写入计划。

    Returns:
        (entity_write_plan, unit_entity_write_plan, memory_link_plan, enriched_texts)
    """
    # 按 label 分组
    clusters: dict[int, list[int]] = defaultdict(list)
    for i, label in enumerate(labels):
        if label != -1:  # 忽略噪声
            clusters[label].append(i)

    print(f"   共 {len(clusters)} 个有效聚类（忽略噪声）")

    # 过滤过大组
    filtered: dict[int, list[int]] = {}
    for label, members in clusters.items():
        if len(members) <= max_group_size:
            filtered[label] = members
        else:
            print(f"   [WARN] 跳过过大组 {label}（{len(members)} 人）")

    print(f"   过滤后剩余 {len(filtered)} 个组")

    entity_write_plan: list[dict] = []
    unit_entity_write_plan: list[dict] = []
    memory_link_plan: list[dict] = []
    enriched_texts: dict[str, list[str]] = {}
    seen_pairs: set = set()

    for group_label, members in filtered.items():
        print(f"   处理组 {group_label}（{len(members)} 人）...")

        entity_id = f"group_{label_prefix}_{group_label}" if label_prefix else f"group_{group_label}"

        # Step 1: 提取实体名 + 因果对（LLM）
        if not skip_entity and len(members) >= min_llm_size:
            texts_for_llm = [unit_texts[i] for i in members]
            canonical_name, causal_pairs = call_llm_for_entity_with_causal(
                texts_for_llm, api_url=llm_api_url, api_key=llm_api_key, model=llm_model
            )
        else:
            # fallback：取第一个记忆的前 10 字 + entity_id 后缀，保证唯一
            # 避免语义不同但有相同前10字的实体撞名，触发 DB ON CONFLICT 报错
            name_head = unit_texts[members[0]][:10] + "..."
            canonical_name = f"{name_head} [{entity_id}]"
            causal_pairs = []

        entity_write_plan.append(
            {
                "entity_id": entity_id,
                "canonical_name": canonical_name,
                "member_count": len(members),
                "member_ids": [unit_ids[i] for i in members],
            }
        )

        # Step 2: 关联 unit_entities
        for idx in members:
            unit_entity_write_plan.append(
                {
                    "unit_id": unit_ids[idx],
                    "entity_id": entity_id,
                }
            )

        # Step 3: 因果链检测
        if not skip_entity and len(members) >= min_llm_size and causal_pairs:
            # LLM 路径：使用 LLM 返回的因果对
            group_links = convert_llm_causal_pairs(
                causal_pairs, members, unit_ids, unit_texts, seen_pairs,
                group_label=str(group_label), label_prefix=label_prefix,
            )
        else:
            # 小簇或跳过 LLM：正则路径
            group_links = _detect_causal_in_group(
                group_label=str(group_label),
                members=members,
                unit_ids=unit_ids,
                unit_texts=unit_texts,
                seen_pairs=seen_pairs,
            )
        memory_link_plan.extend(group_links)

        # Step 4: 富化文本
        for link in group_links:
            if "enriched_to_text" in link and link["enriched_to_text"]:
                uid = str(link["to_id"])
                if uid not in enriched_texts:
                    enriched_texts[uid] = []
                enriched_texts[uid].append(link["enriched_to_text"])

    return entity_write_plan, unit_entity_write_plan, memory_link_plan, enriched_texts


def merge_similar_entities(
    entity_centroids: dict[str, np.ndarray],
    threshold: float = 0.85,
) -> dict[str, str]:
    """合并相似实体：返回 {entity_id → merge_into_entity_id} 映射。

    用 Union-Find 处理传递相似关系。合并时保留成员最多的实体。

    Args:
        entity_centroids: {entity_id: centroid_embedding}
        threshold: 余弦相似度阈值

    Returns:
        {要删除的 entity_id → 合并到的 entity_id}
    """
    eids = list(entity_centroids.keys())
    if len(eids) < 2:
        return {}

    centroids = np.stack([entity_centroids[eid] for eid in eids])
    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    sim_matrix = (centroids @ centroids.T) / (norms @ norms.T + 1e-10)

    # Union-Find
    parent = list(range(len(eids)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    n_entities = len(eids)
    for i in range(n_entities):
        for j in range(i + 1, n_entities):
            if sim_matrix[i, j] >= threshold:
                union(i, j)

    # 按连通分量分组
    groups: dict[int, list[int]] = {}
    for i in range(n_entities):
        groups.setdefault(find(i), []).append(i)

    merge_map: dict[str, str] = {}
    for indices in groups.values():
        if len(indices) < 2:
            continue
        # 保留第一个实体，其余合并进去
        keeper_idx = indices[0]
        keeper_id = eids[keeper_idx]
        for idx in indices[1:]:
            merge_map[eids[idx]] = keeper_id
            print(f"   [MERGE] 合并实体: {eids[idx]} \u2192 {keeper_id}")

    return merge_map


def _detect_causal_in_group_incremental(
    group_label: str,
    new_members: list[int],
    old_members: list[int],
    unit_ids: list,
    unit_texts: list[str],
    seen_pairs: set,
    group_prefix: str = "",
) -> list[dict]:
    """增量检测组内因果链：只检测 new×new 和 new×old，不重复检测 old×old。

    Args:
        group_label: 组标签（用于日志/reason）
        new_members: 新成员在 unit_ids/unit_texts 中的索引列表
        old_members: 旧成员在 unit_ids/unit_texts 中的索引列表
        unit_ids: 全局 unit_id 列表
        unit_texts: 全局文本列表
        seen_pairs: 已见过的因果对集合（用于去重）
        group_prefix: 组前缀

    Returns:
        新增的因果链列表
    """
    links: list[dict] = []
    _cached_pairs: dict[int, list] = {}

    all_new = list(new_members)
    all_old = list(old_members)

    def _get_cached_pairs(idx: int) -> list:
        if idx not in _cached_pairs:
            _cached_pairs[idx] = detect_causal_pairs(unit_texts[idx])
        return _cached_pairs[idx]

    def _add_link(
        from_idx: int,
        to_idx: int,
        cause_subj: str,
        effect_subj: str,
        confidence: float,
        link_type: str,
    ) -> None:
        key = (unit_ids[from_idx], unit_ids[to_idx], link_type)
        if key in seen_pairs or confidence < 0.6:
            return
        seen_pairs.add(key)
        links.append({
            "from_id": unit_ids[from_idx],
            "to_id": unit_ids[to_idx],
            "link_type": link_type,
            "weight": min(1.0, confidence),
            "confidence": confidence,
            "reason": f"[因果聚类] {group_prefix}组{group_label}内因果词匹配: {cause_subj} → {effect_subj}",
            "enriched_to_text": enrich_text(
                unit_texts[to_idx],
                unit_texts[from_idx],
                [cause_subj, effect_subj],
            ),
        })

    # new × new 组合
    for i_idx in range(len(all_new)):
        for j_idx in range(i_idx + 1, len(all_new)):
            mem_i, mem_j = all_new[i_idx], all_new[j_idx]
            pairs_i = _get_cached_pairs(mem_i)
            pairs_j = _get_cached_pairs(mem_j)
            for cause_subj, effect_subj, confidence, link_type in pairs_i:
                _add_link(mem_i, mem_j, cause_subj, effect_subj, confidence, link_type)
            for cause_subj, effect_subj, confidence, link_type in pairs_j:
                _add_link(mem_j, mem_i, cause_subj, effect_subj, confidence, link_type)

    # new × old 组合
    for new_idx in all_new:
        for old_idx in all_old:
            pairs_new = _get_cached_pairs(new_idx)
            pairs_old = _get_cached_pairs(old_idx)
            for cause_subj, effect_subj, confidence, link_type in pairs_new:
                _add_link(new_idx, old_idx, cause_subj, effect_subj, confidence, link_type)
            for cause_subj, effect_subj, confidence, link_type in pairs_old:
                _add_link(old_idx, new_idx, cause_subj, effect_subj, confidence, link_type)

    return links


def dedup_memory_links(links: list[dict]) -> list[dict]:
    """对 memory_links 去重，基于 (min(from_id, to_id), max(from_id, to_id), link_type)。

    保留第一次出现的链接，后续重复的跳过。

    Args:
        links: memory_link 列表，每个 dict 需包含 from_id、to_id、link_type

    Returns:
        去重后的 links 列表
    """
    seen: set[tuple[str, str, str]] = set()
    result: list[dict] = []
    for link in links:
        from_id = str(link.get("from_id", ""))
        to_id = str(link.get("to_id", ""))
        link_type = str(link.get("link_type", ""))
        key = (min(from_id, to_id), max(from_id, to_id), link_type)
        if key not in seen:
            seen.add(key)
            result.append(link)
    return result


def match_new_to_existing(
    new_centroids: dict[str, np.ndarray],
    existing_centroids: dict[str, np.ndarray],
    threshold: float = 0.92,
) -> dict[str, str]:
    """新实体匹配已有实体，返回 {new_entity_id → existing_entity_id} 映射。

    只做 new→existing 单向最佳匹配，不做 existing↔existing 全量比较，
    避免 n×n 矩阵随实体数增长而膨胀。

    Args:
        new_centroids: 本轮新增实体 {entity_id: centroid}
        existing_centroids: 已有实体 {entity_id: centroid}
        threshold: 余弦相似度阈值

    Returns:
        {新实体_id → 匹配到的已有实体_id}
    """
    if not new_centroids or not existing_centroids:
        return {}

    new_ids = list(new_centroids.keys())
    existing_ids = list(existing_centroids.keys())

    new_matrix = np.stack([new_centroids[eid] for eid in new_ids])         # (m, d)
    existing_matrix = np.stack([existing_centroids[eid] for eid in existing_ids])  # (k, d)

    new_norms = np.linalg.norm(new_matrix, axis=1, keepdims=True)
    existing_norms = np.linalg.norm(existing_matrix, axis=1, keepdims=True)

    # m × k 矩阵，而非 n×n
    sim_matrix = (new_matrix @ existing_matrix.T) / (new_norms @ existing_norms.T + 1e-10)

    merge_map: dict[str, str] = {}
    for i, new_id in enumerate(new_ids):
        best_idx = int(np.argmax(sim_matrix[i]))
        best_sim = float(sim_matrix[i, best_idx])
        if best_sim >= threshold:
            merge_map[new_id] = existing_ids[best_idx]
            print(f"   [MERGE] 新实体 {new_id} \u2192 已有实体 {existing_ids[best_idx]} (sim={best_sim:.4f})")

    return merge_map
