"""pytest conftest — StateGraph 集成测试 mock 框架。

为 tests/graph/test_integration.py 提供：
  - mock_llm:      替换 call_llm 为可控假函数
  - sample_source: 测试用源文档
  - sample_goal:   测试用 report_goal
  - sample_chapters: 测试用 chapter_prompts
"""

from __future__ import annotations

import json as _json
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest


# ── 测试夹具（fixtures） ──────────────────────────────────────


@pytest.fixture
def sample_source() -> str:
    return """# 智能化转型建设规划

## 项目背景
某央企计划在未来五年（2026-2030）推进智能化转型建设，
总投资预算5.20亿元，分三个阶段实施。

## 技术方案
采用三网隔离架构：互联网层、工控网层、内网层。
互联网层部署Qoder用于代码生成，工控网层部署百度文心大模型，
内网层建设数据中台。

## 投资估算
互联网层：260万元/年
工控网层：550万元（四年总投入）
内网层：5.20亿元（四年总投入）
"""


@pytest.fixture
def sample_goal() -> dict[str, Any]:
    return {
        "title": "智能化转型建设规划",
        "purpose": "论证央企智能化转型的技术路线、投资规模和实施路径",
        "target_audience": "企业决策层、技术主管",
        "overall_strategy": "分三阶段推进：先验证后推广、分层建设、分期投入",
        "writing_role": {
            "role": "企业架构师",
            "expertise": ["智能化转型", "企业架构"],
            "tone": "专业客观",
            "voice": "决策者视角",
            "output_conventions": "数据驱动决策，避免空洞口号",
        },
    }


@pytest.fixture
def sample_chapters() -> list[dict[str, Any]]:
    return [
        {
            "title": "可行性分析概述",
            "writing_intent": "概述智能化转型的必要性和可行性",
            "key_points": ["企业现状", "转型目标", "总体投资"],
            "section_type": "intro",
        },
        {
            "title": "技术可行性分析",
            "writing_intent": "论证三网隔离架构的技术可行性",
            "key_points": ["三网架构", "Qoder部署", "百度文心"],
            "section_type": "body",
        },
        {
            "title": "经济可行性评估",
            "writing_intent": "分析投资规模和预期收益",
            "key_points": ["投资预算", "分期投入", "投资回报"],
            "section_type": "body",
        },
    ]


# ── mock LLM ──────────────────────────────────────────────


class MockLLM:
    """可控假 LLM，按 prompt 关键词匹配返回预定义响应。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._responses: dict[str, str] = {}

    def register(self, keyword: str, response: str | dict | list) -> None:
        """注册关键词→响应映射。dict/list 自动 JSON 序列化。"""
        if isinstance(response, (dict, list)):
            response = _json.dumps(response, ensure_ascii=False)
        self._responses[keyword] = response

    def default_response(self) -> str:
        """默认 fallback 响应——简单的结构化 JSON。"""
        return '{"title": "默认标题", "purpose": "默认目的"}'

    def __call__(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append(prompt[:200])
        for keyword, resp in self._responses.items():
            if keyword in prompt:
                return resp
        return self.default_response()


@pytest.fixture
def mock_llm() -> MockLLM:
    return MockLLM()


@pytest.fixture(autouse=True)
def _patch_call_llm(
    monkeypatch: pytest.MonkeyPatch, mock_llm: MockLLM
) -> None:
    """全局自动替换 report_graph 中的 call_llm 为 mock。"""
    monkeypatch.setattr(
        "src.graph.report_graph.call_llm", mock_llm
    )


@pytest.fixture(autouse=True)
def _patch_fact_bank(monkeypatch: pytest.MonkeyPatch) -> None:
    """全局 mock _load_fact_bank 返回 None（跳过文件系统读取）。"""
    monkeypatch.setattr(
        "src.graph.report_graph._load_fact_bank", lambda topic: None
    )
