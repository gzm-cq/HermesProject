# -*- coding: utf-8 -*-
"""
智能化转型实施方案 — 报告生成入口
"""
import glob
import json
import subprocess
import sys
from pathlib import Path

from ai_report.core.orchestrator import ReportWorkflowOrchestrator

# ── 1. 读取源文件 ────────────────────────────────────────────
source_files = glob.glob('/mnt/c/Users/1/Desktop/第X章 智能化转型*构建.txt')
if not source_files:
    print("ERROR: 找不到源文件")
    sys.exit(1)
source_path = Path(source_files[0])
content = source_path.read_text(encoding="utf-8")
print(f"READ: {len(content)} chars  <- {source_path.name}")

# ── 2. 存入 Dify KB（RAG 素材） ────────────────────────────
print("\nSTORE: 存入 Dify KB...")
DIFY_COMPOSE = "/app/dify/docker/docker-compose.yml"
DATASET_ID = "35471710-b45b-4e58-8d71-f5b56972acd3"
API_KEY = "dataset-cb7b6173-a4e7-435c-a00b-08384d45f582"

body = json.dumps({
    "name": "智能化转型源稿",
    "text": content,
    "doc_type": "other",
    "doc_metadata": {"category": "tech_report", "source": "user_doc"},
    "indexing_technique": "high_quality",
    "process_rule": {"mode": "automatic"},
})
curl_cmd = [
    "docker", "compose", "-f", DIFY_COMPOSE,
    "exec", "-T", "api", "curl", "-s",
    "-X", "POST",
    f"http://api:5001/v1/datasets/{DATASET_ID}/document/create-by-text",
    "-H", f"Authorization: Bearer {API_KEY}",
    "-H", "Content-Type: application/json",
    "-d", body,
]
try:
    result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout) if result.stdout.strip() else {}
    doc_id = data.get("document", {}).get("id", data.get("id", "unknown"))
    print(f"  OK: document_id={doc_id}")
except Exception as e:
    print(f"  WARN: 存储跳过: {e}")

# ── 3. 生成报告 ──────────────────────────────────────────
print("\nGEN: 启动报告生成...")
orchestrator = ReportWorkflowOrchestrator()
result = orchestrator.run(
    topic="智能化转型实施方案",
    report_type="tech",
    language="zh",
    output_dir=Path("./test_outputs"),
    skip_quality=False,
    skip_evaluation=False,
)

# ── 4. 输出结果 ──────────────────────────────────────────
state = result["state"]
if result["success"]:
    print(f"\nOK: 报告生成成功!")
    print(f"   type={state.report_type} lang={state.language}")
    if state.plan:
        print(f"   sections={len(state.plan.sections)}")
    if result.get("output_path"):
        print(f"   saved: {result['output_path']}")
    print(f"   time: {result.get('elapsed_display', '?')}")
else:
    print(f"\nFAIL: 报告生成失败")
    for err in state.errors:
        print(f"   - {err}")
