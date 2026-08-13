# drawio-generator

> 根据自然语言描述生成 draw.io 矢量架构图/流程图/管线图。
> 输出 `.drawio` 或 `.svg` 文件。
> 内置自动布局引擎（纯 Python dagre 风格）、7 种配色方案、多页支持。

## 入口

| 文件 | 职责 |
|------|------|
| `run.sh` | 执行入口 |
| `config/default.yaml` | 默认配置 |
| `tests/` | 布局/渲染/回归测试 |

## 安装

```bash
cd /mnt/d/HermesProject/scripts/drawio-generator
pip install -e .
```

## CLI 用法

将结构化布局 JSON 文件渲染为 .drawio 或 .svg：

```bash
cd scripts/drawio-generator && python3 -m drawio_generator layout.json output.drawio
```

从 stdin 读取 JSON 并自动推导输出文件名：

```bash
cat layout.json | python3 -m drawio_generator -
```

列出可用配色方案：

```bash
python3 -m drawio_generator --list-palettes
```

生成后直接在浏览器中打开 / 启动 HTTP 预览服务器：

```bash
python3 -m drawio_generator layout.json output.svg --open
python3 -m drawio_generator layout.json output.drawio --serve --port 8080
```

## Python API

从自然语言或结构化布局字典生成矢量图：

```python
from drawio_generator import generate_svg, render, layout_plan

# 方式一：传入结构化布局字典（支持 dict 或 JSON 字符串）
plan = {
    "title": "微服务架构图",
    "width": 1000,
    "height": 600,
    "palette": "tech",          # 配色：academic / business / minimal / tech / warm
    "format": "drawio",         # 输出格式：drawio 或 svg
    "nodes": [
        {"id": "gw", "label": "API 网关",   "x": 400, "y": 50,  "w": 160, "h": 60, "color": "node_blue"},
        {"id": "svc1", "label": "用户服务",  "x": 100, "y": 200, "w": 160, "h": 60, "color": "node_green"},
        {"id": "svc2", "label": "订单服务",  "x": 400, "y": 200, "w": 160, "h": 60, "color": "node_orange"},
        {"id": "db1", "label": "用户 DB",   "x": 100, "y": 350, "w": 160, "h": 60, "color": "node_red",  "shape": "cylinder"},
        {"id": "db2", "label": "订单 DB",   "x": 400, "y": 350, "w": 160, "h": 60, "color": "node_red",  "shape": "cylinder"},
    ],
    "edges": [
        {"from": "gw", "to": "svc1"},
        {"from": "gw", "to": "svc2"},
        {"from": "svc1", "to": "db1", "label": "读写"},
        {"from": "svc2", "to": "db2", "label": "读写", "dashed": True},
    ],
}
render(plan, "/tmp/architecture.drawio")          # → .drawio 格式
generate_svg(plan, "/tmp/architecture.svg")        # → .svg 格式（兼容别名）

# 方式二：先自动布局，再渲染（只需给出节点/边关系，无需手写坐标）
plan_no_coords = {
    "title": "数据管线图",
    "width": 800,
    "height": 500,
    "palette": "academic",
    "auto_layout": True,
    "layout_direction": "vertical",
    "nodes": [
        {"id": "src", "label": "数据源", "color": "node_blue"},
        {"id": "etl", "label": "ETL 清洗", "color": "node_green"},
        {"id": "dw",  "label": "数据仓库", "color": "node_orange"},
        {"id": "bi",  "label": "BI 报表", "color": "node_purple"},
    ],
    "edges": [
        {"from": "src", "to": "etl"},
        {"from": "etl", "to": "dw"},
        {"from": "dw",  "to": "bi"},
    ],
}
render(plan_no_coords, "/tmp/pipeline.drawio")

# 方式三：独立使用自动布局引擎获取坐标
layout_result = layout_plan(nodes, edges, direction="horizontal")
# layout_result 包含：nodes（含 x/y/w/h）、width、height、edge_routes、has_cycle
```

### 高级特性

```python
# 支持 emoji 图标、子标题、自定义形状、渐变/投影
plan = {
    "title": "部署架构",
    "width": 1200, "height": 800,
    "palette": "tech",
    "gradient": True,            # 渐变填充
    "shadow": True,              # 投影效果
    "arrow_style": "open",       # 箭头样式：classic / open / diamond / circle / thick / none
    "stroke_width": 2,           # 描边宽度
    "font_family": "Microsoft YaHei, sans-serif",
    "nodes": [
        {"id": "fe", "label": "Nginx",   "x": 50, "y": 100, "w": 160, "h": 60, "color": "node_blue", "emoji": "🌐"},
        {"id": "be", "label": "后端",    "x": 350, "y": 100, "w": 160, "h": 60, "color": "node_green", "bold": True},
        {"id": "db", "label": "PostgreSQL", "x": 650, "y": 100, "w": 160, "h": 60, "color": "node_red", "shape": "cylinder", "sub_label": "主从"},
    ],
    "layers": [  # 区域层（用于分组/高亮）
        {"x": 30, "y": 80, "w": 200, "h": 100, "label": "接入层"},
        {"x": 330, "y": 80, "w": 200, "h": 100, "label": "业务层"},
    ],
}
```

## 功能

- 支持 academic / business / minimal / tech / warm / paper-wireframe / paper-grayscale / dark / colorblind-safe 九种配色
- 输出 drawio（可编辑 XML）或 SVG（矢量图）
- 内置纯 Python 自动布局引擎（dagre 风格，零外部依赖）
- 支持 emoji 图标、子标题（sub_label）、粗体节点
- 支持区域层（layers）、渐变填充、投影效果
- 内置后处理校验与自动修复（repair_drawio）
- 支持 HTTP 预览服务器，生成后浏览器直接查看
- 对复杂架构图支持多页

## 自然语言 → 图的完整流程

从自然语言描述到生成矢量图，推荐管线如下：

```
自然语言描述 → LLM 生成结构化布局 JSON → drawio-generator 渲染 → .drawio/.svg
```

示例（配合 LLM 接口使用）：

```python
import json
import requests
from drawio_generator import render

# Step 1: 用 LLM 将自然语言转为结构化布局（示意 prompt）
user_desc = "一个电商系统：用户通过浏览器访问 CDN，CDN 回源到 Nginx，Nginx 反向代理到 Web 应用，Web 读写 MySQL 主库和 Redis 缓存，同时往后发送消息到 Kafka 给数据分析服务。"

# Step 2: 将 LLM 返回的 JSON 传给 drawio-generator
plan = {
    "title": "电商系统架构图",
    "width": 1200, "height": 800,
    "palette": "tech",
    "auto_layout": True,
    "layout_direction": "horizontal",
    "nodes": [
        {"id": "browser", "label": "浏览器", "color": "node_blue"},
        {"id": "cdn",     "label": "CDN",    "color": "node_cyan"},
        {"id": "nginx",   "label": "Nginx",  "color": "node_blue"},
        {"id": "web",     "label": "Web 应用", "color": "node_green"},
        {"id": "redis",   "label": "Redis",  "color": "node_purple"},
        {"id": "mysql",   "label": "MySQL",  "color": "node_red", "shape": "cylinder"},
        {"id": "kafka",   "label": "Kafka",  "color": "node_orange"},
        {"id": "analytics", "label": "分析服务", "color": "node_green"},
    ],
    "edges": [
        {"from": "browser", "to": "cdn"},
        {"from": "cdn",     "to": "nginx"},
        {"from": "nginx",   "to": "web"},
        {"from": "web",     "to": "mysql", "label": "读写"},
        {"from": "web",     "to": "redis", "label": "缓存"},
        {"from": "web",     "to": "kafka", "label": "消息"},
        {"from": "kafka",   "to": "analytics"},
    ],
}
render(plan, "ecommerce-architecture.drawio")
```

## 配色方案

| 名称 | 描述 | 适用场景 |
|------|------|----------|
| academic | Nature/PNAS 风格，低饱和度、色盲友好 | 论文/学术报告 |
| business | Google Material 3 风格 | 商务汇报 |
| minimal | Figma 原型风格，节点统一灰色 | 线框图/原型 |
| tech | Vercel/Linear 风格，高饱和色彩 | 技术博客/演示 |
| warm | 咖啡/陶土暖色系 | 非正式/创意 |
| paper-wireframe | 极简线框，黑白打印 | 论文黑白打印 |
| paper-grayscale | 全灰度，极致黑白印刷 | 论文黑白印刷 |
| dark | 深色主题，暗背景高对比度 | 暗色演示 / 终端风格 |
| colorblind-safe | 基于 Okabe-Ito 色盲友好调色板 | 无障碍演示 / 科研 |

## 配置

见 `config/default.yaml` — LLM 模型、默认配色、尺寸、节点间距。

## 依赖

- Python 3.10+
- 无外部 Python 依赖（渲染引擎 100% 纯 Python，零外部包）
- 可选：`drawpy` 或 draw.io 官方渲染器（用于 CLI 批量转换 .drawio → PNG）

## 测试

```bash
cd /mnt/d/HermesProject/scripts/drawio-generator && pip install -e . && pytest
```

测试覆盖布局引擎、配色方案、颜色工具函数、drawio 渲染（节点/边/虚线/emoji/粗体/区域层/描边宽度/子标题/边标签）、SVG 渲染（字体/投影/渐变/自动裁剪/虚线边/水平竖直边）。

## 部署

通过项目统一部署脚本 deploy.sh 部署到生产环境：

```bash
./deploy/deploy.sh deploy drawio-generator --yes
```

## Skill

对应 skill：`drawio-generator`（software-development 分类），在 Hermes Gateway 中通过 skill_view() 加载后可通过自然语言直接调用。

Skill 调用方式：加载 skill 后在对话中描述架构需求，skill 自动调用核心库生成 .drawio/.svg 文件并返回路径。

## 辅助脚本（scripts/）

`scripts/` 下的独立工具不属 pip 包，但各有专项用途与单测，非死代码：

| 脚本 | 用途 |
|------|------|
| `buildup.py` | 批量将多个 .drawio 合并为单一文件（层级堆叠） |
| `heatmap.py` | 按调用频次/权重生成热度图 |
| `restyle.py` | 批量替换配色 / 节点样式（重皮肤化） |
| `svgflow.py` | 将 SVG 边转为带动画的 flow 效果（animateMotion） |
| `drawiohtml.py` | 导出可在浏览器直接预览的 HTML 包装页 |
