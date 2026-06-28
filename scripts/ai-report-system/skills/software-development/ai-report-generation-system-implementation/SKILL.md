---
name: ai-report-generation-system-implementation
description: >-
  AI report generation system. v5.5.0: optimize_structure 不做LLM重生成(raise ValueError)。
  不中断管线、不拆分为两次调用。report_goal.json 是单一全量文件，
  管线每完成一步就往里写一步。
version: 5.9.7
author: Hermes Agent
license: MIT
tags: [report-generation, ai-workflow, pipeline-driven, intent-driven]
related_skills:
  - hermes-toolset-architecture-migration
  - writing-plans
verified: true
---

# AI Report Generation System v5.3

## 写报告指令响应规则

**用户说"写报告"→ 优先使用 `bash scripts/run_report.sh "<主题>"`。**

这是**推荐入口**，不是硬规则。选择依据：

| 场景 | 用脚本 | 直接管线 |
|------|--------|----------|
| 首次运行（无 report_goal/fact_bank） | ✅ 推荐 | ❌ 缺前置条件 |
| 需要重新提取目标/事实 | ✅ 推荐 | ❌ 跳过检查 |
| 已有 report_goal（含 chapter_prompts）+ fact_bank（无冲突） | ❌ extract_facts 会从0重跑，冲突循环 | ✅ 直接启动 |
| 用户主动要求用脚本 | ✅ | ❌ |
| 脚本 stuck 在 extract_facts 循环 | ❌ | ✅ 手动检查条件后直接管线 |

**脚本的已知问题**：`extract_facts` 永远从0重跑，覆盖已处理的 `fact_bank.json`，冲突反复出现。当两个前置条件已满足时，应跳过脚本直接启动管线。

禁止：
1. ❌ 在 agent 中用 `execute_code` 拼凑 Python 片段来替代脚本或管线。
2. ❌ 在脚本之外手动执行 `define_goal` / `extract_facts` / pipeline 中的任何一步 **除非前置条件已满足且脚本 stuck**。

用户已经因为手动拆解步骤导致过目标确认跳过、目录未落盘、目录名不一致等问题。
如果脚本有 bug，先修脚本再运行——"先整理程序再做事"。

## 素材来源：用户自备 vs 搜索

管线支持两种素材来源，**不要假定必须由你来找素材**：

| 来源 | 用户行为 | 你的操作 |
|------|----------|----------|
| 用户自备 | 用户把文件放进 `inputs/` | 直接运行脚本，脚本会自动发现。**不需要问「素材在哪」「需要搜吗」** |
| 需要检索 | 用户说「查一下xx数据」 | 先 web_search，再把结果放入 `inputs/` 作为 .md |

**用户明确偏好**：自己把素材放进 `inputs/`。不要主动帮用户搜，除非用户明确要求。

```bash
bash scripts/run_report.sh "<报告主题>"
```

脚本自动执行：
```
[1] 提取目标 → 展示 → 你确认 y/n    ← 不可跳过
[2] extract_facts → 事实提取 → 冲突检测
[3] 检查冲突 → 有则列出等你处理
[4] 全部就绪 → 启动管线
```

> `pre_search` 不是强制步骤。目录在聊天中由用户确认，不需要 web 搜索来生成目录。

## 你的唯一任务：确认报告目标

**⚠️ 这一步不可跳过。** 必须展示目标供用户确认/修改，用户点头后才能进入后续步骤。

## 目标确认的迭代流程（来自用户纠正）

**用户明确要求：目标不是一次能确定的。** 必须多轮迭代，不可急于推进。

### 管线三步走的总流程

```
第一步：确定目标（goal）—— 可迭代多轮，直到用户说"可以了"
第二步：提取事实（extract_facts）
第三步：生成管线（pipeline）→ post_process_charts（图表+docx）
```

**不要急于推进。** 目标是管线的基础，目标不准确，后面生成内容视角就会偏。用户的模式：指正3-4个问题 → 修正 → 再看 → 指出新问题 → 反复直到满意。接受这个节奏。

### 流程

1. 通过 `run_goal_definition()` 提取目标摘要并展示
2. **等待用户反馈** — 不要问"可以吗？"就默认通过
3. 用户会指出遗漏项，如：
   - 缺少设备清单（硬件型号/数量/配置）
   - 缺少安全合规标准引用（等保2.0、ISO 27001）
   - 缺少与其他系统的接口定义（如与互联网层/内网层的边界）
   - 缺少预定义的图表需求
   - 缺少硬件供应周期风险
   - 术语措辞不统一（如"封闭测试场"应为"独立网络环境验证平台"）
4. 逐一修正后，重新展示目标
5. 重复步骤2-4直到用户说"可以了"
6. **只有用户确认后**才能进入 extract_facts 阶段

### 方案类 vs 分析类文档的视角差异

| 维度 | 项目建设方案（方案类） | 分析报告（分析类） |
|------|----------------------|------------------|
| 角色 | 企业信息化项目建设工程师 | 可行性研究分析师 |
| 核心问题 | 建什么、怎么建、花多少钱、什么时候建好 | 是否可行、条件是什么、风险有哪些 |
| chapter_prompts 结构 | 建设背景→目标→技术方案→预算→计划→组织→风险→请示 | 评估框架→各维度分析→综合结论 |
| 关键遗漏项（需主动检查） | 设备清单、合规标准、接口定义、图表需求、硬件供应风险 | 收益测算框架、NPV/ROI口径、沉没成本计算 |

### report_goal.json 的必填字段校验

管线 `run_full_pipeline_test.py` 会校验 `writing_role` 字段。已知缺失字段：
- **`tone`**：必须存在，管线会检查。如果初始目标只有 `voice`，需手动补充 `tone` 字段。

```python
# 修复示例
goal["writing_role"]["tone"] = goal["writing_role"].get("voice", "项目建设工程师视角")
```

## extract_facts 的常见假阳性冲突

以下冲突是误报，可以安全解析：

| 冲突名称 | 实际原因 | 判断方法 |
|----------|---------|---------|
| [数字冲突] 总投资金额不一致 | 总投5.27亿与各层细分(互联网50万+工控网650万+内网5.20亿=5.27亿)完全一致，差异仅为是否提及运营费用 | 验算：50+650+52000=52700 |

**处理方式**：将冲突移至 `resolved_conflicts`，标注"非实质冲突"后直接跳管线，不要重新运行 extract_facts（会再次触发同一冲突）。

### 从 extract_facts 循环中退出

当 `run_report.sh` 反复触发同一假阳性冲突时：
1. 手动编辑 `fact_bank.json` 解决冲突
2. **不重新运行脚本**，而是直接跳管线：
   ```bash
   python3 scripts/run_full_pipeline_test.py "<主题>"
   ```
   详见"直接启动管线（绕过循环脚本）"节。

## 前置条件：两个文件就绪

```bash
✅ reports/<topic>/report_goal.json 存在
✅ reports/<topic>/fact_bank.json 存在
✅ fact_bank.json conflicts 为空（冲突已处理）
```

## report_goal.json：单一全量文件

`report_goal.json` 是管线里唯一的中间文件。管线每完成一步就往里写一步，逐步累积：

```
管线开始前 → 只含目标（title/purpose/overall_strategy/writing_role）
管线规划后 → + chapter_prompts[]（含 title/writing_intent/key_points/materials_text/supplement_needed）
            + _execution.stategraph_done
管线结束后 → + 输出路径、阶段性标记
```

不需要额外中间文件。用户打开 `report_goal.json` 看到的即是管线当前进度。

## 管线拓扑

```
search_refs → [optimize_structure] → synthesize → curate
  → [coverage_check] → prompt_review → write → output
```

| 节点 | 职责 | 说明 |
|------|------|------|
| `optimize_structure` | 读取预定义章节结构 | 必须有 `report_goal.chapter_prompts`（含 title/writing_intent/key_points），否则 raise ValueError 报错退出。**不走 LLM 生成**——目录是用户在聊天确认的确定性信息，不需要管线内修正 |
| `synthesize` | 格式化传递 | chapter_prompts 为空时告警（兼容旧数据）|
| `coverage_check` | 覆盖度检查 + 自动补充 | <70% 触发搜索，补完重检；阈值从 report_config.yaml 读取；搜索结果去重 |
| `prompt_review` | **prompt 格式化审核**（非覆盖度检查） | `_build_prompt_review_prompt` 将现有杂散数据格式化为标准 prompt，LLM 输出优化后的 chapter_prompts。LLM 可追加 key_points、调整 chart_spec/section_type，但不能删改已有的 key_points 和 writing_intent。发现问题时直接输出优化 JSON 修正，不单独报告问题。实际的覆盖度检查由 `coverage_check` 负责。 |

## 架构原则

1. **skill 只做一件事：确认目标。** 一切确定性步骤（目录优化、curate、覆盖度检查、自动搜索）固化到管线或前置脚本中。
2. **管线是目标驱动，不是源文档驱动。** 章节结构由 `overall_strategy` 的评估维度决定。素材只在 `curate` 阶段才进入。**`optimize_structure` 节点不接收 `source_content`。**
3. **覆盖度不足自动补充。** `coverage_check` 从缺失 key_points 提取搜索词 → `MaterialService` 搜索 → 补充 `materials_text` → 重检。搜索结果与已有素材做子串去重。多章按缺失数降序、每章 15s 超时。
4. **管线不切断。** `orch.run()` 必须保持单次连续调用。中间产物写回 `report_goal.json`，不需要分两次调用。
5. **确认目标 + extract_facts 两步不可跳过。** 两次流程错误的教训。
6. **review_goal.json 逐步累积。** 管线跑完后同一个文件包含全部信息（目录+目标+key+素材+标记）。
7. **用户确认的目录必须结构化落盘到 `report_goal.chapter_prompts`。** 仅写在聊天记录里是不够的，必须作为结构化字段写入文件。`writing_intent` 必须非空，`key_points` 承载二级目录结构。
8. **`report_goal.json` = 用户在每个决策点上冻结的确定性记录。** 管线节点只读、不猜、不改。缺的信息要么在 report_goal 里，要么在前置脚本（extract_facts）里产生。
9. **`optimize_structure` 不做 LLM 生成。** 目录是在聊天中用户和你确认的，不是 LLM 优化的。`chapter_prompts` 为空时报错退出，不给 LLM "猜"的机会。

## 文档类型视角校验（Goal 确认后、管线启动前）

管线生成的 `report_goal` 会自动推断文档角色与写作视角，但**LLM 推断的角色不一定匹配用户实际需求**。在确认目标阶段必须主动校验视角是否准确：

### 常见视角偏差

| 用户需要的视角 | LLM 可能推断的视角 |
|--------------|-------------------|
| **项目建设方案**（建什么、怎么建、花多少、何时建成） | 可行性分析/技术评估（是否可行、风险多大） |
| **可行性评估报告**（条件可行/不可行、风险与回报） | 项目建设规划（怎么做、分几步） |

### 校验方法

在用户确认目标前，主动检查 `writing_role.role` 字段：

- 「建设项目负责人/项目建设工程师」→ 建设方案视角 ✅
- 「技术战略分析师/可行性研究分析师」→ 通常是评估/分析视角，需二次确认

**不要等用户逐字段纠正。** 当用户说「是建设方案，重新从项目落地角度生成」时，说明已生成的整个 `report_goal`（purpose/overall_strategy/writing_role）都偏了。此时应整体重写 goal，而非只改 role 字段。

### 核心执行纪律

**用户确认目标前，不得启动管线。** 目标可能需要多轮迭代才能确定——用户明确说过"不要急，目标不是一次能确定的"。确认流程：

1. **生成目标 → 展示 → 等待用户反馈**（不要自动确认）
2. **用户提出修改意见 → 调整 → 再次展示**
3. **用户说"可以了" → 锁定 report_goal.json → 再启动管线**

禁止在目标确认步骤中跳过或加速。每次修改 `report_goal.json` 后，必须重新展示全文（purpose + overall_strategy + nine-chapter outline）让用户确认。

## 管线生成内容的标题层级修复（已知问题）

管线生成的 `.md` 文件经常出现标题层级错乱问题，这是 LLM 写作阶段的系统性问题，需要在生成后的 QA 环节专门检查并修复：

### 常见标题层级问题

1. **章节编号重复**：LLM 可能对所有章节使用相同的编号（如多个章节都标"第四章"）
2. **H2/H3 混用**：LLM 可能把应该用 `# H1` 的章节标题写成 `## H2`
3. **LLM 元评论嵌入正文**：如「好的，收到。作为项目负责人，我已仔细审查您提供的章节内容...」——这类 LLM 自我对话内容必须删除
4. **H4/H5 多层嵌套**：目标中定义的 H1→H2 两层结构，LLM 可能生成为 H3→H4→H5

### 修复方法

生成 `.md` 后，检查标题结构是否符合 `report_goal.chapter_prompts` 中定义的层级。发现上述问题后，手工重写整个 `.md` 的标题结构（不要用 patch 逐条修复——标题层级错乱通常是全局性的，patch 难以覆盖所有用例）。

### 恢复工作（Resume）流程

当用户说"继续刚才的工作"（或类似表述）时，按以下顺序检查，不要直接跳入管线执行：

1. **定位正确的项目目录** — 项目根在 `/mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system/`，不在 `/root/reports/`。`/root/reports/` 可能存有陈旧副本，与实际项目不同步。始终从项目根（pyproject.toml所在目录）出发。
2. **检查当前状态** — 看 `reports/<topic>/` 下有什么：
   - `report_goal.json` 是否存在？是否已有 `chapter_prompts`？
   - `fact_bank.json` 是否存在？有无冲突？
   - `inputs/` 里是否有素材？
3. **展示完整目录给用户确认** — 不要直接问"可以跑吗"，而是展示每章的 `title` + `key_points`（H2子节），让用户确认后再推进。尤其注意：
   - 标题是否与上次确认的一致
   - H2子节是否完整
   - `writing_intent` 是否非空
4. **识别缺失环节** — 缺哪个补哪个（如缺 fact_bank → 跑 extract_facts），而不是跳过检查直接跑管线。

## 管线后处理：标题结构验证

管线生成的 `.md` 文件虽然章节数正确、字数达标，但 **LLM 生成的标题层级和编号可能完全错误**。已知问题：

| 问题 | 现象 | 影响 |
|------|------|------|
| 多个章节标同一编号 | 三个不同的章都标"第四章" | 目录混乱，编号失效 |
| H2/H3 混用做章节标题 | H2 当了 H1 的角色 | 目录结构扁平化 |
| LLM 元评论嵌入正文 | "好的，收到。作为XX负责人..." | 非正式内容混入正式文档 |
| 章节编号跳跃 | 从4.1直接跳到4.1（重复） | 编号体系崩溃 |

**必须执行的验证步骤：**

```bash
# 检查H1层级：应该只有 执行摘要 + 九章H1
grep "^# " reports/<topic>/<file>.md

# 检查编号连续性：应该1.1→1.2→...→9.3
grep "^## " reports/<topic>/<file>.md

# 检查是否有重复的章节号
grep "^# " reports/<topic>/<file>.md | grep -oP '[一二三四五六七八九]' | sort | uniq -d
```

如果发现标题层级错误，不要修复管线代码——直接手动重写 `.md` 文件的标题，然后用 `docx_exporter` 重新导出 DOCX。

## 管线后处理：docx 导出与目录清理

管线完成 + `post_process_charts.py` 运行后，会生成多个带时间戳的文件。**必须清理归一化**：

```bash
# 文件清理流程
# 1. 保留最新的 .md（无时间戳标准名）
cp "reports/<topic>/工控网智能制造AI能力建设方案_20260504_104724.md" \
   "reports/<topic>/工控网智能制造AI能力建设方案.md"

# 2. 删除中间产物（时间戳版本和备份文件）
rm -f "reports/<topic>/"*_*.md  # 时间戳 md
rm -f "reports/<topic>/"*_*.docx # 时间戳 docx（如果有同名标准化版本）
rm -f "reports/<topic>/fact_bank_*.json"  # fact_bank 备份
rm -f "reports/<topic>/~$*"  # Office 锁文件

# 3. 标准化 docx 文件名（如果 post_process_charts 产出的是带时间戳版本）
cp "reports/<topic>/工控网智能制造AI能力建设方案_20260504_104713.docx" \
   "reports/<topic>/工控网智能制造AI能力建设方案.docx"
```

**注意**：如果 Windows 文件系统上 `rm` 报 `Permission denied`，用 `chmod 777` 后再删。

## post_process_charts LLM 调用机制与退路流程

### 正常路径（2026-05-21 修复后）

`post_process_charts.py` 在独立 Python 环境（非 Hermes agent 上下文）中调用 LLM 时，走降级路径 `_call_direct()`。该路径从环境变量读取 API Key，按优先级查找：

1. `LITELLM_MASTER_KEY` → LiteLLM 网关（http://127.0.0.1:4142）
2. `DEEPSEEK_API_KEY` → DeepSeek 官方
3. `SILICONFLOW_API_KEY` → SiliconFlow
4. `GLM_API_KEY` → 智谱 AI
5. `SHANGTANG_API_KEY` → 商汤 API

查到第一个非空 key 即可正常调用。输出示例：
```
[1/3] LLM 分析图表推荐...
  推荐 5/11 章配图
[2/3] 提取数据 + 渲染图表...
  ✅ architecture_table 已渲染
[3/3] 导出 .docx...
  ✅ .docx (1 图, 155KB)
```

### 退路流程（当 env 中无可用 API Key 时）

LLM 用于两步文字分析（推荐配图位置 + 提取图表数据）。如果 LLM 调用失败，手动配图+DOCX：

```bash
# Step 1: 创建 topic 目录结构
mkdir -p "/mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system/reports/<topic>/charts"

# Step 2: 用 infogen.sh 生成关键信息图（详见 infogen skill）
cd "/mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system/reports/<topic>/charts"
# 每张图一行（生成+下载原子化）
SHANGTANG_API_KEY="${SHANGTANG_API_KEY}" \
curl -s https://token.sensenova.cn/v1/images/generations \
  -H "Authorization: Bearer ${SHANGTANG_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"sensenova-u1-fast","prompt":"信息图描述"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['url'])" \
  | xargs -I{} curl -L -o <chart_name>.png {}

# Step 3: 写独立 python-docx 导出脚本（不触发项目包的 __init__.py）
python3 scripts/export_docx_with_charts.py "<topic>"
```

### 配图位置标记规则

在脚本中通过标题关键字匹配插入配图。常用标记对：

| 配图 | 匹配的标题文本 |
|------|---------------|
| 组织架构图 | `二、组织架构` 或 `项目组织架构` |
| 数仓架构图 | `1.3 数据仓库` 或 `数据仓库分层` |
| 路线图/时间轴 | `三、实施路线图` 或 `项目路线图` |
| 依赖关系图 | `四、工作项依赖` 或 `依赖关系` |

匹配逻辑：`if key in section_title`，不要求完全相等。

### 脚本模板位置

一个经过验证的完整导出脚本保存在 `templates/export_docx_with_charts.py`，包含：
- 封面页生成
- Markdown 标题层级解析（H1-H4）
- 表格渲染（从 Markdown 表格行解析）
- 配图插入 + 图注
- 引用块格式化
- 代码块/依赖图等宽字体

复用时只改：`MD_PATH`、`CHARTS_DIR`、`CHART_MAP` 三个变量。

⚠️ **关键限制**：python-docx 的图片插入不支持用 BytesIO 做内存内的带样式居中排版——必须从磁盘路径读取 `run.add_picture(str_path)`。

---

管线（`run_full_pipeline_test.py`）默认**跳过图表生成和 docx 导出**，需要在管线完成后单独运行 `post_process_charts.py`：

```bash
cd "/mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system"
python3 scripts/post_process_charts.py "报告主题"
```

该脚本自动完成：
1. LLM 分析报告内容 → 推荐需配图的章节
2. 提取数据 → 渲染图表（PNG）— 支持三种图表类型：
   - 数据图表（柱状图/折线图/饼图）→ matplotlib 渲染
   - 信息图（时间轴/流程图/路线图/对比图）→ infogen.sh（商汤 sensenova-u1-fast）
3. 导出 .docx（含封面、目录、内嵌图表）

输出文件：`reports/<topic>/<title>_<timestamp>.docx`

如果希望对特定 .md 文件做 docx 而非自动查找最新文件：
```bash
python3 scripts/post_process_charts.py "报告主题" --md "reports/<topic>/<特定文件>.md"
```

## 独立文档写作 + DOCX 导出（非管线流程 — ⚠️ 用户偏好：走管线）

**用户明确要求所有文档类型都走三步生成管线（`run_report.sh`）**，包括方案/计划类文档。手动作者流程是反模式，仅在以下情况使用：
- 用户明确要求"直接写"且不通过管线
- 需要快速验证内容方向时做草稿

手动作者工作流（仅用于上述场景）：

1. 初始化目录：`mkdir -p reports/<主题名称>/charts/ reports/<主题名称>/inputs/`
2. 手动编写 `.md` 源文件
3. 通过 `docx_exporter.export_to_docx()` 导出 Word

```python
from pathlib import Path
import sys
sys.path.insert(0, "<项目根>/src")
from export.docx_exporter import export_to_docx

result = export_to_docx(
    title="文档标题",
    full_content=open("源文件.md", encoding="utf-8").read(),
    chart_images=[(chapter_index, Path("图表路径.png")), ...],
    output_path=Path("输出路径.docx"),
)
```

### ⚠️ matplotlib 依赖陷阱

`export.docx_exporter` 通过 `__init__.py` 引用了 `chart_renderer`，该模块依赖 `matplotlib`。如果环境中未安装 matplotlib，`from export.docx_exporter import export_to_docx` 会报 `ModuleNotFoundError: No module named 'matplotlib'`。

**解决方案优先级**：
1. 安装 matplotlib：`pip3 install matplotlib`（最彻底）
2. 临时绕过：写独立导出脚本，直接使用 `python-docx`，不触发项目包的 `__init__.py`

**独立导出脚本模板**：
```python
import re
from pathlib import Path
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

md_path = Path('源文件.md')        # 输入 markdown 路径
output_path = md_path.with_suffix('.docx')  # 输出 docx 路径

with open(md_path, 'r', encoding='utf-8') as f:
    text = f.read()

doc = Document()

# 设置默认字体
style = doc.styles['Normal']
font = style.font
font.name = 'Microsoft YaHei'
font.size = Pt(11)
style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')

def add_run_text(p, text):
    """支持 **bold** 内联标记"""
    parts = re.split(r'(\*\*.*?\*\*)', text)
    for part in parts:
        if not part:
            continue
        inner = part[2:-2] if part.startswith('**') else part
        run = p.add_run(inner)
        run.font.bold = part.startswith('**')
        run.font.size = Pt(11)
        run.font.name = 'Microsoft YaHei'
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')

for line in text.split('\n'):
    stripped = line.strip()
    if not stripped:
        continue
    if stripped.startswith('#'):
        level = min(len(stripped.split()[0]), 9)
        h = doc.add_heading(stripped.lstrip('#').strip(), level=level)
        for run in h.runs:
            run.font.name = 'Microsoft YaHei'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
    else:
        p = doc.add_paragraph()
        add_run_text(p, stripped)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = Pt(18)

doc.save(str(output_path))
print(f'导出成功: {output_path}')
```

管线流程与手动流程的区别：

| 维度 | AI 管线流程 | 手动作者流程 |
|------|-----------|------------|
| 适用场景 | 数据驱动的分析报告 | 策略/方案/计划类文档 |
| 章节结构生成 | 管线自动（optimize_structure） | 人工编写 |
| 图表映射参数 | `prompt_idx + 2` 偏移 | 按 H1 章节序号（从1开始） |
| 依赖工具 | `run_report.sh` / `post_process_charts.py` | `docx_exporter.export_to_docx()` |

## 直接启动管线（绕过循环脚本）

当以下前置条件全部满足时，可以跳过 `run_report.sh` 直接启动管线：

```bash
# 确认三条件
cd "/mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system"
python3 -c "
import json
g = json.load(open('reports/<topic>/report_goal.json'))
fb = json.load(open('reports/<topic>/fact_bank.json'))
assert g.get('chapter_prompts'), '缺少 chapter_prompts'
assert fb.get('facts'), 'fact_bank 为空'
assert not fb.get('conflicts'), '仍有未处理冲突'
print('✅ 全部就绪')
"

# 直接启动管线
python3 scripts/run_full_pipeline_test.py "<主题>"
```

### 何时应该用直接启动而非脚本

`run_report.sh` 每次都会重新跑 `extract_facts`，这会从零生成 `fact_bank.json`，覆盖你已处理的冲突。当你已经：
- 有 `report_goal.json`（含 `chapter_prompts`）
- 有 `fact_bank.json`（冲突已处理）
- 用户已确认目标

……应该直接启动管线，避免 extract_facts 循环。

### 何时必须用脚本

- 首次运行（没有 `report_goal.json` 或 `fact_bank.json`）
- 需要重新提取目标或事实
- 用户主动要求用脚本

## 独立 .md → DOCX 快速导出（管线第三步的退路）

当用户有**已完成的 .md 文件**需要直接导出 DOCX（不走管线前两步），使用 `post_process_charts.py` 的 `--md` 参数：

```bash
cd /mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system
python3 scripts/post_process_charts.py "主题名" \
  --md "reports/主题名/源文件.md" \
  --output "输出路径.docx"
```

**前置条件**：
- 仅依赖 .md 文件本身，不依赖管线中间数据（report_goal.json / fact_bank.json）
- `pip install requests` 确保已安装

**注意**：`docx_exporter.py` 的表格解析分隔行检测已修复。`_parse_markdown_table()` 中使用正则 `r'^\|[-:]+(\|[-:]+)*\|$'` 支持任意列数表格。如果导出后表格异常，先确认部署版本是否已同步最新代码。

## 相关参考

- `references/backup-knowledge-base-security-value.md` — 独立网络环境下备份知识库的企业知识安全定位：核心价值是防人员流失导致知识灭失（恶意删除），并非防硬件故障。描述时应强调"独立于个人意志的不可逆保全机制"
- `references/construction-plan-cost-breakdown.md` — 建设方案硬件/软件费用分解模式：硬件清单单价推断、软件授权费用明细、三表（硬件/软件/预算）交叉验证模型，含常见设备参考价：区分确定/不确定收益、五年窗口与全生命周期NPV、三种情景分析
- `references/recruitment-necessity-article-pattern.md` — 招聘必要性文章结构：转型任务背景→现状差距分析→团队规划→岗位逐一对应→结论，含关键原则与常见错误
- `references/construction-plan-pipeline-fix-patterns.md` — 建设方案管线产出系统性修复模式：8步检查清单（标题层级→硬件核价→软件清重→预算交叉→请示同步→设备延用→术语统一→交付确认），优先级排序，单轮修复时间估算
- `references/construction-plan-cross-section-audit-patterns.md` — 建设方案跨节/跨章审计模式（6种模式）：数字一致性交叉验证、验收标准完整性、交付物-里程碑时序、POC定性一致性、时间口径跨节一致性、告警阈值一致性。适用于5轮以上审计后的深层扫描
- `references/search-dependency-setup.md` — 搜索依赖（duckduckgo_search）安装方法：uv-managed 环境用 `pip3 install --break-system-packages`，已知网络限制问题
- [源文件逻辑审计方法](references/source-document-logical-audit.md)：数字加总一致性、自相矛盾范围、编号残留、章节顺序错位、预算明细遗漏、术语偏离、标题层级跳跃、时间跨度错误、批量修正双重验证、PPTX交叉验证——在投入 pipeline 前审查源文件，以及在修正源文件后同步校验PPTX。包含5轮递进审计框架（算术/结构 → 细节/术语 → 元级 → 领域逻辑/假设链 → 跨章/决策门），其中第4轮新增跨章假设一致性、情景数学透明度、框架承诺交付差、因果链正确性、术语范围歧义5个检查维度，第5轮新增决策触发器可达性、章节边界完整性、跨表范围对齐3个检查维度

## 陷阱

- ❌ 不要跳过目标确认步骤。必须先展示、等确认、再保存。
- ❌ **不要急于通过目标。** 用户明确说"目标不是一次能确定的"。需要多轮迭代：展示 → 用户指正 → 修正 → 重新展示 → 重复，直到用户说"可以了"。
- ❌ 不要在调度脚本之外手动拼凑步骤。用 `scripts/run_report.sh`。  
- ❌ 不要把管线切成两段调用来实现中间产物输出。在 `orchestrator.run()` 内部写回 `report_goal.json`。
- ❌ 不要硬编码报告类型特定的示例结构到 prompt 中——系统是通用的。
- ❌ 不要添加中间确认暂停点。只管输出到 `report_goal.json`，用户自行查看。
- ❌ 不要让 `optimize_structure` 接收 `source_content`。素材只在 `curate` 阶段进入。
- ❌ `pre_search` 不是强制步骤。
- ❌ 不要假定管线生成的 DOCX 目录结构是正确的。LLM 生成的标题层级经常错乱：多个章节标同一编号、H2/H3 混用、LLM 元评论残留。生成后用 `grep "^# " .md` 和 `grep "^## " .md` 验证层级。发现问题后直接手动重写标题结构。
- ❌ 方案类文档审计时，**请示事项中的预算数字是常见过时点**。管线的请示事项章节在正文更新预算后不会自动同步。必须逐项核对请示事项中的硬件/软件/安全/实施/知识库/不可预见六类金额是否与正文预算表一致。不一致则手动修正请示事项。
- ❌ 不要在无用户确认前导出 DOCX。用户明确要求：修改方案 → 让用户看 → 用户说可以 → 再导 DOCX。
- ✅ `docx_exporter.py` 中 `_parse_markdown_table` 的分隔行检测已修复，使用正则 `r'^\|[-:]+(\|[-:]+)*\|$'` 支持任意列数。详见 `references/docx-exporter-table-separator-bug-20260523.md`。
- ❌ 不要在管道后直接交付 `post_process_charts.py` 产出的带时间戳文件。**必须清理归一化**：删掉中间产物版本和备份 .json 文件，只保留标准化名称的 .md 和 .docx。
