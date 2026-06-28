---
name: test-driven-development
description: 严格 RED-GREEN-REFACTOR TDD 循环：先写失败测试 → 写最少代码通过 → 重构
---
---
name: test-driven-development
description: 严格 RED-GREEN-REFACTOR TDD 循环：先写失败测试 → 写最少代码通过 → 重构
---

# 测试驱动开发

强制 RED-GREEN-REFACTOR 循环。先写测试，再写代码。

## 循环
1. **RED** — 写一个失败的测试，验证它确实失败
2. **GREEN** — 写最少量的代码让测试通过
3. **REFACTOR** — 重构代码，保持测试绿色

## 约束
- 没有测试就不写实现代码
- 先写测试再写实现，这是非协商的
- 测试覆盖率优先于功能完整性
- 删除在测试之前编写的所有代码
