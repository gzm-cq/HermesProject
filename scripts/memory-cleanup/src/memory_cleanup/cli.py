"""CLI 入口 — typer 实现，完整两阶段流水线编排。"""

import contextlib
import io
import json
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import typer

from memory_cleanup.adapters.llm_client import LLMClient
from memory_cleanup.adapters.memory_store import MemoryFileStore
from memory_cleanup.adapters.session_db import SessionDB
from memory_cleanup.config import AppConfig, load_config, setup_logging
from memory_cleanup.core.classifier import calc_remove_candidates, classify_all
from memory_cleanup.core.lifecycle import (
    compute_capacity_ratio,
    detect_cold_memories,
    detect_hot_memories,
)
from memory_cleanup.core.reporter import print_report, print_v2_detail
from memory_cleanup.core.verifier import phase2_verify

logger = logging.getLogger(__name__)

# ── 全局中断标志 ──
_running = True


def _signal_handler(signum: int, _frame: object) -> None:
    """处理 SIGINT/SIGTERM，设置中断标志。"""
    global _running
    if _running:
        _running = False
        print("\n\n⚠️ 收到中断信号，等待当前批次完成后退出...", flush=True)


app = typer.Typer(
    name="memory-cleanup",
    help="MEMORY.md + USER.md 两阶段分类清理（Phase 1 LLM分类 → Phase 2 验证 → 执行）",
    add_completion=False,
)


def _make_summary(
    source: str,
    entries: list[str],
    result: dict[str, Any],
    v2_result: dict[str, Any],
    char_limit: int = 50000,
) -> dict[str, Any]:
    """构建单个源的汇总字典。"""
    remove_indices: set[int] = {
        r.get("index", -1) for r in result.get("remove", []) if r.get("index", -1) >= 0
    }
    for m in result.get("merge", []):
        remove_indices.update(m.get("indices", []))
    for c in result.get("compress", []):
        idx = c.get("index", -1)
        if idx >= 0:
            remove_indices.add(idx)
    for h in result.get("hindsight", []):
        idx = h.get("index", -1)
        if idx >= 0:
            remove_indices.add(idx)
    keep_chars = sum(len(entries[i]) for i in range(len(entries)) if i not in remove_indices)
    flagged = result.get("flagged", [])
    total_flagged = sum(f.get("count", 0) for f in flagged)
    return {
        "total_entries": len(entries),
        "char_limit": char_limit,
        "phase1_merge": len(result.get("merge", [])),
        "phase1_compress": len(result.get("compress", [])),
        "phase1_hindsight": len(result.get("hindsight", [])),
        "phase1_remove": len(result.get("remove", [])),
        "phase1_flagged": total_flagged,
        "after_cleanup": {
            "keep": len(entries) - len(remove_indices),
            "keep_chars": keep_chars,
        },
        "phase2": {
            "correct": len(v2_result.get("correct", [])),
            "corrected": len(v2_result.get("corrected", [])),
            "keep": len(v2_result.get("keep", [])),
        },
    }


@app.command()
def run(
    apply: bool = typer.Option(False, "--apply", help="实际执行清理（默认 dry-run，不修改数据）"),
    config_path: str = typer.Option(
        "config/default.yaml", "--config", help="配置文件路径（YAML）"
    ),
    log_level: str | None = typer.Option(None, "--log-level", help="日志级别（DEBUG/INFO/WARNING）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出结果"),
    quiet: bool = typer.Option(False, "--quiet", help="简洁模式：只输出空间占用和执行概要（适合 cron 通知）"),
    # 投票策略：vote > 0 时每轮独立分类，remove 决策取并集（任一表决删除即删除），
    # 其他决策（merge/compress/hindsight）取交集（须全体一致）。
    # vote=0 时使用配置的轮数（cfg.vote_count），vote>=2 启用多轮投票。
    vote: int = typer.Option(0, "--vote", help="投票轮数（0=使用配置，>1=多轮投票：remove 并集，其他决策取交集）"),
) -> None:
    """执行 MEMORY.md + USER.md 分类清理。

    默认 dry-run（只分类报告，不修改数据），加 --apply 才真正执行清理。
    """
    # ── 信号处理 ──
    global _running
    _running = True
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    # ── 计时 ──
    start_time = time.monotonic()

    # 加载配置
    yaml_data = load_config(config_path)
    cfg = AppConfig.from_env(yaml_data)
    if log_level:
        cfg.log_level = log_level.upper()
    if json_output:
        cfg.output_mode = "json"
    if vote > 0:
        cfg.vote_count = vote
    setup_logging(cfg.log_level)

    # JSON / quiet 模式下重定向 stdout，抑制中间 print 输出
    if cfg.output_mode == "json" or quiet:
        stdout_cm: contextlib.AbstractContextManager[None] = contextlib.redirect_stdout(io.StringIO())
    else:
        stdout_cm = contextlib.nullcontext()

    # 构建 adapter 实例（依赖注入）
    llm_client = LLMClient(cfg)
    session_db = SessionDB(cfg)
    mem_store = MemoryFileStore(cfg)

    if not _running:
        return

    # ── 所有 print 输出在 json 模式下被抑制 ──
    with stdout_cm:
        print(f"\n{'=' * 70}")
        print(f"  分类清理 V6 — MEMORY/USER 独立 prompt，6 数组")
        if apply:
            print(f"  ⚠️  --apply 模式：将实际修改 MEMORY.md 和 USER.md")
        else:
            print(f"  🔒 dry-run 模式：只分类报告，不修改数据")
        print(f"{'=' * 70}")

        # 加载文件
        mem_entries = mem_store.load_file(cfg.memory_path)
        user_entries = mem_store.load_file(cfg.user_path)
        print(f"\n📄 MEMORY.md: {len(mem_entries)} 条 + USER.md: {len(user_entries)} 条")
        print(f"  流水线：MEMORY Phase1 → 即时 MEMORY Phase2，不等 USER Phase1")

        if not _running:
            return

        # ── Phase 0：生命周期检查（冷记忆淘汰 + 高频回升） ──
        mem_evict_list: list[dict[str, Any]] = []
        user_evict_list: list[dict[str, Any]] = []
        promote_list: list[dict[str, Any]] = []
        hindsight_entries: list[dict[str, Any]] = []

        # 容量守卫：MEMORY 占用超安全阈值时强制触发淘汰（2026-09-04）
        capacity_ratio = compute_capacity_ratio(mem_entries, cfg.memory_char_limit)
        capacity_exceeded = capacity_ratio > cfg.memory_capacity_safe_ratio
        if capacity_exceeded:
            print(f"  ⚠️ MEMORY 容量 {capacity_ratio:.1%} 超过安全阈值 {cfg.memory_capacity_safe_ratio:.0%}，强制触发冷记忆淘汰")

        if cfg.cold_memory_eviction or cfg.hot_memory_promotion or capacity_exceeded:
            print(f"\n{'=' * 70}")
            print(f"  🔄 Phase 0：生命周期检查")
            print(f"{'=' * 70}")

        if cfg.cold_memory_eviction or capacity_exceeded:
            total_l2 = len(mem_entries) + len(user_entries)
            print(f"  冷记忆淘汰：已启用（阈值 {cfg.cold_memory_days} 天，L2 上限 {cfg.l2_max_entries} 条）")

            mem_entry_dicts = [
                {"index": i, "content": e, "source": "MEMORY"}
                for i, e in enumerate(mem_entries)
            ]
            user_entry_dicts = [
                {"index": i, "content": e, "source": "USER"}
                for i, e in enumerate(user_entries)
            ]

            mem_cold = detect_cold_memories(mem_entry_dicts, cfg.cold_memory_days)
            user_cold = detect_cold_memories(user_entry_dicts, cfg.cold_memory_days)

            if total_l2 > cfg.l2_max_entries or capacity_exceeded:
                mem_evict_list = mem_cold
                user_evict_list = user_cold
            else:
                mem_evict_list = []
                user_evict_list = []
                print(f"    L2 当前 {total_l2} 条，未超过上限 {cfg.l2_max_entries}，跳过冷记忆淘汰")

            print(f"    MEMORY: 检测到 {len(mem_cold)} 条冷记忆，淘汰 {len(mem_evict_list)} 条")
            print(f"    USER:   检测到 {len(user_cold)} 条冷记忆，淘汰 {len(user_evict_list)} 条")

        if cfg.hot_memory_promotion:
            print(f"  高频回升：已启用（阈值 {cfg.hot_memory_access_count} 次）")
            hindsight_entries = mem_store.fetch_hindsight_entries()
            hot_entries = detect_hot_memories(hindsight_entries, cfg.hot_memory_access_count)
            promote_list = [{"content": e.get("content", "")} for e in hot_entries]
            print(f"    Hindsight: {len(hindsight_entries)} 条，高频 {len(hot_entries)} 条，回升 {len(promote_list)} 条")

        if not _running:
            return

        # ── Phase 1：MEMORY + USER 并行启动 ──
        phase1_start = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as p1_executor:
            mem_p1 = p1_executor.submit(
                classify_all, mem_entries, "MEMORY", llm_client, cfg.batch_size, cfg.max_workers, cfg.vote_count
            )
            user_p1 = p1_executor.submit(
                classify_all, user_entries, "USER", llm_client, cfg.user_batch_size, cfg.max_workers, cfg.vote_count
            )

            if not _running:
                return

            # ── MEMORY Phase 1 完成，立即输出 + 启动 Phase 2 ──
            mem_result = mem_p1.result()
            print(f"\n{'=' * 70}")
            print(f"  📊 MEMORY.md Phase 1 完成，即时启动 Phase 2...")
            print(f"{'=' * 70}")
            print_report("MEMORY.md", mem_entries, mem_result, cfg.memory_char_limit)

            mem_direct, mem_need_v2 = calc_remove_candidates(mem_entries, mem_result)
            print(
                f"\n  MEMORY.md: remove {len(mem_result.get('remove', []))} 条"
                f"（直接删 {len(mem_direct)} + 需验证 {len(mem_need_v2)}）"
            )

            if not _running:
                return

            # ── MEMORY Phase 2 投入后台 ──
            with ThreadPoolExecutor(max_workers=cfg.max_workers) as p2_executor:
                mem_p2 = p2_executor.submit(
                    phase2_verify, mem_entries, mem_need_v2, "MEMORY.md", llm_client, session_db, cfg.max_workers
                )

                if not _running:
                    return

                # ── USER Phase 1 完成 ──
                user_result = user_p1.result()

                if not _running:
                    return

                print(f"\n{'=' * 70}")
                print(f"  📊 USER.md Phase 1 完成")
                print(f"{'=' * 70}")
                print_report("USER.md", user_entries, user_result, cfg.user_char_limit)

                user_direct, user_need_v2 = calc_remove_candidates(user_entries, user_result)
                print(
                    f"  USER.md:     remove {len(user_result.get('remove', []))} 条"
                    f"（直接删 {len(user_direct)} + 需验证 {len(user_need_v2)}）"
                )

                if not _running:
                    return

                # ── USER Phase 2 与 MEMORY Phase 2 并行 ──
                user_p2 = None
                if user_need_v2:
                    user_p2 = p2_executor.submit(
                        phase2_verify, user_entries, user_need_v2, "USER.md", llm_client, session_db, cfg.max_workers
                    )

                # ── 等待两个 Phase 2 完成 ──
                mem_v2 = mem_p2.result()
                user_v2 = user_p2.result() if user_p2 else {"correct": [], "corrected": [], "keep": []}
        # p1_executor + p2_executor 自动 shutdown

        phase1_time = time.monotonic() - phase1_start

        if not _running:
            return

        # ── 总览 ──
        print(f"\n{'=' * 70}")
        print(f"  📈 总览")
        print(f"{'=' * 70}")

        def _calc(entries: list[str], result: dict) -> dict:
            remove_indices: set[int] = {
                r.get("index", -1) for r in result.get("remove", []) if r.get("index", -1) >= 0
            }
            for m in result.get("merge", []):
                remove_indices.update(m.get("indices", []))
            for c in result.get("compress", []):
                idx = c.get("index", -1)
                if idx >= 0:
                    remove_indices.add(idx)
            for h in result.get("hindsight", []):
                idx = h.get("index", -1)
                if idx >= 0:
                    remove_indices.add(idx)
            keep_chars = sum(len(entries[i]) for i in range(len(entries)) if i not in remove_indices)
            return {
                "keep": len(entries) - len(remove_indices),
                "keep_chars": keep_chars,
                "remove": len(remove_indices),
                "merge": len(result.get("merge", [])),
                "compress": len(result.get("compress", [])),
                "hindsight": len(result.get("hindsight", [])),
            }

        mr = _calc(mem_entries, mem_result)
        ur = _calc(user_entries, user_result) if user_entries else None

        print(
            f"  MEMORY.md: 保留 {mr['keep']:>3} 条 / {mr['keep_chars']:,} 字符"
            f"（{mr['keep_chars'] / cfg.memory_char_limit * 100:.0f}%）"
            f" | merge {mr['merge']:>2} compress {mr['compress']:>2} remove {mr['remove']:>3}"
        )
        if ur:
            print(
                f"  USER.md:    保留 {ur['keep']:>3} 条 / {ur['keep_chars']:,} 字符"
                f"（{ur['keep_chars'] / cfg.user_char_limit * 100:.0f}%）"
                f" | merge {ur['merge']:>2} compress {ur['compress']:>2} hindsight {ur['hindsight']:>2} remove {ur['remove']:>3}"
            )

        # ── Phase 2 统计摘要 ──
        def _p2_stats(v2: dict[str, list], label: str) -> None:
            all_items = v2.get("correct", []) + v2.get("corrected", []) + v2.get("keep", [])
            if not all_items:
                return
            print(
                f"  📊 {label}: 候选 {len(all_items)} 条"
                f" | LLM 验证: {len(all_items)}/{len(all_items)} 条"
                f" | verdict: correct={len(v2.get('correct', []))}"
                f"  corrected={len(v2.get('corrected', []))}"
                f"  keep={len(v2.get('keep', []))}"
            )

        _p2_stats(mem_v2, "MEMORY Phase 2")
        _p2_stats(user_v2, "USER Phase 2")

        # ── Phase 2 详细报告 ──
        print(f"\n{'=' * 70}")
        print(f"  📋 Phase 2 详细报告")
        print(f"{'=' * 70}")
        print(
            f"  MEMORY.md: 直接删 {len(mem_direct)} 条"
            f" | Phase2: correct={len(mem_v2['correct'])}"
            f"  corrected={len(mem_v2['corrected'])}"
            f"  keep={len(mem_v2['keep'])}"
        )
        print(
            f"  USER.md:    直接删 {len(user_direct)} 条"
            f" | Phase2: correct={len(user_v2['correct'])}"
            f"  corrected={len(user_v2['corrected'])}"
            f"  keep={len(user_v2['keep'])}"
        )

        print_v2_detail("MEMORY keep（不应移除）", mem_v2["keep"], mem_entries)
        print_v2_detail("MEMORY corrected（有偏差需修正）", mem_v2["corrected"], mem_entries)
        print_v2_detail("MEMORY correct（正确可删）", mem_v2["correct"], mem_entries)
        print_v2_detail("USER keep（不应移除）", user_v2["keep"], user_entries)
        print_v2_detail("USER corrected（有偏差需修正）", user_v2["corrected"], user_entries)
        print_v2_detail("USER correct（正确可删）", user_v2["correct"], user_entries)

        # ── 执行清理 ──
        exec_results: dict[str, Any] = {"MEMORY.md": None, "USER.md": None}
        if apply:
            print(f"\n{'=' * 70}")
            print(f"  🚀 执行清理（MemoryStore + Hindsight API）")
            print(f"{'=' * 70}")

            mem_r = mem_store.execute_cleanup(
                mem_entries, "MEMORY.md", "memory",
                mem_result.get("merge", []), mem_result.get("compress", []),
                mem_result.get("remove", []),
                mem_v2["correct"], mem_v2["corrected"], mem_v2["keep"],
                hindsight_list=mem_result.get("hindsight", []),
                evict_list=mem_evict_list,
                promote_list=promote_list,
            )
            hindsight_list = user_result.get("hindsight", [])
            user_r = mem_store.execute_cleanup(
                user_entries, "USER.md", "user",
                user_result.get("merge", []), user_result.get("compress", []),
                user_result.get("remove", []),
                user_v2["correct"], user_v2["corrected"], user_v2["keep"],
                hindsight_list,
                evict_list=user_evict_list,
            )
            exec_results["MEMORY.md"] = {
                "ok": len(mem_r.get("ok", [])),
                "fail": len(mem_r.get("fail", [])),
            }
            exec_results["USER.md"] = {
                "ok": len(user_r.get("ok", [])),
                "fail": len(user_r.get("fail", [])),
            }

            print(f"\n  MEMORY.md: ok={exec_results['MEMORY.md']['ok']} fail={exec_results['MEMORY.md']['fail']}")
            for f in mem_r.get("fail", []):
                print(f"    ❌ {f[0]} idx={f[1]}: {f[2]}")
            print(f"  USER.md:    ok={exec_results['USER.md']['ok']} fail={exec_results['USER.md']['fail']}")
            for f in user_r.get("fail", []):
                print(f"    ❌ {f[0]} idx={f[1]}: {f[2]}")
        else:
            print(f"\n{'=' * 70}")
            print(f"  🔒 dry-run 完成，未修改任何文件")
            print(f"  如需执行清理，加 --apply 参数：memory-cleanup --apply")
            print(f"{'=' * 70}")

        total_time = time.monotonic() - start_time
        print(f"\n{'=' * 70}")
        print(f"  ⏱ 耗时: {total_time:.1f}s (Phase1+Phase2: {phase1_time:.1f}s)")
        token_msg = ""
        if llm_client.total_prompt_tokens or llm_client.total_completion_tokens:
            token_msg = (
                f" | tokens: prompt={llm_client.total_prompt_tokens:,}"
                f" completion={llm_client.total_completion_tokens:,}"
            )
            print(f"  💰 Token 消耗:{token_msg}")
        print(f"{'=' * 70}\n")
    # ── stdout_cm 结束，json 模式下 print 恢复 ──

    # ── 构建报告数据 ──
    report_data: dict[str, Any] = {
        "version": "6",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "apply" if apply else "dry-run",
        "total_time_s": round(time.monotonic() - start_time, 1),
        "tokens": {
            "prompt": llm_client.total_prompt_tokens,
            "completion": llm_client.total_completion_tokens,
        },
        "sources": {},
    }
    report_data["sources"]["MEMORY.md"] = _make_summary("MEMORY.md", mem_entries, mem_result, mem_v2, cfg.memory_char_limit)
    report_data["sources"]["USER.md"] = _make_summary("USER.md", user_entries, user_result, user_v2, cfg.user_char_limit)
    if apply:
        report_data["execution"] = exec_results

    # ── 保存报告到文件 ──
    ts = time.strftime("%Y%m%d_%H%M%S")
    mem_dir = Path(cfg.memory_path).parent
    report_path = mem_dir / f"cleanup-report-{ts}.json"
    try:
        mem_dir.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        print(f"  📄 报告保存: {report_path}", flush=True)
    except Exception as e:
        logger.warning("保存报告失败: %s", e)

    # ── JSON 模式输出最终结果到 stdout ──
    if cfg.output_mode == "json":
        print(json.dumps(report_data, ensure_ascii=False, indent=2))

    # ── Quiet 模式输出简洁摘要（空间 + 执行概要） ──
    if quiet and cfg.output_mode != "json":
        mem_src = report_data["sources"].get("MEMORY.md", {})
        user_src = report_data["sources"].get("USER.md", {})
        mem_after = mem_src.get("after_cleanup", {})
        user_after = user_src.get("after_cleanup", {})
        mem_limit = mem_src.get("char_limit", 50000)
        user_limit = user_src.get("char_limit", 15000)
        mem_chars = mem_after.get("keep_chars", 0)
        user_chars = user_after.get("keep_chars", 0)
        mem_usage = round(mem_chars / mem_limit * 100, 1) if mem_limit else 0
        user_usage = round(user_chars / user_limit * 100, 1) if user_limit else 0
        total_compress = mem_src.get("phase1_compress", 0) + user_src.get("phase1_compress", 0)
        total_hindsight = mem_src.get("phase1_hindsight", 0) + user_src.get("phase1_hindsight", 0)
        total_remove = mem_src.get("phase1_remove", 0) + user_src.get("phase1_remove", 0)
        total_merge = mem_src.get("phase1_merge", 0) + user_src.get("phase1_merge", 0)
        mode_str = "apply" if apply else "dry-run"
        print(f"记忆清理完成 ({mode_str})")
        print(f"  MEMORY.md: {mem_chars:,}/{mem_limit:,} chars ({mem_usage}%)")
        print(f"  USER.md:   {user_chars:,}/{user_limit:,} chars ({user_usage}%)")
        print(f"  产出: compress={total_compress} hindsight={total_hindsight} remove={total_remove} merge={total_merge}")
        if report_data.get("total_time_s"):
            print(f"  耗时: {report_data['total_time_s']}s")


def main() -> None:
    """CLI 主入口。"""
    try:
        app()
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断", file=sys.stderr)
        raise typer.Exit(1)
    except Exception as e:
        quiet = sys.argv == ["--quiet"]
        if quiet:
            print(f"记忆清理失败: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
