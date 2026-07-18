"""CLI 共享辅助函数。

从 cli.py 提取的公共工具函数，供各命令模块复用。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from knowledge_tree_builder.adapters.database import parse_k_vector


class JSONFormatter(logging.Formatter):
    """统一 JSON 日志格式器"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """初始化日志系统"""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
    )


def _resolve_input_dir(input_dir: str) -> Path:
    """解析输入目录为绝对路径"""
    path = Path(input_dir)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _scan_articles(input_dir: Path) -> list[dict[str, Any]]:
    """扫描目录下的文章文件"""
    articles: list[dict[str, Any]] = []

    if not input_dir.exists():
        print(f"   ❌ 输入目录不存在: {input_dir}")
        return articles

    for f in sorted(input_dir.iterdir()):
        if f.suffix.lower() in (".md", ".txt", ".yaml", ".yml"):
            try:
                text = f.read_text(encoding="utf-8")
                title = f.stem
                articles.append({"path": str(f), "title": title, "text": text})
                print(f"   📄 {f.name} ({len(text)} 字符)")
            except Exception as e:
                print(f"   ⚠️  跳过 {f.name}: {e}")

    return articles


def _print_tree_node(node: dict[str, Any], prefix: str = "", index: int | None = None) -> None:
    """递归打印树节点"""
    label = f"#{index}" if index else ""
    if node["type"] == "leaf":
        points = node.get("points", [])
        name = node.get("name", "")
        if name:
            print(f"{prefix}🌿 {name} ({len(points)} 条知识点)")
        else:
            for pt in points[:3]:
                print(f"{prefix}🌿 {pt}")
            if len(points) > 3:
                print(f"{prefix}   ... 还有 {len(points) - 3} 条")
    else:
        children = node.get("children", [])
        name = node.get("name", "")
        if name:
            print(f"{prefix}📂 {name} ({len(children)} 个子节点)")
        else:
            print(f"{prefix}📂 子簇 ({len(children)} 个子节点)")
        for i, child in enumerate(children):
            _print_tree_node(child, prefix + "  ", i + 1)


def _count_nodes(tree: list[dict[str, Any]]) -> int:
    """统计树节点总数"""
    count = 0
    for node in tree:
        count += 1
        for child in node.get("children", []):
            count += _count_nodes([child])
    return count


def _count_structure(tree: list[dict[str, Any]], target: str) -> int:
    """统计特定结构类型的节点数"""
    count = 0
    for node in tree:
        if node.get("structure") == target:
            count += 1
        for child in node.get("children", []):
            count += _count_structure([child], target)
    return count


def _collect_all_nodes(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """递归收集所有节点"""
    nodes: list[dict[str, Any]] = []
    for node in tree:
        nodes.append(node)
        for child in node.get("children", []):
            nodes.extend(_collect_all_nodes([child]))
    return nodes


def _load_subjects_for_consolidation(adapter: Any) -> list[dict[str, Any]]:
    """从 PG 加载科目数据供 ConsolidationEngine.run() 使用。

    返回每个非叶子 subject 的：
    - id, name, point_count
    - points: 子节点文本列表
    - embeddings: 子节点 k_vector 矩阵 (np.ndarray)
    - children_embeddings, sibling_embeddings: 局部偏移计算用
    """
    cursor = adapter.cursor

    # 一次性查询所有 domain
    cursor.execute(
        "SELECT id, name FROM knowledge_tree "
        "WHERE parent_id IS NULL AND node_type = 'subject' "
        "ORDER BY id"
    )
    domain_rows = cursor.fetchall()

    # 一次性查询所有 domain 的子节点（带 k_vector）
    domain_ids = [r[0] for r in domain_rows]
    if not domain_ids:
        return []

    cursor.execute(
        "SELECT kt.parent_id, kt.id, kpt.text, kt.k_vector "
        "FROM knowledge_tree kt "
        "LEFT JOIN knowledge_point_texts kpt ON kpt.tree_node_id = kt.id "
        "WHERE kt.parent_id = ANY(%s) AND kt.k_vector IS NOT NULL "
        "ORDER BY kt.parent_id, kt.id",
        (domain_ids,),
    )
    children_rows = cursor.fetchall()

    # 按 parent_id 分组
    children_by_parent: dict[int, list[tuple[int, str, Any]]] = {}
    for parent_id, cid, text, k_vec in children_rows:
        children_by_parent.setdefault(parent_id, []).append((cid, text, k_vec))

    subjects = []
    for domain_id, domain_name in domain_rows:
        rows = children_by_parent.get(domain_id, [])
        if not rows:
            continue

        points = []
        embeddings = []
        dim = None
        for cid, text, k_vec_data in rows:
            text = text or ""
            k_list = parse_k_vector(k_vec_data)
            if k_list is not None:
                if dim is None:
                    dim = len(k_list)
                if len(k_list) != dim:
                    logging.getLogger(__name__).warning(
                        "k_vector 维度不一致: 期望 %d, 实际 %d, 跳过", dim, len(k_list)
                    )
                    continue
                points.append(text)
                embeddings.append(k_list)

        if len(points) < 3:
            continue

        if not embeddings:
            continue

        subjects.append({
            "id": domain_id,
            "name": domain_name,
            "point_count": len(points),
            "points": points,
            "embeddings": np.array(embeddings, dtype=np.float32),
            "placement_delta": 0,
            "k_vector_change": 0,
            "days_since_review": 30,
            "recall_count_decayed": 0,
        })

    return subjects


__all__ = [
    "JSONFormatter",
    "setup_logging",
    "_resolve_input_dir",
    "_scan_articles",
    "_print_tree_node",
    "_count_nodes",
    "_count_structure",
    "_collect_all_nodes",
    "_load_subjects_for_consolidation",
]