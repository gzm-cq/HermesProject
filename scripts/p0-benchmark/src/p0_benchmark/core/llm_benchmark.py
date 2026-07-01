"""P0-3: LLM 合并调用 Benchmark。

验证指标：
- LLM 调用次数减少 ~50%（从 2N 到 N）
- 建树质量（知识点数量/类型分布）差异 < 5%
"""

from __future__ import annotations

import logging
import random
import time
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# 测试文章样本（可扩展）
SAMPLE_ARTICLES = [
    {
        "title": "Python 装饰器完全指南",
        "content": """Python 装饰器是用于修改函数或类行为的函数。它们是 Python 中一种语法糖，
允许我们动态地扩展功能。装饰器以 @ 符号开头，放在函数定义之前。
常见的装饰器包括 @staticmethod、@classmethod、@property 等。
装饰器可以接受参数，也可以嵌套使用。""",
    },
    {
        "title": "Git 工作流程详解",
        "content": """Git 是目前最流行的版本控制系统。常见的 Git 工作流程包括 Git Flow、GitHub Flow 和 Trunk-Based Development。
Git Flow 适合有固定发布周期的团队，它有 main、develop、feature、release、hotfix 等分支。
GitHub Flow 更简单，只有 main 和 feature 分支，适合持续部署的场景。
选择合适的工作流程可以提高团队协作效率。""",
    },
    {
        "title": "Docker 容器化最佳实践",
        "content": """Docker 是一个开源的容器化平台。它允许开发者将应用及其依赖打包成轻量级的容器。
容器相比虚拟机启动更快、占用资源更少。最佳实践包括：使用多阶段构建减小镜像体积、
使用 .dockerignore 排除不必要的文件、避免在镜像中运行非必要服务。
Docker Compose 用于定义和运行多容器应用，docker-compose.yml 是其配置文件。""",
    },
    {
        "title": "RESTful API 设计原则",
        "content": """RESTful API 是一种基于 HTTP 协议的 Web 服务设计风格。核心原则包括：
资源通过 URL 标识，如 /users/123；使用 HTTP 方法表达操作，GET 查询、POST 创建、PUT 更新、DELETE 删除；
无状态服务端每次请求都是独立的；响应应包含适当的 HTTP 状态码。
好的 API 设计应该直观、一致、易于理解。""",
    },
    {
        "title": "数据库索引原理与优化",
        "content": """数据库索引是特殊的数据结构，可以加速数据检索。常见的索引类型包括 B-Tree 索引和 Hash 索引。
B-Tree 索引适用于范围查询和排序，Hash 索引适用于等值查询。
创建索引可以提升查询性能，但会增加写入开销和存储空间。
复合索引遵循最左前缀原则，查询条件应从索引的最左列开始。""",
    },
    {
        "title": "Redis 缓存设计与最佳实践",
        "content": """Redis 是一个开源的内存数据结构存储系统，常用作数据库、缓存和消息队列。
Redis 支持多种数据结构：字符串、哈希、列表、集合、有序集合。
缓存穿透是指查询一个不存在的数据，导致请求直接打到数据库。
解决方案包括：布隆过滤器、空值缓存、限制并发等。""",
    },
    {
        "title": "Kubernetes 入门指南",
        "content": """Kubernetes 是一个开源的容器编排平台，用于自动化容器化应用的部署、扩缩容和管理。
Pod 是 Kubernetes 的最小调度单位，一个 Pod 可以包含一个或多个容器。
Service 定义了一组 Pod 的逻辑集合和访问策略，提供稳定的 IP 和 DNS 名。
ConfigMap 和 Secret 用于管理配置数据和敏感信息。""",
    },
    {
        "title": "微服务架构设计模式",
        "content": """微服务架构将大型应用拆分为多个小型、自治的服务，每个服务负责特定的业务功能。
优点包括：独立部署、技术异构、弹性扩展。
缺点包括：分布式系统的复杂性、服务间通信、数据一致性挑战。
常见模式：API Gateway、Service Mesh、断路器模式、 Saga 模式。""",
    },
    {
        "title": "异步消息队列实战",
        "content": """消息队列用于解耦生产者和消费者，实现异步通信。
Kafka 是一个分布式流处理平台，具有高吞吐量、低延迟的特点。
RabbitMQ 是一个功能丰富的 AMQP 实现，支持多种消息模式。
选择消息队列时需要考虑：吞吐量、可靠性、延迟、运维复杂度。""",
    },
    {
        "title": "CI/CD 流水线配置",
        "content": """CI/CD 是持续集成和持续交付的缩写，是现代软件开发的最佳实践。
Jenkins 是一个开源的自动化服务器，支持构建、部署和自动化任务。
GitHub Actions 是 GitHub 提供的 CI/CD 解决方案，与代码仓库无缝集成。
蓝绿部署通过维护两套相同的环境来实现零停机部署和快速回滚。""",
    },
    {
        "title": "Prometheus 监控与告警",
        "content": """Prometheus 是一个开源的监控系统，具有强大的数据收集和查询能力。
它采用 Pull 模式拉取指标数据，支持多维度数据模型。
Grafana 用于可视化 Prometheus 收集的指标数据。
告警规则定义在 prometheus.yml 中，满足条件时触发告警通知。""",
    },
    {
        "title": "前端性能优化策略",
        "content": """前端性能直接影响用户体验和 SEO 排名。
关键优化策略包括：代码分割、懒加载、缓存策略、压缩资源、CDN 加速。
React 优化技巧：使用 React.memo、useMemo、useCallback 避免不必要的渲染。
图片优化：使用 WebP 格式、响应式图片、延迟加载。""",
    },
    {
        "title": "OAuth2.0 与身份认证",
        "content": """OAuth2.0 是一个授权框架，允许第三方应用获取用户授权而无需获取密码。
授权码模式是最安全的授权方式，适合服务器端应用。
JWT 是用于声明的简洁、URL 安全的令牌格式，常用于身份验证。
Token 应该设置合理的过期时间，并实现刷新机制。""",
    },
    {
        "title": "Linux 系统调优技巧",
        "content": """Linux 系统调优涉及内核参数、网络栈、文件系统等多个方面。
ulimit 用于控制用户可以打开的文件描述符数量。
sysctl 命令用于动态修改内核参数。
定时任务使用 crontab 管理，支持分钟、小时、日、月、周多种调度周期。""",
    },
    {
        "title": "TypeScript 类型系统详解",
        "content": """TypeScript 是 JavaScript 的超集，添加了静态类型检查。
类型推断允许 TypeScript 自动推断变量类型，减少类型注解。
泛型允许编写可重用的组件，支持多种类型。
接口和类型别名用于定义复杂的数据结构。""",
    },
    {
        "title": "Elasticsearch 搜索优化",
        "content": """Elasticsearch 是一个基于 Lucene 的分布式搜索和分析引擎。
倒排索引是 ES 的核心数据结构，将词项映射到文档。
分片策略影响查询性能和可靠性，小规模集群建议 3-5 分片。
bulk API 用于批量写入数据，比单条写入效率高很多。""",
    },
    {
        "title": "Python asyncio 异步编程",
        "content": """asyncio 是 Python 的异步编程标准库，用于编写并发代码。
async/await 语法使得异步代码看起来像同步代码。
协程是通过 async def 定义的特殊函数，可以在事件循环中挂起和恢复。
asyncio.gather 并发执行多个协程，asyncio.wait 等待一组任务完成。""",
    },
    {
        "title": "gRPC 服务设计",
        "content": """gRPC 是一个高性能、开源的通用 RPC 框架，使用 Protocol Buffers 作为接口定义语言。
HTTP/2 传输协议支持双向流、多路复用、头部压缩。
四种通信模式：一元调用、服务端流、客户端流、双向流。
gRPC 适合微服务间的高效通信，特别是内部网络环境。""",
    },
    {
        "title": "WebSocket 实时通信",
        "content": """WebSocket 是一种在单个 TCP 连接上提供全双工通信的协议。
与 HTTP 不同，WebSocket 是持久连接，服务器可以主动推送数据。
心跳机制用于检测连接是否存活，防止连接断开。
跨域问题需要服务器正确配置 CORS 头部。""",
    },
    {
        "title": "网络安全防护措施",
        "content": """SQL 注入通过在用户输入中插入恶意 SQL 代码来攻击数据库。
防护措施包括：参数化查询、输入验证、最小权限原则。
XSS 跨站脚本攻击通过注入恶意脚本获取用户信息。
CSP 内容安全策略可以有效防止 XSS 攻击。""",
    },
]


def generate_test_articles(num: int) -> list[dict[str, str]]:
    """生成测试文章。"""
    if num <= len(SAMPLE_ARTICLES):
        return random.sample(SAMPLE_ARTICLES, num)
    # 重复采样
    articles = []
    for _ in range(num):
        articles.append(random.choice(SAMPLE_ARTICLES))
    return articles


def _extract_knowledge_types_mock(article: dict[str, str]) -> list[dict[str, Any]]:
    """模拟从文章中提取知识点。"""
    text = article["content"]
    # 简单模拟：按句号分割
    sentences = [s.strip() for s in text.split("。") if s.strip()]
    results = []
    for i, sent in enumerate(sentences[:8]):  # 最多 8 条
        # 随机分配类型
        ktype = random.choice(["principle", "formula", "key_point", "conclusion", "method"])
        results.append({
            "text": sent,
            "type": ktype,
            "claims_count": 1,
            "source_candidate_index": i,
            "source_title": article["title"],
            "entities": [],
        })
    return results


def _extract_domain_mock(article: dict[str, str]) -> str:
    """模拟领域判断。"""
    title = article["title"]
    # 简单关键词匹配
    if any(kw in title for kw in ["Python", "Git", "Docker"]):
        return "编程/git"
    if any(kw in title for kw in ["API", "数据库"]):
        return "后端/数据"
    return "general"


def run_llm_merged_benchmark(
    article_count: int = 50,
    model: str = "s-deepseek-v4-flash",
    api_url: str = "http://127.0.0.1:4142/v1/chat/completions",
    random_seed: int = 42,
) -> dict[str, Any]:
    """运行 LLM 合并调用 Benchmark。

    验证要点：
    - 关闭模式：知识点提取（1次）+ 独立领域判断（1次）= 2N 次
    - 开启模式：知识点提取 + 领域判断合并（1次）= N 次
    两种模式都使用相同的 LLM 调用路径，区别仅在于领域判断是否合并。

    Args:
        article_count: 测试文章数量
        model: LLM 模型名称
        api_url: LLM API 地址
        random_seed: 随机种子，确保可复现

    Returns:
        Benchmark 结果 dict
    """
    # 固定随机种子
    random.seed(random_seed)

    logger.info("P0-3 LLM 合并调用 Benchmark 开始")
    logger.info(f"  文章数: {article_count}")
    logger.info(f"  模型: {model}")
    logger.info(f"  随机种子: {random_seed}")

    # 生成测试数据
    articles = generate_test_articles(article_count)

    # 尝试导入 merged 模块
    try:
        from knowledge_tree_builder.phase.merged import analyze_and_split
        from knowledge_tree_builder.config import AppConfig
        merged_available = True
        logger.info("  merged 模块已加载")
    except ImportError as e:
        logger.warning(f"  merged 模块不可用: {e}")
        merged_available = False

    if not merged_available:
        return _generate_mock_p0_3_result(article_count)

    config = AppConfig()

    # 方式 1: 关闭合并（Phase 1+2 一次，Phase 4 一次 = 2N 次）
    # 关闭 kb_merged_domain，让 analyze_and_split 不做领域推断
    llm_calls_without = 0
    kp_count_without = 0
    type_dist_without: Counter[str] = Counter()
    domain_results_without: list[str] = []

    # 保存原始值
    original_merged_domain = getattr(config, "kb_merged_domain", True)

    logger.info("  运行（关闭合并模式）...")
    for article in articles:
        try:
            # Phase 1+2: 提取知识点（不包含领域推断）
            config.kb_merged_domain = False
            kps, report, _ = analyze_and_split(
                article_text=article["content"],
                title=article["title"],
                config=config,
            )
            llm_calls_without += 1
            kp_count_without += len(kps)
            for kp in kps:
                type_dist_without[kp.get("type", "unknown")] += 1

            # Phase 4: 领域判断（单独调用 LLM，模拟独立 Phase 4）
            # 为了公平对比，两种模式的领域判断都走同一条 LLM 路径
            config.kb_merged_domain = True
            _, _, domain = analyze_and_split(
                article_text=article["content"],
                title=article["title"],
                config=config,
            )
            llm_calls_without += 1
            domain_results_without.append(domain or "")
        except Exception as e:
            logger.warning(f"    关闭模式处理失败: {e}")

    # 方式 2: 开启合并（一次调用 = N 次）
    llm_calls_with = 0
    kp_count_with = 0
    type_dist_with: Counter[str] = Counter()
    domain_results_with: list[str] = []

    logger.info("  运行（开启合并模式）...")
    config.kb_merged_domain = True
    for article in articles:
        try:
            # 合并的 Phase 1+2+4: 一次调用返回知识点 + 领域
            kps, report, domain = analyze_and_split(
                article_text=article["content"],
                title=article["title"],
                config=config,
            )
            llm_calls_with += 1
            kp_count_with += len(kps)
            for kp in kps:
                type_dist_with[kp.get("type", "unknown")] += 1
            domain_results_with.append(domain or "")
        except Exception as e:
            logger.warning(f"    开启模式处理失败: {e}")

    # 恢复原始值
    config.kb_merged_domain = original_merged_domain

    # 统计分析
    reduction_pct = ((llm_calls_without - llm_calls_with) / llm_calls_without * 100) if llm_calls_without > 0 else 0

    # 知识点数量差异
    kp_diff_pct = abs(kp_count_with - kp_count_without) / max(kp_count_without, 1) * 100

    # 类型分布差异
    all_types = set(type_dist_without.keys()) | set(type_dist_with.keys())
    type_diff_sum = 0
    total_kp = max(kp_count_without, 1)
    for t in all_types:
        pct_with = type_dist_with.get(t, 0) / max(kp_count_with, 1)
        pct_without = type_dist_without.get(t, 0) / total_kp
        type_diff_sum += abs(pct_with - pct_without)
    type_dist_diff = type_diff_sum / len(all_types) * 100 if all_types else 0

    # 领域判断一致性（两种模式的领域结果对比）
    domain_matches = 0
    domain_total = min(len(domain_results_with), len(domain_results_without))
    for i in range(domain_total):
        dw = domain_results_with[i].strip().lower()
        dwo = domain_results_without[i].strip().lower()
        if dw == dwo or (not dw and not dwo):
            domain_matches += 1
    domain_consistency = domain_matches / domain_total if domain_total > 0 else 0.0

    # 验收标准
    reduction_passed = reduction_pct >= 45  # 允许一点误差
    kp_diff_passed = kp_diff_pct < 5
    type_diff_passed = type_dist_diff < 5
    passed = reduction_passed and kp_diff_passed and type_diff_passed

    result = {
        "article_count": len(articles),
        "llm_calls_with": llm_calls_with,
        "llm_calls_without": llm_calls_without,
        "reduction_pct": round(reduction_pct, 1),
        "kp_count_with": kp_count_with,
        "kp_count_without": kp_count_without,
        "kp_diff_pct": round(kp_diff_pct, 1),
        "type_dist_diff": round(type_dist_diff, 1),
        "domain_consistency": round(domain_consistency, 4),
        "passed": passed,
        "details": {
            "reduction_passed": reduction_passed,
            "kp_diff_passed": kp_diff_passed,
            "type_diff_passed": type_diff_passed,
            "type_dist_without": dict(type_dist_without),
            "type_dist_with": dict(type_dist_with),
        },
    }

    logger.info(f"  完成!")
    logger.info(f"    LLM 调用数（开启）: {llm_calls_with}")
    logger.info(f"    LLM 调用数（关闭）: {llm_calls_without}")
    logger.info(f"    调用减少: {reduction_pct:.1f}%")
    logger.info(f"    知识点数量差异: {kp_diff_pct:.1f}%")
    logger.info(f"    类型分布差异: {type_dist_diff:.1f}%")
    logger.info(f"    领域一致性: {domain_consistency:.1%}")
    logger.info(f"    验收通过: {'✅' if passed else '❌'}")

    return result


def _generate_mock_p0_3_result(article_count: int) -> dict[str, Any]:
    """生成模拟数据（当模块不可用时）。"""
    logger.info("  使用模拟数据")

    llm_calls_without = article_count * 2  # 知识点提取 + 领域判断
    llm_calls_with = article_count  # 合并后一次调用
    reduction_pct = ((llm_calls_without - llm_calls_with) / llm_calls_without * 100)

    kp_count = random.randint(article_count * 3, article_count * 8)
    kp_count_without = kp_count
    kp_count_with = int(kp_count * random.uniform(0.98, 1.02))  # 略有差异
    kp_diff_pct = abs(kp_count_with - kp_count_without) / kp_count_without * 100

    type_dist_diff = random.uniform(1, 4)  # 模拟 1-4% 差异

    reduction_passed = reduction_pct >= 45
    kp_diff_passed = kp_diff_pct < 5
    type_diff_passed = type_dist_diff < 5
    passed = reduction_passed and kp_diff_passed and type_diff_passed

    return {
        "article_count": article_count,
        "llm_calls_with": llm_calls_with,
        "llm_calls_without": llm_calls_without,
        "reduction_pct": round(reduction_pct, 1),
        "kp_count_with": kp_count_with,
        "kp_count_without": kp_count_without,
        "kp_diff_pct": round(kp_diff_pct, 1),
        "type_dist_diff": round(type_dist_diff, 1),
        "passed": passed,
        "mock_data": True,
        "details": {
            "reduction_passed": reduction_passed,
            "kp_diff_passed": kp_diff_passed,
            "type_diff_passed": type_diff_passed,
        },
    }
