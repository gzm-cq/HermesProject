"""批处理清单 — 逐篇记录 atomics，支持断点续传"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


STATUS_PENDING = "pending"       # 待处理
STATUS_EXTRACTED = "extracted"   # LLM 提取完成，atomics 已存盘
STATUS_WRITTEN = "written"       # 已写入 PG
STATUS_FAILED = "failed"         # 提取失败


@dataclass
class ManifestItem:
    path: str
    title: str
    status: str = STATUS_PENDING
    error: str = ""
    atomics_file: str = ""        # atomics JSON 文件路径


class Manifest:
    """批处理清单 + 逐篇 atomics 存盘。

    用法:
        m = Manifest("batch.json")
        m.init(files)           # 首次初始化
        m.load()                # 续传时加载
        for item in m.pending():
            atomics = extract(item)
            m.save_atomics(item, atomics)  # 存盘 + 标记 extracted
        # 全部提取完成后统一 admit + write
        all_atomics = m.load_all_atomics()
        m.mark_written(item)    # 写入后标记
    """

    def __init__(self, path: str = "") -> None:
        self.path = path or f".kb_batch_{int(time.time())}.json"
        self.domain: str = ""
        self.items: list[ManifestItem] = []
        self._atomics_dir: str = ""

    # ========== 初始化 ==========

    def init(self, files: list[dict[str, str]], domain: str = "") -> None:
        """从文件列表初始化清单。"""
        self.domain = domain
        self._atomics_dir = self.path.replace(".json", "_atomics")
        self.items = [
            ManifestItem(path=f["path"], title=f["title"])
            for i, f in enumerate(files)
        ]
        self._save()

    def load(self) -> bool:
        """加载已有清单。

        Returns:
            True=有未完成的条目可续传, False=全部完成或清单不存在
        """
        if not os.path.exists(self.path):
            return False
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.domain = data.get("domain", "")
            self._atomics_dir = data.get("atomics_dir", "")
            self.items = [ManifestItem(**item) for item in data.get("items", [])]
        except Exception as e:
            logger.warning("加载清单失败: %s", e)
            return False

        remaining = [i for i in self.items if i.status != STATUS_WRITTEN]
        if not remaining:
            logger.info("所有条目已写入 PG")
            return False

        done = sum(1 for i in self.items if i.status == STATUS_EXTRACTED)
        pending = sum(1 for i in self.items if i.status == STATUS_PENDING)
        failed = sum(1 for i in self.items if i.status == STATUS_FAILED)
        logger.info(
            "清单: %d 总, %d 已提取, %d 待处理, %d 失败",
            len(self.items), done, pending, failed,
        )
        return True

    # ========== 状态 ==========

    def pending(self) -> list[ManifestItem]:
        return [i for i in self.items if i.status == STATUS_PENDING]

    def failed(self) -> list[ManifestItem]:
        return [i for i in self.items if i.status == STATUS_FAILED]

    def need_extract(self) -> list[ManifestItem]:
        return [i for i in self.items if i.status in (STATUS_PENDING, STATUS_FAILED)]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def done_count(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_EXTRACTED)

    @property
    def written_count(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_WRITTEN)

    # ========== 逐篇 atomics 存盘 ==========

    def save_atomics(self, item: ManifestItem, atomics: list[dict]) -> None:
        """提取后将 atomics 存盘，标记 extracted。"""
        os.makedirs(self._atomics_dir, exist_ok=True)
        safe = _safe_name(item.title)
        # 用路径哈希避免同名覆盖（YAML 前置元数据可能产生相同标题）
        path_hash = abs(hash(item.path)) % 100000
        af = os.path.join(self._atomics_dir, "%s_%05d.json" % (safe, path_hash))
        # 为每个 atomic 注入 source_title（兼容旧 checkpoint 未携带该字段）
        for a in atomics:
            if "source_title" not in a:
                a["source_title"] = item.title
        with open(af, "w", encoding="utf-8") as f:
            json.dump({
                "title": item.title,
                "path": item.path,
                "atomics": atomics,
                "count": len(atomics),
                "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)
        item.atomics_file = af
        item.status = STATUS_EXTRACTED
        item.error = ""
        self._progress()
        self._save()

    def mark_failed(self, item: ManifestItem, error: str = "") -> None:
        item.status = STATUS_FAILED
        item.error = error[:200]
        self._progress()
        self._save()

    def mark_written(self, item: ManifestItem) -> None:
        """标记已写入 PG 并删除 atomics 文件。"""
        item.status = STATUS_WRITTEN
        if item.atomics_file and os.path.exists(item.atomics_file):
            try:
                os.remove(item.atomics_file)
            except Exception:
                pass
        self._save()

    def load_all_atomics(self) -> list[dict]:
        """加载所有已提取的 atomics（用于 admit + write）。

        兼容旧 checkpoint：atomict 文件可能未含 source_title 字段，
        此时从父级 data["title"] 补填。
        """
        all_atomics: list[dict] = []
        for item in self.items:
            if item.status == STATUS_EXTRACTED and item.atomics_file:
                try:
                    with open(item.atomics_file, encoding="utf-8") as f:
                        data = json.load(f)
                    file_title = data.get("title", item.title)
                    for a in data.get("atomics", []):
                        if "source_title" not in a:
                            a["source_title"] = file_title
                        all_atomics.append(a)
                except Exception as e:
                    logger.warning("加载 atomics 失败 %s: %s", item.title, e)
        return all_atomics

    # ========== 进度 ==========

    def _progress(self) -> None:
        done = self.done_count
        written = self.written_count
        total = self.total
        pct = (done + written) * 100 // max(total, 1)
        bar_len = 20
        filled = (done + written) * bar_len // max(total, 1)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(
            "\r  进度: |%s| %d/%d (%d%%) 已提取=%d 已写入=%d 失败=%d      "
            % (bar, done + written, total, pct, done, written,
               sum(1 for i in self.items if i.status == STATUS_FAILED)),
            end="", flush=True,
        )

    def summary(self) -> None:
        print()
        written = self.written_count
        failed = [i for i in self.items if i.status == STATUS_FAILED]
        print("  ─────────────────────────────────────")
        print("  已写入: %d  失败: %d  总计: %d" % (written, len(failed), self.total))
        if failed:
            print("  失败:")
            for item in failed:
                print("    ❌ %s: %s" % (item.title[:40], item.error[:80]))
        if self._atomics_dir and os.path.exists(self._atomics_dir):
            remaining = os.listdir(self._atomics_dir)
            if remaining:
                print("  残留 atomics 文件: %d" % len(remaining))

    # ========== 内部 ==========

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "domain": self.domain,
                    "atomics_dir": self._atomics_dir,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "items": [
                        {
                            "path": i.path,
                            "title": i.title,
                            "status": i.status,
                            "error": i.error,
                            "atomics_file": i.atomics_file,
                        }
                        for i in self.items
                    ],
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存清单失败: %s", e)


def _safe_name(title: str) -> str:
    """将标题转为安全文件名。"""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
    return safe[:80].strip() or "untitled"
