# AI 报告导出工具

高质量 Markdown 转 DOCX 导出工具集。零路径依赖，任意位置可用。

## 定位

配合商汤 `sn-deep-research` skill 使用：
- 研究和写作：用 `sn-deep-research` 生成 .md 报告
- 导出交付：用本工具将 .md 转为精美的 .docx 文档

## 主要特性

- **零路径依赖**：输入输出通过参数指定，不假定目录结构
- **Markdown 完整支持**：标题、表格、列表、引用块、代码块、加粗、斜体、链接、删除线、行内代码、任务列表、嵌套列表
- **代码块**：围栏代码块（```），灰色背景 + Consolas 等宽字体，支持语言标记
- **行内代码**：`` `代码` `` 保留 Consolas 字体 + 浅灰背景
- **超链接**：`[文本](url)` 保留为可点击超链接（蓝色 + 下划线）
- **表格列对齐**：支持 `|:---|`（左）、`|:---:|`（中）、`|---:|`（右）
- **引用块合并**：连续 `>` 行合并为一段，支持引用块内的行内标记
- **嵌套列表**：按缩进自动识别层级（每 2 空格一级）
- **任务列表**：`- [ ]` 渲染为 ☐，`- [x]` 渲染为 ☑
- **HTTP 图片**：`![](https://...)` 自动下载后嵌入
- **图片自适应**：竖图按高度限制，横图按宽度限制，不变形
- **Mermaid 图**：识别 ` ```mermaid ` 块，可自动调用 sn-image-base 生成信息图并插入正确位置，支持内容缓存
- **水平分隔线**：支持 `---`、`***`、`___` 三种 Markdown 规范
- **配图插入**：按标题关键字匹配 charts/ 目录下的 PNG
- **信息图生成**：集成商汤 `sn-image-base`，自动生成缺失图表
- **VLM 审核**：生成后自动审核质量，PASS 才保留
- **封面 + 目录**：支持自定义标题、副标题、目录开关（默认开启）
- **文档元数据**：写入 docx 的 title/author/subject/created（可在 Word 属性面板查看）
- **原子写入**：先写 .tmp 再 rename，避免文件被占用时产生半成品
- **批注提取**：从 .docx 提取批注，按章节分组输出 JSON（保留文档顺序）

## 安装

通过 HermesProject 统一部署脚本安装：

```bash
./deploy/deploy.sh deploy ai-report-system --yes
```

部署后项目位于 `~/.hermes/scripts/ai-report-system/`。

## 依赖

- python-docx >= 1.1.0
- matplotlib >= 3.7.0（图表渲染，可选）

---

## 使用方式

所有命令都在项目根目录下执行，或用绝对路径调用脚本。

### 基础导出

最简单的用法，把 .md 转成 .docx：

```bash
python3 scripts/export_docx.py report.md -o report.docx
```

参数：
- `report.md`：输入的 Markdown 文件（必填）
- `-o report.docx`：输出路径，不填则同目录同名 .docx

---

### 自定义封面和元数据

设置标题、副标题和文档元数据，生成专业的封面页：

```bash
python3 scripts/export_docx.py report.md -o report.docx \
  --title "2026年度技术白皮书" \
  --subtitle "技术战略部 · 内部资料" \
  --author "张三" \
  --subject "2026年度技术规划"
```

参数：
- `--title`：封面主标题（不填则取 md 文件名）
- `--subtitle`：封面副标题（可选）
- `--author`：文档作者（写入 docx 元数据，默认 "Hermes AI"，在 Word 属性面板可见）
- `--subject`：文档主题（写入 docx 元数据，默认取标题）

---

### 目录控制

目录默认自动生成，不需要可以关掉：

```bash
# 默认：生成目录页
python3 scripts/export_docx.py report.md -o report.docx

# 不要目录
python3 scripts/export_docx.py report.md -o report.docx --no-toc
```

参数：
- `--toc`：生成目录（默认）
- `--no-toc`：不生成目录

---

### 插入配图

按章节标题关键字匹配图片，自动插入到对应章节后面：

**第一步**：准备配图目录和映射文件

```
charts/
├── 组织架构.png
├── 数仓架构.png
└── 路线图.png

chart_map.json
```

`chart_map.json` 格式：

```json
{
  "组织架构": "组织架构.png",
  "数据仓库": "数仓架构.png",
  "实施路线": "路线图.png"
}
```

> 匹配规则：标题文本包含 key 就算匹配。比如"组织架构与职责"会匹配 key "组织架构"。

**第二步**：导出时指定配图目录和映射：

```bash
python3 scripts/export_docx.py report.md -o report.docx \
  --charts ./charts/ \
  --chart-map chart_map.json
```

参数：
- `--charts`：配图目录路径
- `--chart-map`：配图映射 JSON 文件（不填则使用内置的默认映射）

---

### 自动生成缺失图表

如果某些图表还没有，调用商汤 `sn-image-base` 自动生成（需要先安装 sn-image-base skill）：

```bash
python3 scripts/export_docx.py report.md -o report.docx \
  --charts ./charts/ \
  --chart-map chart_map.json \
  --generate
```

生成后用 VLM 审核质量，PASS 才保留，FAIL 则删掉重试：

```bash
python3 scripts/export_docx.py report.md -o report.docx \
  --charts ./charts/ \
  --chart-map chart_map.json \
  --generate --review --max-rounds 3
```

参数：
- `--generate`：启用自动生成（默认关闭）
- `--review`：生成后 VLM 审核（需要 --generate）
- `--max-rounds`：最多重试几轮（默认 1）
- `--image-size`：图片尺寸，默认 `2k`
- `--aspect-ratio`：宽高比，默认 `16:9`
- `--api-key` / `--base-url` / `--model`：商汤 API 配置（可选，默认读环境变量）

---

### Mermaid 图自动渲染

如果 md 里有 ` ```mermaid ` 代码块，可自动调用 sn-image-base 生成信息图，插入到原位置：

```bash
python3 scripts/export_docx.py report.md -o report.docx \
  --render-mermaid
```

生成的图片默认存放在 `mermaid_images/` 目录（和 md 同目录），可用 `--mermaid-output` 指定：

```bash
python3 scripts/export_docx.py report.md -o report.docx \
  --render-mermaid \
  --mermaid-output ./output/mermaid/ \
  --review \
  --max-rounds 3
```

强制重新生成所有 Mermaid 图片（忽略缓存）：

```bash
python3 scripts/export_docx.py report.md -o report.docx \
  --render-mermaid \
  --force
```

参数：
- `--render-mermaid`：启用 Mermaid 渲染
- `--mermaid-output`：图片输出目录（默认 `<md同目录>/mermaid_images/`）
- `--force`：忽略缓存，强制重新生成所有 Mermaid 图片
- `--review` / `--max-rounds`：VLM 审核 + 重试（和 --generate 共享参数）

> 支持的 Mermaid 类型：graph/flowchart、sequence、class、state、er、pie、gantt、journey、mindmap、timeline
> 生成失败的 Mermaid 块会保留原代码，不影响其他内容。
> 未闭合的 ` ```mermaid ` 块（缺少结束 ```）会被跳过并警告，不会吞掉后续内容。
> **内容缓存**：相同 mermaid 代码（md5 一致）会复用已生成的图片，避免重复调用 API。缓存文件为输出目录下的 `.mermaid_cache.json`。

### 只预览不导出

看看会匹配到哪些图表，不实际生成 docx：

```bash
python3 scripts/export_docx.py report.md \
  --charts ./charts/ \
  --chart-map chart_map.json \
  --dry-run
```

也可以预览 Mermaid 块数量：

```bash
python3 scripts/export_docx.py report.md \
  --render-mermaid \
  --dry-run
```

---

### 其他 CLI 参数

```bash
# 查看版本
python3 scripts/export_docx.py --version

# 详细日志（含 sn_agent_runner 的 stderr 详情）
python3 scripts/export_docx.py report.md -o report.docx --render-mermaid -v

# 静默模式（只显示 WARNING 及以上）
python3 scripts/export_docx.py report.md -o report.docx -q
```

参数：
- `--version`：显示版本号
- `-v` / `--verbose`：DEBUG 级日志
- `-q` / `--quiet`：WARNING 级日志
- `--dry-run`：只预览不导出

---

### 提取 Word 批注

从别人改过的 .docx 里把批注提取出来，按章节分组，方便对接修订流程：

```bash
# 终端打印摘要
python3 scripts/docx_comments.py report.docx

# 输出 JSON 文件
python3 scripts/docx_comments.py report.docx -o comments.json

# 包含每个章节的完整文本（方便 LLM 理解上下文）
python3 scripts/docx_comments.py report.docx --full -o comments.json
```

输出 JSON 格式：

```json
[
  {
    "chapter_title": "一、项目背景",
    "comments": ["这里需要补充数据来源", "建议增加对比分析"],
    "comment_count": 2,
    "full_content": "章节完整文本（--full 时包含）"
  }
]
```

---

### 典型工作流示例

**场景：用商汤 skill 写报告，然后导出 docx 交付**

```bash
# 1. 用 sn-deep-research 生成 report.md（在 Hermes 对话中完成）

# 2. 准备配图（可选）
mkdir -p charts
# 把已有的图片放进去，或者让 sn-infographic 生成

# 3. 准备 chart_map.json
cat > chart_map.json << 'EOF'
{
  "系统架构": "架构图.png",
  "实施路线": "路线图.png"
}
EOF

# 4. 导出带封面带目录带配图的 docx
python3 scripts/export_docx.py report.md -o report.docx \
  --title "XX项目技术方案" \
  --subtitle "2026年7月 · v1.0" \
  --charts ./charts/ \
  --chart-map chart_map.json

# 5. 发给同事评审，收回带批注的 docx
# 6. 提取批注，准备修订
python3 scripts/docx_comments.py report_reviewed.docx -o comments.json
```

---

## 支持的 Markdown 语法

| 元素 | 语法 | 说明 |
|------|------|------|
| 标题 | `# H1` `## H2` `### H3` ... | 最多 9 级，用 Word Heading 样式 |
| 粗体 | `**文字**` | 加粗显示 |
| 斜体 | `*文字*` | 斜体显示 |
| 行内代码 | `` `代码` `` | Consolas 字体 + 浅灰背景 |
| 删除线 | `~~文字~~` | 删除线显示 |
| 链接 | `[文本](url)` | 可点击超链接（蓝色 + 下划线，保留 URL） |
| 换行 | `<br>` | 行内换行 |
| 代码块 | <code>\`\`\`lang</code> | 灰色背景 + Consolas 等宽字体，带语言标记 |
| Mermaid 图 | <code>\`\`\`mermaid</code> | --render-mermaid 时自动生成信息图并插入；否则标注说明 |
| 图片 | `![alt](path)` 或 `![alt](http://...)` | 居中插入，带图注（统一编号），支持 HTTP 下载 |
| 无序列表 | `- 项` 或 `* 项` | Word 项目符号，支持嵌套（按缩进识别层级） |
| 有序列表 | `1. 项` 或 `1、项` | Word 编号列表，支持嵌套 |
| 任务列表 | `- [ ] 项` / `- [x] 项` | 渲染为 ☐ / ☑ |
| 引用块 | `> 引用内容` | 左边框灰色背景，连续行合并为一段，支持行内标记 |
| 表格 | Markdown 表格 | 自动渲染为 Word 表格，支持列对齐 `:---` / `:---:` / `---:` |
| 水平分隔线 | `---` `***` `___` | 转为分页符 |

---

## 项目结构

```
ai-report-system/
├── src/
│   └── ai_report/
│       ├── export/          # 导出核心模块
│       │   ├── docx_exporter.py   # DOCX 导出主逻辑
│       │   └── chart_renderer.py  # matplotlib 图表渲染（可选）
│       └── __init__.py
├── scripts/
│   ├── export_docx.py       # 主导出脚本（CLI 入口）
│   └── docx_comments.py     # Word 批注提取工具
├── tests/                   # 单元测试
├── README.md
├── pyproject.toml
└── requirements.txt
```

---

## 与商汤 skill 的协作

| 阶段 | 使用工具 | 说明 |
|------|---------|------|
| 深度研究 | `sn-deep-research` | 多 Agent 调研，多维度并行 |
| 报告写作 | `sn-deep-research` (report-writer) | 大纲 → 分章写作 → 全文缝合 |
| 信息图制作 | `sn-infographic` / 本工具 `--generate` | 商汤 skill 质量更高 |
| DOCX 导出 | 本工具 `export_docx.py` | md → 精美 docx |
| 批注反馈 | 本工具 `docx_comments.py` | 提取 Word 批注，对接修订 |
