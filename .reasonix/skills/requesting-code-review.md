---
name: requesting-code-review
description: 在完成代码后派遣审查 agent 检查代码质量，按严重级别报告问题
---
---
name: requesting-code-review
description: 在完成代码后派遣审查 agent 检查代码质量，按严重级别报告问题
---
# 请求代码审查
在任务之间触发，审查已实现的代码是否按计划执行。

## 流程
1. 对比代码与计划规格，检查是否按设计实现
2. 逐文件检查：正确性、安全性、测试覆盖、边界情况
3. 按严重级别报告：critical（阻塞）/ warning（建议）/ nit（风格）
4. critical 问题必须修复后才能继续
