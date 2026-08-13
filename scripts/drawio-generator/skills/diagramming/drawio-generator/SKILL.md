---
name: drawio-generator
description: 根据自然语言描述生成 draw.io 矢量架构图/流程图/管线图，输出 .drawio 或 SVG 文件
version: 1.2.0
author: Hermes Agent
triggers:
  - "帮我画一张图"
  - "生成架构图"
  - "生成 drawio"
  - "画图表"
  - "画个流程图"
  - "画个管线图"
metadata:
  category: diagramming
  tier: 1
  user_visible: true
---

# drawio-generator

## 功能

根据自然语言描述，自动规划布局并生成 **.drawio** 或 **SVG** 矢量图文件。
生成的文件可用 draw.io 桌面版打开自由编辑，或直接插入论文 Word 文档。

## 用法（端用户）

直接描述需求，示例：
> "帮我画一个三层系统架构图，上层Hermes Agent中间Hindsight底层LiteLLM"

输出文件默认保存到当前工作目录，或由用户通过参数指定路径。

## 流程

1. Agent（LLM）理解描述，自行规划布局（节点坐标、连接线、层级、配色）
2. 输出结构化布局 JSON → 写入临时文件
3. 调用渲染引擎将 JSON 转为 .drawio / .svg 文件：
   ```bash
   python <SKILL_DIR>/scripts/render.py <layout.json> <output.drawio|.svg>
   ```
   - `<SKILL_DIR>` = 本 SKILL.md 所在目录（`/root/.hermes/skills/diagramming/drawio-generator/`）
   - 输出格式由扩展名决定：`.drawio` → draw.io XML，`.svg` → SVG
4. 生成的文件路径告知用户，可用 draw.io 打开后可按需精修

## 布局规范（Agent 执行时遵守）

- 标准节点尺寸：宽 150-170，高 50-65
- 同层节点水平对齐（相同 y），水平间隔 50-70
- 层间垂直间隔 100-140
- 箭头方向：从左到右或从上到下
- 图宽 1000-1100，高 500-650

## 节点字段参考

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 唯一标识，纯 ASCII |
| `label` | string | 是 | 显示文字，`<br>` 换行 |
| `x`/`y`/`w`/`h` | int | 是 | 坐标和尺寸 |
| `color` | string | 否 | 配色键名，默认 `node_blue` |
| `emoji` | string | 否 | 节点顶部显示 emoji 图标（如 🗄️ 🔬） |
| `bold` | bool | 否 | 文字是否加粗 |
| `image` | string | 否 | 图片 URL（替代矩形渲染） |

## 📄 内置配色方案（论文线 / 报告线）

### 📘 论文线 — 期刊投稿、学位论文、黑白打印

#### academic — 学术论文

Nature/PNAS 风格，低饱和度、优雅 muted、色盲友好。
适合投稿 IEEE/ACM/Elsevier 等期刊。

| 名称 | fill / stroke |
|------|--------------|
| `node_blue`   | `#EEF2F8` / `#4A6FA5` |
| `node_green`  | `#EAF3EE` / `#4A8C6A` |
| `node_orange` | `#F6F0E4` / `#B57A4A` |
| `node_yellow` | `#F6F2DC` / `#9E904A` |
| `node_purple` | `#F0ECF4` / `#7A5A9E` |
| `node_red`    | `#F4E8E8` / `#A05050` |
| `node_cyan`   | `#E8F2F2` / `#4A7A7A` |
| bg / layer / title / text | `#FFFFFF` `#F7F8FA` `#E0E2E6` `#1A1A1A` `#333333` |

#### paper-wireframe — 极简线框

高对比灰阶、无彩色填充、干净结构线。
适合论文中的黑白示意图、审稿用图。

| 名称 | fill / stroke |
|------|--------------|
| 所有节点 | `#F5F5F7` / `#5C5C5C` （顶部 `#F8F8FA` 轻微区分） |
| bg / layer / title / text | `#FFFFFF` `#FAFAFB` `#D0D0D0` `#1A1A1A` `#333333` |

#### paper-grayscale — 全灰度

纯灰度配色，高对比度，极致黑白印刷优化。
适合正式出版物、黑白论文插图。

| 名称 | fill / stroke |
|------|--------------|
| 深色节点 | `#F0F0F0` / `#333333` |
| 浅色节点 | `#E8E8E8` / `#333333` |
| bg / layer / title / text | `#FFFFFF` `#F5F5F5` `#CCCCCC` `#000000` `#222222` |

---

### 📊 报告线 — 商务汇报、技术方案、产品发布

#### business — 商务汇报

Google Material 3 升级版，干净、信任感强、高可读性。
适合 PPT、Word 报告、商务方案。

| 名称 | fill / stroke |
|------|--------------|
| `node_blue`   | `#E1F0FF` / `#1976D2` |
| `node_green`  | `#E6F7EC` / `#388E3C` |
| `node_orange` | `#FFF4E5` / `#F57C00` |
| `node_yellow` | `#FFFDE7` / `#FBC02D` |
| `node_purple` | `#F3E5F5` / `#7B1FA2` |
| `node_red`    | `#FFEBEE` / `#D32F2F` |
| `node_cyan`   | `#E0F7FA` / `#0097A7` |
| bg / layer / title / text | `#FFFFFF` `#F4F6F8` `#D5DCE4` `#1A1A1A` `#333333` |

#### tech — 科技公司

Vercel/Linear 风格，高饱和、高对比、现代感强。
适合技术方案、产品发布、Startup Pitch。

| 名称 | fill / stroke |
|------|--------------|
| `node_blue`   | `#EFF6FF` / `#3B82F6` |
| `node_green`  | `#F0FDF4` / `#22C55E` |
| `node_orange` | `#FFF7ED` / `#F97316` |
| `node_yellow` | `#FEFCE8` / `#EAB308` |
| `node_purple` | `#FAF5FF` / `#A855F7` |
| `node_red`    | `#FEF2F2` / `#EF4444` |
| `node_cyan`   | `#ECFEFF` / `#06B6D4` |
| bg / layer / title / text | `#FFFFFF` `#F4F6FA` `#D1D5DB` `#111827` `#374151` |

#### warm — 温暖大地

咖啡/陶土色调，暖系、有质感。
适合非正式分享、头脑风暴、创意展示。

| 名称 | fill / stroke |
|------|--------------|
| `node_blue`   | `#F0ECE3` / `#8B7355` |
| `node_green`  | `#EDF2E0` / `#6B8F3A` |
| `node_orange` | `#F5E6DA` / `#C0703E` |
| `node_yellow` | `#F5F0D0` / `#B89B30` |
| `node_purple` | `#F0EAF0` / `#8B6B8B` |
| `node_red`    | `#F2E4E4` / `#A05555` |
| `node_cyan`   | `#E6EEEE` / `#5A8A8A` |
| bg / layer / title / text | `#FEFCF8` `#F5F1EA` `#D4CEC4` `#3C2F1F` `#5C4F3F` |

#### minimal — 通用线框（向后兼容）

灰色边框，无彩色填充。已由 `paper-wireframe` 替代，保留作为兼容别名。

自定义示例：
```json
"palette": {
  "node_blue": {"fill": "#E8F0FE", "stroke": "#1967D2"},
  "node_green": {"fill": "#E6F4EA", "stroke": "#137333"}
}
```

## 关键坑点

- **edge 必须用 `from`+`to` 引用节点 ID**，不能用 x/y 定位
- **edge 的 mxGeometry 必须设 `relative="1"`**，否则报 `d.setId is not a function`
- **节点/箭头 ID 用纯 ASCII**，不用 Unicode
- 渲染器支持 drawio 和 svg 两种格式，SVG 可直接插入 Word

## 限制

- 适合：层级架构图、流程图、管线图、对比图、时间轴
- 不适合：手绘风、曲线/不规则形状、精确数据图表（如柱状图/折线图）、节点超 30 个的复杂图
- 美工不足是预期行为——AI 负责搭骨架，精修在 draw.io 里拖几下就行

## 🚀 专业进阶配置

渲染引擎支持以下高级配置，可直接在 plan JSON 中设置。

### 预设模式（一键切换）

| 字段 | 类型 | 说明 |
|------|------|------|
| `paper_mode` | bool | **论文模式**。强制 SVG 格式，Times New Roman 字体 + 0.75pt 细线 + open arrow（开放箭头）+ 灰度配色 + 自动裁剪，满足期刊投稿要求 |
| `presentation` | bool | **汇报模式**。开启渐变填充 + 投影阴影 + 经典箭头，视觉效果更专业 |

### 视觉配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `font_family` | string | `SimSun, Arial` | SVG 字体。论文用 `Times New Roman, serif`，报告用 `Arial, sans-serif` |
| `font_size` | dict | `{"title":16, "label":11, "small":9}` | 三级字号：标题/正文/小字 |
| `stroke_width` | number | SVG 1.5 / drawio 1 | 描边宽度。论文推荐 0.75，报告推荐 1.5-2 |
| `arrow_style` | string | `classic` | 箭头样式：`classic`/`open`/`diamond`/`circle`/`thick`/`none` |
| `grayscale` | bool | false | 灰度模式，自动将所有配色转灰度（黑白打印） |
| `gradient` | bool | false | SVG 节点使用线性渐变填充 |
| `shadow` | bool | false | SVG 节点使用投影效果 |
| `auto_fit` | bool | true | 自动裁剪 viewBox 到节点范围，消除大片空白 |

### 节点/边增强字段

| 字段 | 位置 | 说明 |
|------|------|------|
| `sub_label` | node | 数据标注，节点底部小字显示（如 `100ms`、`5TB` 等性能指标） |
| `label` | edge | 边标签，显示在箭头中间文字（如 `HTTP`、`gRPC`） |
| `arrow_style` | edge | 单边箭头样式覆盖，同全局 `arrow_style` 可选值 |

### 论文模式（paper_mode）示例

```json
{
  "title": "系统架构图",
  "width": 800,
  "height": 500,
  "nodes": [
    {"id": "a", "label": "前端层", "x": 100, "y": 200, "w": 150, "h": 60},
    {"id": "b", "label": "业务层", "x": 350, "y": 200, "w": 150, "h": 60},
    {"id": "c", "label": "数据层", "x": 600, "y": 200, "w": 150, "h": 60}
  ],
  "edges": [
    {"from": "a", "to": "b", "label": "REST"},
    {"from": "b", "to": "c", "label": "JDBC"}
  ],
  "paper_mode": true,
  "journal": "ieee"
}
```

### 汇报模式（presentation）示例

```json
{
  "title": "产品技术架构",
  "width": 1000,
  "height": 600,
  "nodes": [
    {"id": "web", "label": "Web 应用", "x": 100, "y": 50, "w": 160, "h": 60, "color": "node_blue", "sub_label": "React"},
    {"id": "api", "label": "API 服务", "x": 100, "y": 200, "w": 160, "h": 60, "color": "node_green", "sub_label": "50ms"},
    {"id": "db", "label": "数据库", "x": 100, "y": 350, "w": 160, "h": 60, "color": "node_purple", "sub_label": "PostgreSQL"}
  ],
  "edges": [
    {"from": "web", "to": "api", "label": "Fetch"},
    {"from": "api", "to": "db", "label": "SQL"}
  ],
  "presentation": true,
  "palette": "tech",
  "font_family": "Arial, sans-serif",
  "auto_fit": true
}
```

---

## 渲染工具 CLI 参考

本 SKILL 附带一个独立渲染脚本 `scripts/render.py`，可将 layout JSON 文件渲染为 .drawio 或 SVG 文件。
该脚本可在命令行独立使用，不依赖本 SKILL。

### 路径

部署后路径：`/root/.hermes/skills/diagramming/drawio-generator/scripts/render.py`

脚本内部自动搜索以下位置（按优先级）：
1. 向上查找 `pyproject.toml` → `src/drawio_generator`（开发环境）
2. 部署路径 `/root/.hermes/scripts/drawio-generator/src/`
3. HermesProject 工作区 fallback

### 用法

```
python render.py <input.json> <output.drawio|.svg>
```

| 参数 | 说明 |
|------|------|
| `input.json` | 布局 JSON 文件路径（格式见本 SKILL 布局规范） |
| `output.drawio` | 输出 `.drawio` 格式（draw.io XML） |
| `output.svg` | 输出 SVG 格式（可直接插入 Word/浏览器） |

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 渲染成功 |
| 1 | 参数错误或文件不存在 |

### 示例

```bash
# 渲染为 draw.io 格式
python /root/.hermes/skills/diagramming/drawio-generator/scripts/render.py layout.json output.drawio

# 渲染为 SVG 格式（论文用）
python /root/.hermes/skills/diagramming/drawio-generator/scripts/render.py layout.json output.svg
```
