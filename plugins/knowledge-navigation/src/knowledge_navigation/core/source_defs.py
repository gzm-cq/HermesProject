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
        description="回答这个问题是否需要查阅结构化知识记录（方案、协议、报告、会话快照等）？",
        examples=("技术协议里怎么写的", "数据中台方案", "上次巡检报告结果"),
    ),
}


def build_router_prompt() -> str:
    """从 SOURCES 拼接 Router system prompt。

    基于 2026-07-14/15 eval 优化结果 + 2026-07-16 排他规则审查:
    - Round 2: Macro F1=0.602, Exact Match=47%, Parse fail=0
    - 2026-07-16: 删除"技术调试不要开SAG"排他规则（SAG内容覆盖技术调试场景）
    - 保留"纯知识问题不要开S"排他规则
    """
    return (
        "你是注入路由判断器。回答用户消息是否需要以下知识源补充：\n"
        "H — 经验：参考历史经验、之前方案、教训？\n"
        "KT — 知识：引用客观定义、原理、架构、事实关系？\n"
        "S — 能力：参考操作步骤、配置方法、工具用法？\n"
        "SAG — 文档：查阅结构化知识记录（反思笔记、方案、协议、报告等）？\n"
        "\n"
        "判断规则：\n"
        "1. 纯社交用语或用户明确表示不需要（好的、谢谢、先还原、我还没想好）→ 全关\n"
        "2. 操作指令/短命令（修、修复、跑、重启、验证、测试、部署、检查）→ 全开\n"
        "3. 问「怎么用/怎么配置/怎么部署」→ S 开（可能需要 H/KT）\n"
        "4. 问「是什么/为什么/原理」→ KT 开（可能需要 H）\n"
        "5. 问「之前怎么做的/上次遇到」→ H 开\n"
        "6. 查阅方案/协议/报告 → SAG 开\n"
        "7. 消息含问号或技术名词（工具名/组件名/参数名）→ 不算闲聊，至少开一路\n"
        "8. 不确定 → 全开\n"
        "\n"
        "⚠️ 排他规则（必须先判断）：\n"
        "- 纯知识/原理问题不要开 S：「litellm能配置向量吗」「RRF公式是什么」→ 只开 KT\n"
        "\n"
        "示例：\n"
        '消息："gbrain怎么用？" → {"h":true,"kt":true,"s":true,"sag":false,"confidence":0.9}\n'
        '消息："数据中台的技术协议里怎么写的" → {"h":false,"kt":false,"s":false,"sag":true,"confidence":0.9}\n'
        '消息："SAG 500超时是什么原因？" → {"h":true,"kt":true,"s":false,"sag":true,"confidence":0.8}\n'
        '消息："报告中还有什么未办项？" → {"h":true,"kt":false,"s":false,"sag":true,"confidence":0.8}\n'
        '消息："为什么用0.5不用0.05？" → {"h":true,"kt":true,"s":false,"sag":false,"confidence":0.7}\n'
        '消息："好的" → {"h":false,"kt":false,"s":false,"sag":false,"confidence":0.9}\n'
        "\n"
        '输出 JSON：{"h": bool, "kt": bool, "s": bool, "sag": bool, "confidence": float}\n'
        "不要思考过程，直接输出 JSON。confidence<0.3 时全开。"
    )
