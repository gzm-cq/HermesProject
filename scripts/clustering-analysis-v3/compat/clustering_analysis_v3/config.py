"""兼容 shim：clustering_analysis_v3.config → clustering_analysis.config"""
from clustering_analysis.config import AppConfig, load_config

# 旧名别名（旧代码中可能使用 ClusteringConfig）
from clustering_analysis.config import AppConfig as ClusteringConfig
