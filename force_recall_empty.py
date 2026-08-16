"""受控测试：通过已部署的 knowledge-navigation recall 钩子强制产生 recall_empty_results 事件。

背景：gateway API(:8642) 的 web_server 没有"注入消息"的端点（只有 /chat 的 PTY）。
但 recall 钩子 pre_llm_call 就是 gateway 在每轮 LLM 前运行的同一段代码。本脚本在
WSL 内用已部署模块直接调用它，复用实时 trace.log 的 handler（config.setup_logging
在 import 时按 KN_TRACE_LOG_PATH 把 RotatingFileHandler 挂到 knowledge_navigation logger），
因此事件会落进与 gateway 相同的 trace.log，可被 recall_empty_trace_analyzer 读取。

注意：仅用纯乱码查询触发"全路由空结果"，不触达 LLM、不修改任何生产数据。
"""

import os
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor

# 1) 先加载生产 .env（含 KN_TRACE_LOG_PATH / KN_HINDSIGHT_URL 等），必须在 import config 之前
HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
env_path = os.path.join(HERMES_HOME, ".env")
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# 2) 让已部署的插件源码在 sys.path 最前（egg-info 可能存在，但显式优先更稳）
PLUGIN_SRC = "/root/.hermes/plugins/knowledge-navigation/src"
if PLUGIN_SRC not in sys.path:
    sys.path.insert(0, PLUGIN_SRC)

# 3) import config -> 自动 setup_logging() -> 挂 trace.log handler 到 knowledge_navigation logger
import knowledge_navigation.config as kn_config  # noqa: E402

# 4) import 钩子模块，注入 recall 线程池（独立进程必须手动初始化，gateway 启动时已建好）
from knowledge_navigation.core.hooks import router as kn_router  # noqa: E402

kn_router._recall_executor = ThreadPoolExecutor(max_workers=4)

# 强制 Router mask 全开：独立环境下 LLM Router 对乱码返回全关导致 recall 被跳过，
# 无法走到"空结果"分支。这里强制开启（模拟真实查询时 router 开启 recall 的正常状态）。
kn_router._router_route = lambda *a, **k: {"h": True, "kt": True, "s": True, "sag": False}

# 受控"无相关记忆"：语义 recall 对乱码也会返回最近邻，故直接让三路后端返回空，
# 模拟"查询在知识库中无命中"的空结果场景 -> 触发 recall_empty_results（带新字段）。
kn_router._do_hindsight_recall = lambda *a, **k: {"results": []}
kn_router._do_kt_recall = lambda *a, **k: []
kn_router._do_skill_match = lambda *a, **k: ""

# 5) 纯乱码查询：确保任何路由都不会命中
TEST_SESSION = "controlled-empty-test-%d" % int(time.time())
GIBBERISH = "zmwpqx ldvk 9302 txqw florbneq 771 vnpwk qxr 无意义的乱码探针查询 zzqwkj"

logging.getLogger("knowledge_navigation").info(
    "controlled test start",
    extra={"event": "controlled_test_start", "session_id": TEST_SESSION, "query": GIBBERISH},
)

try:
    out = kn_router.pre_llm_call(TEST_SESSION, GIBBERISH, platform="cli")
    print("pre_llm_call returned:", repr(out)[:200])
except Exception as e:  # noqa: BLE001
    print("pre_llm_call raised:", type(e).__name__, e)

print("trace.log path:", kn_config.CONFIG.trace_log_path or os.environ.get("KN_TRACE_LOG_PATH"))
print("DONE")
