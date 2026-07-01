# Router 全查率降低 SPEC 实施计划

> **审计状态**：已完成分析（2026-07-01）
> **根因数据**：基于 24h trace.log（262 次 Router 调用）
> **原则**：不修改 Hermes Gateway 源码，所有优化在插件层面实施
> **风险**：无漏查风险（未改动 fallback 逻辑）

---

## 一、问题全景

**当前全查率（all_true）：61.5%（161/262）**

```
总调用 262 次
├── 被迫全开 145 次 (55.3%)
│   ├── 401 认证错误       102 次 (39.0%) → ✅ 已过时（MoA 配置期间，当前 0）
│   ├── JSON 解析失败       34 次 (13.0%) → 🎯 P0
│   └── 其他错误             9 次 (3.4%)  → ⏸ 零散偶发
└── 自愿全开                16 次 (6.1%)  → 🎯 P1
```

**正常决策时（排除 fallback），86.3% 已经是非全开的**，说明 prompt 本身在正常工作。

---

## 二、P0 — JSON 解析失败（34次/天，13%）

### 问题
`router.py` 第 88-93 行已有 `reasoning_content` 兜底提取，但 34 次解析仍失败。

当前提取逻辑：
```python
m = re.search(r"\{[^}]*[\"']h[\"'][^}]*\}", rc, re.DOTALL)
```
问题：正则 `[^}]*` 不允许 `}` 出现在 JSON 内部，遇到嵌套 JSON 或 JSON 中包含对象值（如 `{"h":true,"kt":false}`）时匹配失败。

### 实现方案

在 `router.py` 修改 `_parse_mask()` 函数，增加 robust JSON 提取：

```diff
  def _parse_mask(text: str) -> dict[str, bool] | None:
      try:
          data = json.loads(text)
      except (json.JSONDecodeError, TypeError):
+         # 尝试从垃圾字符中提取完整的 JSON 对象
+         m = re.search(r'\{(?:[^{}]|"(?:\\.|[^"\\])*")*\}', text, re.DOTALL)
+         if m:
+             try:
+                 data = json.loads(m.group(0))
+             except json.JSONDecodeError:
+                 return None
+         else:
              return None
```

改动范围：`router.py` 一个函数，3 行。

验证方法：
- 收集 trace.log 中解析失败的 34 条原始 LLM 响应
- 用新正则逐一测试，确保全部能解析
- 测试特殊情况：嵌套括号、extra chars、换行等

---

## 三、P1 — prompt 自愿全开（16次/天，6.1%）

### 问题
`source_defs.py` 第 61 行 `"宁可多开不遗漏"` 鼓励模型在不确定时全开。

### 实现方案

在 `source_defs.py` 的 `build_router_prompt()` 中做两处修改：

**① 删消极规则：**
```diff
-    lines.append("- 宁可多开不遗漏")
```

**② 加积极约束：**
```diff
+    lines.append("")
+    lines.append("输出约束：")
+    lines.append("- 至少一项为 false")
+    lines.append("- 当只需要一个源时，只设那个源为 true")
+    lines.append("- "可能需要"不等于"需要"——不确定时不查")
```

改动范围：`source_defs.py` 一个函数，1 删 4 加。

---

## 四、验收标准

| 标准 | 当前 | 目标 | 测量方式 |
|:----|:---:|:----:|:--------|
| 自愿 all_true 占比 | 6.1%（16次） | **<3%** | trace.log `router_mask` 分布（24h 窗口） |
| JSON 解析失败数 | 34次/天 | **<5次/天** | trace.log `JSON 解析失败` 计数 |
| 非全开决策占比 | 38.5% | **>60%** | trace.log `router_mask` 分布 |
| 漏查（用户投诉） | 无 | 无新增 | 观察飞书/CLI 反馈 |

---

## 五、实施步骤

| 步 | 内容 | 文件 | 修改行数 |
|:--:|:----|:----|:--------:|
| 1 | 改 `build_router_prompt()`：删"宁可多开"+加输出约束 | `source_defs.py` | 1 删 4 加 |
| 2 | 改 `_parse_mask()`：加健壮 JSON 提取 | `router.py` | ~3 行 |
| 3 | 部署 `knowledge-navigation` | `deploy/deploy.sh deploy knowledge-navigation --yes` | — |
| 4 | 观察 24h 验证 | trace.log | — |

---

## 六、回退方案

如果需要回退，通过 deploy rollback 一键恢复：

```bash
cd /mnt/d/HermesProject && bash deploy/deploy.sh rollback knowledge-navigation
```

回滚后 gateway 会自动重启加载旧版本。
