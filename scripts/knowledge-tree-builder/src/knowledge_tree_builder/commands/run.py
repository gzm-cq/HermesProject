"""知识提取管线主命令。

包含完整的知识提取管线实现：
  - _run_pipeline: 管线主控入口
  - _run_merged_phase: 合并模式（分析+拆解单次LLM调用）
  - _run_analyze_phase: Phase 1 分析文章
  - _run_split_phase: Phase 2 拆解与质量评估
  - _run_admit_phase: Phase 3 准入与去重
  - _run_place_phase: Phase 4 树定位
  - _cleanup_cache: 清理运行缓存
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import typer

from knowledge_tree_builder.config import AppConfig, load_config
from knowledge_tree_builder.adapters.database import DatabaseAdapter
from knowledge_tree_builder.phase.scan import scan_input_dir
from knowledge_tree_builder.phase.analyze import analyze_article
from knowledge_tree_builder.phase.split import process_candidates as split_candidates
from knowledge_tree_builder.phase.admit import admit_knowledge
from knowledge_tree_builder.place import place_knowledge, _write_to_db
from knowledge_tree_builder.manifest import Manifest, ManifestItem, STATUS_EXTRACTED
from knowledge_tree_builder.phase.merged import analyze_and_split as merged_analyze_and_split
from knowledge_tree_builder.core.embeddings import batch_embed, cosine_similarity
from knowledge_tree_builder.llm.client import call_llm_json


def _run_pipeline(
    input_dir: str,
    phase: str,
    config_path: str,
    dry_run: bool,
    merged: bool,
    concurrent: int,
    verbose: bool,
) -> None:
    """执行知识提取管线核心逻辑。"""
    os.environ.setdefault("LITELLM_MASTER_KEY", "")
    os.environ.setdefault("KT_DB_URL", "")

    config_dict = load_config(config_path)
    config = AppConfig.from_dict(config_dict)
    phases = ["all"] if phase == "all" else [phase]

    _db: Any = None
    if not dry_run:
        db_url = config_dict.get("db_url", "")
        if db_url:
            try:
                _db = DatabaseAdapter(db_url)
                print("   ✅ DB 已连接")
            except Exception as e:
                print(f"   ⚠️ DB 连接失败（降级为 dry-run）: {e}")
                dry_run = True

    scan_result: Any = None
    _all_reports: list[Any] = []
    _all_atomics: list[Any] = []
    _admit_result: Any = None

    if "all" in phases or "scan" in phases:
        print("\n📂 Pre-phase: 扫描输入目录...")
        scan_result = scan_input_dir(input_dir)
        print(f"   入列 {len(scan_result['admitted_files'])} 篇，跳过 {len(scan_result['skipped'])} 篇")
        if scan_result["empty_dir"] or not scan_result["admitted_files"]:
            print("   ❌ 无入列文件，管线终止")
            raise typer.Exit(0)

    if merged and ("all" in phases or "analyze" in phases or "split" in phases):
        _all_atomics, _all_reports, _manifest = _run_merged_phase(
            scan_result=scan_result,
            input_dir=input_dir,
            concurrent=concurrent,
            config=config,
            config_dict=config_dict,
        )
        _analyze_or_split_done = True
    else:
        _analyze_or_split_done = False

    if not _analyze_or_split_done and ("all" in phases or "analyze" in phases):
        _all_reports = _run_analyze_phase(
            scan_result=scan_result,
            config=config,
        )

    if "all" in phases or "split" in phases:
        if _analyze_or_split_done:
            print("   ⏭️  已由合并模式产出，跳过独立拆解")
            _all_reviews = []
        else:
            _all_atomics, _all_reviews = _run_split_phase(
                all_reports=_all_reports,
                config=config,
                verbose=verbose,
            )

    if "all" in phases or "admit" in phases:
        _admit_result = _run_admit_phase(
            all_atomics=_all_atomics,
            db=_db,
            config_dict=config_dict,
        )

    if "all" in phases or "place" in phases:
        _run_place_phase(
            admit_result=_admit_result,
            all_reports=_all_reports,
            db=_db,
            dry_run=dry_run,
            concurrent=concurrent,
            input_dir=input_dir,
            config_dict=config_dict,
        )

    if not dry_run and merged and "_manifest" in dir():
        try:
            for item in _manifest.items:
                if item.status == "extracted":
                    _manifest.mark_written(item)
            _manifest.summary()
        except Exception:
            pass

    if not dry_run:
        _cleanup_cache(input_dir)

    if _db is not None:
        _db.close()

    print("\n✅ 管线完成")


def _run_merged_phase(
    scan_result: dict[str, Any],
    input_dir: str,
    concurrent: int,
    config: AppConfig,
    config_dict: dict[str, Any],
) -> tuple[list[Any], list[Any], Any]:
    """合并模式：分析+拆解单次 LLM 调用。

    Returns:
        (all_atomics, all_reports, manifest)
    """
    print("\n⚡ 合并模式: 分析+拆解（单次 LLM 调用）...")

    _manifest = Manifest(f".kb_manifest_{Path(input_dir).name}.json")
    _has_ckpt = _manifest.load()

    if not _has_ckpt:
        _manifest.init(scan_result["admitted_files"])

    need = _manifest.need_extract()
    _all_reports: list[Any] = []
    _all_atomics: list[Any] = []

    if need:
        _extract_lock = threading.Lock()

        def _extract_one(item: ManifestItem) -> None:
            try:
                text = Path(item.path).read_text(encoding="utf-8")
                atomics, summary = merged_analyze_and_split(text, item.title, config=config)
                with _extract_lock:
                    _manifest.save_atomics(item, [dict(a) for a in atomics])
                    _all_reports.append({"article_title": item.title, "analysis": {"content_summary": summary}})
                print(f"   ✅ {item.title[:35]}: {len(atomics)} 条")
            except Exception as e:
                with _extract_lock:
                    _manifest.mark_failed(item, str(e))
                print(f"   ❌ {item.title[:35]}: {e}")

        print(f"   待提取: {len(need)} 篇（并发 {concurrent} 路）")
        if concurrent <= 1:
            for item in need:
                _extract_one(item)
        else:
            with ThreadPoolExecutor(max_workers=concurrent) as pool:
                futures = [pool.submit(_extract_one, item) for item in need]
                for f in as_completed(futures):
                    pass
        print()

    _all_atomics = _manifest.load_all_atomics()
    print(f"   加载 {len(_all_atomics)} 条原子知识")

    if not _all_atomics:
        print("   ❌ 无原子知识产出")
        raise typer.Exit(0)

    for item in _manifest.items:
        already = any(r.get("article_title") == item.title for r in _all_reports)
        if item.status == STATUS_EXTRACTED and not already:
            _all_reports.append({"article_title": item.title, "analysis": {"content_summary": ""}})

    return _all_atomics, _all_reports, _manifest


def _run_analyze_phase(
    scan_result: dict[str, Any],
    config: AppConfig,
) -> list[Any]:
    """Phase 1: 分析文章。"""
    print("\n📝 阶段1: 分析文章...")
    _all_reports: list[Any] = []

    for f in scan_result["admitted_files"]:
        title = f["title"]
        fpath = f["path"]
        try:
            text = Path(fpath).read_text(encoding="utf-8")
        except Exception as e:
            print(f"   ⚠️ 读取失败 {fpath}: {e}")
            continue
        report = analyze_article(text, title, config=config)
        candidates = report.get("candidates", [])
        print(f"   📄 {title}: {len(candidates)} 条候选")
        _all_reports.append(report)

    if not _all_reports:
        print("   ❌ 所有文章分析失败")
        raise typer.Exit(0)

    return _all_reports


def _run_split_phase(
    all_reports: list[Any],
    config: AppConfig,
    verbose: bool,
) -> tuple[list[Any], list[Any]]:
    """Phase 2: 拆解与质量评估。"""
    print("\n✂️  阶段2: 拆解与质量评估...")
    _all_atomics: list[Any] = []
    _all_reviews: list[Any] = []

    for report in all_reports:
        split_result = split_candidates(report, config=config)
        atomics = split_result.get("atomic_knowledge", [])
        reviews = split_result.get("review_queue_items", [])
        stats = split_result.get("stats", {})
        print(f"   📄 {report['article_title']}: 拆出 {len(atomics)} 条原子，"
              f"{stats.get('review', 0)} 条入审查队列")
        _all_atomics.extend(atomics)
        _all_reviews.extend(reviews)

    if not _all_atomics:
        print("   ❌ 无原子知识通过拆解")
        raise typer.Exit(0)

    if _all_reviews and verbose:
        print(f"   ⚠️ 审查队列新增 {len(_all_reviews)} 条")

    return _all_atomics, _all_reviews


def _run_admit_phase(
    all_atomics: list[Any],
    db: Any,
    config_dict: dict[str, Any],
) -> Any:
    """Phase 3: 准入与去重。"""
    print("\n🔍 阶段3: 准入与去重...")

    existing_vectors: list[dict[str, Any]] = []
    cold_start = True
    if db is not None:
        try:
            existing_vectors = db.get_leaf_nodes()
            if len(existing_vectors) >= config_dict.get("cold_start_text_dedup_count", 50):
                cold_start = False
        except Exception as e:
            print(f"   ⚠️ 加载已有知识失败（走冷启动）: {e}")

    _embed_fn = partial(
        batch_embed,
        base_url=config_dict.get("embed_base_url", "https://api.siliconflow.cn/v1"),
        model=config_dict.get("embed_model", "BAAI/bge-m3"),
        api_key=config_dict.get("embed_api_key", ""),
        batch_size=config_dict.get("embed_batch_size", 20),
    )

    _admit_result = admit_knowledge(
        all_atomics,
        existing_vectors=existing_vectors,
        embed_fn=_embed_fn,
        cosine_sim_fn=cosine_similarity,
        threshold_direct=config_dict.get("dedup_threshold_direct", 0.95),
        threshold_llm=config_dict.get("dedup_threshold_llm", 0.90),
        cold_start_text_dedup=cold_start,
    )

    print(f"   通过 {_admit_result.stats['passed']} 条，"
          f"去重合并 {_admit_result.stats['dedup_merged']} 条，"
          f"矛盾 {_admit_result.stats['conflicts']} 条")

    return _admit_result


def _run_place_phase(
    admit_result: Any,
    all_reports: list[Any],
    db: Any,
    dry_run: bool,
    concurrent: int,
    input_dir: str,
    config_dict: dict[str, Any],
) -> None:
    """Phase 4: 树定位。"""
    print("\n🌲 阶段4: 树定位...")

    if admit_result is None:
        print("   ⚠️ 缺少 Phase 3 结果，需先运行全管线或 --phase admit")
        print("   ⏭️  跳过 Phase 4")
        raise typer.Exit(0)

    if not admit_result.passed:
        print("   ⚠️ Phase 3 无通过知识点")
        print("   ⏭️  跳过 Phase 4")
        raise typer.Exit(0)

    _p4_cache_path = f".kb_phase4_{Path(input_dir).name}.json"
    _p4_domains: dict[str, str] = {}
    _p4_placed: set[str] = set()
    _p4_records: list[dict[str, Any]] = []
    _p4_dirty = False

    if os.path.exists(_p4_cache_path):
        try:
            with open(_p4_cache_path) as _f:
                _ckpt = json.load(_f)
            _p4_domains = _ckpt.get("domains", {})
            _p4_placed = set(_ckpt.get("placed_titles", []))
            _p4_records = _ckpt.get("records", [])
            print(f"   📦 Phase 4 缓存: {len(_p4_domains)} 个领域, {len(_p4_placed)} 篇已定位")
        except Exception as _e:
            print(f"   ⚠️ 缓存读取失败: {_e}")

    def _phase4_save_cache() -> None:
        try:
            with open(_p4_cache_path, "w") as _f:
                json.dump({
                    "domains": _p4_domains,
                    "placed_titles": list(_p4_placed),
                    "records": _p4_records,
                }, _f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _llm_domain(title_summary: str, existing_domains: list[str]) -> str:
        prompt = f"判断以下文章最合适的知识领域。已有领域：{existing_domains or '无'}。文章：{title_summary[:500]}"
        sys_prompt = (
            "从已有领域中选择一个，或提出新的领域名。"
            "如果不确定，不从已有领域选择，而是从文章标题中提取最有区分度的2-3个关键词作为领域名。"
            "禁止返回 'general'、'无'、空字符串。"
            "只返回JSON：{\"domain\": \"领域路径\"}"
        )
        result = call_llm_json(
            prompt, system_prompt=sys_prompt,
            temperature=0,
            api_url=config_dict.get("llm_api_url", ""),
            api_key=config_dict.get("llm_api_key", ""),
            model=config_dict.get("llm_model", "s-deepseek-v4-flash"),
        )
        if "error" in result:
            err_msg = result.get("error", "")
            if "parse_failed:" in err_msg:
                raw = err_msg.split("parse_failed:", 1)[1].strip().strip("'\"")
                if raw and raw not in ("general", "无"):
                    return raw
            return _extract_domain_from_title(title_summary)
        domain = str(result.get("domain", ""))
        if not domain or domain in ("general", "无"):
            return _extract_domain_from_title(title_summary)
        return domain

    def _extract_domain_from_title(title_summary: str) -> str:
        import re as _re
        title = title_summary.split("。")[0].split(".")[0][:80]
        words = _re.findall(r"[a-zA-Z\-]{2,}|[\u4e00-\u9fff]{2,}", title)
        if len(words) >= 2:
            return "/".join(words[:2])
        elif words:
            return words[0]
        return "unsorted"

    if concurrent <= 1:
        for report in all_reports:
            title = report["article_title"]
            if title in _p4_placed:
                continue

            summary = report.get("analysis", {}).get("content_summary", "")
            admitted = [a for a in (admit_result.passed if admit_result else [])
                       if a.get("source_title") == title]
            if not admitted:
                _p4_placed.add(title)
                _p4_dirty = True
                continue

            if title not in _p4_domains:
                _p4_domains[title] = _llm_domain(
                    (title[:80] + " " + (summary or "")[:200]).strip(),
                    list(_p4_domains.values()),
                )
                _p4_dirty = True

            domain = _p4_domains[title]
            try:
                _embed_fn = partial(
                    batch_embed,
                    base_url=config_dict.get("embed_base_url", "https://api.siliconflow.cn/v1"),
                    model=config_dict.get("embed_model", "BAAI/bge-m3"),
                    api_key=config_dict.get("embed_api_key", ""),
                    batch_size=config_dict.get("embed_batch_size", 20),
                )
                _pr = place_knowledge(admitted, title, summary,
                    db_adapter=db, embed_fn=_embed_fn,
                    cosine_sim_fn=cosine_similarity,
                    llm_domain_fn=lambda _ts, _ed: domain, write_db=False)
                for r in _pr.records:
                    r["_article_title"] = title
                _p4_records.extend(_pr.records)
                _p4_placed.add(title)
                _p4_dirty = True
                print(f"   📄 {title}: {_pr.stats['placed']} 条")
            except Exception as e:
                print(f"   ⚠️ 定位失败 {title}: {e}")

            if _p4_dirty:
                _phase4_save_cache()
                _p4_dirty = False
    else:
        print(f"   领域判断（并发 {concurrent} 路）...")
        _p4_lock = threading.Lock()

        def _process_one(report: dict[str, Any]) -> bool:
            title = report["article_title"]
            summary = report.get("analysis", {}).get("content_summary", "")
            admitted = [a for a in (admit_result.passed if admit_result else [])
                       if a.get("source_title") == title]

            with _p4_lock:
                if title in _p4_placed:
                    return False
                if not admitted:
                    _p4_placed.add(title)
                    return False
                domain = _p4_domains.get(title)
                existing_domains_snapshot = list(_p4_domains.values())

            if domain is None:
                computed_domain = _llm_domain(
                    (title[:80] + " " + (summary or "")[:200]).strip(),
                    existing_domains_snapshot,
                )
                with _p4_lock:
                    domain = _p4_domains.setdefault(title, computed_domain)

            try:
                _embed_fn = partial(
                    batch_embed,
                    base_url=config_dict.get("embed_base_url", "https://api.siliconflow.cn/v1"),
                    model=config_dict.get("embed_model", "BAAI/bge-m3"),
                    api_key=config_dict.get("embed_api_key", ""),
                    batch_size=config_dict.get("embed_batch_size", 20),
                )
                _pr = place_knowledge(admitted, title, summary,
                    db_adapter=db, embed_fn=_embed_fn,
                    cosine_sim_fn=cosine_similarity,
                    llm_domain_fn=lambda _ts, _ed: domain, write_db=False)
                with _p4_lock:
                    for r in _pr.records:
                        r["_article_title"] = title
                    _p4_records.extend(_pr.records)
                    _p4_placed.add(title)
                print(f"   📄 {title}: {_pr.stats['placed']} 条")
                return True
            except Exception as e:
                print(f"   ⚠️ 定位失败 {title}: {e}")
                return False

        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            pool.map(_process_one, all_reports)
        _phase4_save_cache()

    if db and _p4_records and not dry_run:
        print(f"\n   💾 批量写入 PG: {len(_p4_records)} 条记录...")
        _domain_groups: dict[str, list[dict[str, Any]]] = {}
        for r in _p4_records:
            d = r.get("domain", "general")
            _domain_groups.setdefault(d, []).append(r)
        for d, records in _domain_groups.items():
            try:
                _write_to_db(records, d, db)
                print(f"     📂 {d}: {len(records)} 条")
            except Exception as e:
                print(f"     ❌ {d} 写入失败: {e}")

        try:
            os.remove(_p4_cache_path)
        except Exception:
            pass

    _new_subjects = len({r.get("subject", "") for r in _p4_records})
    print(f"   📊 定位 {len(_p4_records)} 条, {_new_subjects} 个科目")


def _cleanup_cache(input_dir: str) -> None:
    """清理运行缓存。"""
    _input_dir_name = Path(input_dir).name if input_dir else ""
    for _cache_pattern in [
        f".kb_manifest_{_input_dir_name}.json",
        ".kb_embed_cache.json",
    ]:
        _cp = Path(_cache_pattern)
        if _cp.exists():
            try:
                _cp.unlink()
            except Exception:
                pass

    _atomics_dir = Path(f".kb_manifest_{_input_dir_name}_atomics")
    if _atomics_dir.is_dir():
        try:
            shutil.rmtree(_atomics_dir)
        except Exception:
            pass


__all__ = [
    "_run_pipeline",
    "_run_merged_phase",
    "_run_analyze_phase",
    "_run_split_phase",
    "_run_admit_phase",
    "_run_place_phase",
    "_cleanup_cache",
]