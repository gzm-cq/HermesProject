"""
Hermes 插件模板 - 基础实现
"""


def register(ctx) -> None:
    """Hermes 插件注册入口函数。
    
    Args:
        ctx: Hermes 插件上下文对象，提供 register_hook 等方法
    """
    # 示例钩子注册
    # ctx.register_hook("pre_llm_call", pre_llm_call)
    # ctx.register_hook("post_llm_call", post_llm_call)
    
    # 基础健康检查
    if hasattr(ctx, 'logger'):
        ctx.logger.info("Hermes 插件模板已加载")
