# AGENTS.md - Customizing Qoder CLI Agent Interactions

## Top: Chinese conversation

Qoder CLI supports Chinese language interaction for all agent types. You can use Chinese to:
- Ask questions about codebase structure and functionality
- Request code modifications and implementations
- Configure agents and customize behavior
- Get documentation and usage guidance

All agents (Explore, Plan, general-purpose, qoder-guide, statusline-setup) understand and respond in Chinese.

> ✅ **Execution Rule**: When I say "正在执行...", "正在运行...", "正在调用...", or any other present-tense phrase indicating immediate action, execute it *without waiting for your confirmation*. This applies to all tools (Bash, Edit, Write, etc.).

This file documents how to configure and customize agent behavior in Qoder CLI.

## Available Agent Types

| Agent Type | Purpose | When to Use |
|------------|---------|-------------|
| `Explore` | Fast codebase exploration | Finding files, searching keywords, understanding code structure |
| `Plan` | Software architecture planning | Designing implementation strategies for complex tasks |
| `general-purpose` | Research and multi-step tasks | Open-ended questions requiring multiple queries |
| `qoder-guide` | Qoder CLI documentation | Questions about features, skills, settings, hooks |
| `statusline-setup` | Status line configuration | Customizing shell status line display |

## Configuration Options

### Agent-Specific Settings
- `thoroughness`: `quick`, `medium`, or `very thorough` (for Explore agent)
- `isolation`: `default` or `worktree` for isolated execution
- `model`: `sonnet`, `opus`, or `haiku` (if model override needed)

### Global Settings
- Agent timeout limits
- Default working directory for agents
- Permission modes for tool access

## Best Practices

- Use `Explore` agent for initial codebase discovery
- Enter `Plan` mode before implementing non-trivial features
- Prefer dedicated tools over Bash when possible
- Always verify file existence before editing
- Use worktrees for isolated development branches

## Example Usage

```bash
# Launch Explore agent for quick file search
qodercli agent explore --pattern "**/*.ts" --thoroughness quick

# Launch Plan agent for feature implementation
qodercli agent plan --prompt "Add user authentication flow"
```

> **Note**: This file serves as documentation and configuration reference. Actual agent configuration is managed through Qoder CLI's settings system.
## 已解决问题（2026-06 全部完成）

| # | 问题 | 状态 |
|---|------|------|
| 4 | post_llm_call 增量放置（格式不匹配 + numpy 布尔歧义已修复，提取入库已验证） | ✅ 已完成 |
| 5 | 因果链质量改进（enrich_text 守卫 + CAUSAL_TRIGGERS 预过滤 + 置信度分级已部署验证） | ✅ 已完成 |
| 6 | 知识树 recall 质量评估（每次注入 5 条，LLM 实际使用效果已确认） | ✅ 已完成 |
| 7 | redistribute general/root（773 条已清理，子科目已拆完，质量差的因果链已清理） | ✅ 已完成 |
