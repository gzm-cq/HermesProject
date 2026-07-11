"""三路注入源定义 — Router prompt 拼接 + turn_gate 引用。"""

from dataclasses import dataclass


@dataclass
class SourceDef:
    """单路注入源定义。"""

    key: str
    name: str
    domain: str
    description: str
    examples: tuple[str, ...]


SOURCES = {
    "h": SourceDef(
        key="h",
        name="Hindsight",
        domain="经验",
        description="回答这个问题是否需要参考过去做过的类似事情、之前遇到的方案和教训、历史经验？",
        examples=("上次 LiteLLM 怎么修的", "之前那个方案结果怎样", "gateway 为啥崩了"),
    ),
    "kt": SourceDef(
        key="kt",
        name="Knowledge Tree",
        domain="知识",
        description="回答这个问题是否需要引用客观的概念定义、原理、公式、架构说明、事实关系？需要「这个东西是什么、怎么工作的」这类知识？",
        examples=("RRF 融合公式", "Hindsight 的架构", "什么是原子性知识点"),
    ),
    "s": SourceDef(
        key="s",
        name="Skill",
        domain="能力",
        description="回答这个问题是否需要参考操作步骤、配置方法、部署流程、工具用法？需要「这个事怎么做」这类指南？",
        examples=("怎么部署插件", "如何配置 Hindsight", "用什么工具查日志"),
    ),
    "sag": SourceDef(
        key="sag",
        name="SAG",
        domain="文档",
        description="回答这个问题是否需要查阅反思笔记、设计方案、协议规范、报告等结构化知识记录？",
        examples=("技术协议里怎么写的", "数据中台方案", "工控网投资预算"),
    ),
}


def build_router_prompt() -> str:
    """从 SOURCES 拼接 Router system prompt。"""
    lines = [
        "你是一个注入路由判断器。核心原则：宁可多查不漏。",
        "",
        "回答用户消息是否需要以下知识源补充？",
        f"H — 经验：{SOURCES['h'].description}",
        f"KT — 知识：{SOURCES['kt'].description}",
        f"S — 能力：{SOURCES['s'].description}",
        f"SAG — 文档：{SOURCES['sag'].description}",
        "",
        "规则：",
        '1. 短命令/缩写（如"修Router"、"daily"、"跑"）→ 全开',
        "2. 纯闲聊/确认（好的、知道了、谢谢）→ 全关",
        "3. 不理解的 → 全开",
        "4. SAG 不只是正式文档，也包含所有结构化知识记录",
        "5. 犹豫时开，不确定时全开",
        "",
        '输出 JSON：{"h": bool, "kt": bool, "s": bool, "sag": bool, "confidence": float}',
        "confidence<0.3 时全开。",
        "只输出 JSON，不要任何包裹文字。",
    ]
    return "\n".join(lines)
