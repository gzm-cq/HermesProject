# AI报告生成系统

基于Hermes工具集的智能AI报告生成系统，支持从多种数据源自动分析、生成和格式化专业报告。

## 项目概述

AI报告生成系统是一个现代化、模块化的Python应用程序，旨在自动化报告生成流程。系统利用AI技术（通过Hermes工具集）分析数据、提供洞察，并生成结构化的专业报告。

## 主要特性

- **多数据源支持**: 支持Excel、CSV、数据库、API等多种数据源
- **智能分析**: 利用AI技术进行数据分析和洞察提取
- **多种输出格式**: 支持Word、PDF、PPT、HTML、Markdown等格式
- **质量保证**: 内置数据验证和质量检查
- **可扩展架构**: 模块化设计，易于扩展新功能

## 安装

通过 HermesProject 统一部署脚本安装：

```bash
./deploy/deploy.sh deploy ai-report-system --yes
```

该命令会将项目源码部署至 `~/.hermes/scripts/ai-report-system/`，并完成依赖安装和配置。

## 依赖

- **Hermes 工具集**: 提供 AI 分析能力、数据加载、报告生成等核心组件
- **LiteLLM 网关**: 提供统一的大模型调用入口，支持 DeepSeek、SiliconFlow 等多种模型供应商
- **环境变量**: 所有配置通过 `AI_REPORT_*` 环境变量管理（参见下方环境变量表）

## 基本使用

```python
from ai_report_system import ReportGenerator

# 创建报告生成器
generator = ReportGenerator()

# 加载数据
data = generator.load_data("data/sample.csv")

# 生成报告
report = generator.analyze(data)

# 导出报告
report.export("输出报告.docx")
```

### CLI使用

```bash
# 生成简单报告
ai-report generate --input data.csv --output report.docx

# 查看系统状态
ai-report status
```

## 项目结构

```
ai_report_system/
├── src/                    # 源代码
│   ├── core/              # 核心模块
│   ├── data/              # 数据处理模块
│   ├── analysis/          # AI分析模块
│   ├── report/            # 报告生成模块
│   ├── export/            # 输出导出模块
│   └── utils/             # 工具函数
├── tests/                 # 测试代码
├── docs/                  # 文档
└── config/                # 配置文件
```

## 模块说明

### 核心模块 (src/core)
- 系统配置管理
- 日志和监控
- 任务调度
- 错误处理

### 数据处理模块 (src/data)
- 数据加载和解析
- 数据清洗和预处理
- 数据转换和整合
- 数据验证

### AI分析模块 (src/analysis)
- 统计分析和描述性统计
- AI洞察生成
- 趋势分析和预测
- 异常检测

### 报告生成模块 (src/report)
- 报告结构定义
- 内容组织和编排
- 风格和格式管理

### 输出导出模块 (src/export)
- Word文档导出
- PDF生成
- PPT演示文稿
- HTML和Markdown
- 图片和图表导出

## 部署

系统通过 HermesProject 统一部署流程管理。详情请参阅 [deploy/README.md](../../deploy/README.md) 了解部署架构和配置说明。

部署后系统位于 `~/.hermes/scripts/ai-report-system/`，由 Hermes Gateway 统一管理。

## 环境变量

所有环境变量统一使用 `AI_REPORT_` 前缀。旧环境变量名作为 fallback 保留向后兼容。

| 变量名 | 说明 | 默认值 | Fallback 旧名 |
|--------|------|--------|---------------|
| `AI_REPORT_WORK_DIR` | 工作目录 | `./reports` | — |
| `AI_REPORT_LOG_LEVEL` | 日志级别 | `INFO` | — |
| `AI_REPORT_CACHE_ENABLED` | 是否启用缓存 | `true` | — |
| `AI_REPORT_LLM_API_KEY` | LLM API 密钥 | (空) | `OPENAI_API_KEY` |
| `AI_REPORT_LLM_BASE_URL` | LLM API 基础 URL | (空) | — |
| `AI_REPORT_LLM_MODEL` | LLM 模型名称 | `gpt-4` | — |
| `AI_REPORT_LITELLM_API_KEY` | LiteLLM 网关密钥（降级路径） | (空) | `LITELLM_MASTER_KEY` |
| `AI_REPORT_DEEPSEEK_API_KEY` | DeepSeek API 密钥（降级路径） | (空) | `DEEPSEEK_API_KEY` |
| `AI_REPORT_SILICONFLOW_API_KEY` | SiliconFlow API 密钥（降级路径） | (空) | `SILICONFLOW_API_KEY` |
| `AI_REPORT_GLM_API_KEY` | 智谱 AI API 密钥（降级路径） | (空) | `GLM_API_KEY` |
| `AI_REPORT_SHANGTANG_API_KEY` | 商汤 API 密钥（降级路径） | (空) | `SHANGTANG_API_KEY` |
| `AI_REPORT_HERMES_URL` | Hermes 服务地址 | `http://localhost:8080` | — |
| `AI_REPORT_HERMES_API_KEY` | Hermes API 密钥 | (空) | — |
| `AI_REPORT_TAVILY_API_KEY` | Tavily 搜索 API 密钥 | (空) | `TAVILY_API_KEY` |
| `AI_REPORT_SEARCH_TIMEOUT` | 搜索超时（秒） | `30` | — |
| `AI_REPORT_SEARCH_MAX_RESULTS` | 最大搜索结果数 | `10` | — |
| `AI_REPORT_SOURCE_DOC_PATH` | 源文档路径 | (空) | `SOURCE_DOC_PATH` |
| `AI_REPORT_DIFY_COMPOSE` | Dify docker-compose 路径 | `/app/dify/docker/docker-compose.yml` | `DIFY_COMPOSE` |
| `AI_REPORT_DIFY_API` | Dify API 地址 | `http://api:5001` | `DIFY_API` |

> **优先级**：`AI_REPORT_*` 环境变量 > 旧环境变量名 > 默认值
>
> 使用方式：在代码中通过 `from ai_report.config import get_env_config` 获取统一配置，
> 不要直接调用 `os.getenv()` 或 `os.environ.get()`。