"""Knowledge Tree Builder — 知识分域建树管线"""

__version__ = "0.1.0"

# ========== 公共 API ==========
# 供 plugins/knowledge-tree-plugin 等外部项目导入
# 遵循 RULE-4 模块边界隔离：外部项目只能从 public API 导入

from knowledge_tree_builder.core.embeddings import batch_embed, cosine_similarity
from knowledge_tree_builder.core.extractor import extract_knowledge_points
from knowledge_tree_builder.core.admission import filter_knowledge_points
from knowledge_tree_builder.core.incremental import dedup_before_insert, detect_conflict, local_q
from knowledge_tree_builder.adapters.database import DatabaseAdapter

# Phase A: 新管线模块
from knowledge_tree_builder.models import (
    KnowledgeType,
    KNOWLEDGE_TYPE_NAMES,
    KNOWLEDGE_TYPE_LABELS,
    adjust_claims_count,
)
from knowledge_tree_builder.phase.scan import scan_input_dir
from knowledge_tree_builder.phase.analyze import analyze_article
from knowledge_tree_builder.phase.split import process_candidates

__all__ = [
    # 旧管线 API（保留）
    "batch_embed",
    "cosine_similarity",
    "extract_knowledge_points",
    "filter_knowledge_points",
    "dedup_before_insert",
    "detect_conflict",
    "local_q",
    "DatabaseAdapter",
    # Phase A: 新管线 API
    "KnowledgeType",
    "KNOWLEDGE_TYPE_NAMES",
    "KNOWLEDGE_TYPE_LABELS",
    "adjust_claims_count",
    "scan_input_dir",
    "analyze_article",
    "process_candidates",
]
