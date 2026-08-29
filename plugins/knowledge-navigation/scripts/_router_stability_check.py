#!/usr/bin/env python3
"""_router_stability_check.py — Router 模型稳定性单次检测

被 kn-router-health-check.sh 循环调用 5 次。
输出 OK / FAIL 到 stdout，非零退出码视为 FAIL。

修复历史：
- 2026-07-19: max_tokens 512→2048，加 thinking:disabled，
  content 空时从 reasoning_content 兜底提取 JSON，
  防止 LiteLLM 降级到 sensenova 等推理模型时误报。
- 2026-08-16: 对 s-deepseek*/agnes 模型强制 thinking enabled + max_tokens=16384（业务硬约束），
  其余模型保持 thinking disabled / 2048。
"""
import json
import os
import re
import sys

try:
    import httpx
except ImportError:
    print("FAIL")
    sys.exit(1)

key = os.environ.get("KN_ROUTER_API_KEY", "")
model = os.environ.get("KN_ROUTER_MODEL", "agnes-2.5-flash")
if not key:
    print("FAIL")
    sys.exit(1)

try:
    # s-deepseek*/agnes 必须启用 thinking 且 max_tokens>8192（业务硬约束）；
    # 默认 sensenova 等保持原行为（thinking disabled / 2048）
    _rs_think = {"type": "enabled"} if model.startswith(("s-deepseek", "agnes")) else {"type": "disabled"}
    _rs_mt = 16384 if model.startswith(("s-deepseek", "agnes")) else 2048
    resp = httpx.post(
        "http://127.0.0.1:4142/v1/chat/completions",
        json={
            "model": model,
            "temperature": 0.1,
            "max_tokens": _rs_mt,
            "thinking": _rs_think,
            "messages": [
                {
                    "role": "system",
                    'content': '你是一个注入路由判断器。输出 JSON 格式如：{"h": false, "kt": false, "s": false, "sag": false}',
                },
                {"role": "user", "content": "消息：测试\n\nJSON 输出："},
            ],
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"]
    content = (msg.get("content") or "").strip()

    if not content:
        # 支持两种字段名：reasoning（DeepSeek）和 reasoning_content（OpenAI o1/o3）
        reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
        if reasoning:
            m = re.search(r'\{[^{}]*"h"[^{}]*\}', reasoning, re.DOTALL)
            if not m:
                tail = reasoning[max(0, len(reasoning) - 500) :]
                m = re.search(r"\{[^{}]*\}", tail)
                if m and any(k in m.group(0) for k in ("h", "kt", "sag", "s")):
                    content = m.group(0)
            else:
                content = m.group(0)

    if not content:
        print("FAIL")
        sys.exit(0)

    # 去掉 markdown 代码块包裹
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    content = content.strip()
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    result = json.loads(content)
    if isinstance(result, dict) and all(k in result for k in ("h", "kt", "s", "sag")):
        print("OK")
    else:
        print("FAIL")
except Exception:
    print("FAIL")
