"""反思回路核心逻辑 — 失败分析 + 结构化反思

基于 Reflexion 范式：
  失败轨迹 → LLM 分析原因 → 结构化反思结果 → 注入重试 prompt
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from kanban_reflection.adapters.llm_client import LLMClient
from kanban_reflection.config import KanbanReflectionConfig

logger = logging.getLogger(__name__)

# 反思 prompt 模板（参考 SE-Agent Revision 算子）
REFLECTION_PROMPT_TEMPLATE = """你是一个 Kanban 任务失败分析专家。请分析以下失败轨迹，输出结构化分析结果。

任务目标：
{task_goal}

失败轨迹（最近 {trace_count} 轮交互）：
{trace_content}

请分析失败原因并输出 JSON 格式（不要 markdown 包裹，纯 JSON）：
{{
    "failure_reason": "一句话概括失败原因",
    "failure_type": "错误类型",
    "suggestion": "具体的优化建议，下轮重试时应如何改进",
    "confidence": 0.0-1.0 之间的置信度
}}

错误类型必须是以下之一：
- tool_execution_error: 工具执行错误（调用失败、超时、参数错误）
- output_mismatch: 输出不符合要求（格式错误、内容不完整）
- status_inconsistency: 状态不一致（预期状态与实际不符）
- user_correction: 用户纠正（用户明确指出错误方向）
- kanban_timeout: Kanban 任务超时
- llm_anomaly: LLM 异常输出（幻觉、重复、偏离主题）
"""


@dataclass
class ReflectionResult:
    """结构化反思结果"""
    task_id: str
    failure_reason: str
    failure_type: str
    suggestion: str
    confidence: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_inject_prompt(self) -> str:
        """生成可注入到重试 prompt 的反思文本"""
        return (
            f"[反思分析] 上轮失败原因：{self.failure_reason}。"
            f"优化建议：{self.suggestion}"
        )


def read_trace_lines(trace_path: str, task_id: str, max_lines: int = 5) -> list[dict[str, Any]]:
    """从 trace.log 中读取指定任务的最近 N 轮消息

    Args:
        trace_path: trace.log 路径
        task_id: 任务 ID 关键词
        max_lines: 最多读取的行数

    Returns:
        匹配的消息行列表（JSON parsed）
    """
    matches: list[dict[str, Any]] = []
    pattern = re.compile(re.escape(task_id), re.IGNORECASE)

    try:
        with open(trace_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if pattern.search(line):
                    try:
                        matches.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        logger.warning("trace 文件不存在: %s", trace_path)
        return []
    except PermissionError:
        logger.warning("trace 文件无权限: %s", trace_path)
        return []

    return matches[-max_lines:]  # 取最近 N 条


def build_reflection_prompt(
    task_goal: str,
    trace_lines: list[dict[str, Any]],
    max_input_length: int = 4000,
) -> list[dict[str, str]]:
    """构建反思 prompt 消息列表"""
    trace_text = json.dumps(trace_lines, ensure_ascii=False, indent=2)
    user_msg = REFLECTION_PROMPT_TEMPLATE.format(
        task_goal=task_goal or "（未提供）",
        trace_count=len(trace_lines),
        trace_content=trace_text[:max_input_length],
    )
    return [
        {"role": "system", "content": "你是 Kanban 任务失败分析专家，输出结构化 JSON 分析结果。"},
        {"role": "user", "content": user_msg},
    ]


def reflect_on_failure(
    task_id: str,
    task_goal: str,
    trace_lines: list[dict[str, Any]],
    config: KanbanReflectionConfig | None = None,
    llm_client: LLMClient | None = None,
) -> ReflectionResult:
    """核心函数：分析失败轨迹 → 返回结构化反思结果

    Args:
        task_id: Kanban 任务 ID
        task_goal: 任务目标描述
        trace_lines: 最近 N 轮交互消息列表
        config: 配置对象（可选，默认从 ENV 加载）
        llm_client: LLM 客户端（可选，默认从配置创建）

    Returns:
        结构化反思结果 ReflectionResult
    """
    cfg = config or KanbanReflectionConfig.from_env()
    client = llm_client or LLMClient(
        api_url=cfg.llm_api_url,
        model=cfg.llm_model,
        api_key=cfg.llm_api_key,
        timeout=cfg.llm_timeout,
    )

    messages = build_reflection_prompt(task_goal, trace_lines, cfg.max_input_length)

    logger.info("正在分析任务 %s 的失败原因...", task_id)
    try:
        response = client.chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=16384,  # min 16384 for sensenova-6.8-flash-lite fallback JSON output
            response_format={"type": "json_object"},
        )
        text = client.extract_content(response)
        data = client.parse_json_response(text)
    except (ConnectionError, ValueError) as e:
        logger.error("反思分析失败: %s", e)
        return ReflectionResult(
            task_id=task_id,
            failure_reason=f"反思分析调用失败: {e}",
            failure_type="llm_anomaly",
            suggestion="请检查 LLM 服务状态后重试",
            confidence=0.0,
        )

    # 解析反射结果
    reason = data.get("failure_reason", "未知错误")
    ftype = data.get("failure_type", "other")
    suggestion = data.get("suggestion", "建议检查任务状态后重试")
    confidence = float(data.get("confidence", 0.0))

    # 验证 failure_type 是否在合法范围内
    valid_types = set(cfg.failure_types)
    if ftype not in valid_types:
        logger.warning("未知失败类型 '%s'，已标记为 other", ftype)
        ftype = "other"

    result = ReflectionResult(
        task_id=task_id,
        failure_reason=reason,
        failure_type=ftype,
        suggestion=suggestion,
        confidence=min(max(confidence, 0.0), 1.0),
    )

    logger.info(
        "反思完成: %s | %s | 置信度 %.2f",
        result.failure_type, result.failure_reason, result.confidence,
    )
    return result
