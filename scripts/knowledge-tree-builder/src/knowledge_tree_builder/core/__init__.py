"""知识树建树管线核心模块 — embedding / 增量放置 / 准入"""

from knowledge_tree_builder.core.cache_manager import (
    CacheManager,
    CacheInfo,
    DOMAIN_CACHE_NAME,
    EMBEDDING_CACHE_NAME,
    METADATA_CACHE_NAME,
    migrate_old_caches,
    get_migration_candidates,
)
from knowledge_tree_builder.core.embeddings import (
    batch_embed,
    cosine_similarity,
    cosine_similarity_matrix,
)
from knowledge_tree_builder.core.incremental import (
    dedup_before_insert,
    detect_conflict,
    local_q,
    compute_subject_offset,
)
from knowledge_tree_builder.core.admission import filter_knowledge_points
from knowledge_tree_builder.core.extractor import extract_knowledge_points

__all__ = [
    "batch_embed",
    "cosine_similarity",
    "cosine_similarity_matrix",
    "dedup_before_insert",
    "detect_conflict",
    "local_q",
    "compute_subject_offset",
    "filter_knowledge_points",
    "extract_knowledge_points",
]
