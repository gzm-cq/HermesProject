---
name: verification-before-completion
description: 声称完成之前必须运行验证：测试、类型检查、lint，以证据为准
---
---
name: verification-before-completion
description: 声称完成之前必须运行验证：测试、类型检查、lint
---
# 完成前验证
声称完成之前必须运行验证，以证据为准。

## 流程
1. 运行测试套件（全部通过）
2. 运行类型检查（无错误）
3. 运行 lint（无新增警告）
4. 检查是否有未提交的改动
5. 只有全部通过才能声称完成
