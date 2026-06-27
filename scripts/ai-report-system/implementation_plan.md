# AI报告生成系统 - 3天实施计划

## 项目概述
基于Hermes工具集的AI报告生成系统，适配WSL2环境，支持手动记忆检索功能。

## 设计原则
1. **Hermes工具优先**：关键模块使用Hermes内置工具，确保100%可用性
2. **多源搜索集成**：主用Hermes browser工具，备选web_search
3. **本地文档管理**：使用Hermes文件处理工具
4. **图表技能适配**：集成Hermes创意技能
5. **质量技能集成**：使用Hermes审查技能
6. **手动记忆检索**：按需调用，用户完全控制

## 3天实施计划

### Day 1: 数据采集与处理
**目标**: 实现搜索和文档解析功能

#### 模块1: HermesWebSearcher
- 替换假设的AI助手WebSearch工具
- 实现多级回退策略: browser → web_search → cache → local
- 智能缓存机制，1小时TTL
- 结果去重和相关性评分

#### 模块2: HermesDocumentParser
- 多格式文档解析: txt, md, py, json, yaml, csv, html等
- 回退链: 格式特定解析 → Markdown → 纯文本 → 元数据提取
- 10MB文件大小限制
- 错误恢复和优雅降级

### Day 2: 内容增强与质量控制
**目标**: 实现图表生成、质量评估和状态管理

#### 模块3: HermesDiagramGenerator
- 智能技能选择: 基于图表类型的优先级排序
- 4级技能回退: architecture-diagram → excalidraw → ascii-art → text
- 质量评分和自动回退
- 缓存已生成的图表

#### 模块4: HermesQualityAssessor
- 多维度质量评估: 语法、结构、风格、技术
- 层次化检查清单
- 可操作的改进建议
- 基于评估深度的置信度评分

#### 模块5: HermesStateManager
- 多存储引擎支持: SQLite → JSON → 内存
- 长时间任务的过程检查点
- 错误恢复和上下文保留
- 进度跟踪和报告

### Day 3: 报告规划与评估
**目标**: 实现报告规划、内容生成和最终评估

#### 模块6: HermesReportPlanner
- 基于任务的报告类型检测
- 资源需求评估(图表、数据、参考文献)
- 写作时间估算(含缓冲区计算)
- 内容优先级分配和工作流调度

#### 模块7: HermesContentGenerator
- 多模板系统，支持受众适配
- 智能段落生成，带质量过滤
- 关键词集成和内容丰富化
- 重复内容缓存优化

#### 模块8: HermesReportEvaluator
- 8维度评分系统: 语法、结构、风格、技术、可读性、完整性、可操作性、原创性
- 基于弱点的优化任务生成
- 版本评估的A/B比较
- 基于评估完整性的置信度评分

## 项目结构调整

### 新目录结构
```
ai_report_system/
├── src/
│   ├── core/                  # 核心模块
│   │   ├── __init__.py
│   │   ├── datatypes.py      # ReportTask, ReportOutline, GeneratedReport
│   │   ├── config.py         # 配置管理
│   │   └── exceptions.py     # 自定义异常
│   │
│   ├── hermes_tools/         # Hermes工具适配器
│   │   ├── __init__.py
│   │   ├── web_searcher.py   # HermesWebSearcher
│   │   ├── document_parser.py # HermesDocumentParser
│   │   ├── diagram_generator.py # HermesDiagramGenerator
│   │   └── quality_assessor.py # HermesQualityAssessor
│   │
│   ├── planning/             # 规划模块
│   │   ├── __init__.py
│   │   ├── report_planner.py # HermesReportPlanner
│   │   └── content_generator.py # HermesContentGenerator
│   │
│   ├── evaluation/           # 评估模块
│   │   ├── __init__.py
│   │   ├── report_evaluator.py # HermesReportEvaluator
│   │   └── state_manager.py  # HermesStateManager
│   │
│   ├── integration/          # 集成模块
│   │   ├── __init__.py
│   │   ├── workflow_orchestrator.py
│   │   └── memory_retrieval.py # 手动记忆检索系统
│   │
│   └── cli/                  # 命令行接口
│       ├── __init__.py
│       ├── main.py
│       └── commands.py
│
├── tests/                    # 测试
│   ├── __init__.py
│   ├── test_web_searcher.py
│   ├── test_document_parser.py
│   ├── test_diagram_generator.py
│   └── test_integration.py
│
├── docs/                     # 文档
│   ├── api.md
│   └── usage.md
│
├── config/                   # 配置文件
│   ├── default.yaml
│   └── local.yaml.example
│
├── templates/                # 报告模板
│   ├── technical/           # 技术报告模板
│   ├── business/           # 商业报告模板
│   └── academic/           # 学术报告模板
│
├── data/                     # 示例数据
│   ├── sample_reports/
│   └── test_documents/
│
├── scripts/                  # 辅助脚本
│   ├── init_db.py
│   └── test_installation.py
│
└── requirements.txt          # 依赖包
```

## 实施步骤

### 步骤1: 备份现有内容
```bash
cd /mnt/c/Users/1/Desktop/AI/报告团队/ai_report_system
mkdir -p backup
cp -r src backup/
cp -r tests backup/
cp requirements.txt backup/
```

### 步骤2: 创建新目录结构
按上述结构创建所有目录和初始化文件。

### 步骤3: 实现Day 1模块
1. 实现HermesWebSearcher
2. 实现HermesDocumentParser
3. 编写单元测试
4. 测试端到端搜索和文档解析

### 步骤4: 实现Day 2模块
1. 实现HermesDiagramGenerator
2. 实现HermesQualityAssessor
3. 实现HermesStateManager
4. 测试图表生成和质量评估

### 步骤5: 实现Day 3模块
1. 实现HermesReportPlanner
2. 实现HermesContentGenerator
3. 实现HermesReportEvaluator
4. 测试完整工作流

### 步骤6: 集成手动记忆检索
1. 创建manual_memory_retrieval模块
2. 实现按需调用接口
3. 集成到工作流协调器
4. 提供使用示例

### 步骤7: 创建命令行接口
1. 创建CLI应用结构
2. 实现基本命令
3. 添加配置管理
4. 测试命令操作

## 质量保证

### 测试策略
1. **单元测试**: 每个模块>90%覆盖率
2. **集成测试**: 模块间数据流
3. **端到端测试**: 完整报告生成流程
4. **性能测试**: 响应时间和资源使用

### 代码质量标准
1. 遵循Hermes Code Rules规范
2. 所有公共函数都有类型注解
3. 完整的文档字符串
4. 错误处理和恢复机制
5. 适当的日志记录

## 成功指标
1. **生成成功率**: ≥85%
2. **平均质量评分**: ≥0.7
3. **生成时间**: ≤5分钟(2000字报告)
4. **缓存命中率**: ≥40%
5. **错误恢复率**: ≥90%

## 风险管理
1. **Hermes工具可用性**: 实现多级回退
2. **性能问题**: 实施缓存和优化
3. **内容质量问题**: 多层质量检查
4. **用户接受度**: 手动记忆检索控制

---

**下一步**: 立即开始实施，按3天计划分配开发任务。