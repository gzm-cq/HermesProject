"""
Phase 2 测试: HermesWebSearcher + HermesDocumentParser
遵循Hermes Code Rules规范
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

from ai_report.adapters.web_search import HermesWebSearcher, SearchResult, SearchResultItem
from ai_report.adapters.web_search import DelegationTask, MODE_HERMES
from ai_report.adapters.document import HermesDocumentParser

import pytest


@pytest.mark.unit
def test_web_searcher_initialization() -> None:
    """测试搜索适配器初始化"""
    print("=== 测试搜索适配器初始化 ===")

    searcher = HermesWebSearcher()
    # 默认模式：构建委托任务
    task = searcher.prepare("test query")
    assert task is not None
    assert hasattr(task, "goal")
    assert task.mode == "hermes"

    print("✓ 搜索适配器初始化正常")
    print(f"  模式: {task.mode}")
    print()


@pytest.mark.unit
def test_web_searcher_basic_search() -> None:
    """测试基本搜索功能"""
    print("=== 测试基本搜索功能 ===")

    searcher = HermesWebSearcher()

    # 测试默认模式：返回 DelegationTask
    task = searcher.search("Python异步编程", max_results=3)
    assert task is not None
    assert isinstance(task, DelegationTask)
    assert "Python" in task.goal
    assert task.max_searches == 3
    assert task.mode == MODE_HERMES

    print(f"✓ 搜索委托任务: mode={task.mode}, goal='{task.goal[:40]}'")

    # 测试 force_tavily 模式：返回 SearchResult
    result = searcher.search("Python异步编程", force_tavily=True)
    assert isinstance(result, SearchResult)
    assert hasattr(result, "items")
    assert hasattr(result, "success")

    print(f"✓ Tavily模式: success={result.success}")
    print()


@pytest.mark.unit
def test_web_searcher_cache() -> None:
    """测试委托任务复用（缓存语义）。"""
    print("=== 测试委托任务复用 ===")

    searcher = HermesWebSearcher()
    query = "机器学习Transformer"

    # 默认模式返回 DelegationTask
    task1 = searcher.search(query, max_results=2)
    assert isinstance(task1, DelegationTask)

    # 相同查询应返回结构相似的任务
    task2 = searcher.search(query, max_results=2)
    assert isinstance(task2, DelegationTask)
    assert task2.goal == task1.goal

    print("✓ 委托任务复用正常")
    print(f"  goal: {task1.goal[:40]}")
    print()


@pytest.mark.unit
def test_web_searcher_empty_query() -> None:
    """测试空查询——应正常返回委托任务（不抛异常）。"""
    print("=== 测试空查询 ===")

    searcher = HermesWebSearcher()
    task = searcher.search("")
    assert isinstance(task, DelegationTask)
    assert task.goal == ""
    print("✓ 空查询正常返回委托任务")
    print()


@pytest.mark.unit
def test_web_searcher_prepare_modes() -> None:
    """测试 prepare 和 search_tavily 两种模式。"""
    print("=== 测试 prepare/search_tavily 模式 ===\n")

    searcher = HermesWebSearcher()

    # prepare: 返回委托任务
    task = searcher.prepare("AI芯片", max_results=3)
    assert isinstance(task, DelegationTask)
    assert task.goal == "AI芯片"
    assert task.max_searches == 3
    print(f"✓ prepare -> DelegationTask: skill={task.skill}")

    # search_tavily: 返回 SearchResult（不依赖真实 API）
    result = searcher.search_tavily("AI芯片")
    assert isinstance(result, SearchResult)
    assert hasattr(result, "items")
    assert hasattr(result, "success")
    print(f"✓ search_tavily -> SearchResult: success={result.success}")
    print()


@pytest.mark.unit
def test_web_searcher_local_search(tmp_path: Path) -> None:
    """测试本地文件搜索（DocumentParser 模式）。"""
    print("=== 测试本地文件搜索 ===\n")

    # 创建临时知识文件
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    test_file = knowledge_dir / "deep_learning_notes.md"
    test_file.write_text("# 深度学习笔记\n\n## Transformer架构\n\n自注意力机制...", encoding="utf-8")

    # 使用 HermesDocumentParser 解析本地文件
    parser = HermesDocumentParser()
    result = parser.parse(str(test_file))
    assert result["format"] in ("markdown", "text")
    assert "深度学习" in result.get("content", "") or "深度学习" in result.get("preview", "")

    print("✓ 本地文件搜索正常")
    print(f"  文件: {test_file.name}")
    print(f"  格式: {result['format']}")
    print()


@pytest.mark.unit
def test_document_parser_initialization() -> None:
    """测试文档解析器初始化"""
    print("=== 测试文档解析器初始化 ===")

    parser = HermesDocumentParser()
    assert parser.COMPONENT_NAME == "HermesDocumentParser"
    assert parser.MAX_FILE_SIZE == 10 * 1024 * 1024

    print("✓ 文档解析器初始化正常")
    print()


@pytest.mark.unit
def test_document_parser_text(tmp_path: Path) -> None:
    """测试解析纯文本文件"""
    print("=== 测试解析文本文件 ===")

    parser = HermesDocumentParser()
    file_path = tmp_path / "sample.txt"
    file_path.write_text("Hello World\n这是测试内容\n第三行", encoding="utf-8")

    result = parser.parse(str(file_path))
    assert result["format"] == "text"
    assert result["lines"] == 3
    assert result["size"] > 0
    assert "Hello World" in result["preview"]

    print(f"✓ 文本文件解析成功")
    print(f"  格式: {result['format']}, 行数: {result['lines']}, 大小: {result['size_display']}")
    print()


@pytest.mark.unit
def test_document_parser_markdown(tmp_path: Path) -> None:
    """测试解析Markdown文件"""
    print("=== 测试解析Markdown文件 ===")

    parser = HermesDocumentParser()
    file_path = tmp_path / "doc.md"
    file_path.write_text(
        "# 标题1\n\n内容1\n\n## 标题2\n\n内容2\n\n### 标题3\n",
        encoding="utf-8",
    )

    result = parser.parse(str(file_path))
    assert result["format"] == "markdown"
    assert len(result["sections"]) == 3

    print(f"✓ Markdown解析成功: {len(result['sections'])}个章节")
    for s in result["sections"]:
        print(f"  H{s['level']}: {s['title']}")
    print()


@pytest.mark.unit
def test_document_parser_json(tmp_path: Path) -> None:
    """测试解析JSON文件"""
    print("=== 测试解析JSON文件 ===")

    parser = HermesDocumentParser()
    file_path = tmp_path / "data.json"
    file_path.write_text(
        json.dumps({"name": "测试", "items": [1, 2, 3], "nested": {"key": "val"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = parser.parse(str(file_path))
    assert result["format"] == "json"
    assert len(result["sections"]) > 0

    print(f"✓ JSON解析成功: {len(result['sections'])}个顶层键")
    print(f"  预览: {result['preview'][:80]}...")
    print()


@pytest.mark.unit
def test_document_parser_html(tmp_path: Path) -> None:
    """测试解析HTML文件"""
    print("=== 测试解析HTML文件 ===")

    parser = HermesDocumentParser()
    file_path = tmp_path / "page.html"
    file_path.write_text(
        "<html><body><h1>标题</h1><p>段落内容</p></body></html>",
        encoding="utf-8",
    )

    result = parser.parse(str(file_path))
    assert result["format"] == "html"
    # HTML剥离后应该有可读文本
    assert "标题" in result["content"]

    print(f"✓ HTML解析成功")
    print(f"  提取文本: {result['content'][:80]}")
    print()


@pytest.mark.unit
def test_document_parser_content() -> None:
    """测试解析文本内容"""
    print("=== 测试解析文本内容 ===")

    parser = HermesDocumentParser()

    # JSON内容
    json_result = parser.parse_content('{"a": 1, "b": {"c": 2}}', format_hint="json")
    assert json_result["format"] == "json"
    assert len(json_result["sections"]) == 2

    # Markdown内容
    md_result = parser.parse_content("# Title\n\n## Section 1\n\n## Section 2", format_hint="markdown")
    assert md_result["format"] == "markdown"
    assert len(md_result["sections"]) >= 2

    print(f"✓ 内容解析成功")
    print(f"  JSON: {len(json_result['sections'])}个section")
    print(f"  Markdown: {len(md_result['sections'])}个section")
    print()


@pytest.mark.unit
def test_document_parser_file_not_found() -> None:
    """测试文件不存在"""
    print("=== 测试文件不存在错误处理 ===")

    parser = HermesDocumentParser()
    try:
        parser.parse("/tmp/nonexistent_file_xyz.txt")
        assert False, "应该抛出异常"
    except Exception as e:
        assert "不存在" in str(e) or "not" in str(e).lower()
        print(f"✓ 文件不存在正确拒绝: {type(e).__name__}")
    print()


@pytest.mark.unit
def test_document_parser_code(tmp_path: Path) -> None:
    """测试解析Python源码"""
    print("=== 测试解析Python源码 ===")

    parser = HermesDocumentParser()
    file_path = tmp_path / "example.py"
    file_path.write_text(
        "import os\n\nclass MyClass:\n    pass\n\n\ndef my_function():\n    pass\n\nasync def async_func():\n    pass\n",
        encoding="utf-8",
    )

    result = parser.parse(str(file_path))
    assert result["format"] == "python"
    # 应该提取到类和函数
    sections = result["sections"]
    func_count = sum(1 for s in sections if s["type"] == "function")
    class_count = sum(1 for s in sections if s["type"] == "class")

    print(f"✓ Python源码解析成功")
    print(f"  章节: {len(sections)}个 ({class_count}个类, {func_count}个函数)")
    for s in sections:
        print(f"  [{s['type']}] {s['title']} (行{s['line']})")
    print()


def run_phase2_tests(custom_tmpdir: Optional[Path] = None) -> bool:
    """运行所有Phase 2测试"""
    print("🚀 开始Phase 2测试: 搜索适配器 + 文档解析器\n")

    # 使用提供的临时目录或默认的tmp_path
    tmp_base = custom_tmpdir or tmp_path  # type: ignore[name-defined]

    test_functions = [
        # Web Searcher
        test_web_searcher_initialization,
        test_web_searcher_basic_search,
        test_web_searcher_cache,
        test_web_searcher_empty_query,
        test_web_searcher_clear_cache,
        # Document Parser
        test_document_parser_initialization,
        test_document_parser_text,
        test_document_parser_markdown,
        test_document_parser_json,
        test_document_parser_html,
        test_document_parser_content,
        test_document_parser_file_not_found,
        test_document_parser_code,
    ]

    test_functions_with_tmp = [
        test_document_parser_text,
        test_document_parser_markdown,
        test_document_parser_json,
        test_document_parser_html,
        test_document_parser_code,
    ]

    passed = 0
    failed = 0

    for test_func in test_functions:
        try:
            if test_func in test_functions_with_tmp:
                test_func(tmp_base)
            else:
                test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"✗ {test_func.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()
            print()

    print(f"\n📊 Phase 2 测试结果: {passed} 通过, {failed} 失败")

    if failed == 0:
        print("\n✅ Phase 2 全部通过! 搜索适配器和文档解析器已就绪。")
        print("下一步 (Phase 3): 图表生成器 + 质量评估 + 状态管理")
    else:
        print(f"\n❌ 有 {failed} 个测试失败，需要修复。")

    return failed == 0


def run_all_tests() -> bool:
    """运行Phase 1 + Phase 2全部测试"""
    print("=" * 60)
    print("          AI报告生成系统 - 完整测试套件")
    print("=" * 60)

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        global tmp_path
        tmp_path = Path(tmpdir)

        phase2_ok = run_phase2_tests(custom_tmpdir=tmp_path)

        print()
        print("\n--- Phase 1 回归测试 ---\n")
        from tests.test_basic_framework import run_all_tests as run_phase1
        phase1_ok = run_phase1()

    total_ok = phase1_ok and phase2_ok
    print(f"\n{'=' * 60}")
    print(f"整体结果: {'✅ 全部通过' if total_ok else '❌ 有失败'}")
    print(f"{'=' * 60}")

    return total_ok


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        globals()["tmp_path"] = Path(tmpdir)
        success = run_phase2_tests()
        sys.exit(0 if success else 1)
