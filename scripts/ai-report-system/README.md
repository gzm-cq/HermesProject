# AI报告生成系统

基于Hermes工具集的智能AI报告生成系统，支持从多种数据源自动分析、生成和格式化专业报告。

## 项目概述

AI报告生成系统是一个现代化、模块化的Python应用程序，旨在自动化报告生成流程。系统利用AI技术（通过Hermes工具集）分析数据、提供洞察，并生成结构化的专业报告。

## 主要特性

- **多数据源支持**: 支持Excel、CSV、数据库、API等多种数据源
- **智能分析**: 利用AI技术进行数据分析和洞察提取
- **多种输出格式**: 支持Word、PDF、PPT、HTML、Markdown等格式
- **模板系统**: 可定制的报告模板系统
- **批量处理**: 支持批量生成报告
- **质量保证**: 内置数据验证和质量检查
- **可扩展架构**: 模块化设计，易于扩展新功能

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/your-org/ai-report-system.git
cd ai-report-system

# 安装依赖
pip install -e .  # 安装核心包
pip install -e ".[dev]"  # 安装开发依赖
pip install -e ".[cli]"  # 安装CLI工具
pip install -e ".[web]"  # 安装Web界面
```

### 基本使用

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
ai-report generate --input data.csv --output report.docx --template basic

# 批量生成报告
ai-report batch --input-dir ./data --output-dir ./reports

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
├── data/                  # 示例数据
├── config/                # 配置文件
├── templates/             # 报告模板
└── tools/                 # 工具脚本
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
- 模板渲染

### 输出导出模块 (src/export)
- Word文档导出
- PDF生成
- PPT演示文稿
- HTML和Markdown
- 图片和图表导出

## 配置系统

系统使用TOML格式的配置文件：

```toml
[api]
openai_api_key = "your-api-key"
model = "gpt-4"

[database]
type = "postgresql"
host = "localhost"
port = 5432

[report]
default_template = "professional"
output_dir = "./reports"
```

## 开发指南

### 代码规范
- 使用Python 3.10+
- 遵循PEP 8代码风格
- 使用类型注解
- 编写单元测试

### 开发环境设置
```bash
# 安装开发依赖
pip install -e ".[dev]"

# 设置预提交钩子
pre-commit install

# 运行测试
pytest tests/

# 代码检查
black src/
isort src/
ruff check src/
mypy src/
```

### 添加新功能
1. 创建新的模块或类
2. 编写类型注解和文档字符串
3. 添加单元测试
4. 更新文档
5. 提交Pull Request

## 部署

### Docker部署
```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "ai_report_system.cli"]
```

### 云部署
- AWS Lambda (无服务器)
- Docker容器
- Kubernetes集群

## 贡献指南

欢迎贡献！请参阅[CONTRIBUTING.md](CONTRIBUTING.md)了解详细信息。

1. Fork项目
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add some amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 打开Pull Request

## 许可证

本项目采用MIT许可证。详见[LICENSE](LICENSE)文件。

## 支持

- 问题反馈: [GitHub Issues](https://github.com/your-org/ai-report-system/issues)
- 文档: [项目文档](https://ai-report-system.readthedocs.io/)
- 邮件: report_team@example.com

## 致谢

- Hermes工具集团队
- 所有贡献者和用户
- 开源社区的支持和反馈

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