"""复杂命令模块。

包含知识提取管线的核心命令：consolidate（纠错回路）和 run（新管线）。
"""

from __future__ import annotations

from typing import Any

import typer

from knowledge_tree_builder.config import load_config
from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.consolidate.review import process_timeouts as _process_timeouts
from knowledge_tree_builder.consolidate.confidence import batch_update_from_logs
from knowledge_tree_builder.core.consolidation import ConsolidationEngine
from knowledge_tree_builder.commands._utils import _load_subjects_for_consolidation


def cmd_consolidate(
    action: str,
    config_path: str,
    dry_run: bool,
    merge_domains: bool,
    min_domain_nodes: int,
    domain_merge_threshold: float,
    build_edges: bool,
) -> None:
    """纠错回路：更新 confidence + 处理超时审查项 + 碎片 domain 合并。

    - run: 从使用日志更新所有知识的 confidence
    - process-timeouts: 处理 review_queue 中超时的审查项

    domain 合并(--merge-domains):
      子节点 < min-domain-nodes 的碎片 domain 合并到最近的大 domain。
      整合在 consolidate 中，无需独立命令。
    """
    config = load_config(config_path)
    db_url = config.get("db_url", "")
    if not db_url:
        print("   ❌ 未配置 db_url")
        raise typer.Exit(1)

    try:
        adapter = DatabaseAdapter(db_url)
    except Exception as e:
        print(f"   ❌ 数据库连接失败: {e}")
        raise typer.Exit(1)

    try:
        if action == "process-timeouts":
            print("\n⏰ 处理超时审查项...")
            processed = _process_timeouts(adapter)
            print(f"   已处理 {processed} 条超时审查项")
            if not dry_run:
                adapter.conn.commit()

        elif action == "run":
            print("\n🔄 执行纠错回路...")

            # 1. 从使用日志加载事件
            print("   ⏳ 步骤 1/7: 加载使用日志...")
            try:
                use_logs = adapter.get_recent_use_logs(days=30)
            except Exception:
                use_logs = []
                adapter.conn.rollback()
            print(f"   ✅ 加载 {len(use_logs)} 条使用日志")

            # 2. 获取当前所有知识的 confidence
            print("   ⏳ 步骤 2/7: 加载 confidence...")
            try:
                nodes = adapter.get_all_nodes_with_confidence()
            except Exception:
                nodes = []
                adapter.conn.rollback()
            current_conf = {str(n["id"]): n.get("retrieval_confidence", 1.0) for n in nodes}
            print(f"   ✅ 当前 {len(current_conf)} 条知识有 confidence 记录")

            # 3. 批量更新 confidence
            if use_logs and current_conf:
                print("   ⏳ 步骤 3/7: 更新 confidence...")
                results = batch_update_from_logs(use_logs, current_conf)
                print(f"   更新 {len(results)} 条知识 confidence")

                if not dry_run:
                    # 批量 UPDATE：一条 SQL 完成全部
                    if results:
                        case_parts = []
                        ids = []
                        for kid_str, (new_conf, action) in results.items():
                            kid = int(kid_str)
                            case_parts.append(f"WHEN {kid} THEN {new_conf}")
                            ids.append(str(kid))
                        sql = (
                            "UPDATE knowledge_tree SET retrieval_confidence = CASE id "
                            + " ".join(case_parts)
                            + f" END WHERE id IN ({','.join(ids)})"
                        )
                        adapter.cursor.execute(sql)
                        adapter.conn.commit()
                        print(f"   已写入 {len(results)} 条更新")
                else:
                    # dry-run: 只展示需要关注的知识
                    needs_attention = {k: v for k, v in results.items() if v[1] in ("review", "remove")}
                    if needs_attention:
                        print(f"\n   ⚠️  需关注的知识 ({len(needs_attention)} 条):")
                        for kid, (conf, action) in list(needs_attention.items())[:10]:
                            print(f"      ID={kid} confidence={conf:.3f} → {action}")

            # 4. 碎片 domain 合并（--merge-domains 启用）
            _ce_engine = ConsolidationEngine()

            if merge_domains:
                print("   ⏳ 步骤 4/7: 合并碎片 domain...")
                dm = _ce_engine.merge_small_domains(
                    adapter,
                    min_nodes=min_domain_nodes,
                    threshold=domain_merge_threshold,
                    dry_run=dry_run,
                )
                if dry_run:
                    print(f"   📊 预览: {dm['fragments']} 个碎片")
                else:
                    print(f"   ✅ 合并 {dm['merged']} 个, 删除 {dm['deleted']} 个")

            # 5. 子科目拆分（调用 ConsolidationEngine）
            print("   ⏳ 步骤 5/7: 子科目拆分...")

            # 从 PG 加载 subject 数据
            subjects = _load_subjects_for_consolidation(adapter)
            print(f"   加载 {len(subjects)} 个科目")
            if subjects:
                result = _ce_engine.run(
                    subjects,
                    dry_run=dry_run,
                    db_adapter=adapter,
                )
                splits = result.get("splits", [])
                if splits:
                    for s in splits:
                        if s.get("status") == "split_ready" and dry_run:
                            print(f"   📊 可拆分: {s['name']} → {len(s['clusters'])} 个子科")
                        elif s.get("status") == "split_applied":
                            print(f"   ✅ 已拆分: {s['name']} → {len(s['clusters'])} 个子科")
                if not dry_run:
                    adapter.conn.commit()

            # 6. 处理超时
            print("   ⏳ 步骤 6/7: 处理超时审查项...")
            processed = _process_timeouts(adapter)
            if processed:
                print(f"   超时处理: {processed} 条")
                if not dry_run:
                    adapter.conn.commit()

            # 7. 构建 KP 级关联边（--build-edges 启用）
            if build_edges:
                print("   ⏳ 步骤 7/7: 构建 KP 级关联边...")
                try:
                    edge_result = _ce_engine.build_kp_edges(
                        adapter,
                        max_source_kps=50,
                        vector_threshold=0.85,
                        same_subject_threshold=0.95,
                        dry_run=dry_run,
                    )
                    print(f"   同源共现: {edge_result['source_edges']} 边")
                    print(f"   向量桥接: {edge_result['vector_edges']} 边")
                    print(f"   同科高相似: {edge_result['same_subject_edges']} 边")
                    print(f"   合计: {edge_result['total']} 边")
                except Exception as e:
                    print(f"   ⚠️ 建边异常（跳过）: {e}")

        else:
            print(f"   ❌ 未知操作: {action}，可选 run / process-timeouts")

    except Exception as e:
        print(f"   ❌ 执行失败: {e}")
        if adapter.conn:
            adapter.conn.rollback()
        raise
    finally:
        adapter.close()


__all__ = ["cmd_consolidate"]