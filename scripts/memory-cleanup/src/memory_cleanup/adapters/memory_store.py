"""记忆文件读写适配器 — 读取 MEMORY.md/USER.md 并执行清理操作。"""

import json
import importlib.util
import logging
import re
import shutil
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any

from memory_cleanup.config import AppConfig, CONFIG, validate_path

logger = logging.getLogger(__name__)

# _remove_key 使用的候选唯一前缀长度列表（P2-MC-029）
_UNIQUE_PREFIX_LENGTHS = (80, 120, 160, 240, 320)


class MemoryFileStore:
    """MEMORY.md / USER.md 读写适配器。

    提供：
    - load_file(): 读取并去重条目
    - execute_cleanup(): 执行 merge/compress/remove + Hindsight retain
    """

    def __init__(self, config: AppConfig = CONFIG) -> None:
        self._config = config

    def load_file(self, path: str) -> list[str]:
        """加载记忆文件，按 entry_delimiter 分割并去重。"""
        p = Path(path)
        if not p.exists():
            return []
        raw = p.read_text(encoding="utf-8")
        entries = [e.strip() for e in raw.split(self._config.entry_delimiter)]
        entries = [e for e in entries if e]  # 过滤空字符串（含仅分隔符文件）
        seen: set[str] = set()
        deduped: list[str] = []
        for e in entries:
            norm = re.sub(r"\s+", "", e).lower()
            if norm not in seen:
                seen.add(norm)
                deduped.append(e)
        return deduped

    def _retain(self, content: str, tags: list[str] | None = None) -> bool:
        """retain 到 Hindsight（2 次重试），返回成功/失败。

        Args:
            content: 记忆内容
            tags: 可选的关键词标签列表
        """
        item: dict[str, Any] = {"content": content}
        if tags:
            item["tags"] = tags
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self._config.hindsight_url,
                    data=json.dumps({"items": [item]}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    if r.status < 200 or r.status >= 300:
                        raise OSError(f"Hindsight API returned HTTP {r.status}")
                    return True
            except Exception as e:
                if attempt < 1:
                    continue
                logger.warning("retain failed after 2 attempts: %s", e)
                return False

    def fetch_hindsight_entries(self) -> list[dict[str, Any]]:
        """从 Hindsight 获取条目列表。

        暂时返回空列表（mock 实现），等 Hindsight API 支持查询后再接上真实数据。

        Returns:
            Hindsight 条目列表，每个条目含 content、tags 等字段
        """
        logger.info("fetch_hindsight_entries: 暂用 mock 实现，返回空列表")
        return []

    def execute_cleanup(
        self,
        entries: list[str],
        source: str,
        target: str,
        merge_list: list[dict[str, Any]],
        compress_list: list[dict[str, Any]],
        remove_list: list[dict[str, Any]],
        v2_correct: list[dict[str, Any]],
        v2_corrected: list[dict[str, Any]],
        v2_keep: list[dict[str, Any]],
        hindsight_list: list[dict[str, Any]] | None = None,
        evict_list: list[dict[str, Any]] | None = None,
        promote_list: list[dict[str, Any]] | None = None,
    ) -> dict[str, list]:
        """执行清理：merge/compress/remove + Phase 2 三类判决 + hindsight + evict/promote。

        延迟导入 hermes-agent MemoryStore，仅在 --apply 模式下触发。
        """
        # 延迟导入 hermes-agent MemoryStore — 使用 importlib 直接加载，避免路径注入
        agent_path = Path(self._config.hermes_agent_path)
        memory_tool_path = agent_path / "tools" / "memory_tool.py"
        if not memory_tool_path.exists():
            raise FileNotFoundError(f"MemoryStore module not found: {memory_tool_path}")
        spec = importlib.util.spec_from_file_location("tools.memory_tool", memory_tool_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load MemoryStore module from {memory_tool_path}")
        module = importlib.util.module_from_spec(spec)
        # add hermes-agent root to sys.path so memory_tool.py can import
        # hermes_constants and utils (cron PYTHONPATH may not include it)
        agent_root = str(agent_path.resolve())
        if agent_root not in sys.path:
            sys.path.insert(0, agent_root)
        spec.loader.exec_module(module)
        MemoryStore = module.MemoryStore  # type: ignore[assignment]

        # 校验路径在允许目录树内（P1-1）
        validate_path(self._config.memory_path)
        validate_path(self._config.hermes_agent_path)

        # 备份
        mem_dir = Path(self._config.memory_path).parent
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_path = mem_dir / f"{target.upper()}.md.bak.{ts}"
        src_path = mem_dir / f"{target.upper()}.md"
        if src_path.exists():
            shutil.copy2(str(src_path), str(bak_path))
            print(f"  📦 备份: {bak_path.name}", flush=True)

        limit = self._config.memory_char_limit if target == "memory" else self._config.user_char_limit
        store = MemoryStore(memory_char_limit=limit, user_char_limit=limit)
        store.load_from_disk()

        results: dict[str, list] = {"ok": [], "fail": []}
        removed_already: set[int] = set()

        def _remove_key(idx: int) -> str:
            """Return a substring that uniquely identifies entries[idx] in this run.

            Hermes MemoryStore.remove() deliberately refuses ambiguous substring
            matches. USER.md can contain near-duplicates with identical first
            80 chars, so a fixed prefix causes "Multiple entries matched". Use
            the shortest unique prefix and fall back to the full entry.
            """
            entry = entries[idx]
            for length in (*_UNIQUE_PREFIX_LENGTHS, len(entry)):
                key = entry[: min(length, len(entry))]
                if sum(1 for e in entries if key and key in e) == 1:
                    return key
            return entry

        def _remove(idx: int) -> bool:
            r = store.remove(target, _remove_key(idx))
            if r.get("success"):
                results["ok"].append((source, idx, "remove"))
                removed_already.add(idx)
                return True
            results["fail"].append((source, idx, f"remove: {r.get('error', '')}"))
            return False

        def _add(content: str) -> bool:
            r = store.add(target, content)
            if r.get("success"):
                results["ok"].append((source, -1, "add"))
                return True
            results["fail"].append((source, -1, f"add: {r.get('error', '')}"))
            return False

        # 1. Merge
        for m in merge_list:
            merged = m.get("合并为", "")
            indices = m.get("indices", [])
            if not merged:
                continue
            added = False
            removed_ok: list[int] = []
            try:
                if _add(merged):
                    added = True
                    for j in indices:
                        if j < len(entries):
                            if not _remove(j):
                                raise Exception(f"remove failed at index {j}")
                            removed_ok.append(j)
            except Exception:
                if added:
                    store.remove(target, merged)
                    # 恢复已成功删除的条目，保证原子性（要么全成，要么回到原状）
                    for j in removed_ok:
                        try:
                            store.add(target, entries[j])
                        except Exception:
                            pass
                results["fail"].append((source, indices, "merge rollback triggered"))

        # 2. Compress（先 add 再 remove，防止 add 失败导致数据丢失）
        for c in compress_list:
            idx = c.get("index", -1)
            compressed = c.get("精简为", "")
            if idx < 0 or idx >= len(entries) or not compressed:
                continue
            added = False
            try:
                if _add(compressed):
                    added = True
                    if _remove(idx):
                        results["ok"].append((source, idx, "compress"))
                    else:
                        raise Exception("compress remove failed after add")
            except Exception:
                if added:
                    store.remove(target, compressed)
                results["fail"].append((source, idx, "compress rollback triggered"))

        # 3+4. 收集 retain 任务（corrected + correct）
        retain_tasks: list[tuple[int, str, str]] = []

        for item in v2_corrected:
            idx = item.get("index", -1)
            corrected = item.get("corrected_text", "").strip()
            if idx < 0 or idx >= len(entries):
                continue
            original = entries[idx]
            orig_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", original))
            corr_kw = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", corrected))
            kw_overlap = len(orig_kw & corr_kw) / max(len(orig_kw), 1) if corrected else 0
            # 英文主导场景回退到字符级重叠
            total_kw = max(len(orig_kw), len(corr_kw))
            char_overlap = kw_overlap
            if total_kw < 3 and corrected:
                orig_chars = set(original.lower())
                corr_chars = set(corrected.lower())
                char_overlap = len(orig_chars & corr_chars) / max(len(orig_chars | corr_chars), 1)
                effective_overlap = char_overlap
            else:
                effective_overlap = kw_overlap
            has_real_fix = (
                corrected
                and len(corrected) > 10
                and corrected != original[: len(corrected)]
                and "修正" not in corrected[:20]
                and "需补充" not in corrected[:20]
                and effective_overlap > 0.2
            )
            retain_tasks.append((idx, corrected if has_real_fix else original, "corrected"))

        for item in v2_correct:
            idx = item.get("index", -1)
            if idx < 0 or idx >= len(entries):
                continue
            retain_tasks.append((idx, entries[idx], "correct"))

        # 并行 retain
        print(f"\n  {source}: retain {len(retain_tasks)} 条（{self._config.max_workers} 线程并行）...", flush=True)
        retain_ok: set[int] = set()
        retain_fail: set[int] = set()

        def _retain_worker(task: tuple[int, str, str]) -> tuple[int, bool, str]:
            idx, content, label = task
            ok = self._retain(content)
            return idx, ok, label

        with ThreadPoolExecutor(max_workers=self._config.max_workers) as pool:
            future_to_task = {pool.submit(_retain_worker, t): t for t in retain_tasks}
            for f in as_completed(future_to_task):
                try:
                    idx, ok, label = f.result(timeout=150)
                    if ok:
                        retain_ok.add(idx)
                        results["ok"].append((source, idx, label))
                    else:
                        retain_fail.add(idx)
                        results["fail"].append((source, idx, f"{label}: retain failed"))
                except TimeoutError:
                    idx, _, label = future_to_task[f]
                    retain_fail.add(idx)
                    results["fail"].append((source, idx, f"{label}: retain timeout (150s)"))

        print(f"    retain: {len(retain_ok)} OK / {len(retain_fail)} 失败跳过", flush=True)

        # 5. 串行 remove（只删 retain 成功的）
        for idx in sorted(retain_ok):
            _remove(idx)

        # 6. Hindsight retain → remove（先 retain 再删，防止数据丢失）
        if hindsight_list:
            print(f"    hindsight: {len(hindsight_list)} 条 retain → remove...", flush=True)
            for h in hindsight_list:
                idx = h.get("index", -1)
                if idx < 0 or idx >= len(entries):
                    continue
                if idx in removed_already:
                    continue
                tags = None
                if self._config.keyword_backfill:
                    raw_tags = h.get("关键词", [])
                    if isinstance(raw_tags, list):
                        tags = [t for t in raw_tags if t and isinstance(t, str)]
                        if not tags:
                            tags = None
                if self._retain(entries[idx], tags=tags):
                    if _remove(idx):
                        results["ok"].append((source, idx, "hindsight"))
                    else:
                        results["fail"].append((source, idx, "hindsight: remove failed after retain"))
                else:
                    logger.warning("hindsight [%d]: retain 失败，保留原始条目", idx)
                    results["fail"].append((source, idx, "hindsight: retain failed"))

        # 7. 直接删（空§/合并覆盖/清理自身记录）
        skip_set = {i.get("index", -1) for i in v2_correct + v2_corrected}
        keep_set = {i.get("index", -1) for i in v2_keep}

        for r in remove_list:
            idx = r.get("index", -1)
            if idx < 0:
                continue
            if idx in skip_set or idx in keep_set or idx in removed_already:
                continue
            _remove(idx)

        # 8. 冷记忆淘汰（evict_to_hindsight）：从 L2 移到 Hindsight
        if evict_list:
            print(f"    evict_to_hindsight: {len(evict_list)} 条...", flush=True)
            for e in evict_list:
                idx = e.get("index", -1)
                if idx < 0 or idx >= len(entries):
                    continue
                if idx in removed_already:
                    continue
                tags = None
                if self._config.keyword_backfill:
                    raw_tags = e.get("关键词", [])
                    if isinstance(raw_tags, list):
                        tags = [t for t in raw_tags if t and isinstance(t, str)]
                        if not tags:
                            tags = None
                if self._retain(entries[idx], tags=tags):
                    if _remove(idx):
                        results["ok"].append((source, idx, "evict_to_hindsight"))
                    else:
                        results["fail"].append((source, idx, "evict_to_hindsight: remove failed after retain"))
                else:
                    logger.warning("evict_to_hindsight [%d]: retain 失败，保留原始条目", idx)
                    results["fail"].append((source, idx, "evict_to_hindsight: retain failed"))

        # 9. 高频回升（promote_to_l2）：从 Hindsight 移到 L2
        if promote_list:
            print(f"    promote_to_l2: {len(promote_list)} 条...", flush=True)
            for p in promote_list:
                content = p.get("content", "")
                if not content:
                    continue
                if _add(content):
                    results["ok"].append((source, -1, "promote_to_l2"))
                else:
                    results["fail"].append((source, -1, "promote_to_l2: add failed"))

        return results
