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
}


def build_router_prompt() -> str:
    """从 SOURCES 拼接 Router system prompt。"""
    lines = [
        "你是一个注入路由判断器。",
        "判断：为了准确回答用户消息，是否需要从以下知识源补充信息？\n",
    ]
    for s in SOURCES.values():
        lines.append(f"{s.key.upper()} — {s.domain}/{s.name}")
        lines.append(f"  {s.description}\n")
    lines.append("输出 JSON：{\"h\": bool, \"kt\": bool, \"s\": bool}")
    lines.append("")
    lines.append("要求：")
    lines.append("- 思考问题是「本质需要哪种知识」")
    lines.append("- 宁可多开不遗漏")
    lines.append("- 只输出 JSON，不要任何包裹格式")
    lines.append("- 相同语义的问题输出一致")
    return "\n".join(lines)
