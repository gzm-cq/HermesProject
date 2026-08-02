"""单元测试：mark_memory.py 核心函数（不依赖数据库）。"""

import re

import pytest

from mark_memory import _keyword_matches, MARK_PATTERN

# ======== _keyword_matches ========


class TestKeywordMatches:
    def test_english_word_boundary(self) -> None:
        """纯英文关键词应使用单词边界匹配。"""
        assert _keyword_matches("OOM", "系统 OOM 崩溃")
        assert not _keyword_matches("OOM", "room 不够")  # OOM 是 room 子串
        assert not _keyword_matches("OOM", "looking good")
        assert _keyword_matches("OOM", "发生 OOM，进程被杀")

    def test_english_multiple_words(self) -> None:
        """纯英文关键词含多个字母。"""
        assert _keyword_matches("error", "an error occurred")
        assert not _keyword_matches("error", "terrorist")  # 子串
        assert _keyword_matches("bug", "this is a bug")

    def test_chinese_substring(self) -> None:
        """中文关键词使用子串匹配。"""
        assert _keyword_matches("失败", "系统登录失败")
        assert _keyword_matches("失败", "失败原因分析")
        assert _keyword_matches("失败", "没有明显失败")  # 含子串

    def test_mixed_keyword(self) -> None:
        """中英混的关键词视为子串匹配。"""
        assert _keyword_matches("磁盘满", "磁盘满了报警")
        assert not _keyword_matches("磁盘满", "磁盘空间足够")

    def test_empty_text(self) -> None:
        """空文本不匹配。"""
        assert not _keyword_matches("失败", "")


# ======== MARK_PATTERN ========


class TestMarkPattern:
    def test_matches_mark_prefix(self) -> None:
        """MARK_PATTERN 匹配标准标记前缀。"""
        assert MARK_PATTERN.search("[标记: 错误]")
        assert MARK_PATTERN.search("内容 [标记: 作废]")
        assert MARK_PATTERN.search("[标记: 可疑] 备注")
        assert MARK_PATTERN.search("正文\n[标记: 已解决]")

    def test_not_match_plain_text(self) -> None:
        """MARK_PATTERN 不匹配普通文本。"""
        assert not MARK_PATTERN.search("系统登录失败")
        assert not MARK_PATTERN.search("标记: 错误")
        assert not MARK_PATTERN.search("[标记] 错误类型")
        assert not MARK_PATTERN.search("")


# ======== EXCLUDE_PATTERNS 正确性 ========


class TestExcludePatterns:
    """验证自动标记的排除正则行为正确。"""

    PATTERNS = [
        r'\b无(?:任何)?(?:bug|报错|失败|错误|异常|问题|故障)\b',
        r'\b0\s*[个]*(?:错误|bug|报错|失败|异常)',
        r'没有(?:任何)?(?:错误|bug|报错|失败|异常|问题)',
        r'(?:无|没有)(?:任何)?异常',
        r'已(?:修复|解决|更正|纠正)\b',
        r'测试\s*通过',
    ]

    def test_should_match_no_any_error(self) -> None:
        """“无任何错误”应匹配排除。"""
        for p in self.PATTERNS:
            if re.search(p, "无任何错误", re.I):
                return  # 至少一个匹配即可
        pytest.fail("'无任何错误' 未匹配任何排除 pattern")

    def test_should_not_match_no_ren_error(self) -> None:
        """“无任错误”不应被排除（不是正规的“什么错误”表述）。"""
        # 新 regex 中 [任何]* 已改为 (?:任何)?，不再匹配“无任错误”“无何错误”
        matched_any = any(re.search(p, "无任错误", re.I) for p in self.PATTERNS)
        assert not matched_any, "'无任错误' 不应匹配排除 pattern"

    def test_should_not_match_no_he_error(self) -> None:
        matched_any = any(re.search(p, "无何错误", re.I) for p in self.PATTERNS)
        assert not matched_any, "'无何错误' 不应匹配排除 pattern"

    def test_should_match_mei_you_ren_he(self) -> None:
        assert any(re.search(p, "没有任何异常", re.I) for p in self.PATTERNS), \
            "'没有任何异常' 应匹配排除"

    def test_should_not_match_mei_you_he(self) -> None:
        matched_any = any(re.search(p, "没有何异常", re.I) for p in self.PATTERNS)
        assert not matched_any, "'没有何异常' 不应匹配排除 pattern"

    def test_should_match_test_pass(self) -> None:
        assert any(re.search(p, "测试通过", re.I) for p in self.PATTERNS), \
            "'测试通过' 应匹配排除"

    def test_should_match_fixed(self) -> None:
        assert any(re.search(p, "已修复", re.I) for p in self.PATTERNS), \
            "'已修复' 应匹配排除"

    def test_should_match_zero_errors(self) -> None:
        assert any(re.search(p, "0个错误", re.I) for p in self.PATTERNS), \
            "'0个错误' 应匹配排除"

    def test_should_match_no_any_abnormal(self) -> None:
        assert any(re.search(p, "无异常", re.I) for p in self.PATTERNS), \
            "'无异常' 应匹配排除"

    def test_should_match_no_any_bug(self) -> None:
        assert any(re.search(p, "无任何bug", re.I) for p in self.PATTERNS), \
            "'无任何bug' 应匹配排除"


# ======== 自动标记 RULES / CONCEPT_REDUCER 边界 ========


class TestAutoMarkRules:
    """验证自动标记的关键词规则和排除逻辑。"""

    # 模拟 mark_keyword_memories 中的常量和逻辑
    RULES = [
        ("失败", "错误", "失败"),
        ("报错", "错误", "报错"),
        ("OOM", "错误", "OOM"),
        ("磁盘满", "错误", "磁盘满"),
        ("拒绝连接", "错误", "拒绝连接"),
        ("连接失败", "错误", "连接失败"),
        ("超时", "可疑", "超时"),
        ("异常", "可疑", "异常"),
        ("熔断", "可疑", "熔断"),
    ]
    EXCLUDE_CONTEXT = ["讨论", "研究", "探索"]
    _EXCLUDE_PROXIMITY = 5
    CONCEPT_REDUCER = ["方案", "机制", "流程", "策略", "设计", "处理", "规范", "配置", "设置", "情况", "原因", "场景"]
    EXCLUDE_PATTERNS = [
        r'\b无(?:任何)?(?:bug|报错|失败|错误|异常|问题|故障)\b',
        r'\b0\s*[个]*(?:错误|bug|报错|失败|异常)',
        r'没有(?:任何)?(?:错误|bug|报错|失败|异常|问题)',
        r'已(?:修复|解决|更正|纠正)\b',
        r'测试\s*通过',
    ]
    BROAD_KEYS = {"失败", "错误", "异常", "超时", "阻塞", "降级"}

    def _should_mark(self, text: str) -> bool:
        """模拟一条规则是否应对该文本标记（至少一条规则匹配且不被排除）。"""
        import re as _re
        for keyword, _mt, _note in self.RULES:
            if keyword.isascii() and keyword.isalpha():
                if not _re.search(rf"\b{_re.escape(keyword)}\b", text, _re.I):
                    continue
            else:
                if keyword.lower() not in text.lower():
                    continue
            # EXCLUDE_CONTEXT 毗邻匹配：仅在关键词附近 _EXCLUDE_PROXIMITY 字符范围内才跳过
            _kw_pos = text.lower().find(keyword)
            if _kw_pos >= 0:
                _excluded = False
                for ctx in self.EXCLUDE_CONTEXT:
                    _ctx_pos = text.lower().find(ctx)
                    if _ctx_pos >= 0 and abs(_ctx_pos - _kw_pos) <= self._EXCLUDE_PROXIMITY:
                        _excluded = True
                        break
                if _excluded:
                    continue
            # 毗邻匹配：关键词与概念词在 3 字符范围内时才跳过
            if keyword in self.BROAD_KEYS:
                _skip = False
                for cr in self.CONCEPT_REDUCER:
                    kpos = text.lower().find(keyword)
                    if kpos >= 0:
                        cpos = text.lower().find(cr, max(0, kpos - 3))
                        if cpos >= 0 and abs(cpos - kpos) <= max(len(keyword), len(cr)):
                            _skip = True
                            break
                        cpos2 = text.lower().find(cr, kpos + len(keyword))
                        if kpos >= 0 and cpos2 >= 0 and cpos2 - kpos <= len(keyword) + 3:
                            _skip = True
                            break
                if _skip:
                    continue
            if any(_re.search(p, text, _re.I) for p in self.EXCLUDE_PATTERNS):
                continue
            return True
        return False

    def test_actual_error_should_mark(self) -> None:
        """实际故障事件应被标记。"""
        assert self._should_mark("系统登录失败")
        assert self._should_mark("API 调用超时")
        assert self._should_mark("数据库连接超时")
        assert self._should_mark("任务运行异常")
        assert self._should_mark("收到 OOM 错误")
        assert self._should_mark("服务熔断")
        assert self._should_mark("部署报错")
        assert self._should_mark("发现失败原因是数据库连接超时")  # 非相邻概念词，应标记

    def test_concept_discussion_should_not_mark(self) -> None:
        """概念性讨论/方案/处理不应被标记。"""
        assert not self._should_mark("失败处理方案")
        assert not self._should_mark("异常处理机制")
        assert not self._should_mark("超时时间设置")
        assert not self._should_mark("失败原因分析")
        assert not self._should_mark("异常处理策略")
        assert not self._should_mark("超时配置说明")
        assert not self._should_mark("失败场景分析")
        assert self._should_mark("大规模系统全面运维失败后的测试讨论记录")  # "讨论"距"失败"=6，无 CONCEPT_REDUCER 干扰
        assert not self._should_mark("讨论了超时问题")  # "讨论"紧邻"超时"（BROAD_KEY），应排除

    def test_success_context_should_not_mark(self) -> None:
        """排除 pattern 匹配时不应标记。"""
        assert not self._should_mark("无任何错误")
        assert not self._should_mark("已修复该问题")
        assert not self._should_mark("测试通过")
        assert not self._should_mark("服务运行正常")

    def test_specific_keyword_immune_to_concept_reducer(self) -> None:
        """具体的纯动作关键词（磁盘满、拒绝连接、连接失败）不受 CONCEPT_REDUCER 影响。"""
        assert self._should_mark("磁盘满了请处理")
        assert self._should_mark("配置了拒绝连接策略")
        assert self._should_mark("异常连接失败处理")  # "连接失败"在 BROAD_KEYS 但在 BROAD_KEYS 中? 不 — 检查确否
        # 验证：连接失败、磁盘满 不在 BROAD_KEYS 中，CONCEPT_REDUCER 不应用
        assert "连接失败" not in self.BROAD_KEYS
        assert "磁盘满" not in self.BROAD_KEYS


class TestUuidValidation:
    def test_invalid_uuid_format(self) -> None:
        """无效 UUID 格式应被 validate_uuid 拒绝。"""
        import mark_memory
        with pytest.raises(SystemExit):
            mark_memory.validate_uuid("not-a-uuid")

    def test_valid_uuid_format(self) -> None:
        import mark_memory
        mark_memory.validate_uuid("09f472ff-1234-5678-abcd-ef0123456789")
        # 没有异常即通过
