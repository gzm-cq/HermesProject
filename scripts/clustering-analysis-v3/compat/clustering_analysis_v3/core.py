"""兼容 shim：clustering_analysis_v3.core → clustering_analysis.core"""
from clustering_analysis.core.clustering import (
    NOISE_WORDS,
    compute_entity_similarity,
    compute_info_density_similarity,
    compute_semantic_similarity,
    detect_causal_pairs,
    enrich_text,
    merge_similar_entities,
    process_clusters,
    run_hdbscan_clustering,
)

# 旧名别名：run_dbscan_clustering 已被 run_hdbscan_clustering 替代
run_dbscan_clustering = run_hdbscan_clustering

from clustering_analysis.core.embeddings import batch_embed, call_llm_for_entity
