"""分类器单元测试"""

from unittest.mock import MagicMock

import pytest

from memory_cleanup.core.classifier import (
    AUTO_REMOVE_PATTERNS,
    _chinese_bigrams,
    _extract_dates,
    _extract_key_numbers,
    _extract_proper_nouns,
    backfill_hindsight_keywords,
    calc_remove_candidates,
    classify_all,
    extract_hindsight_keywords,
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
    """测试 validate_compress_quality 校验函数（非严格模式，向后兼容）。"""

    def test_valid_compress_preserves_entities(self, sample_entries: list[str]) -> None:
        """保留关键实体的压缩应通过。"""
        compress_list = [{"index": 5, "精简为": "PG 端口 5434，库 hindsight"}]
        result = validate_compress_quality(sample_entries, compress_list, strict_mode=False)
        assert len(result) == 1

    def test_too_short_compress_is_rejected(self, sample_entries: list[str]) -> None:
        """过短的压缩应被过滤。"""
        compress_list = [{"index": 3, "精简为": "不要并发"}]
        result = validate_compress_quality(sample_entries, compress_list, strict_mode=False)
        assert len(result) == 0

    def test_entity_dropped_compress_is_rejected(self, sample_entries: list[str]) -> None:
        """遗漏 IP/URL/端口的压缩应被过滤。"""
        compress_list = [{"index": 0, "精简为": "LiteLLM 网关地址已配置"}]
        result = validate_compress_quality(sample_entries, compress_list, strict_mode=False)
        assert len(result) == 0

    def test_compress_allows_partial_path_loss(self) -> None:
        """部分路径遗漏（1/2=50%）但关键实体保留的压缩应通过。"""
        entries = [
            "路径 /home/user/project，版本 v1.2.0",
        ]
        # 压缩后保留了版本号但省略了路径（遗漏率 50% ≤ 70%）
        compress_list = [{"index": 0, "精简为": "版本 v1.2.0，已 配置"}]
        result = validate_compress_quality(entries, compress_list, strict_mode=False)
        assert len(result) == 1

    def test_compress_allows_high_noncritical_loss(self) -> None:
        """非关键实体遗漏率 67%（在 50%~70% 区间内）但关键实体保留应通过。"""
        entries = [
            "路径 /var/log，路径 /home/project，版本 v2.0.0",
        ]
        # 关键实体 v2.0 保留；非关键 2 条路径全部丢失 = 遗漏率 2/3≈67% ≤ 70%
        compress_list = [{"index": 0, "精简为": "版本 v2.0.0 已 部署"}]
        result = validate_compress_quality(entries, compress_list, strict_mode=False)
        assert len(result) == 1

    def test_compress_rejects_excessive_noncritical_loss(self) -> None:
        """非关键实体遗漏率 >70% 应被过滤。"""
        entries = [
            "路径 /a/b，路径 /c/d，路径 /e/f，路径 /g/h，路径 /i/j",
        ]
        # 无关键实体；5 条路径全部丢失 = 5/5=100% > 70%
        compress_list = [{"index": 0, "精简为": "已 配置 完成"}]
        result = validate_compress_quality(entries, compress_list, strict_mode=False)
        assert len(result) == 0

    def test_compress_rejects_critical_entity_loss(self) -> None:
        """遗漏关键实体（IP/端口）的压缩应被过滤。"""
        entries = [
            "服务 地址 127.0.0.1:8080，路径 /home/user/project",
        ]
        # 压缩后去掉了 IP 和端口
        compress_list = [{"index": 0, "精简为": "路径 /home/user/project"}]
        result = validate_compress_quality(entries, compress_list, strict_mode=False)
        assert len(result) == 0

    def test_empty_compress_list(self) -> None:
        result = validate_compress_quality(["条目A"], [], strict_mode=False)
        assert result == []

    def test_compress_quality_with_english_keywords(self) -> None:
        """纯英文条目的 compress 质量校验应使用英文单词回退。"""
        entries = [
            "memory cleanup pipeline after long discussion",
        ]
        # 压缩版保留了 memory 和 cleanup，英文单词覆盖率 2/6=33% ≥ 20%
        compress_list = [{"index": 0, "精简为": "use memory cleanup"}]
        result = validate_compress_quality(entries, compress_list, strict_mode=False)
        assert len(result) == 1

    def test_compress_rejects_pure_english_no_overlap(self) -> None:
        """纯英文条目压缩后无英文单词重叠应被过滤。"""
        entries = [
            "memory cleanup pipeline after long discussion",
        ]
        # 压缩版完全换用新词，无重叠
        compress_list = [{"index": 0, "精简为": "completely different text here now"}]
        result = validate_compress_quality(entries, compress_list, strict_mode=False)
        assert len(result) == 0

    # ── USER 专用 compress 质量测试 ──

    def test_user_compress_chinese_bigram_passes(self) -> None:
        """USER 中文条目重述后 bigram overlap ≥ 10% 应通过。"""
        entries = [
            "用户偏好项目计划拆解到月级别，制定实施计划，需要定期汇报",
        ]
        # LLM 合理重述：改变了词语分组但保留了核心字符
        compress_list = [{"index": 0, "精简为": "用户习惯：按月拆解计划，定期汇报"}]
        result = validate_compress_quality(entries, compress_list, source="USER", strict_mode=False)
        assert len(result) == 1

    def test_user_compress_no_savings_rejected(self) -> None:
        """USER 压缩后几乎无节省（测试 57→57 场景）应被过滤。"""
        entries = [
            "用户偏好面向对象编程",
        ]
        # 仅替换最后一个字，长度不变（11→11），违反节省检查
        compress_list = [{"index": 0, "精简为": "用户偏好面向对象编制"}]
        result = validate_compress_quality(entries, compress_list, source="USER", strict_mode=False)
        assert len(result) == 0

    def test_user_compress_mixed_cn_en_passes(self) -> None:
        """USER 中英文混合条目通过 bigram + 英文单词检查。"""
        entries = [
            "用户要求 cleanup pipeline 的 testing 以并行方式执行",
        ]
        # 压缩后保留 cleanup 和 testing 英文词 + 部分中文 bigram
        compress_list = [{"index": 0, "精简为": "cleanup testing 并行执行"}]
        result = validate_compress_quality(entries, compress_list, source="USER", strict_mode=False)
        assert len(result) == 1

    def test_user_compress_no_overlap_rejected(self) -> None:
        """USER 中英文条目完全无关键词重叠应被过滤。"""
        entries = [
            "用户要求在项目交付前完成所有文档评审",
        ]
        # 完全换词
        compress_list = [{"index": 0, "精简为": "Test driven delivery approach"}]
        result = validate_compress_quality(entries, compress_list, source="USER", strict_mode=False)
        assert len(result) == 0

    def test_user_compress_short_entry_still_valid(self) -> None:
        """USER 短条目有效压缩（从 17→10 字符）应通过。"""
        entries = [
            "用户偏好结论先行并列出所有核心要点",
        ]
        # 有实际节省（10 < 17*0.95）且保留关键词（bigram overlap ≥10%）
        compress_list = [{"index": 0, "精简为": "结论先行，列核心要点"}]
        result = validate_compress_quality(entries, compress_list, source="USER", strict_mode=False)
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


class TestExtractDates:
    """测试 _extract_dates 辅助函数。"""

    def test_extract_yyyy_mm_dd(self) -> None:
        """提取 YYYY-MM-DD 格式日期。"""
        text = "项目于 2026-06-28 完成部署"
        dates = _extract_dates(text)
        assert "2026-06-28" in dates

    def test_extract_yyyy_slash_mm_slash_dd(self) -> None:
        """提取 YYYY/MM/DD 格式日期。"""
        text = "截止日期 2026/06/28"
        dates = _extract_dates(text)
        assert "2026/06/28" in dates

    def test_extract_mm_month_dd_day(self) -> None:
        """提取 MM月DD日 格式日期。"""
        text = "6月28日是星期日"
        dates = _extract_dates(text)
        assert "6月28日" in dates

    def test_extract_multiple_dates(self) -> None:
        """提取多种格式混合。"""
        text = "2026-01-01 开始，2026/03/15 结束，6月28日完成"
        dates = _extract_dates(text)
        assert "2026-01-01" in dates
        assert "2026/03/15" in dates
        assert "6月28日" in dates

    def test_no_dates(self) -> None:
        """无日期时返回空集合。"""
        text = "这是一段普通文本"
        dates = _extract_dates(text)
        assert dates == set()


class TestExtractKeyNumbers:
    """测试 _extract_key_numbers 辅助函数。"""

    def test_extract_number_with_english_unit(self) -> None:
        """提取带英文单位的数字。"""
        text = "延迟约 100ms 响应时间"
        numbers = _extract_key_numbers(text)
        assert "100ms" in numbers

    def test_extract_number_with_chinese_unit(self) -> None:
        """提取带中文单位的数字。"""
        text = "共迁移 5000条 记录"
        numbers = _extract_key_numbers(text)
        assert "5000条" in numbers

    def test_extract_decimal_number(self) -> None:
        """提取小数。"""
        text = "精度为 3.14 圆周率"
        numbers = _extract_key_numbers(text)
        assert "3.14" in numbers

    def test_extract_percent(self) -> None:
        """提取百分比。"""
        text = "成功率 95%"
        numbers = _extract_key_numbers(text)
        assert "95%" in numbers

    def test_no_key_numbers(self) -> None:
        """无数字时返回空集合。"""
        text = "纯中文文本没有数字"
        numbers = _extract_key_numbers(text)
        assert numbers == set()


class TestExtractProperNouns:
    """测试 _extract_proper_nouns 辅助函数。"""

    def test_extract_python(self) -> None:
        """提取 Python 等专有名词。"""
        text = "使用 Python 进行开发"
        nouns = _extract_proper_nouns(text)
        assert "Python" in nouns

    def test_extract_postgresql(self) -> None:
        """提取 PostgreSQL。"""
        text = "数据库使用 PostgreSQL"
        nouns = _extract_proper_nouns(text)
        assert "PostgreSQL" in nouns

    def test_extract_hdbscan(self) -> None:
        """提取 HDBSCAN。"""
        text = "聚类算法 HDBSCAN"
        nouns = _extract_proper_nouns(text)
        assert "HDBSCAN" in nouns

    def test_skip_short_words(self) -> None:
        """跳过长度小于 4 的词。"""
        text = "Use SQL DB"
        nouns = _extract_proper_nouns(text)
        assert nouns == set()

    def test_no_proper_nouns(self) -> None:
        """无专有名词时返回空集合。"""
        text = "全是小写字母 python java"
        nouns = _extract_proper_nouns(text)
        assert nouns == set()


class TestStrictModeCompressQuality:
    """测试严格模式下的 compress 质量校验。"""

    def test_strict_mode_lower_compression_ratio_memory(self) -> None:
        """严格模式下 MEMORY 压缩比上限更严格（8.0 vs 12.0）。"""
        original = "项目配置部署测试完成 系统升级数据迁移 性能优化安全审计 监控告警日志分析 容灾备份故障恢复" * 4
        compressed = "项目配置部署测试完成 系统升级数据迁移"
        ratio = len(original) / len(compressed)
        assert 8.0 < ratio < 12.0
        entries = [original]
        compress_list = [{"index": 0, "精简为": compressed}]
        result_strict = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        result_non_strict = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=False)
        assert len(result_strict) == 0
        assert len(result_non_strict) == 1

    def test_strict_mode_lower_compression_ratio_user(self) -> None:
        """严格模式下 USER 压缩比上限更严格（5.0 vs 12.0）。"""
        original = "用户偏好项目计划拆解到月级别制定实施计划需要定期汇报进度并且总结复盘" * 3
        compressed = "用户偏好项目计划拆解"
        ratio = len(original) / len(compressed)
        assert 5.0 < ratio < 12.0
        entries = [original]
        compress_list = [{"index": 0, "精简为": compressed}]
        result_strict = validate_compress_quality(entries, compress_list, source="USER", strict_mode=True)
        result_non_strict = validate_compress_quality(entries, compress_list, source="USER", strict_mode=False)
        assert len(result_strict) == 0
        assert len(result_non_strict) == 1

    def test_strict_mode_higher_keyword_overlap_memory(self) -> None:
        """严格模式下 MEMORY 关键词重叠要求更高（0.30 vs 0.20）。"""
        original = "项目配置 部署测试 验证完成 项目管理 系统升级 性能优化 安全审计 监控告警 日志分析 容灾备份 故障恢复"
        compressed = "项目配置 部署测试 验证完成"
        orig_kw = 11
        overlap_kw = 3
        overlap = overlap_kw / orig_kw
        assert 0.20 <= overlap < 0.30
        entries = [original]
        compress_list = [{"index": 0, "精简为": compressed}]
        result_strict = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        result_non_strict = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=False)
        assert len(result_strict) == 0
        assert len(result_non_strict) == 1

    def test_strict_mode_higher_keyword_overlap_user(self) -> None:
        """严格模式下 USER 关键词重叠要求更高（0.25 vs 0.10）。"""
        entries = ["用户偏好项目计划拆解到月级别，制定实施计划，需要定期汇报进度"]
        compress_list = [{"index": 0, "精简为": "用户习惯：按月拆解计划"}]
        result_strict = validate_compress_quality(entries, compress_list, source="USER", strict_mode=True)
        result_non_strict = validate_compress_quality(entries, compress_list, source="USER", strict_mode=False)
        assert len(result_strict) == 0
        assert len(result_non_strict) == 1

    def test_non_strict_mode_backward_compatible(self) -> None:
        """非严格模式下使用原有阈值，保持向后兼容。"""
        entries = ["LiteLLM 网关地址: http://127.0.0.1:4142"]
        compress_list = [{"index": 0, "精简为": "LiteLLM 网关地址已配置"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=False)
        assert len(result) == 0


class TestDateRetentionCheck:
    """测试日期保留检查（严格模式）。"""

    def test_date_retained_passes(self) -> None:
        """日期全部保留应通过。"""
        entries = ["项目部署 2026-06-28 完成部署 系统上线 数据迁移 性能优化"]
        compress_list = [{"index": 0, "精简为": "项目部署 2026-06-28 完成 系统上线"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 1

    def test_date_missing_rejected(self) -> None:
        """日期丢失应被过滤。"""
        entries = ["项目部署 2026-06-28 完成部署 系统上线 数据迁移 性能优化"]
        compress_list = [{"index": 0, "精简为": "项目部署 完成 系统上线"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 0

    def test_chinese_date_retained(self) -> None:
        """中文日期保留应通过。"""
        entries = ["6月28日 完成上线 系统部署 数据迁移"]
        compress_list = [{"index": 0, "精简为": "6月28日 上线 系统部署"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 1

    def test_no_dates_no_check(self) -> None:
        """无日期时不触发检查。"""
        entries = ["项目部署完成 系统上线 数据迁移 性能优化 安全审计"]
        compress_list = [{"index": 0, "精简为": "项目部署 系统上线 数据迁移 性能优化"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 1

    def test_date_check_only_in_strict_mode(self) -> None:
        """非严格模式下不检查日期。"""
        entries = ["项目部署 2026-06-28 完成部署 系统上线"]
        compress_list = [{"index": 0, "精简为": "项目部署 完成 系统上线"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=False)
        assert len(result) == 1


class TestNumberRetentionCheck:
    """测试数字保留检查（严格模式）。"""

    def test_number_retained_passes(self) -> None:
        """关键数字全部保留应通过。"""
        entries = ["系统延迟 100ms，共迁移 5000条 记录，性能优化 95% 成功率"]
        compress_list = [{"index": 0, "精简为": "系统延迟 100ms，迁移 5000条 记录，性能 95%"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 1

    def test_number_missing_rejected(self) -> None:
        """关键数字丢失应被过滤。"""
        entries = ["系统延迟 100ms，共迁移 5000条 记录，性能优化"]
        compress_list = [{"index": 0, "精简为": "系统延迟 较短，迁移 记录 很多，性能优化"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 0

    def test_number_check_only_in_strict_mode(self) -> None:
        """非严格模式下不检查数字。"""
        entries = ["系统延迟 100ms，共迁移 5000条 记录"]
        compress_list = [{"index": 0, "精简为": "系统延迟 较短，迁移 记录 很多"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=False)
        assert len(result) == 1


class TestProperNounRetentionCheck:
    """测试专有名词保留检查（严格模式）。"""

    def test_proper_noun_retained_passes(self) -> None:
        """专有名词全部保留应通过。"""
        entries = ["使用 Python 语言 PostgreSQL 数据库 开发 后端服务 系统集成"]
        compress_list = [{"index": 0, "精简为": "使用 Python PostgreSQL 开发 后端服务"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 1

    def test_proper_noun_missing_rejected(self) -> None:
        """专有名词丢失应被过滤。"""
        entries = ["使用 Python 语言 PostgreSQL 数据库 开发 后端服务"]
        compress_list = [{"index": 0, "精简为": "使用编程语言 数据库 开发 后端服务"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 0

    def test_proper_noun_check_only_in_strict_mode(self) -> None:
        """非严格模式下不检查专有名词。"""
        entries = ["使用 Python 语言 PostgreSQL 数据库 开发"]
        compress_list = [{"index": 0, "精简为": "使用编程语言 数据库 开发"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=False)
        assert len(result) == 1

    def test_hdbscan_proper_noun(self) -> None:
        """HDBSCAN 等全大写专有名词也应识别。"""
        entries = ["聚类算法 HDBSCAN 效果很好 性能优化 数据处理"]
        compress_list = [{"index": 0, "精简为": "HDBSCAN 聚类效果好 性能优化"}]
        result = validate_compress_quality(entries, compress_list, source="MEMORY", strict_mode=True)
        assert len(result) == 1


class TestExtractHindsightKeywords:
    """测试 extract_hindsight_keywords 关键词提取函数。"""

    def test_extracts_chinese_keywords(self) -> None:
        """中文条目应提取中文关键词。"""
        text = "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入"
        keywords = extract_hindsight_keywords(text, max_count=5)
        assert len(keywords) >= 1
        assert any("审计" in k for k in keywords)

    def test_extracts_english_proper_nouns_first(self) -> None:
        """英文专有名词应优先提取。"""
        text = "Python PostgreSQL 数据库连接配置完成"
        keywords = extract_hindsight_keywords(text, max_count=5)
        assert "Python" in keywords
        assert "PostgreSQL" in keywords
        # 专有名词应出现在中文词之前
        prop_idx_py = keywords.index("Python")
        prop_idx_pg = keywords.index("PostgreSQL")
        cn_idx = next(i for i, k in enumerate(keywords) if any("\u4e00" <= c <= "\u9fff" for c in k))
        assert prop_idx_py < cn_idx
        assert prop_idx_pg < cn_idx

    def test_respects_max_count(self) -> None:
        """应遵守最大关键词数量限制。"""
        text = "审计 方法论 风险 检查 模式 维度 深入 递进 聚焦"
        keywords = extract_hindsight_keywords(text, max_count=3)
        assert len(keywords) == 3

    def test_clamps_max_count_to_3_8_range(self) -> None:
        """max_count 应限制在 3-8 范围内。"""
        text = "审计 方法论 风险 检查 模式 维度 深入 递进 聚焦 优化 部署 测试"
        keywords_low = extract_hindsight_keywords(text, max_count=1)
        assert len(keywords_low) == 3
        keywords_high = extract_hindsight_keywords(text, max_count=100)
        assert len(keywords_high) == 8

    def test_empty_text_returns_empty(self) -> None:
        """空文本应返回空列表。"""
        assert extract_hindsight_keywords("", max_count=5) == []

    def test_deduplicates_case_insensitive(self) -> None:
        """关键词应大小写不敏感去重。"""
        text = "Python python PYTHON"
        keywords = extract_hindsight_keywords(text, max_count=5)
        assert len(keywords) == 1

    def test_mixed_cn_en_keywords(self) -> None:
        """中英文混合条目应正确提取。"""
        text = "Data Flywheel 架构遵循注意力机制与分域原则"
        keywords = extract_hindsight_keywords(text, max_count=5)
        assert len(keywords) >= 2
        assert "Data" in keywords
        assert any("架构" in k for k in keywords)


class TestBackfillHindsightKeywords:
    """测试 backfill_hindsight_keywords 回填函数。"""

    def test_backfill_missing_keywords(self) -> None:
        """缺少关键词字段的条目应被回填。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入",
        ]
        hindsight_list = [{"index": 0}]
        result = backfill_hindsight_keywords(entries, hindsight_list, keyword_count=5)
        assert len(result) == 1
        assert "关键词" in result[0]
        assert len(result[0]["关键词"]) == 5

    def test_supplements_existing_keywords(self) -> None:
        """关键词数量不足时应补齐。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入",
        ]
        hindsight_list = [{"index": 0, "关键词": ["审计"]}]
        result = backfill_hindsight_keywords(entries, hindsight_list, keyword_count=5)
        assert len(result[0]["关键词"]) == 5
        assert result[0]["关键词"][0] == "审计"

    def test_keeps_existing_when_enough(self) -> None:
        """关键词数量足够时不修改。"""
        entries = ["条目内容"]
        original = ["审计", "风险", "检查", "模式", "维度"]
        hindsight_list = [{"index": 0, "关键词": original.copy()}]
        result = backfill_hindsight_keywords(entries, hindsight_list, keyword_count=5)
        assert result[0]["关键词"] == original

    def test_invalid_index_skipped(self) -> None:
        """无效 index 应被跳过。"""
        entries = ["条目一"]
        hindsight_list = [{"index": -1}, {"index": 100}]
        result = backfill_hindsight_keywords(entries, hindsight_list, keyword_count=5)
        assert len(result) == 2
        assert "关键词" not in result[0]

    def test_empty_tags_are_backfilled(self) -> None:
        """空关键词列表应被回填。"""
        entries = ["审计方法论：风险检查与递进模式"]
        hindsight_list = [{"index": 0, "关键词": []}]
        result = backfill_hindsight_keywords(entries, hindsight_list, keyword_count=5)
        assert len(result[0]["关键词"]) >= 1

    def test_non_list_tags_are_replaced(self) -> None:
        """非列表类型的关键词应被替换。"""
        entries = ["审计方法论：风险检查与递进模式"]
        hindsight_list = [{"index": 0, "关键词": "not-a-list"}]
        result = backfill_hindsight_keywords(entries, hindsight_list, keyword_count=5)
        assert isinstance(result[0]["关键词"], list)
        assert len(result[0]["关键词"]) >= 1

    def test_backfill_merges_without_duplicates(self) -> None:
        """回填时应去重（大小写不敏感）。"""
        entries = ["Python python 审计 审计"]
        hindsight_list = [{"index": 0, "关键词": ["python"]}]
        result = backfill_hindsight_keywords(entries, hindsight_list, keyword_count=5)
        lowercase = [k.lower() for k in result[0]["关键词"]]
        assert len(lowercase) == len(set(lowercase))


class TestKeywordBackfillIntegration:
    """关键词回填与分类流程的集成测试。"""

    def test_hindsight_without_tags_gets_backfilled(
        self, mock_llm_client: MagicMock
    ) -> None:
        """LLM 返回的 hindsight 没有关键词时，应被回填。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入",
        ]
        mock_llm_client.classify_batch.return_value = {
            "merge": [],
            "remove": [],
            "compress": [],
            "hindsight": [{"index": 0}],  # 没有关键词
        }
        result = classify_all(entries, "USER", mock_llm_client, batch_size=10, max_workers=1)
        assert len(result["hindsight"]) == 1
        assert "关键词" in result["hindsight"][0]
        assert len(result["hindsight"][0]["关键词"]) >= 3

    def test_hindsight_with_tags_passes_through(
        self, mock_llm_client: MagicMock
    ) -> None:
        """LLM 返回的 hindsight 有关键词时应保留。"""
        entries = [
            "审计方法论：采用10+轮递进检查模式，每轮聚焦不同风险维度，逐步深入",
        ]
        mock_llm_client.classify_batch.return_value = {
            "merge": [],
            "remove": [],
            "compress": [],
            "hindsight": [{"index": 0, "关键词": ["审计", "风险", "检查", "模式", "维度"]}],
        }
        result = classify_all(entries, "USER", mock_llm_client, batch_size=10, max_workers=1)
        assert len(result["hindsight"]) == 1
        assert result["hindsight"][0]["关键词"][0] == "审计"

    def test_memory_source_no_hindsight_backfill(
        self, mock_llm_client: MagicMock
    ) -> None:
        """MEMORY 源不进行 hindsight 处理（也不回填）。"""
        entries = ["条目一"]
        mock_llm_client.classify_batch.return_value = {
            "merge": [],
            "remove": [],
            "compress": [],
            "hindsight": [{"index": 0}],
        }
        result = classify_all(entries, "MEMORY", mock_llm_client, batch_size=10, max_workers=1)
        assert len(result["hindsight"]) == 1
        # MEMORY 源不调用 validate_hindsight_quality，也不回填
        # 但关键词字段是否存在取决于 LLM 返回
        # 这里测试的是不报错即可
        assert result["hindsight"][0].get("index") == 0
