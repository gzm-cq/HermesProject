"""分类器单元测试"""

from unittest.mock import MagicMock

import pytest

from memory_cleanup.core.classifier import (
    AUTO_REMOVE_PATTERNS,
    calc_remove_candidates,
    classify_all,
    validate_compress_quality,
    validate_hindsight_quality,
    validate_merge_quality,
)


class TestCalcRemoveCandidates:
    """测试 calc_remove_candidates 分拣逻辑。"""

    def test_empty_entries(self) -> None:
        direct, need_v2 = calc_remove_candidates([], {"remove": [], "merge": [], "compress": []})
        assert direct == []
        assert need_v2 == []

    def test_empty_section_is_direct(self, sample_entries: list[str]) -> None:
        """空条目（仅§）应直接删除。"""
        result = {"remove": [{"index": 6, "原因": "空条目"}], "merge": [], "compress": []}
        direct, need_v2 = calc_remove_candidates(sample_entries, result)
        assert len(direct) == 1
        assert direct[0]["index"] == 6
        assert need_v2 == []

    def test_auto_pattern_is_direct(self, sample_entries: list[str]) -> None:
        """包含 AUTO_REMOVE_PATTERNS 关键词的条目应直接删除。"""
        # index=7 包含 "清理" 和 "V5" 关键词
        result = {"remove": [{"index": 7, "原因": "清理流程记录"}], "merge": [], "compress": []}
        direct, need_v2 = calc_remove_candidates(sample_entries, result)
        assert len(direct) == 1
        assert need_v2 == []

    def test_business_data_needs_verify(self, sample_entries: list[str]) -> None:
        """业务数据类条目需要 Phase 2 验证。"""
        result = {"remove": [{"index": 2, "原因": "业务数据"}], "merge": [], "compress": []}
        direct, need_v2 = calc_remove_candidates(sample_entries, result)
        assert direct == []
        assert len(need_v2) == 1
        assert need_v2[0]["index"] == 2

    def test_merged_index_is_direct(self, sample_entries: list[str]) -> None:
        """已被 merge 覆盖的索引应直接删除。"""
        result = {
            "remove": [{"index": 0, "原因": "已合并"}],
            "merge": [{"indices": [0, 5], "合并为": "网络配置合并"}],
            "compress": [],
        }
        direct, need_v2 = calc_remove_candidates(sample_entries, result)
        assert any(r["index"] == 0 for r in direct)

    def test_mixed_candidates(self, sample_entries: list[str]) -> None:
        """混合情况：部分直接删，部分需验证。"""
        result = {
            "remove": [
                {"index": 2, "原因": "业务数据"},   # need_v2
                {"index": 4, "原因": "论文信息"},   # need_v2
                {"index": 6, "原因": "空条目"},    # direct (§)
                {"index": 7, "原因": "清理记录"},   # direct (AUTO_PATTERN)
            ],
            "merge": [],
            "compress": [],
        }
        direct, need_v2 = calc_remove_candidates(sample_entries, result)
        assert len(direct) == 2
        assert len(need_v2) == 2
        direct_indices = {r["index"] for r in direct}
        v2_indices = {r["index"] for r in need_v2}
        assert direct_indices == {6, 7}
        assert v2_indices == {2, 4}


class TestAutoRemovePatterns:
    """测试 AUTO_REMOVE_PATTERNS 常量。"""

    def test_contains_expected_patterns(self) -> None:
        assert "清理" in AUTO_REMOVE_PATTERNS
        assert "V6" in AUTO_REMOVE_PATTERNS
        assert "方法论" in AUTO_REMOVE_PATTERNS
        assert "memory cleanup pipeline" in AUTO_REMOVE_PATTERNS

    def test_patterns_are_strings(self) -> None:
        assert all(isinstance(p, str) for p in AUTO_REMOVE_PATTERNS)


class TestClassifyAll:
    """测试 classify_all 并行分批逻辑。"""

    def test_empty_entries(self, mock_llm_client: MagicMock) -> None:
        result = classify_all([], "MEMORY", mock_llm_client)
        assert result == {"merge": [], "remove": [], "compress": [], "hindsight": [], "flagged": []}
        mock_llm_client.classify_batch.assert_not_called()

    def test_single_outer_batch_splits_into_sub_batches(self, sample_entries: list[str], mock_llm_client: MagicMock) -> None:
        """10 条条目、batch_size=20 时仍按 5 条子批拆成 2 次 LLM 调用。"""
        result = classify_all(sample_entries, "MEMORY", mock_llm_client, batch_size=20, max_workers=2)
        assert "merge" in result
        assert "remove" in result
        assert "compress" in result
        assert mock_llm_client.classify_batch.call_count == 2

    def test_multiple_batches(self, sample_entries: list[str], mock_llm_client: MagicMock) -> None:
        """batch_size=3 应拆分为多批调用。"""
        result = classify_all(sample_entries, "MEMORY", mock_llm_client, batch_size=3, max_workers=2)
        assert mock_llm_client.classify_batch.call_count > 1
        assert "remove" in result

    def test_dedup_remove(self, mock_llm_client: MagicMock) -> None:
        """多批返回相同索引时应去重。"""
        mock_llm_client.classify_batch.return_value = {
            "merge": [],
            "remove": [{"index": 0, "原因": "重复"}],
            "compress": [],
        }
        entries = ["条目A", "条目B", "条目C", "条目D", "条目E"]
        result = classify_all(entries, "MEMORY", mock_llm_client, batch_size=2, max_workers=2)
        # 每批都返回 index=0，最终结果中应只有一条
        remove_indices = [r["index"] for r in result["remove"]]
        assert remove_indices.count(0) == 1

    def test_llm_error_is_skipped(self, mock_llm_client: MagicMock) -> None:
        """LLM 返回 error 时该批跳过，不影响其他批次。"""
        mock_llm_client.classify_batch.return_value = {"error": "连接失败"}
        entries = ["条目A", "条目B"]
        result = classify_all(entries, "MEMORY", mock_llm_client, batch_size=10, max_workers=1)
        assert result["remove"] == []
        assert result["merge"] == []
        assert result["compress"] == []

    def test_vote_mode_remove_union(self, mock_llm_client: MagicMock) -> None:
        """多轮投票中 remove 取并集，避免不同轮次候选被交集清空。"""
        entries = ["条目A", "条目B", "条目C"]
        mock_llm_client.classify_batch.side_effect = [
            {"merge": [], "remove": [{"index": 0, "原因": "过期"}, {"index": 1, "原因": "重复"}], "compress": []},
            {"merge": [], "remove": [{"index": 1, "原因": "重复"}], "compress": []},
        ]
        result = classify_all(entries, "MEMORY", mock_llm_client, batch_size=10, max_workers=1, vote_count=2)
        remove_indices = {r["index"] for r in result["remove"]}
        assert remove_indices == {0, 1}

    def test_vote_mode_remove_union_single_round_candidate(self, mock_llm_client: MagicMock) -> None:
        """只被一轮标记的 remove 候选也应保留给 Phase 2 验证。"""
        entries = ["条目A"]
        mock_llm_client.classify_batch.side_effect = [
            {"merge": [], "remove": [{"index": 0, "原因": "过时"}], "compress": []},
            {"merge": [], "remove": [], "compress": []},
        ]
        result = classify_all(entries, "MEMORY", mock_llm_client, batch_size=10, max_workers=1, vote_count=2)
        remove_indices = {r["index"] for r in result["remove"]}
        assert remove_indices == {0}


class TestValidateMergeQuality:
    """测试 validate_merge_quality 校验函数。"""

    def test_valid_abstract_merge_passes(self) -> None:
        """真正的抽象合并应通过校验。"""
        entries = [
            "LiteLLM 网关 地址 配置 完成",
            "PG 数据库 连接 配置 完成",
        ]
        merge_list = [{"indices": [0, 1], "合并为": "API 网关 和 数据库 配置 完成"}]
        result = validate_merge_quality(entries, merge_list)
        assert len(result) == 1

    def test_concat_merge_is_rejected(self, sample_entries: list[str]) -> None:
        """简单拼接的 merge 应被过滤。"""
        merge_list = [{"indices": [0, 5], "合并为": "LiteLLM 网关地址: http://127.0.0.1:4142\n\nPG 端口: 5434，数据库名: hindsight"}]
        result = validate_merge_quality(sample_entries, merge_list)
        assert len(result) == 0

    def test_date_in_merged_is_rejected(self, sample_entries: list[str]) -> None:
        """所有原文均含日期但合并后仍含日期 → 应过滤。"""
        # index=2 含 "2026-05-01"，index=9 含 "2026-Q1"
        merge_list = [{"indices": [2, 9], "合并为": "2026年5月至Q2已完成MES迁移和上线"}]
        result = validate_merge_quality(sample_entries, merge_list)
        assert len(result) == 0

    def test_small_batch_merge_passes_with_partial_overlap(self) -> None:
        """≤3 条目的 merge，只要任一原文关键词重叠就放行。"""
        entries = [
            "节点 配置 已完成 并且 验证 通过",
            "项目 B 部署 成功 运行 环境",
            "C 版本 更新 补丁 修复",
        ]
        # avg_coverage=0.11 < 0.15 但 max=0.33 > 0，因 ≤3 条应放行
        merge_list = [{"indices": [0, 1, 2], "合并为": "验证 通过"}]
        result = validate_merge_quality(entries, merge_list)
        assert len(result) == 1

    def test_large_batch_merge_still_requires_avg(self) -> None:
        """>3 条目的 merge 仍需 avg ≥ 0.15。"""
        entries = [
            "节点 配置 已完成 并且 验证 通过",
            "项目 B 部署 成功 运行 环境",
            "C 版本 更新 补丁 修复",
            "D 模块 调试 日志 输出",
        ]
        # avg_coverage=0.08 < 0.15，应拒绝
        merge_list = [{"indices": [0, 1, 2, 3], "合并为": "验证 通过"}]
        result = validate_merge_quality(entries, merge_list)
        assert len(result) == 0

    def test_merge_passes_with_english_keywords(self) -> None:
        """纯英文条目的 merge 质量校验应使用英文单词回退。"""
        entries = [
            "memory cleanup pipeline after long discussion",
            "pipeline cleanup and testing done",
        ]
        # ≤3 条目，max 覆盖率 > 0 应放行
        merge_list = [{"indices": [0, 1], "合并为": "cleanup pipeline done"}]
        result = validate_merge_quality(entries, merge_list)
        assert len(result) == 1

    def test_merge_rejects_english_no_overlap(self) -> None:
        """纯英文 merge 无英文单词重叠应被过滤。"""
        entries = [
            "memory cleanup pipeline",
            "completely different unrelated text",
        ]
        merge_list = [{"indices": [0, 1], "合并为": "something else entirely"}]
        result = validate_merge_quality(entries, merge_list)
        assert len(result) == 0

    def test_merge_passes_with_mixed_cn_en_keywords(self) -> None:
        """中英文混合条目的 merge 关键词检查工作正常。"""
        entries = [
            "配置 cleanup pipeline 已完成",
            "部署 testing 环境 完成",
        ]
        merge_list = [{"indices": [0, 1], "合并为": "cleanup testing 配置 完成"}]
        result = validate_merge_quality(entries, merge_list)
        assert len(result) == 1

    def test_empty_merge_list(self) -> None:
        result = validate_merge_quality(["条目A"], [])
        assert result == []


class TestValidateCompressQuality:
    """测试 validate_compress_quality 校验函数。"""

    def test_valid_compress_preserves_entities(self, sample_entries: list[str]) -> None:
        """保留关键实体的压缩应通过。"""
        compress_list = [{"index": 5, "精简为": "PG 端口 5434，库 hindsight"}]
        result = validate_compress_quality(sample_entries, compress_list)
        assert len(result) == 1

    def test_too_short_compress_is_rejected(self, sample_entries: list[str]) -> None:
        """过短的压缩应被过滤。"""
        compress_list = [{"index": 3, "精简为": "不要并发"}]
        result = validate_compress_quality(sample_entries, compress_list)
        assert len(result) == 0

    def test_entity_dropped_compress_is_rejected(self, sample_entries: list[str]) -> None:
        """遗漏 IP/URL/端口的压缩应被过滤。"""
        compress_list = [{"index": 0, "精简为": "LiteLLM 网关地址已配置"}]
        result = validate_compress_quality(sample_entries, compress_list)
        assert len(result) == 0

    def test_compress_allows_partial_path_loss(self) -> None:
        """部分路径遗漏（1/2=50%）但关键实体保留的压缩应通过。"""
        entries = [
            "路径 /home/user/project，版本 v1.2.0",
        ]
        # 压缩后保留了版本号但省略了路径（遗漏率 50% ≤ 70%）
        compress_list = [{"index": 0, "精简为": "版本 v1.2.0，已 配置"}]
        result = validate_compress_quality(entries, compress_list)
        assert len(result) == 1

    def test_compress_allows_high_noncritical_loss(self) -> None:
        """非关键实体遗漏率 67%（在 50%~70% 区间内）但关键实体保留应通过。"""
        entries = [
            "路径 /var/log，路径 /home/project，版本 v2.0.0",
        ]
        # 关键实体 v2.0 保留；非关键 2 条路径全部丢失 = 遗漏率 2/3≈67% ≤ 70%
        compress_list = [{"index": 0, "精简为": "版本 v2.0.0 已 部署"}]
        result = validate_compress_quality(entries, compress_list)
        assert len(result) == 1

    def test_compress_rejects_excessive_noncritical_loss(self) -> None:
        """非关键实体遗漏率 >70% 应被过滤。"""
        entries = [
            "路径 /a/b，路径 /c/d，路径 /e/f，路径 /g/h，路径 /i/j",
        ]
        # 无关键实体；5 条路径全部丢失 = 5/5=100% > 70%
        compress_list = [{"index": 0, "精简为": "已 配置 完成"}]
        result = validate_compress_quality(entries, compress_list)
        assert len(result) == 0

    def test_compress_rejects_critical_entity_loss(self) -> None:
        """遗漏关键实体（IP/端口）的压缩应被过滤。"""
        entries = [
            "服务 地址 127.0.0.1:8080，路径 /home/user/project",
        ]
        # 压缩后去掉了 IP 和端口
        compress_list = [{"index": 0, "精简为": "路径 /home/user/project"}]
        result = validate_compress_quality(entries, compress_list)
        assert len(result) == 0

    def test_empty_compress_list(self) -> None:
        result = validate_compress_quality(["条目A"], [])
        assert result == []

    def test_compress_quality_with_english_keywords(self) -> None:
        """纯英文条目的 compress 质量校验应使用英文单词回退。"""
        entries = [
            "memory cleanup pipeline after long discussion",
        ]
        # 压缩版保留了 memory 和 cleanup，英文单词覆盖率 2/6=33% ≥ 20%
        compress_list = [{"index": 0, "精简为": "use memory cleanup"}]
        result = validate_compress_quality(entries, compress_list)
        assert len(result) == 1

    def test_compress_rejects_pure_english_no_overlap(self) -> None:
        """纯英文条目压缩后无英文单词重叠应被过滤。"""
        entries = [
            "memory cleanup pipeline after long discussion",
        ]
        # 压缩版完全换用新词，无重叠
        compress_list = [{"index": 0, "精简为": "completely different text here now"}]
        result = validate_compress_quality(entries, compress_list)
        assert len(result) == 0

    # ── USER 专用 compress 质量测试 ──

    def test_user_compress_chinese_bigram_passes(self) -> None:
        """USER 中文条目重述后 bigram overlap ≥ 10% 应通过。"""
        entries = [
            "用户偏好项目计划拆解到月级别，制定实施计划，需要定期汇报",
        ]
        # LLM 合理重述：改变了词语分组但保留了核心字符
        compress_list = [{"index": 0, "精简为": "用户习惯：按月拆解计划，定期汇报"}]
        result = validate_compress_quality(entries, compress_list, source="USER")
        assert len(result) == 1

    def test_user_compress_no_savings_rejected(self) -> None:
        """USER 压缩后几乎无节省（测试 57→57 场景）应被过滤。"""
        entries = [
            "用户偏好面向对象编程",
        ]
        # 仅替换最后一个字，长度不变（11→11），违反节省检查
        compress_list = [{"index": 0, "精简为": "用户偏好面向对象编制"}]
        result = validate_compress_quality(entries, compress_list, source="USER")
        assert len(result) == 0

    def test_user_compress_mixed_cn_en_passes(self) -> None:
        """USER 中英文混合条目通过 bigram + 英文单词检查。"""
        entries = [
            "用户要求 cleanup pipeline 的 testing 以并行方式执行",
        ]
        # 压缩后保留 cleanup 和 testing 英文词 + 部分中文 bigram
        compress_list = [{"index": 0, "精简为": "cleanup testing 并行执行"}]
        result = validate_compress_quality(entries, compress_list, source="USER")
        assert len(result) == 1

    def test_user_compress_no_overlap_rejected(self) -> None:
        """USER 中英文条目完全无关键词重叠应被过滤。"""
        entries = [
            "用户要求在项目交付前完成所有文档评审",
        ]
        # 完全换词
        compress_list = [{"index": 0, "精简为": "Test driven delivery approach"}]
        result = validate_compress_quality(entries, compress_list, source="USER")
        assert len(result) == 0

    def test_user_compress_short_entry_still_valid(self) -> None:
        """USER 短条目有效压缩（从 17→10 字符）应通过。"""
        entries = [
            "用户偏好结论先行并列出所有核心要点",
        ]
        # 有实际节省（10 < 17*0.95）且保留关键词（bigram overlap ≥10%）
        compress_list = [{"index": 0, "精简为": "结论先行，列核心要点"}]
        result = validate_compress_quality(entries, compress_list, source="USER")
        assert len(result) == 1


class TestValidateHindsightQuality:
    """测试 validate_hindsight_quality 校验函数。"""

    def test_valid_hindsight_with_tags(self) -> None:
        """有合法 index 和关键词标签的 hindsight 应通过。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入",
        ]
        hindsight_list = [
            {"index": 0, "关键词": ["审计", "methodology", "风险检查"]},
        ]
        result = validate_hindsight_quality(entries, hindsight_list)
        assert len(result) == 1
        assert result[0]["index"] == 0

    def test_invalid_hindsight_no_tags(self) -> None:
        """缺少关键词字段的 hindsight 应被过滤。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入",
        ]
        hindsight_list = [
            {"index": 0},  # 缺 "关键词"
        ]
        result = validate_hindsight_quality(entries, hindsight_list)
        assert len(result) == 0

    def test_invalid_hindsight_empty_tags(self) -> None:
        """关键词列表为空的 hindsight 应被过滤。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入",
        ]
        hindsight_list = [
            {"index": 0, "关键词": []},
        ]
        result = validate_hindsight_quality(entries, hindsight_list)
        assert len(result) == 0

    def test_invalid_hindsight_too_short(self) -> None:
        """条目长度 < 20 字符的 hindsight 应被过滤。"""
        entries = [
            "短条目",
        ]
        hindsight_list = [
            {"index": 0, "关键词": ["简短"]},
        ]
        result = validate_hindsight_quality(entries, hindsight_list)
        assert len(result) == 0

    def test_hindsight_prevents_double_remove(self) -> None:
        """compress 与 hindsight 冲突时 compress 优先，hindsight index 不计入 need_v2。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度",
            "设计哲学：Data Flywheel 架构遵循注意力机制与分域原则",
        ]
        result = {
            "remove": [{"index": 0, "原因": "被合并覆盖"}],
            "merge": [],
            "compress": [{"index": 1, "精简为": "设计哲学：Data Flywheel + 注意力机制"}],
            "hindsight": [{"index": 1, "关键词": ["设计哲学", "架构"]}],  # 与 compress 冲突
        }
        direct, need_v2 = calc_remove_candidates(entries, result)
        # compress index=1 应已标记为 remove，不进入 need_v2
        assert need_v2 == []

    def test_empty_hindsight_list(self) -> None:
        """空 hindsight 列表应返回空。"""
        entries = ["审计方法论：采用10+轮递进检查模式"]
        result = validate_hindsight_quality(entries, [])
        assert result == []
