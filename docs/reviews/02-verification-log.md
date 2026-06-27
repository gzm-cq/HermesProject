# Verification Log — memory/knowledge review

> **文档状态：历史验证日志 / 过程快照**  
> 本文包含修复过程中的失败输出，保留作证据链，不代表最终状态。最终审计见 `03-post-fix-audit-2026-06-15.md`。


Generated: 2026-06-15T08:40:24+08:00

## Import smoke
imports ok

## Shell syntax: daily-learn
daily_learn.sh syntax ok

## Test: knowledge-navigation
........................................................................ [ 83%]
..............                                                           [100%]
86 passed in 6.37s

## Test: knowledge-tree-plugin
.................FF..................                                    [100%]
=================================== FAILURES ===================================
________________ test_get_cached_adapter_reuses_healthy_adapter ________________
tests/test_public_api.py:10: in test_get_cached_adapter_reuses_healthy_adapter
    public_api._adapter_cache.clear()
E   AttributeError: module 'knowledge_tree_plugin.public_api' has no attribute '_adapter_cache'
_____________ test_get_cached_adapter_recreates_unhealthy_adapter ______________
tests/test_public_api.py:23: in test_get_cached_adapter_recreates_unhealthy_adapter
    public_api._adapter_cache.clear()
E   AttributeError: module 'knowledge_tree_plugin.public_api' has no attribute '_adapter_cache'
=========================== short test summary info ============================
FAILED tests/test_public_api.py::test_get_cached_adapter_reuses_healthy_adapter
FAILED tests/test_public_api.py::test_get_cached_adapter_recreates_unhealthy_adapter
2 failed, 35 passed in 0.41s

## Test: knowledge-tree-builder
........................................................................ [ 27%]
........................................................................ [ 54%]
........................................................................ [ 81%]
.................................................                        [100%]
265 passed in 6.28s

## Test: clustering-analysis-v3
..............FF..F.F..F...............................................  [100%]
=================================== FAILURES ===================================
__________ TestRunHDBSCANClustering.test_clustering_with_clear_groups __________
tests/test_clustering.py:146: in test_clustering_with_clear_groups
    labels, probs, silhouette = run_hdbscan_clustering(embeddings, min_cluster_size=2)
src/clustering_analysis/core/clustering.py:489: in run_hdbscan_clustering
    raise ImportError("HDBSCAN is not available. Please install scikit-learn >= 1.3.")
E   ImportError: HDBSCAN is not available. Please install scikit-learn >= 1.3.
----------------------------- Captured stdout call -----------------------------

[Phase 4] HDBSCAN 聚类...
___________ TestRunHDBSCANClustering.test_random_data_produces_noise ___________
tests/test_clustering.py:159: in test_random_data_produces_noise
    labels, probs, silhouette = run_hdbscan_clustering(embeddings, min_cluster_size=3)
src/clustering_analysis/core/clustering.py:489: in run_hdbscan_clustering
    raise ImportError("HDBSCAN is not available. Please install scikit-learn >= 1.3.")
E   ImportError: HDBSCAN is not available. Please install scikit-learn >= 1.3.
----------------------------- Captured stdout call -----------------------------

[Phase 4] HDBSCAN 聚类...
_______________ TestConvertLLMCausalPairs.test_basic_conversion ________________
tests/test_clustering.py:243: in test_basic_conversion
    assert links[0]["weight"] == 0.85
E   assert 0.7 == 0.85
_________ TestConvertLLMCausalPairs.test_out_of_bounds_indices_skipped _________
tests/test_clustering.py:265: in test_out_of_bounds_indices_skipped
    assert len(links) == 1
E   assert 0 == 1
E    +  where 0 = len([])
_____________ TestConvertLLMCausalPairs.test_non_dict_pair_skipped _____________
tests/test_clustering.py:292: in test_non_dict_pair_skipped
    assert len(links) == 1
E   assert 0 == 1
E    +  where 0 = len([])
=============================== warnings summary ===============================
tests/test_clustering.py::TestComputeSemanticSimilarity::test_basic_computation
  /mnt/d/HermesProject/scripts/clustering-analysis-v3/tests/test_clustering.py:79: DeprecationWarning: compute_semantic_similarity is deprecated and will be removed in a future version.
    sim = compute_semantic_similarity(embeddings, use_gpu=False)

tests/test_clustering.py::TestComputeSemanticSimilarity::test_range_between_minus_one_and_one
  /mnt/d/HermesProject/scripts/clustering-analysis-v3/tests/test_clustering.py:87: DeprecationWarning: compute_semantic_similarity is deprecated and will be removed in a future version.
    sim = compute_semantic_similarity(embeddings, use_gpu=False)

tests/test_clustering.py::TestComputeEntitySimilarity::test_jaccard_similarity
  /mnt/d/HermesProject/scripts/clustering-analysis-v3/tests/test_clustering.py:102: DeprecationWarning: compute_entity_similarity is deprecated and will be removed in a future version.
    sim = compute_entity_similarity(unit_entity_sets, use_gpu=False)

tests/test_clustering.py::TestComputeEntitySimilarity::test_empty_entities
  /mnt/d/HermesProject/scripts/clustering-analysis-v3/tests/test_clustering.py:113: DeprecationWarning: compute_entity_similarity is deprecated and will be removed in a future version.
    sim = compute_entity_similarity(unit_entity_sets, use_gpu=False)

tests/test_clustering.py::TestComputeInfoDensitySimilarity::test_basic_computation
  /mnt/d/HermesProject/scripts/clustering-analysis-v3/tests/test_clustering.py:124: DeprecationWarning: compute_info_density_similarity is deprecated and will be removed in a future version.
    sim = compute_info_density_similarity(texts)

tests/test_clustering.py::TestComputeInfoDensitySimilarity::test_symmetric
  /mnt/d/HermesProject/scripts/clustering-analysis-v3/tests/test_clustering.py:132: DeprecationWarning: compute_info_density_similarity is deprecated and will be removed in a future version.
    sim = compute_info_density_similarity(texts)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/test_clustering.py::TestRunHDBSCANClustering::test_clustering_with_clear_groups
FAILED tests/test_clustering.py::TestRunHDBSCANClustering::test_random_data_produces_noise
FAILED tests/test_clustering.py::TestConvertLLMCausalPairs::test_basic_conversion
FAILED tests/test_clustering.py::TestConvertLLMCausalPairs::test_out_of_bounds_indices_skipped
FAILED tests/test_clustering.py::TestConvertLLMCausalPairs::test_non_dict_pair_skipped
5 failed, 66 passed, 6 warnings in 1.33s

## Test: memory-cleanup
........................................................................ [ 58%]
...................................................                      [100%]
123 passed in 8.96s

## Test: self-evolving
>           result = operator.execute(
                candidate_content=content,
                failure_patterns=["return None multiple times", "redundant checks"],
            )

tests/test_operators.py:337: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/self_evolving/operators/refinement.py:352: in execute
    risk_report = self.scan_risks(current_content)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <self_evolving.operators.refinement.RefinementOperator object at 0x78e68652faf0>
content = '\ndef process_data(data):\n    # Step 1: Validate input\n    if data is None:\n        return None\n    if data == ""...\n    result = []\n    for item in data:\n        result.append(item)\n    \n    # Step 3: Return\n    return result\n'
failure_patterns = None

    def scan_risks(self, content: str,
                   failure_patterns: List[str] = None) -> RiskReport:
        risk_factors = []
    
        # LLM-based risk scanning
        if self.config.risk_scanning_enabled and len(content) > 200:
            prompt = RISK_SCAN_PROMPT.format(
                content=content[:self.config.max_input_length],
            )
            data = self._call_llm_json([
                {"role": "system", "content": "你是代码安全与质量审查专家。"},
                {"role": "user", "content": prompt},
            ])
            for rf in data.get("risk_factors", []):
                try:
                    category = RiskCategory(rf.get("category", "unknown"))
                except ValueError:
                    category = RiskCategory.UNKNOWN
                try:
                    severity = RiskLevel(rf.get("severity", "low"))
                except ValueError:
                    severity = RiskLevel.LOW
                risk_factors.append(RiskFactor(
                    category=category,
                    description=rf.get("description", ""),
                    severity=severity,
                    likelihood=float(rf.get("likelihood", 0.5)),
                    impact=float(rf.get("impact", 0.5)),
                ))
    
        # Fallback: keyword matching for quick patterns
        if not risk_factors:
            content_lower = content.lower()
            quick_patterns = {
                "bare except": (RiskCategory.SYNTAX, "Bare except clause", RiskLevel.MEDIUM),
            }
            for pattern, (cat, desc, sev) in quick_patterns.items():
                if pattern in content_lower:
                    risk_factors.append(RiskFactor(
                        category=cat, description=desc,
                        severity=sev, likelihood=0.5, impact=0.5,
                    ))
    
        # Check custom failure patterns
        patterns = failure_patterns or self.failure_pattern_db
        for pattern in patterns:
            if pattern.lower() in content.lower():
                risk_factors.append(RiskFactor(
                    category=RiskCategory.UNKNOWN,
                    description=f"Custom failure pattern matched: {pattern}",
                    severity=RiskLevel.HIGH, likelihood=0.7, impact=0.6,
                ))
    
        # Calculate overall risk
        if not risk_factors:
            overall_risk = RiskLevel.NONE
            risk_score = 0.0
        else:
>           max_sev = max(f.severity for f in risk_factors)
E           TypeError: '>' not supported between instances of 'RiskLevel' and 'RiskLevel'

src/self_evolving/operators/refinement.py:293: TypeError
=========================== short test summary info ============================
FAILED tests/test_operators.py::TestRefinementOperator::test_refinement_execute
1 failed, 46 passed in 31.74s
