"""dream-synth 测试辅助工具 — 共享的工厂函数。

集中管理跨测试文件复用的工具，避免重复定义。
"""

from __future__ import annotations


def make_reflection(sid="s1", title="反思标题", score=5, content=None):
    """构造一个反思字典，用于 phase_patterns / phase_feishu 等测试。

    content 为 None 时使用默认模板（含摘要、关键决策、知识要点、待办事项四节）。
    """
    if content is None:
        content = (
            f"# {title}\n\n"
            f"## 摘要\n这是关于{title}的摘要内容。\n\n"
            "## 关键决策\n测试决策。\n\n"
            "## 知识要点\n测试知识点。\n\n"
            "## 待办事项\n测试待办。"
        )
    return {
        "session_id": sid,
        "title": title,
        "score": score,
        "content": content,
    }


def make_session(sid="s1", title="测试对话"):
    """构造一个会话字典，用于 phase_synthesize 测试。"""
    body = "这是一段测试对话内容。用户说什么，助手回应什么。" * 100
    return {
        "id": sid,
        "title": title,
        "text": f"[用户] {title}相关的讨论\n\n[助手] 好的，关于{title}，我们来分析一下。\n\n" + body,
        "text_len": 3000 + len(title) * 2,
    }
