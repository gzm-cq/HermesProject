#!/usr/bin/env python3
"""Graphiti 时间维度桥接（P1-2，轻量自实现，对齐 Graphiti 语义）。

依据 docs/融合计划/20260822-数据飞轮增强执行方案.md §3.2：
为配置类知识提供时间维度——同一配置变更后，旧值标注过期，召回时只返回
当前有效版本。

设计权衡：
- 完整 Graphiti（graphiti-core + Neo4j）是重型依赖，且 Neo4j 端口与 Cognee 冲突；
- 本 bridge 用本地 JSON 持久化实现相同的「时间版本 + as_of 查询」语义，
  零外部依赖，默认不启用，不影响生产；
- 若后续需完整图时序能力，可将本接口替换为 graphiti-core 的
  Graphiti.add_episode() / search()（接口已对齐：add_config_knowledge ↔ add_episode，
  search_as_of ↔ search(as_of=...)）。

持久化格式（state.json）：
{
  "entities": [
    {"key": "<配置标识>", "text": "<知识文本>", "valid_from": <ts>, "valid_to": <ts|null>, "source": "<来源>"}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

DEFAULT_STATE = os.getenv(
    "KN_GRAPHITI_STATE",
    os.path.join(os.path.expanduser("~"), ".hermes", "knowledge-navigation", "graphiti_state.json"),
)


def _load() -> dict[str, Any]:
    if not os.path.exists(DEFAULT_STATE):
        return {"entities": []}
    try:
        with open(DEFAULT_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entities": []}


def _save(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(DEFAULT_STATE), exist_ok=True)
    tmp = DEFAULT_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, DEFAULT_STATE)


def add_config_knowledge(key: str, text: str, valid_from: float | None = None, source: str = "") -> dict[str, Any]:
    """记录一条配置类知识版本（逻辑等价于 Graphiti.add_episode）。

    同一 key 的新版本会使旧版本的 valid_to 闭合（标注过期）。
    """
    state = _load()
    now = valid_from or time.time()
    # 闭合同 key 的未过期版本
    for e in state["entities"]:
        if e["key"] == key and e.get("valid_to") is None:
            e["valid_to"] = now
    ent = {
        "key": key,
        "text": text,
        "valid_from": now,
        "valid_to": None,
        "source": source,
    }
    state["entities"].append(ent)
    _save(state)
    return ent


def search_as_of(query_key: str, as_of: float | None = None) -> dict[str, Any] | None:
    """查询某配置在指定时间点的有效版本（逻辑等价于 Graphiti.search(as_of=...)）。

    返回当前有效版本；若该 key 所有版本均已过期且无有效版本，返回 None 并标记 expired。
    """
    state = _load()
    now = as_of or time.time()
    best: dict[str, Any] | None = None
    for e in state["entities"]:
        if e["key"] != query_key:
            continue
        vf = e.get("valid_from", 0)
        vt = e.get("valid_to")
        if vf <= now and (vt is None or vt > now):
            # 当前有效
            if best is None or vf > best.get("valid_from", 0):
                best = e
    return best


def is_expired(key: str, as_of: float | None = None) -> bool:
    """判断某配置 key 当前是否无有效版本（已过期）。"""
    return search_as_of(key, as_of) is None


def list_entities() -> list[dict[str, Any]]:
    state = _load()
    return state.get("entities", [])


def main() -> int:
    ap = argparse.ArgumentParser(description="Graphiti 时间维度桥接")
    sub = ap.add_subparsers(dest="cmd")
    a_add = sub.add_parser("add", help="记录配置版本")
    a_add.add_argument("--key", required=True)
    a_add.add_argument("--text", required=True)
    a_add.add_argument("--source", default="")
    a_search = sub.add_parser("search", help="查询有效版本")
    a_search.add_argument("--key", required=True)
    a_list = sub.add_parser("list", help="列出所有版本")
    args = ap.parse_args()

    if args.cmd == "add":
        ent = add_config_knowledge(args.key, args.text, source=args.source)
        print(f"[graphiti] 已记录 {args.key} valid_from={ent['valid_from']:.0f}")
    elif args.cmd == "search":
        e = search_as_of(args.key)
        if e:
            print(f"[graphiti] {args.key} 有效: {e['text'][:80]}")
        else:
            print(f"[graphiti] {args.key} 已过期/无有效版本")
    elif args.cmd == "list":
        for e in list_entities():
            vt = e.get("valid_to")
            status = "valid" if vt is None else "expired"
            print(f"  {e['key']} [{status}] {e['text'][:60]}")
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
