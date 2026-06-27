"""向后兼容 shim：clustering_analysis_v3 → clustering_analysis

此包在未来版本中将被移除。请使用 clustering_analysis 代替。
"""
import warnings

warnings.warn(
    "Package 'clustering_analysis_v3' is deprecated, use 'clustering_analysis' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from clustering_analysis import (
    AppConfig,
    batch_embed,
    detect_causal_pairs,
    enrich_text,
    run_dbscan_clustering,
)
