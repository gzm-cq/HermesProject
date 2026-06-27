# drawio-generator

> 根据自然语言描述生成 draw.io 矢量架构图/流程图/管线图。
> 输出 `.drawio` 或 `.svg` 文件。

## 入口

| 文件 | 职责 |
|------|------|
| `run.sh` | 执行入口 |
| `config/default.yaml` | 默认配置 |
| `tests/` | 布局/渲染/回归测试 |

## 使用

```bash
cd scripts/drawio-generator && python3 -m drawio_generator "..." --output output.drawio
```

## 功能

- 支持 academic / business / minimal / tech / warm 五种配色
- 输出 drawio（可编辑）或 SVG（矢量图）
- 对复杂架构图支持多页

## 依赖

- Python 3.10+
- `drawpy` 或 drawio 命令行渲染器

## 配置

见 `config/default.yaml` — LLM 模型、默认配色、尺寸。

## Skill

对应 skill：`drawio-generator`（software-development 分类）
