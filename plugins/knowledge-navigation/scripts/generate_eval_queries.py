#!/usr/bin/env python3
"""
generate_eval_queries.py — 从 Hindsight 记忆库自动生成评测查询

工作原理：
    1. 从 PG 读取 memory_units，按 entity 分组
    2. 每组取代表性记忆文本 → 调 LLM 生成自然用户发问
    3. 输出 enhanced eval_queries.json（含 expected_ids）

用法：
    python3 scripts/generate_eval_queries.py [--count 100] [--db-url ...] [--llm-url ...] [--model ...]

环境变量：
    CLUSTERING_DB_URL  — PostgreSQL 连接串（默认已设）
    LLM_API_URL        — OpenAI 兼容 API 端点
    LLM_API_KEY        — API 密钥
    LLM_MODEL          — 模型名（默认 s-deepseek-v4-flash）
"""
import argparse
import json
import os
import random
import ssl
import sys
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="从 Hindsight 记忆库自动生成评测查询")
    p.add_argument("--count", type=int, default=100, help="生成的查询数量（默认 100）")
    p.add_argument("--db-url", default="", help="PostgreSQL 连接串")
    p.add_argument("--llm-url", default="", help="LLM API 端点")
    p.add_argument("--api-key", default="", help="LLM API 密钥")
    p.add_argument("--model", default="s-deepseek-v4-flash", help="LLM 模型名")
    p.add_argument("--output", default="", help="输出路径（默认 stdout）")
    return p.parse_args()


def get_llm_config(args: argparse.Namespace) -> dict:
    """获取 LLM 配置，优先用命令行参数，fallback 到环境变量。"""
    return {
        "url": args.llm_url or os.getenv("LLM_API_URL", "http://127.0.0.1:4142/v1/chat/completions"),
        "key": args.api_key or os.getenv("LLM_API_KEY", ""),
        "model": args.model or os.getenv("LLM_MODEL", "s-deepseek-v4-flash"),
    }


def call_llm(prompt: str, config: dict) -> str:
    """调用 OpenAI 兼容 API。"""
    headers = {
        "Content-Type": "application/json",
    }
    if config["key"]:
        headers["Authorization"] = f"Bearer {config['key']}"

    body = json.dumps({
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 150,
    }).encode("utf-8")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(config["url"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[WARN] LLM 调用失败: {e}", file=sys.stderr)
        return ""


def fetch_memories(db_url: str, max_count: int = 5000) -> list[dict]:
    """从 PG 读取记忆单元及其实体分组。"""
    import psycopg2

    conn = psycopg2.connect(db_url)
    memories: list[dict] = []

    with conn.cursor() as cur:
        # 读最近的有文本的记忆
        cur.execute("""
            SELECT mu.id::text, mu.text, mu.fact_type, mu.created_at
            FROM memory_units mu
            WHERE mu.text IS NOT NULL AND length(mu.text) > 20
            ORDER BY mu.created_at DESC
            LIMIT %s
        """, (max_count,))
        for row in cur.fetchall():
            memories.append({
                "id": str(row[0]),
                "text": str(row[1]),
                "fact_type": str(row[2]) if row[2] else "unknown",
                "created_at": str(row[3]) if row[3] else "",
            })

    # 读取单元-实体映射
    unit_to_entities: dict[str, list[str]] = defaultdict(list)
    all_unit_ids = [m["id"] for m in memories]
    if all_unit_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT unit_id::text, entity_id::text FROM unit_entities WHERE unit_id::text = ANY(%s)",
                (all_unit_ids,),
            )
            for uid, eid in cur.fetchall():
                unit_to_entities[uid].append(eid)

    conn.close()

    # 按 entity 分组
    entity_groups: dict[str, list[dict]] = defaultdict(list)
    no_entity: list[dict] = []
    for m in memories:
        eids = unit_to_entities.get(m["id"], [])
        if eids:
            for eid in eids:
                entity_groups[eid].append(m)
        else:
            no_entity.append(m)

    # 从有实体的组中采样
    sampled: list[dict] = []
    groups = list(entity_groups.values())
    random.shuffle(groups)

    per_group = max(1, max_count // (len(groups) + 1))
    for g in groups:
        sampled.extend(random.sample(g, min(per_group, len(g))))
        if len(sampled) >= max_count:
            break

    # 补一些无实体的
    if len(sampled) < max_count:
        remaining = max_count - len(sampled)
        sampled.extend(random.sample(no_entity, min(remaining, len(no_entity))))

    return sampled[:max_count]


def generate_query(llm_config: dict, memory_text: str, topic: str = "") -> str:
    """用 LLM 为一段记忆生成自然用户发问。

    返回 (query, keywords_list)
    """
    topic_hint = f"（主题：{topic}）" if topic else ""
    prompt = f"""你是一个 Hermes AI 助手的用户。你之前和 AI 对话时讨论过以下内容：

{topic_hint}
记忆内容：{memory_text[:500]}

请生成一个用户会自然说出的中文问句（10-30字），
这个问句应该能通过语义检索找到上面的记忆内容。
只输出问句本身，不要多余内容。"""

    result = call_llm(prompt, llm_config)
    if not result or len(result) < 5:
        return ""
    # 清理引号
    result = result.strip('"').strip("'")
    return result


def main() -> None:
    args = parse_args()
    llm_config = get_llm_config(args)
    db_url = args.db_url or os.getenv("CLUSTERING_DB_URL", "")
    if not db_url:
        print("❌ 需要 --db-url 或环境变量 CLUSTERING_DB_URL", file=sys.stderr)
        sys.exit(1)

    print(f"📦 连接 PG 读取记忆...", file=sys.stderr)
    memories = fetch_memories(db_url, max_count=3000)
    print(f"   读取 {len(memories)} 条", file=sys.stderr)

    # 按事实类型分组
    by_type: dict[str, list[dict]] = defaultdict(list)
    for m in memories:
        by_type[m["fact_type"]].append(m)

    print(f"   fact_type 分布: {dict((k, len(v)) for k, v in by_type.items())}", file=sys.stderr)

    queries: list[dict] = []
    seen_texts: set[str] = set()
    total_needed = args.count

    # 轮流从各类型取
    type_list = list(by_type.keys())
    random.shuffle(type_list)
    round_robin_idx = 0

    while len(queries) < total_needed:
        ft = type_list[round_robin_idx % len(type_list)]
        round_robin_idx += 1
        pool = by_type[ft]
        if not pool:
            continue
        m = random.choice(pool)
        if m["id"] in seen_texts:
            continue
        seen_texts.add(m["id"])

        print(f"  [{len(queries)+1}/{total_needed}] LLM 生成查询...", file=sys.stderr)
        query = generate_query(llm_config, m["text"])
        if not query:
            continue

        qid = f"gen_{len(queries) + 1:03d}"
        queries.append({
            "query_id": qid,
            "query": query,
            "expected_ids": [m["id"]],
            "fact_type": m["fact_type"],
            "dimension": "semantic",
        })

    output = args.output or ""
    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(queries, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 已生成 {len(queries)} 条评测查询 → {output}", file=sys.stderr)
    else:
        print(json.dumps(queries, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
