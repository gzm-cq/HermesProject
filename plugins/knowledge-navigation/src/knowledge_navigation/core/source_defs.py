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
        description="回答这个问题是否需要查阅技术文档、协议规范、设计方案、报告等正式文档？",
        examples=("技术协议里怎么写的", "数据中台方案", "工控网投资预算"),
    ),
}


def build_router_prompt() -> str:
    """从 SOURCES 拼接 Router system prompt。"""
    lines = [
        "你是一个注入路由判断器。",
        "核心原则：宁可多查不漏，不确定就开。",
        "",
        "判断：回答用户消息是否需要从以下知识源补充信息？",
        "",
    ]
    for s in SOURCES.values():
        lines.append(f"{s.key.upper()} — {s.domain}/{s.name}：{s.description}")
        if s.examples:
            lines.append(f"  例：{', '.join(s.examples)}")
        lines.append("")
    lines.append("--- 判断规则 ---")
    lines.append("1. 思考问题的本质是「回答这个问题需要哪种知识」，不是关键词匹配")
    lines.append("2. 短命令/缩写/单字指令（如 '修Router'、'daily'、'跑'、'看数据'）→ 默认全开，因为用户隐含了需要上下文知识")
    lines.append("3. 涉及具体操作/配置/部署 → s=true")
    lines.append("4. 涉及架构/原理/概念/技术名词 → kt=true")
    lines.append("5. 涉及过往经验/教训/类似案例/项目背景 → h=true")
    lines.append("6. 涉及文档/协议/方案/报告 → sag=true")
    lines.append("7. 纯闲聊/问候/确认/简单回复（如 '好的'、'知道了'、'谢谢'）→ 全 false")
    lines.append("8. 完全不理解的问题 → 全开（保守兜底）")
    lines.append("")
    lines.append("--- 输出约束 ---")
    lines.append("- 宁可多查不漏：不确定时开启相关源，模糊查询默认全开")
    lines.append("- 不要求至少一项为 false——如果多个知识源都可能有用，全部设为 true")
    lines.append("- 当只需要一个源时，只设那个源为 true")
    lines.append("- confidence 表示你对决策的信心程度，低置信度（<0.5）时建议全开")
    lines.append("")
    lines.append("--- 示例 ---")
    lines.append('消息：修Router → {"h": true, "kt": true, "s": true, "sag": true, "confidence": 0.95}')
    lines.append("  # 涉及历史经验（之前怎么修的）、知识（Router架构）、操作（怎么改）、文档（协议）")
    lines.append('消息：daily → {"h": true, "kt": true, "s": true, "sag": true, "confidence": 0.9}')
    lines.append("  # 模棱两可的短命令，需要查历史/操作/知识/文档")
    lines.append('消息：RRF融合公式 → {"h": false, "kt": true, "s": false, "sag": false, "confidence": 0.95}')
    lines.append("  # 纯概念/公式知识，不需要经验和操作")
    lines.append('消息：怎么部署插件 → {"h": false, "kt": false, "s": true, "sag": false, "confidence": 0.95}')
    lines.append("  # 纯操作指南")
    lines.append('消息：上次LiteLLM怎么修的 → {"h": true, "kt": false, "s": false, "sag": false, "confidence": 0.9}')
    lines.append("  # 纯历史经验")
    lines.append('消息：数据中台方案 → {"h": false, "kt": false, "s": false, "sag": true, "confidence": 0.95}')
    lines.append("  # 查正式文档")
    lines.append('消息：知道了 → {"h": false, "kt": false, "s": false, "sag": false, "confidence": 0.9}')
    lines.append("  # 纯确认回复，不需要任何知识源")
    lines.append("")
    lines.append('输出 JSON：{"h": bool, "kt": bool, "s": bool, "sag": bool, "confidence": float}')
    lines.append("IMPORTANT: Your response must contain ONLY this JSON object.")
    lines.append("No markdown. No code fences. No explanations. No sentences before or after.")
    lines.append("Begin with '{' and end with '}'.")
    lines.append("只输出 JSON，不要任何包裹格式。不允许加说明文字。")
    lines.append("相同语义的问题输出一致。")
    return "\n".join(lines)