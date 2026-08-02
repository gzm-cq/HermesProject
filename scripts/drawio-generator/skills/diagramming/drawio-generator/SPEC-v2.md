# drawio-generator SPEC-v2

> Version 2.1 (基于 v1.2.0 代码库整理，与代码实现对齐)
> 更新日期: 2026-07-31

---

## 1. 概述

### 1.1 定位

`drawio-generator` 是一款**纯 Python** 矢量架构图/流程图/管线图生成器，接收结构化布局 JSON（由 LLM 或人工生成），输出 `.drawio`（draw.io 可编辑 XML）或 `.svg`（矢量图）文件。

### 1.2 核心能力

| 能力 | 说明 |
|------|------|
| 双格式输出 | `.drawio` (draw.io XML) + `.svg` (矢量) |
| 自动布局引擎 | dagre 风格层级布局，零外部依赖；可选 Graphviz 布局 |
| 9 种配色方案 | academic / business / minimal / tech / warm / paper-wireframe / paper-grayscale / dark / colorblind-safe |
| 12 种节点形状 | rect / process / cylinder / hexagon / cloud / note / document / cube / card / step / parallelogram / rhombus |
| 嵌套分组容器 | 支持 `/` 分隔的 group 路径，自动计算包围盒和容器（注：drawio 输出中所有容器 parent="1"，扁平化非真正嵌套） |
| 边线增强 | orthogonal / straight / bezier 曲线；双向箭头；flow_animation 动画；端口分布；箭头间距检查 |
| 图类型模板 | microservices / network-topology / dataflow / er-diagram |
| 图类型样式预设 | architecture / flowchart / ml_model / network_topology / erd / swimlane / pipeline |
| 视觉预设 | dark / colorblind-safe 等样式方案（style_presets 与 PALETTES 同名键保持同步） |
| 形状库 | 46 形状索引（含网络/UML/架构类），支持模糊搜索和分类过滤 |
| AI 品牌图标库 | 98 项 AI 产品/厂商/模型品牌 icon，通过 CDN 嵌入 drawio，SVG 端 fallback 为色块+文字 |
| 自动图例 | 颜色≥3 时自动生成图例，支持多位置布局和动态画布扩展 |
| 手绘风格 | sketch 模式：SVG feTurbulence 抖动滤镜 + drawio sketch=1 |
| 论文/汇报模式 | paper_mode 强制 SVG+Times+open arrow；presentation 开启渐变+投影 |
| 输入校验 + 自动修复 | validate_plan 校验 + repair_drawio 后处理修复；边穿过/交叉检测；布局评分 |
| HTTP 预览服务器 | 生成后浏览器直接查看 |

### 1.3 设计原则

- **零外部 Python 依赖**：渲染引擎 100% 纯 Python
- **文件扩展名优先**：`.svg` / `.drawio` 扩展名决定最终输出格式
- **优雅降级**：未知配色/形状/格式均有 fallback
- **稳定可预测**：确定性布局算法，相同输入产生相同输出

---

## 2. 系统架构

### 2.1 模块结构

```
src/drawio_generator/
├── render.py          # 主入口：render(), generate_svg(), main() CLI
├── layout.py          # 自动布局引擎：layout_plan()
├── graphviz_layout.py # 可选 Graphviz 布局引擎
├── drawio_renderer.py # .drawio XML 渲染：_render_drawio(), repair_drawio()
├── svg_renderer.py   # SVG 渲染：_render_svg()（含 sketch 滤镜）
├── shapes.py          # 12 种节点形状定义 + SVG 形状渲染
├── shape_library.py   # 形状索引库：46 形状，模糊搜索 + 分类过滤
├── aiicons.py         # AI 品牌图标库：98 项，CDN 嵌入 + 模糊搜索
├── legend.py          # 自动图例：build_legend() 颜色≥3 自动生成
├── palettes.py        # 9 种配色方案 + 箭头样式 + 颜色工具
├── geometry.py        # 包围盒计算、节点查找、边路径（orthogonal/straight/bezier）
├── validator.py       # 输入校验：validate_plan()（P6 校验受 validate_layout 开关控制）
├── containers.py      # 分组容器：parse_group_tree() 等
├── templates.py       # 图类型预设模板
├── diagram_presets.py # 图类型样式预设：architecture/flowchart/ml_model 等
├── style_presets.py   # 视觉预设：dark/colorblind-safe 等
└── edge_styles.py     # 边线增强：flow_animation、端口分布、标签背景、箭头间距
```

### 2.2 数据流

```
输入 JSON (plan_dict)
    │
    ▼
┌──────────────────────┐
│  1. 自动布局 (可选)    │  auto_layout=True 或节点缺坐标
│  layout.py            │  拓扑排序 → 层级分配 → 坐标计算
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  2. 容器处理 (可选)    │  节点有 group 字段时
│  containers.py        │  解析分组树 → 计算包围盒 → 分配颜色
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  3. 输入校验          │  validate_plan()
│  validator.py         │  必填字段、类型、引用一致性
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  4. 格式路由          │  文件扩展名 > plan.format
│  .drawio → drawio_renderer.py
│  .svg    → svg_renderer.py
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  5. 渲染 + 输出       │  写入文件
│  drawio: repair_drawio() 后处理修复
│  svg: 直接写入        │
└──────────────────────┘
```

### 2.3 渲染主流程 (render.py#render)

1. **apply_template 前处理**：若 `plan.template` 指定功能模板（microservices / network_topology / dataflow / er_diagram），先调用 `apply_template()` 展开模板节点/边
2. **apply_diagram_type 前处理**：若 `plan.diagram_type` 指定图类型预设（architecture / flowchart / ml_model 等），调用 `apply_diagram_type()` 注入默认 shape/color/edge_style/flow_animation（依据节点 `role` 字段匹配预设，并通过内部标记 `diagram_type_shape_applied` 防止重复应用）
3. **auto_layout 检测**：`plan.get("auto_layout")` 或任一节点缺 `x/y`
4. **容器处理**：检测 `node.get("group")` 字段，构建分组树
5. **输入校验**：`validate_plan()` 产出 error/warning
6. **格式决策**：文件扩展名 > `plan.format` > 默认 `drawio`；paper_mode 强制 svg
7. **配色解析**：字符串名 → **先查 `style_presets`（dark / colorblind-safe / default），再查 `PALETTES`**；dict 直接使用并补 academic 默认值
8. **特殊模式**：paper_mode 强制 SVG/Times/open arrow；presentation 开启 shadow/gradient（仅当用户未显式指定时才覆盖默认值）
9. **auto_legend 后处理**：`auto_legend=True` 且颜色 ≥3 时调用 `build_legend()` 追加图例 layer/node 并扩展画布
10. **sketch 手绘风格**：`sketch=True` 时 `stroke_width × 1.2`，并给节点打 `_sketch=True` 标记
11. **渲染调用**：_render_drawio() 或 _render_svg()
12. **drawio 修复**：repair_drawio() 自动修复常见 XML 问题（仅 drawio 输出）

---

## 3. 数据模型

### 3.1 Plan（布局字典）

```json
{
  "title": "string",           // 必填，图标题
  "width": 1000,               // 画布宽度 (px)
  "height": 800,               // 画布高度 (px)
  "format": "drawio",          // 输出格式："drawio" | "svg"
  "palette": "academic",       // 配色方案名 或 自定义 dict
  "nodes": [...],              // 节点数组
  "edges": [...],              // 边数组
  "layers": [...],             // 区域层数组（可选）
  
  // 自动布局
  "auto_layout": false,        // 是否自动布局
  "layout_direction": "vertical", // "vertical" | "horizontal" | "auto"
  "layout_engine": "native",   // 布局引擎: "native" (纯Python) | "graphviz" (dot -Tplain)
  "validate_layout": false,    // 是否执行 P6 布局质量校验（边穿过/交叉/评分）
  
  // 论文/汇报模式
  "paper_mode": false,         // 论文模式
  "presentation": false,       // 汇报模式
  "sketch": false,             // 手绘风格 (P5)
  "auto_legend": false,        // 自动生成图例 (P5)
  "legend_position": "bottom_right", // 图例位置: top_left/top_right/bottom_left/bottom_right
  
  // 图类型模板 / 预设
  "template": "microservices", // 功能模板: microservices / network_topology / dataflow / er_diagram (P5)
  "diagram_type": "architecture", // 图类型样式预设: architecture/flowchart/ml_model 等 (P5)
  
  // 边线增强
  "flow_animation": false,     // 全局边流动画
  "edge_style": "orthogonal",  // 边线路由样式: orthogonal
  
  // 视觉配置
  "font_family": "string",     // 字体族
  "font_size": {...},          // 字号配置
  "stroke_width": 1.5,         // 描边宽度
  "arrow_style": "classic",    // 箭头样式
  "grayscale": false,          // 灰度模式
  "gradient": false,           // 渐变填充
  "shadow": false,             // 投影效果
  "auto_fit": true,            // 自动裁剪 viewBox
}

// 备注：apply_diagram_type 处理后会注入内部标记字段 `diagram_type_shape_applied: true`，
// 防止 shape 重复分配；该字段为内部状态，用户不应手动设置。
```

### 3.2 Node（节点）

```json
{
  "id": "string",              // 唯一标识，纯 ASCII
  "label": "string",           // 显示文字，"<br>" 换行
  "x": 100,                    // 左上角 X
  "y": 200,                    // 左上角 Y
  "w": 160,                    // 宽度
  "h": 60,                     // 高度
  "color": "node_blue",        // 配色键名
  "shape": "rect",             // 形状名
  "role": "server",            // 节点角色（diagram_presets 据此分配 shape/color，如 start/process/decision/client/server 等）
  "emoji": "🗄️",               // 顶部 emoji 图标
  "sub_label": "100ms",        // 底部小字数据标注
  "bold": false,               // 文字加粗
  "image": "url",              // 图片 URL（替代矩形渲染）
  "group": "server/db",        // 嵌套分组路径（'/' 分隔）
}
```

### 3.3 Edge（边）

```json
{
  "from": "src_id",            // 源节点 id
  "to": "tgt_id",              // 目标节点 id
  "label": "string",           // 边标签文字
  "dashed": false,             // 虚线
  "arrow_style": "classic",    // 单边箭头样式覆盖
  "curve": "orthogonal",       // 曲线类型: orthogonal | straight | bezier
  "bidirectional": false,      // 双向箭头
  "color": "node_red",         // 边颜色（palette key 或 #hex，图例会纳入统计）
  "flow_animation": false,     // 边级流动动画（优先于 plan.flow_animation）
  "points": [[x,y], ...],      // 显式路径点（覆盖自动路由，校验须为 list）
  "back_edge": false           // 内部标记：布局引擎检测到的回边（红色虚线渲染，用户一般不设）
}
```

### 3.4 Layer（区域层）

```json
{
  "x": 30,                     // 左上角 X
  "y": 80,                     // 左上角 Y
  "w": 540,                    // 宽度
  "h": 120,                    // 高度
  "label": "string"            // 区域名称（可选）
}
```

### 3.5 配色字典

```json
{
  "node_blue":   {"fill": "#dae8fc", "stroke": "#6c8ebf"},
  "node_green":  {"fill": "#d5e8d4", "stroke": "#82b366"},
  "node_orange": {"fill": "#ffe6cc", "stroke": "#d79b00"},
  "node_yellow": {"fill": "#fff2cc", "stroke": "#d6b656"},
  "node_purple": {"fill": "#e1d5e7", "stroke": "#9673a6"},
  "node_red":    {"fill": "#f8cecc", "stroke": "#b85450"},
  "node_cyan":   {"fill": "#d4e8f0", "stroke": "#5a8a9a"},
  "bg": "#FFFFFF",
  "layer_bg": "#F4F6F8",
  "layer_stroke": "#D5DCE4",
  "title_color": "#1A1A1A",
  "text_color": "#333333"
}
```

---

## 4. 自动布局引擎

### 4.1 算法流程

```
layout_plan(nodes, edges, direction, gap, layer_gap, padding, gap_auto)
    │
    ├── 1. 间距计算 (gap_auto=True)
    │     ├── ≤5 节点 → SIMPLE_GAP=200, LAYER_GAP=150
    │     ├── 6-10 节点 → MEDIUM_GAP=280, LAYER_GAP=200
    │     └── >10 节点 → COMPLEX_GAP=350, LAYER_GAP=250
    │
    ├── 2. 分离孤立节点 (_separate_isolated)
    │     不参与任何边的节点 → 独立排布
    │
    ├── 3. 构建邻接表 (_build_adjacency)
    │     {node_id: [target_ids]}, 入度表
    │
    ├── 4. 拓扑排序 (_topological_sort)
    │     Kahn 算法，检测环路
    │     有环时追加环中节点到末尾
    │
    ├── 5. 层级分配 (_assign_layers)
    │     最长路径算法：节点层级 = max(前驱层级) + 1
    │
    ├── 6. 自动方向检测 (direction="auto")
    │     width > depth → "horizontal"
    │     否则 → "vertical"
    │
    ├── 7. 坐标计算 (_compute_coordinates)
    │     ├── 垂直：行高=层最大节点高+层间距, 居中对齐
    │     └── 水平：列宽=层最大节点宽+列间距, 居中对齐
    │
    ├── 8. 重心启发式排序 (_reduce_crossings)
    │     2 前向 + 1 反向扫描减少边交叉
    │
    ├── 9. 孤立节点放置 (_place_isolated)
    │     垂直：连通图上方独立区域
    │     水平：连通图左侧独立区域
    │
    └── 10. 边路径计算 (_compute_edge_routes)
          正交（曼哈顿）路径，1-3 段折线
```

### 4.2 间距常量

| 复杂度 | 节点数 | gap | layer_gap | corridor |
|--------|--------|-----|-----------|----------|
| simple | ≤5 | 200 | 150 | 60 |
| medium | 6-10 | 280 | 200 | 80 |
| complex | >10 | 350 | 250 | 100 |

默认间距: gap=60, layer_gap=120, padding=40

### 4.3 返回结构

```python
{
    "nodes": [{"id", "label", "x", "y", "w", "h", ...}],  # 含坐标的节点
    "width": int,           # 总画布宽度
    "height": int,          # 总画布高度
    "has_cycle": bool,      # 是否检测到环路
    "back_edges": [("src", "tgt"), ...],  # 回边列表
    "edge_routes": [{"from", "to", "points": [(x,y), ...]}, ...]  # 边路径
}
```

---

## 5. 渲染引擎

### 5.1 .drawio 渲染 (drawio_renderer.py)

输出 draw.io 24.6.4 兼容 XML 文件。

**结构层次**：
```
mxfile
└── diagram (id="1", name=title[:20])
    └── mxGraphModel
        └── root
            ├── mxCell id="0" (根)
            └── mxCell id="1" (默认父节点)
                ├── title cell (文本)
                ├── layer cells (背景矩形 + 标签)
                ├── container cells (分组容器，虚线边框，parent 均为 "1")
                ├── node cells (节点，parent 为对应容器 cid 或 "1")
                └── edge cells (连接线，parent 均为 "1")
```

> **注（容器扁平化）**：`generate_container_cells` 生成的所有容器 cell `parent` 均为 `"1"`，并未形成真正的嵌套父子层级（即内层容器并非外层容器的子节点）。节点通过 `node_parent_map` 指向其最深容器的 cid，但容器之间是扁平并列的。这是当前实现的已知行为，包围盒与视觉嵌套通过坐标计算实现，而非 XML 层级。

**关键样式**：
- 节点: `rounded=1;whiteSpace=wrap;html=1;fillColor=X;strokeColor=Y;strokeWidth=Z;`
- 节点 (sketch): 追加 `sketch=1;`（sketch_mode=True 时）
- 边: `edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;labelBackgroundColor=#ffffff;endArrow=classic;strokeWidth=Z;` (dashed, verticalLabelPosition, flowAnimation)
- 容器: `dashed=1;verticalAlign=top;fontStyle=2;`

**端口分布 (distribute_ports)**：
渲染边之前调用 `distribute_ports(nodes, edges)` 计算每条边的 `exitX/exitY/entryX/entryY`：
1. 对每个节点统计出/入边数量
2. 根据源/目标节点相对方位选择最近侧边 (top/bottom/left/right)
3. 同侧边均匀分布：`coord = (i + 0.5) / N`
4. 写入 edge 的 `mxGeometry` 属性，使多条边汇入同一节点时端点不重叠

**箭头间距检查 (check_arrowhead_gap)**：
渲染后基于每条边的路径点（倒数第二点到末点）两两计算箭头端点距离，小于 `min_gap=20px` 时打印 `[WARN]` 警告，提示箭头头拥挤。

**后处理修复 (repair_drawio)**：
1. XML 解析校验
2. cell id 重复检测
3. parent 引用有效性 → 修复为 "1"
4. edge mxGeometry relative="1" → 自动添加
5. 非 ASCII id 警告
6. 根节点完整性检查 (id="0", id="1")

### 5.2 SVG 渲染 (svg_renderer.py)

输出标准 SVG 1.1 矢量图。

**结构层次**：
```
<svg viewBox="...">
├── <defs>
│   ├── <marker id="a"> (箭头 marker)
│   ├── <marker id="as"> (双向箭头 start marker)
│   ├── <filter id="shadow"> (投影，可选)
│   ├── <filter id="sketch"> (手绘抖动滤镜，sketch_mode=True 时)
│   └── <linearGradient> (渐变，可选)
├── <rect> (背景)
├── <text> (标题)
├── <rect> (层次背景) + <text> (层次标签)
├── <rect> (图例背景) + <text> (图例标题/色块/文字) — auto_legend 时
├── <shape> (节点形状) + <text> (标签) + <text> (sub_label)
└── <path>/<line> (边) + <rect>/<text> (边标签)
```

**sketch 手绘风格**：
- SVG filter: `<feTurbulence type="fractalNoise" baseFrequency="0.025" numOctaves="2" seed="7"/>` + `<feDisplacementMap in="SourceGraphic" scale="2"/>`
- 节点通过 `filter="url(#sketch)"` 应用（可与 shadow 叠加）
- 边线通过 `filter="url(#sketch)"` 应用

**箭头样式映射**：

| 名称 | drawio | SVG path | 填充 |
|------|--------|----------|------|
| classic | classic | M 0 0 L 10 5 L 0 10 z | filled |
| open | openThin | M 0 0 L 10 5 L 0 10 | none (stroke) |
| diamond | diamondThin | M 0 5 L 5 0 L 10 5 L 5 10 z | filled |
| circle | ovalThin | M 5 0 A 5 5 0 1 1 4.99 0 | filled |
| thick | blockThin | M 0 -3 L 10 0 L 10 6 L 0 9 z | filled |
| none | none | (空) | - |

**自动裁剪 (auto_fit)**：
计算所有节点和 layer 的最小包围盒 + 30px padding，设置为 viewBox。

---

## 6. 配色方案

### 6.1 内置方案

共 9 种配色方案，其中 7 种定义于 `palettes.PALETTES`，另 2 种（`dark` / `colorblind-safe`）同时在 `PALETTES` 与 `style_presets.BUILT_IN_PRESETS` 中保持同步（render.py 解析时先查 style_presets，再查 PALETTES）。

| 名称 | 风格 | 适用场景 |
|------|------|----------|
| `academic` | draw.io 官方标准，低饱和度 | 默认，期刊论文 |
| `business` | Google Material 3 升级版 | 商务汇报、PPT |
| `minimal` | Figma 原型，全灰 | 线框图、原型 |
| `tech` | Vercel/Linear，高饱和 | 技术方案、Pitch |
| `warm` | 咖啡/陶土暖色系 | 非正式分享 |
| `paper-wireframe` | 极简线框，黑白打印 | 论文黑白插图 |
| `paper-grayscale` | 全灰度，极致黑白 | 正式出版物 |
| `dark` | 深色主题，暗背景 (#1A1A2E) 高对比度 | 深色模式展示、夜间演示 |
| `colorblind-safe` | 基于 Okabe-Ito 调色板，红绿/蓝黄色盲均可区分 | 无障碍场景、可访问性要求 |

### 6.2 颜色工具

| 函数 | 用途 |
|------|------|
| `_hex_to_rgb("#RRGGBB")` | → (R, G, B) |
| `_rgb_to_hex(r, g, b)` | → "#RRGGBB" |
| `_desaturate(hex)` | 转灰度 (luminance) |
| `_lighten(hex, factor)` | 变亮 factor (0~1) |
| `_resolve_color(colors, node)` | 取节点颜色，fallback 到 academic |
| `_apply_grayscale(colors)` | 整体转灰度 |

---

## 7. 节点形状

### 7.1 支持的形状

| 名称 | drawio 样式 | SVG 渲染 |
|------|------------|----------|
| `rect` | `rounded=1` | 圆角矩形 (rx=4) |
| `process` | `shape=process` | 双边框无圆角矩形 |
| `cylinder` | `shape=cylinder` | 椭圆顶 + 矩形身 + 弧底 (数据库) |
| `hexagon` | `shape=hexagon` | 六边形 (25% 内凹) |
| `cloud` | `shape=cloud` | 贝塞尔曲线云朵 |
| `note` | `shape=note` | 便签 (右上角折叠) |
| `document` | `shape=document` | 文档 (左下角卷页) |
| `cube` | `shape=cube` | 3D 立方体 (三面可见) |
| `card` | `shape=card` | 大圆角卡片 (rx=8) |
| `step` | `shape=step` | 步骤箭头 (右侧三角) |
| `parallelogram` | `shape=parallelogram` | 平行四边形 (25% 倾斜) |
| `rhombus` | `shape=rhombus` | 菱形 (决策节点) |

### 7.2 节点增强字段

| 字段 | 类型 | 位置 | 说明 |
|------|------|------|------|
| `emoji` | string | 节点顶部 | 24px emoji 图标 |
| `sub_label` | string | 节点底部 | 小字数据标注 (opacity 0.65) |
| `bold` | bool | 节点文字 | `<b>` 包裹 |
| `image` | string | 替代形状 | `shape=image;image=URL` |

---

## 8. 分组容器

### 8.1 group 路径格式

节点可通过 `group` 字段指定嵌套分组：

```json
{"id": "db1", "label": "MySQL", "group": "server/db"}
```

`"server/db"` → 两级容器：外层 `server`，内层 `server/db`

### 8.2 处理流程

```
parse_group_tree(nodes)
    → {gpath, direct, children, ordered}

compute_container_boxes(tree, nodes, padding=24)
    → {(path_tuple): (x, y, w, h)}  // 从最深到最浅计算

assign_group_colors(tree, palette)
    → {group_path: color_key}  // 顶层组分配不同颜色

apply_group_colors_to_nodes(nodes, tree, group_colors, palette)
    // 无自定义 color 的节点自动获得 group 颜色

generate_container_cells(tree, boxes, colors, palette, nid_counter)
    → [(cid, parent, style, x, y, w, h, label), ...]
    // 外层先画，内层后画

compute_node_offsets(tree, boxes)
    → {node_id: (offset_x, offset_y)}
    // 节点坐标减去容器左上角偏移
```

### 8.3 容器样式

```
style="rounded=0;whiteSpace=wrap;html=1;
       fillColor=none;
       strokeColor={group_stroke};
       dashed=1;verticalAlign=top;fontStyle=2;"
```

容器标签取路径最后一段（如 `"server/db"` → `"db"`）。

---

## 9. 输入校验

### 9.1 校验规则 (validate_plan)

常规校验（始终执行）：

| 规则 | 级别 | 说明 |
|------|------|------|
| plan 必须是 dict | error | 非 dict 直接报错 |
| title 必填且非空 | error | 字符串且 strip 后非空 |
| nodes 必须是 list | error | - |
| edges 必须是 list | error | - |
| width/height 为正数 | warning | 非正数警告 |
| format 仅支持 svg/drawio | warning | 未知格式警告 |
| palette 名称已知 | warning | 未知名称 fallback 到 academic（合法集合 = PALETTES ∪ BUILT_IN_PRESETS） |
| 节点 id 唯一 | error | 重复 id 报错 |
| 节点 x/y/w/h 为数字 | error | 缺失或非数字 |
| 节点 color 已知 | warning | 未知 color fallback 到 node_blue |
| 节点 shape 已知 | warning | 未知 shape fallback 到 rect |
| 边 from/to 引用有效节点 | warning | 引用未定义节点 |
| 自环检测 | warning | from == to 时警告 |
| 孤立节点检测 | warning | 无边连接的节点 |
| group 路径格式 | warning | 不以 "/" 开头/结尾，无 "//" |
| 容器嵌套深度 | warning | 超过 3 层时警告 |
| 边 curve 类型 | warning | 仅支持 orthogonal/straight/bezier |
| 边 bidirectional 类型 | warning | 必须是 bool |
| 边 points 类型 | warning | 必须是 list |

P6 布局质量校验（**仅在 `plan.validate_layout=True` 时执行**）：

| 规则 | 级别 | 说明 |
|------|------|------|
| 边穿过节点检测 | warning | `check_edge_through_vertex`：边路径与非端点节点包围盒相交（空间网格索引加速） |
| 边交叉检测 | warning | `check_edge_crossings`：两边线段相交（共享端点不计），向量叉积法 + 包围盒预过滤 |
| 布局质量评分 | warning | `score_layout`：`score = through × 20 + crossings × 10 + total_length / 10000`，分级 优秀/良好/一般/较差 |

### 9.2 校验输出

```python
issues = [
    ("error", "nodes[0].id", "id 'node1' 重复"),
    ("warning", "edges[0].from", "引用未定义节点 'nonexistent'"),
]
```

error 级别导致 `render()` 抛出 `ValueError`；warning 仅打印日志。

---

## 10. CLI 接口

### 10.1 命令

```bash
# 基本用法
python -m drawio_generator <layout.json> <output.drawio|output.svg>

# 列出配色
python -m drawio_generator --list-palettes

# 显示版本
python -m drawio_generator --version

# 自动推导输出路径
python -m drawio_generator layout.json   # → layout.drawio

# stdin 输入
cat layout.json | python -m drawio_generator -

# 浏览器打开
python -m drawio_generator layout.json output.svg --open

# HTTP 预览服务器
python -m drawio_generator layout.json output.drawio --serve --port 8080
```

### 10.2 参数

| 参数 | 说明 |
|------|------|
| `layout_json` | 布局 JSON 文件路径，`-` 表示 stdin |
| `output_path` | 输出路径（可省略），扩展名决定格式 |
| `--list-palettes` | 列出所有配色方案 |
| `--open` | 生成后浏览器打开 |
| `--serve` | 启动 HTTP 预览服务器 |
| `--port` | HTTP 端口，默认 8080 |

### 10.3 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 参数错误或文件不存在 |

### 10.4 部署环境 CLI

Skill 附带独立渲染脚本：
```bash
python /root/.hermes/skills/diagramming/drawio-generator/scripts/render.py <input.json> <output.drawio|.svg>
```

该脚本自动搜索 `drawio_generator` 包路径：
1. 向上搜索 `pyproject.toml` → `src/drawio_generator`（开发环境）
2. `/root/.hermes/scripts/drawio-generator/src/`（部署路径）
3. HermesProject 工作区 fallback

---

## 11. Python API

### 11.1 包导出 (`__init__.py`)

`drawio_generator` 包通过 `__init__.py` 导出 32 个公共符号，按模块分组如下：

| 来源模块 | 导出符号 |
|----------|----------|
| `render` | `render`, `generate_svg`, `PALETTES`, `DEFAULT_PALETTE`, `VERSION`（`__version__`） |
| `layout` | `layout_plan` |
| `templates` | `TEMPLATES`, `apply_template`, `microservices_template`, `network_topology_template`, `dataflow_template`, `er_diagram_template` |
| `shape_library` | `search_shape`, `get_shape`, `list_shapes` |
| `aiicons` | `search_icon`, `get_icon`, `list_icons` |
| `legend` | `build_legend` |
| `validator` | `validate_plan`, `score_layout`, `check_edge_crossings`, `check_edge_through_vertex` |
| `style_presets` | `STYLE_PRESETS` (即 `BUILT_IN_PRESETS`), `load_preset`, `list_presets` |
| `diagram_presets` | `DIAGRAM_PRESETS` (即 `PRESETS`), `list_diagram_types`, `apply_diagram_type` |
| `edge_styles` | `get_base_edge_style`, `apply_flow_animation`, `distribute_ports`, `check_arrowhead_gap` |

> 备注：`graphviz_layout.is_available()` 与 `shape_library.summary()` / `aiicons.summary()` 未在 `__init__.py` 中重导出，需通过子模块路径访问（见 11.5 / 11.6 / 11.8）。

```python
from drawio_generator import render, generate_svg, layout_plan  # 常用入口
from drawio_generator import (
    PALETTES, DEFAULT_PALETTE, TEMPLATES, apply_template,
    microservices_template, network_topology_template,
    dataflow_template, er_diagram_template,
    search_shape, get_shape, list_shapes,
    search_icon, get_icon, list_icons,
    build_legend,
    validate_plan, score_layout, check_edge_crossings, check_edge_through_vertex,
    STYLE_PRESETS, load_preset, list_presets,
    DIAGRAM_PRESETS, list_diagram_types, apply_diagram_type,
    get_base_edge_style, apply_flow_animation, distribute_ports, check_arrowhead_gap,
)
```

### 11.2 render(plan_dict, output_path)

主入口函数。根据 plan 内容渲染矢量图到指定路径。

**参数**：
- `plan_dict` (dict): 布局字典（见第 3 节）
- `output_path` (str): 输出文件路径，扩展名决定格式

**异常**：
- `ValueError`: 输入校验失败（有 error 级别问题）

**示例**：
```python
plan = {
    "title": "微服务架构",
    "width": 1000, "height": 600,
    "palette": "tech",
    "nodes": [
        {"id": "gw", "label": "API 网关", "x": 400, "y": 50, "w": 160, "h": 60, "color": "node_blue"},
        {"id": "svc", "label": "业务服务", "x": 400, "y": 200, "w": 160, "h": 60, "color": "node_green"},
        {"id": "db", "label": "数据库", "x": 400, "y": 350, "w": 160, "h": 60, "color": "node_red", "shape": "cylinder"},
    ],
    "edges": [
        {"from": "gw", "to": "svc", "label": "HTTP"},
        {"from": "svc", "to": "db", "label": "SQL"},
    ],
}
render(plan, "architecture.drawio")
render(plan, "architecture.svg")
```

### 11.3 generate_svg(plan_json, output_path)

兼容别名。支持 dict 或 JSON 字符串输入。

```python
generate_svg(plan_dict, "output.svg")
generate_svg(json_string, "output.svg")
```

### 11.4 layout_plan(nodes, edges, direction="vertical", **kwargs)

独立自动布局引擎。返回含坐标的节点列表和尺寸。

**kwargs 支持的参数**：
- `gap` (int): 同层节点间距，默认 60
- `layer_gap` (int): 层间间距，默认 120
- `padding` (int): 画布内边距，默认 40
- `gap_auto` (bool): 是否按节点数自动选择间距，默认 True
- `corridor_gap` (int): 路由走廊额外间距，默认 0；`gap_auto=True` 且未显式指定时由复杂度自动设置（simple=60 / medium=80 / complex=100）

```python
result = layout_plan(
    nodes=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
    edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
    direction="auto",
)
# result["nodes"] → 含 x, y, w, h 的节点
# result["width"], result["height"]
# result["has_cycle"], result["back_edges"]
# result["edge_routes"]
```

### 11.5 形状库 (shape_library.py)

```python
from drawio_generator.shape_library import search_shape, get_shape, list_shapes, summary

# 模糊搜索形状
results = search_shape("数据库", limit=5)
# → [("cylinder", {"shape": "cylinder", "name": "数据库", ...}, 2.0), ...]

# 按 ID 取形状信息
info = get_shape("cylinder")
# → {"shape": "cylinder", "name": "数据库", "keywords": [...], "category": "data", ...}

# 列出所有形状（可按类别/SVG支持过滤）
all_shapes = list_shapes()
network_shapes = list_shapes(category="network")
svg_only = list_shapes(only_svg=True)

# 统计摘要
stats = summary()
# → {"total": 46, "svg_supported": N, "drawio_fallback": N, "categories": [...], "per_category": {...}}
```

| 函数 | 说明 |
|------|------|
| `search_shape(query, limit=5, threshold=0.35)` | 模糊搜索形状，返回 `(shape_id, info, score)` 列表 |
| `get_shape(shape_id)` | 按 ID 取形状元信息，不存在返回 None |
| `list_shapes(category=None, only_svg=False)` | 列出形状，可按类别和 SVG 支持过滤 |
| `shape_to_drawio_style(shape_id)` | 从形状库取 drawio style 字符串 |
| `shape_svg_supported(shape_id)` | 检查该形状是否支持 SVG 渲染 |
| `summary()` | 返回形状库统计摘要（总数、SVG 支持数、分类明细） |

### 11.6 AI 品牌图标库 (aiicons.py)

```python
from drawio_generator.aiicons import search_icon, get_icon, list_icons, summary

# 模糊搜索 AI 图标
results = search_icon("openai", limit=5)
# → [("openai", {"name": "OpenAI / GPT", "category": "llm", ...}, 3.0), ...]

# 按 key 取品牌信息
info = get_icon("claude")
# → {"name": "Anthropic Claude", "aliases": [...], "category": "llm", ...}

# 按类别列出
llm_icons = list_icons(category="llm")

# 统计摘要
stats = summary()
# → {"total": 98, "categories": [...], "per_category": {...}}
```

| 函数 | 说明 |
|------|------|
| `search_icon(query, limit=5, threshold=0.35)` | 模糊搜索品牌图标，支持别名/中英文 |
| `get_icon(brand_key)` | 按 key 取品牌信息 |
| `list_icons(category=None)` | 列出所有图标，可按类别过滤 |
| `summary()` | 返回图标库统计摘要（总数、分类明细） |

图标通过 `shape=image;image=URL` 嵌入 drawio（CDN: simpleicons.org），SVG 端 fallback 为品牌色块 + 文字。

### 11.7 自动图例 (legend.py)

```python
from drawio_generator.legend import build_legend

legend = build_legend(plan, nodes, edges, palette,
                      position="bottom_right",
                      legend_pad=20, cell_w=140, cell_h=32)
# → {"layers": [...], "nodes": [...], "width": 0, "height": 0}
```

| 函数 | 说明 |
|------|------|
| `build_legend(plan, nodes, edges, palette, position="bottom_right", legend_pad=20, cell_w=140, cell_h=32)` | 自动生成图例，颜色≥3 才生成 |

**参数**：
- `position`: 图例位置，4 选 1：`top_left` / `top_right` / `bottom_left` / `bottom_right`
- `legend_pad`: 图例内边距（px），默认 20
- `cell_w` / `cell_h`: 单个图例条目宽/高（px），默认 140 / 32

图例包含：标题节点 + 颜色色块 + 颜色名称文字。越界时通过返回的 `width` / `height` 增量提示画布扩展。

### 11.8 Graphviz 布局辅助 (graphviz_layout.py)

```python
from drawio_generator.graphviz_layout import layout_plan_graphviz, is_available

# 检查系统是否安装 graphviz (dot -V)
if is_available():
    result = layout_plan_graphviz(nodes, edges, direction="vertical",
                                  nodesep=0.5, ranksep=0.5, padding=40)
```

| 函数 | 说明 |
|------|------|
| `layout_plan_graphviz(nodes, edges, direction="vertical", **kwargs)` | 调用 `dot -Tplain` 计算坐标；不可用时抛 `RuntimeError` |
| `is_available()` | 检查 `dot -V` 是否可执行，返回 bool（用于渲染前预检） |

---

## 12. 配置

### 12.1 默认配置 (config/default.yaml)

> **注**：`config/default.yaml` 仅作文档参考，**运行时不被代码加载**。render.py 中的实际默认值以常量形式硬编码（如 `width=1000`, `height=800`, `palette="academic"`，节点默认尺寸 `DEFAULT_NODE_W=160` / `DEFAULT_NODE_H=60` 见 `layout.py`）。该文件用于向用户展示可配置项的命名与含义。

```yaml
default_format: drawio
default_width: 1000
default_height: 800
default_palette: academic
node:
  default_width: 160
  default_height: 60
  horizontal_gap: 60
  vertical_gap: 120
output_dir: ""
```

### 12.2 paper_mode 预设

| 配置项 | 默认值 | paper_mode 值 |
|--------|--------|---------------|
| format | drawio | svg（强制） |
| font_family | SimSun, Arial, sans-serif | Times New Roman, serif |
| font_size | title=16, label=11, small=9 | title=11, label=9, small=7 |
| stroke_width | 1.5 (svg) / 1 (drawio) | 0.75 |
| arrow_style | classic | open（仅当原值为 classic 时切换） |
| gradient | - | false（强制） |
| shadow | - | false（强制） |
| grayscale | - | true（同时应用灰度滤镜） |

> SVG 默认 `font_family` 实际为 `"SimSun, Arial, sans-serif"`（含 `, sans-serif` 后缀），drawio 端仅取第一段 `SimSun` 作为 `fontFamily`。

### 12.3 presentation 预设

| 配置项 | 默认值 | presentation 值 |
|--------|--------|-----------------|
| gradient | false | true（仅当用户未显式指定 `gradient` 时开启） |
| shadow | false | true（仅当用户未显式指定 `shadow` 时开启） |

> 与 paper_mode 的"强制覆盖"不同，presentation 允许用户覆盖默认值：render.py 通过 `"gradient" in plan` 判断用户是否显式设置，若已设置则保留用户值，否则才注入 `True`。

### 12.4 font_size 配置

```json
{
  "font_size": {
    "title": 16,    // 标题字号
    "label": 11,    // 节点正文字号
    "small": 9      // 小字 (sub_label, 边标签)
  }
}
```

---

## 13. 错误处理

### 13.1 输入校验错误

`ValueError` 抛出条件：`validate_plan()` 返回任一 `("error", ...)` 级别问题。

错误信息格式：
```
[ERROR] nodes[0].id: id 'node1' 重复
[WARN]  edges[0].from: 引用未定义节点 'nonexistent'
布局 JSON 校验失败，请修复以上 ERROR
```

### 13.2 drawio 渲染后处理

`repair_drawio()` 自动修复：
- parent 引用无效 → 重置为 "1"
- edge mxGeometry 缺 relative="1" → 自动添加

### 13.3 边界场景处理

| 场景 | 处理 |
|------|------|
| 空 nodes + 空 edges | 生成只有标题的图 |
| 全部孤立节点 | 简单排成一行/一列 |
| 环路依赖 | 追加环中节点，标记 has_cycle=True，回边用红色虚线 |
| 节点缺坐标 | 触发自动布局 |
| 未知配色/形状 | 警告 + fallback |
| 节点缺 id | 校验 warning，渲染时内部使用数字 id |
| 文件不存在 (CLI) | sys.exit(1) |

---

## 14. 测试

### 14.1 测试框架

- 框架: pytest
- 测试目录: `tests/`
- 运行: `cd scripts/drawio-generator && pip install -e . && pytest`

### 14.2 测试覆盖

| 测试类 | 覆盖范围 |
|--------|----------|
| `TestPalettes` | 9 种配色存在性、必填 key、paper 配色 |
| `TestColorUtils` | _desaturate, _lighten |
| `TestBoundingBox` | 空/单节点自适应裁剪 |
| `TestRenderDrawio` | drawio 渲染：空图、虚线、自定义配色、emoji、bold、无 id 节点、layers、stroke_width、sub_label、edge_label |
| `TestRenderSvg` | SVG 渲染：基础、title_color、垂直边、auto_fit、虚线、open/diamond 箭头、grayscale、paper 配色 |
| `TestConfigPresets` | paper_mode 自动切换、presentation 开启投影 |
| `TestRender` | 统一 render() 入口、全功能组合 |
| `TestMainEntry` | CLI main() 入口、缺参数报错 |
| `TestValidatePlan` | 校验：合法/缺 title/缺 nodes/重复 id/缺坐标/未知配色/未知格式/未知 shape/引用未定义节点/非 dict |
| `TestNodeShapes` | 12 种形状定义存在性、cylinder/hexagon/process/rhombus 的 drawio+SVG 渲染、sketch 模式 SVG/drawio 渲染、sketch+shadow 滤镜叠加 |
| `TestRepairDrawio` | 健康文件、broken parent、missing relative、非 ASCII id、无效 XML、文件不存在、正常渲染产物 |
| `TestAutoLayout` | auto_layout 集成：flag、缺坐标触发、水平方向、已有坐标不变、孤立节点排列、cylinder 小尺寸、font_size 非 dict |
| `TestBuildAdjacency` | 空/无边/单边/未知节点边 |
| `TestTopologicalSort` | 空、线性链、环检测、菱形图 |
| `TestAssignLayers` | 线性链、菱形图、单节点 |
| `TestComputeCoordinates` | 垂直/水平布局、自定义尺寸 |
| `TestReduceCrossings` | 单层、空 adj、交叉优化、双向扫描、三层复杂图 |
| `TestLayoutPlan` | 空输入、单节点、线性垂直/水平、菱形图、环形图、默认/自定义尺寸 |
| `TestIdentifyBackEdges` | 无环、简单回边、环回边、自环、未知节点 |
| `TestSeparateIsolated` | 全连通、全孤立、混合、无 id 节点、未知边引用 |
| `TestIsolatedNodeLayout` | 孤立在上方/左侧、全孤立排列、孤立标签尺寸保留 |
| `TestAutoDirection` | 深度链→vertical、宽图→horizontal |
| `TestOrthogonalEdge` | 水平右/左、垂直下/上、共线、非共线 4 点 |
| `TestStraightEdge` | 水平/垂直直线边裁剪 |
| `TestBezierEdge` | 4 控制点、水平/垂直主导控制点偏移 |
| `TestComputeEdgePath` | orthogonal/straight/bezier 分发、默认 orthogonal |
| `TestClipLineToRect` | 右/左/下/上裁剪、零方向 |
| `TestTemplates` | 微服务/网络拓扑/数据流/ER 模板渲染、未知模板报错、用户覆盖 |
| `TestDiagramPresets` | 7 种预设定义、architecture 形状/颜色分配、flowchart 形状映射、无 diagram_type 不变 |
| `TestStylePresets` | default/dark/colorblind-safe 加载、bg 颜色校验、未知预设返回 None、预设列表 |
| `TestEdgeStyles` | 基础边样式、flow_animation 追加、端口分布、箭头间距检测 |
| `TestValidation` | 边穿过节点检测、边交叉检测、布局评分计算、grade 分级 |
| `TestRegressionBugs` | 扩展名检测、`<br>` 处理、中文 id 映射、basic vertex/edge 计数、paper_mode、presentation、空图、grayscale、Skill CLI |

> **注**: `shape_library.py` / `aiicons.py` / `legend.py` 已补充独立单元测试文件（`test_shape_library.py` / `test_aiicons.py` / `test_legend.py`）。仓库中还存在针对辅助脚本（buildup/drawiohtml/heatmap/restyle/svgflow）的测试文件，未列入上表。

### 14.3 回归 Bug 列表

| # | Bug | 修复 |
|---|-----|------|
| 1 | 扩展名检测失效 | 文件扩展名优先级 > plan.format |
| 2 | `<br>` 在 SVG 中显示为字面文本 | `.replace("<br>", "\n")` 再 split |
| 3 | 输出消息不一致 | 统一 `print("Generated: ...")` |
| 4 | 中文 id 导致 drawio 报错 | 内部映射为数字 id |
| 5 | `<br>` 在 drawio 中双转义 | 避免对已转义文本再调用 escape() |

---

## 15. 部署

### 15.1 部署方式

通过项目统一部署脚本：
```bash
./deploy/deploy.sh deploy drawio-generator --yes
```

### 15.2 部署后路径

| 资源 | 路径 |
|------|------|
| Skill 目录 | `/root/.hermes/skills/diagramming/drawio-generator/` |
| 渲染脚本 | `/root/.hermes/skills/diagramming/drawio-generator/scripts/render.py` |
| 核心库 | `/root/.hermes/scripts/drawio-generator/src/drawio_generator/` |
| SKILL.md | `/root/.hermes/skills/diagramming/drawio-generator/SKILL.md` |

### 15.3 版本信息

当前版本：v1.2.0 (VERSION = "1.2.0" in render.py)

---

## 16. 限制

### 16.1 适用场景

- ✅ 层级架构图
- ✅ 流程图
- ✅ 管线图
- ✅ 对比图
- ✅ 时间轴
- ✅ 嵌套分组容器
- ✅ 手绘风格（sketch 模式，SVG feTurbulence 抖动效果）

### 16.2 不适用场景

- ❌ 精确数据图表（柱状图/折线图）
- ❌ 节点 > 30 个的超复杂图
- ❌ 需要精确像素级控制的场景

### 16.3 已知约束

- SVG 中文字体依赖系统字体（默认 SimSun / Arial）
- drawio 输出用 draw.io 24.6.4 验证
- 自动布局是启发式算法，极复杂图可能需要手动调整
- 容器嵌套深度无硬限制，但建议 ≤3 层

---

## 17. 文件索引

> 行数为实际统计值（截至 2026-07-31）。`shape_library.py` 文件头注释仍写作"40+"，实际形状数已为 46（见 11.5 `summary()`）。

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/drawio_generator/render.py` | 340 | 主入口、格式路由、CLI |
| `src/drawio_generator/layout.py` | 431 | 自动布局引擎 |
| `src/drawio_generator/graphviz_layout.py` | 185 | 可选 Graphviz 布局引擎（含 `is_available()` 预检） |
| `src/drawio_generator/drawio_renderer.py` | 340 | .drawio XML 渲染 + 端口分布 + 箭头间距检查 + 修复（含 sketch_mode） |
| `src/drawio_generator/svg_renderer.py` | 241 | SVG 渲染（含 sketch 滤镜、双向箭头、bezier 曲线） |
| `src/drawio_generator/shapes.py` | 244 | 12 种形状定义和 SVG 渲染（含 rhombus） |
| `src/drawio_generator/shape_library.py` | 509 | 46 形状索引库 + 模糊搜索 + 分类过滤 + `summary()` |
| `src/drawio_generator/aiicons.py` | 896 | 98 项 AI 品牌图标库 + CDN 嵌入 + 模糊搜索 + `summary()` |
| `src/drawio_generator/legend.py` | 109 | 自动图例生成（颜色≥3 自动布局） |
| `src/drawio_generator/palettes.py` | 240 | 9 种配色 + 箭头 + 颜色工具 |
| `src/drawio_generator/geometry.py` | 155 | 包围盒、节点查找、边路径（clip/straight/bezier） |
| `src/drawio_generator/validator.py` | 454 | 输入校验 + P6 增强（边穿过/交叉/评分，受 `validate_layout` 控制） |
| `src/drawio_generator/containers.py` | 209 | 分组容器处理（容器 parent 扁平化为 "1"） |
| `src/drawio_generator/templates.py` | 322 | 图类型预设模板（4 种功能模板） |
| `src/drawio_generator/diagram_presets.py` | 97 | 7 种图类型样式预设 |
| `src/drawio_generator/style_presets.py` | 41 | 视觉预设（default/dark/colorblind-safe） |
| `src/drawio_generator/edge_styles.py` | 183 | 边线增强：flow_animation、端口分布、标签背景、箭头间距检查 |
| `tests/test_render.py` | 1580 | 渲染 + 配色 + 形状 + 修复 + 模板测试 |
| `tests/test_layout.py` | 518 | 布局引擎 + 几何计算测试 |
| `tests/test_graphviz_layout.py` | 62 | Graphviz 布局测试 |
| `tests/test_containers.py` | 172 | 容器分组测试 |
| `tests/test_diagram_presets.py` | 77 | 图类型预设测试 |
| `tests/test_edge_styles.py` | 132 | 边线增强测试 |
| `tests/test_validation.py` | 146 | P6 校验增强测试 |
| `tests/test_regression_bugs.py` | 181 | 回归 Bug 测试 |
| `skills/diagramming/drawio-generator/SKILL.md` | 235 | Skill 说明文档 |
| `skills/diagramming/drawio-generator/scripts/render.py` | 52 | Skill CLI 入口 |
| `config/default.yaml` | 17 | 默认配置（仅作文档参考，运行时不加载） |
| `README.md` | 187 | 项目说明 |