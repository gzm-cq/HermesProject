"""skill_match_cache.py — Skill 匹配结果语义缓存（意图联合键）。

设计（用户确认 2026-09-04）：
- 键 = (ctx_emb, query_emb) 双嵌入组合键：目标（最近对话上下文）与输入（当前消息）分别嵌入
- 命中 = ctx_sim >= KN_SKILL_MATCH_CACHE_CTX_THRESHOLD (0.90)
         AND query_sim >= KN_SKILL_MATCH_CACHE_QUERY_THRESHOLD (0.92)
- context 为空（首轮无目标）时退化为 query-only 单键缓存
- 淘汰 = LFU（hit_count 升序）+ TTL（KN_SKILL_MATCH_CACHE_TTL，默认 24h）
- 容量 = KN_SKILL_MATCH_CACHE_MAX（默认 1000 条）
- 持久化 = JSON 原子写盘（tmp + rename），重启不丢、逐步积累

⚠️ 本模块**不接入生产调用链**——必须通过 tests/test_skill_match_cache.py
   全部验收指标后才能由调用方启用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from knowledge_navigation.core.env_loader import get_env, get_env_float, get_env_int

logger = logging.getLogger(__name__)

# ── 默认值（运行期可被环境变量覆盖）──
DEFAULT_CTX_THRESHOLD = 0.90
DEFAULT_QUERY_THRESHOLD = 0.85  # 实测（2026-09-04）：同意图改写 0.897-0.935，不同意图 0.304-0.421
DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_MAX_ENTRIES = 1000
DEFAULT_CACHE_PATH = Path(os.path.expanduser("~/.hermes/data/skill_match_cache.json"))

# ── L0 全等快路径（2026-09-05 修复）：query 原文 md5 精确匹配 ──
# 服务固定 prompt 的高重复流量（如 system-health-self-heal 每小时整点，
# query 完全一致）。不依赖 embedding 计算，命中前无需调任何模型。
_EXACT_MAX_KEYS = 2000  # exact map 容量上限（LRU-ish：写满删最旧）


def _query_key(query_text: str) -> str:
    """L0 全等快路径键：query 原文（去首尾空白）的 md5。"""
    return hashlib.md5(query_text.strip().encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
    """单条缓存记录。"""

    ctx_emb: np.ndarray | None  # None 表示 query-only 条目（context 为空时）
    query_emb: np.ndarray
    result: list[str]  # 技能名列表（LLM 精排或早退产物）
    hit_count: int = 1  # 新条目从 1 起（约定：与 0 平移等价，真正保护来自同频时 last_hit_ts 决胜）
    created_ts: float = field(default_factory=time.time)
    last_hit_ts: float = field(default_factory=time.time)
    skill_set_hash: str = ""  # 技能集指纹（L1 失效机制）：技能库变化时整库失效
    intent: str = ""  # A 方案意图标签（精排 LLM 输出），监控意图分布用


class SkillMatchCache:
    """意图联合键语义缓存。

    线程安全：所有写操作在 _lock 下进行；读取在快照下进行。
    """

    def __init__(
        self,
        ctx_threshold: float | None = None,
        query_threshold: float | None = None,
        ttl_seconds: float | None = None,
        max_entries: int | None = None,
        cache_path: str | Path | None = None,
        save_interval: float | None = None,
    ) -> None:
        self.ctx_threshold = ctx_threshold if ctx_threshold is not None else self._env_float(
            "KN_SKILL_MATCH_CACHE_CTX_THRESHOLD", DEFAULT_CTX_THRESHOLD
        )
        self.query_threshold = query_threshold if query_threshold is not None else self._env_float(
            "KN_SKILL_MATCH_CACHE_QUERY_THRESHOLD", DEFAULT_QUERY_THRESHOLD
        )
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else self._env_float(
            "KN_SKILL_MATCH_CACHE_TTL", DEFAULT_TTL_SECONDS
        )
        self.max_entries = max_entries if max_entries is not None else self._env_int(
            "KN_SKILL_MATCH_CACHE_MAX", DEFAULT_MAX_ENTRIES
        )
        self.cache_path = Path(cache_path) if cache_path is not None else self._env_path(
            "KN_SKILL_MATCH_CACHE_PATH", DEFAULT_CACHE_PATH
        )

        self._entries: list[CacheEntry] = []
        self._lock = threading.Lock()
        self._dirty = False
        self._last_save_ts = time.time()
        self._save_interval = save_interval if save_interval is not None else 5.0

        # ── L0 全等快路径（2026-09-05 修复）：query 原文 md5 → skills ──
        # 与 embedding 路径解耦：不依赖模型服务，命中前无需调 embedding；
        # 适合固定 prompt 的高重复流量（如 cron 整点巡检）。
        self._exact: dict[str, dict[str, Any]] = {}

        # ── 全局命中率监控（跨重启持久化，随 cache 落盘）──
        self._total_lookups = 0     # lookup() 调用次数
        self._total_hits = 0        # 命中次数
        self._total_misses = 0      # 未命中次数
        self._total_stores = 0      # store() 调用次数
        self._hourly: dict[str, dict[str, int]] = {}  # "YYYY-MM-DDTHH" → {hits, misses}

        self._load()  # 启动时加载磁盘缓存（失败静默降级为空）

    # ── 公共 API ──

    def lookup_exact(self, query_text: str, skill_set_hash: str = "") -> list[str] | None:
        """L0 全等快路径（query 原文 md5 精确匹配，不依赖 embedding）。

        对完全相同的 query 文本（首尾空白已 strip）直接返回缓存结果。
        适合固定 prompt 的高重复流量（如 cron 整点巡检），命中前零模型调用。

        skill_set_hash 非空时参与校验：技能集指纹不匹配的 exact 键
        视为失效（惰性删除，下次 store 重建），保证 L1 语义不被绕过。
        """
        if not query_text:
            return None
        key = _query_key(query_text)
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            self._total_lookups += 1
            self._bump_hourly(now, "lookups")
            ex = self._exact.get(key)
            if ex is None:
                self._total_misses += 1
                self._bump_hourly(now, "misses")
                return None
            if skill_set_hash and ex.get("hash", "") != skill_set_hash:
                # L1 失效：技能集已变，旧 exact 结果作废（惰性删除）
                del self._exact[key]
                self._total_misses += 1
                self._bump_hourly(now, "misses")
                self._dirty = True
                return None
            ex["ts"] = now
            self._total_hits += 1
            self._bump_hourly(now, "hits")
            self._dirty = True
            self._save_if_due_locked(now)
            return list(ex["skills"])

    def lookup(
        self,
        ctx_emb: np.ndarray | None,
        query_emb: np.ndarray,
        skill_set_hash: str = "",
    ) -> list[str] | None:
        """查找缓存。

        Args:
            ctx_emb: 目标上下文 embedding；None 表示退化 query-only
            query_emb: 当前用户输入 embedding
            skill_set_hash: 当前技能集指纹（L1 失效机制）——技能库变化时
                指纹不匹配的条目全部视为 miss（惰性失效，不主动清盘）

        Returns:
            命中返回技能名列表；未命中返回 None。
        """
        now = time.time()
        # 入参一次性归一化（后续 _dot 即余弦相似度）
        query_emb = _normalize(query_emb)
        ctx_emb = _normalize(ctx_emb) if ctx_emb is not None else None
        with self._lock:
            self._purge_expired(now)
            self._total_lookups += 1
            self._bump_hourly(now, "lookups")
            for entry in self._entries:
                # L1 失效：技能集指纹不匹配 → 跳过（缓存结果可能引用已删除/改名技能）
                if skill_set_hash and entry.skill_set_hash != skill_set_hash:
                    continue
                if ctx_emb is not None and entry.ctx_emb is not None:
                    ctx_sim = _dot(ctx_emb, entry.ctx_emb)
                    if ctx_sim < self.ctx_threshold:
                        continue
                elif (ctx_emb is None) != (entry.ctx_emb is None):
                    # 维度不匹配（一个有目标一个没有），不命中
                    continue
                query_sim = _dot(query_emb, entry.query_emb)
                if query_sim < self.query_threshold:
                    continue
                # 命中：更新计数与时间戳（dirty，批量落盘）
                entry.hit_count += 1
                entry.last_hit_ts = now
                self._total_hits += 1
                self._bump_hourly(now, "hits")
                self._dirty = True
                self._save_if_due_locked(now)
                return list(entry.result)
        self._total_misses += 1
        self._bump_hourly(now, "misses")
        return None

    def store_exact(self, query_text: str, result: list[str], skill_set_hash: str = "") -> None:
        """仅写 L0 全等快路径（不写 embedding 条目）。

        embedding 服务不可用/超时降级场景使用：精排结果只登记 md5 键，
        下次相同 query 无需 embedding 即可命中。
        """
        if not result or not query_text:
            return
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            self._total_stores += 1
            self._exact[_query_key(query_text)] = {
                "skills": list(result),
                "ts": now,
                "hash": skill_set_hash,
            }
            self._trim_exact_locked()

    def store(
        self,
        ctx_emb: np.ndarray | None,
        query_emb: np.ndarray,
        result: list[str],
        skill_set_hash: str = "",
        intent: str = "",
        query_text: str = "",
    ) -> None:
        """写入缓存。已存在相同键（含同指纹）则更新 result，否则新增。

        query_text 非空时同时写入 L0 全等快路径（md5 → result）。
        """
        if not result:
            return
        now = time.time()
        with self._lock:
            self._purge_expired(now)  # store 路径也清理过期条目（缺陷 3 修复）
            self._total_stores += 1
            # L0 全等快路径：更新/新建 md5 → result
            if query_text:
                self._exact[_query_key(query_text)] = {
                    "skills": list(result),
                    "ts": now,
                    "hash": skill_set_hash,
                }
                self._trim_exact_locked()
            # 已存在完全相同键 → 更新结果（保留 hit_count，不重置冷启动保护）
            for entry in self._entries:
                if _same_key(
                    entry, ctx_emb, query_emb,
                    self.ctx_threshold, self.query_threshold, skill_set_hash,
                ):
                    entry.result = list(result)
                    entry.created_ts = now
                    if intent:
                        entry.intent = intent
                    self._dirty = True
                    self._save_if_due_locked(now)
                    return
            self._entries.append(CacheEntry(
                ctx_emb=_normalize(ctx_emb) if ctx_emb is not None else None,
                query_emb=_normalize(query_emb),
                result=list(result),
                skill_set_hash=skill_set_hash,
                intent=intent,
            ))
            self._evict_locked()
            self._dirty = True
            self._save_if_due_locked(now)

    def stats(self) -> dict[str, Any]:
        """缓存统计（含全局命中率监控指标）。"""
        with self._lock:
            self._purge_expired(time.time())
            hourly = sorted(
                ({"hour": h, **v} for h, v in self._hourly.items()),
                key=lambda x: x["hour"],
            )[-24:]
            return {
                "entries": len(self._entries),
                "exact_entries": len(self._exact),
                "max_entries": self.max_entries,
                "ctx_threshold": self.ctx_threshold,
                "query_threshold": self.query_threshold,
                "ttl_seconds": self.ttl_seconds,
                "cache_path": str(self.cache_path),
                "total_lookups": self._total_lookups,
                "total_hits": self._total_hits,
                "total_misses": self._total_misses,
                "total_stores": self._total_stores,
                "hit_rate": round(
                    self._total_hits / self._total_lookups, 4
                ) if self._total_lookups else 0.0,
                "intent_histogram": self._intent_histogram_locked(),
                "hourly": hourly,
            }

    def _intent_histogram_locked(self) -> dict[str, int]:
        """缓存条目的意图标签直方图（A 方案监控：看精排输出意图分布）。"""
        hist: dict[str, int] = {}
        for e in self._entries:
            if e.intent:
                hist[e.intent] = hist.get(e.intent, 0) + 1
        return dict(sorted(hist.items(), key=lambda kv: -kv[1])[:20])

    def flush(self) -> None:
        """强制落盘（测试/优雅停机用；生产由批量间隔自动触发）。"""
        with self._lock:
            if self._dirty:
                self._save_locked()

    # ── 内部 ──

    def _bump_hourly(self, now: float, key: str) -> None:
        """每小时窗口计数器（key ∈ lookups/hits/misses）。"""
        hour = time.strftime("%Y-%m-%dT%H", time.localtime(now))
        bucket = self._hourly.setdefault(hour, {"lookups": 0, "hits": 0, "misses": 0})
        bucket[key] = bucket.get(key, 0) + 1

    def _purge_expired(self, now: float) -> None:
        """滑动窗口 TTL：基于 last_hit_ts（命中即续期）。

        冷条目（长时间未命中）过期清除；热条目（高频命中）只靠 LFU 竞争淘汰，
        不会被定时误杀——与\"按频率清退\"设计意图一致。
        """
        keep = [
            e for e in self._entries
            if now - e.last_hit_ts < self.ttl_seconds
        ]
        if len(keep) != len(self._entries):
            self._entries = keep
        # L0 全等快路径同步过期清理（命中续期，冷键淘汰）
        if self._exact:
            stale = [k for k, v in self._exact.items()
                     if now - float(v.get("ts", 0)) >= self.ttl_seconds]
            for k in stale:
                del self._exact[k]

    def _trim_exact_locked(self) -> None:
        """L0 快路径容量上限：写满时删最久未命中（ts 最旧）的键。"""
        if len(self._exact) <= _EXACT_MAX_KEYS:
            return
        for k in sorted(
            self._exact, key=lambda k: float(self._exact[k].get("ts", 0))
        )[: len(self._exact) - _EXACT_MAX_KEYS]:
            del self._exact[k]

    def _evict_locked(self) -> None:
        """LFU 淘汰：超容量时清 hit_count 最低的（同频清最久未命中）。"""
        while len(self._entries) > self.max_entries:
            # 找到 hit_count 最小者；同频取 last_hit_ts 最旧（最久未命中）
            victim_idx = min(
                range(len(self._entries)),
                key=lambda i: (self._entries[i].hit_count, self._entries[i].last_hit_ts),
            )
            self._entries.pop(victim_idx)

    def _save_locked(self) -> None:
        """原子写盘（tmp + rename）。

        2026-09-05 修复：写盘前合并磁盘旧 stats（逐项取 max）——
        多进程（gateway / cron / CLI）各自维护内存统计并覆盖写同一文件，
        之前后写者覆盖先写者导致统计丢失/回退；合并后统计单调不减。
        """
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._merge_disk_stats_locked()
            payload = {
                "version": 2,
                "ctx_threshold": self.ctx_threshold,
                "query_threshold": self.query_threshold,
                "stats": {
                    "total_lookups": self._total_lookups,
                    "total_hits": self._total_hits,
                    "total_misses": self._total_misses,
                    "total_stores": self._total_stores,
                    "hourly": self._hourly,
                },
                "exact": {
                    k: {"skills": v["skills"], "ts": v["ts"], "hash": v.get("hash", "")}
                    for k, v in self._exact.items()
                },
                "entries": [
                    {
                        "ctx_emb": e.ctx_emb.tolist() if e.ctx_emb is not None else None,
                        "query_emb": e.query_emb.tolist(),
                        "result": e.result,
                        "hit_count": e.hit_count,
                        "created_ts": e.created_ts,
                        "last_hit_ts": e.last_hit_ts,
                        "skill_set_hash": e.skill_set_hash,
                        "intent": e.intent,
                    }
                    for e in self._entries
                ],
            }
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.cache_path.parent), prefix=".skill_match_cache_", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, self.cache_path)
            self._dirty = False
            self._last_save_ts = time.time()
        except Exception as e:  # noqa: BLE001
            logger.debug("Skill match cache save failed: %s", e)

    def _merge_disk_stats_locked(self) -> None:
        """读磁盘旧 stats 并入内存统计（逐项取 max，防多进程覆盖回退）。

        在锁内调用（_save_locked 持有 _lock）。读取失败（首次写盘/文件损坏）
        视为无旧数据，静默跳过。
        """
        try:
            if not self.cache_path.exists():
                return
            with open(self.cache_path, "r", encoding="utf-8") as f:
                old_stats = (json.load(f).get("stats") or {})
        except Exception:  # noqa: BLE001
            return
        self._total_lookups = max(self._total_lookups, int(old_stats.get("total_lookups", 0)))
        self._total_hits = max(self._total_hits, int(old_stats.get("total_hits", 0)))
        self._total_misses = max(self._total_misses, int(old_stats.get("total_misses", 0)))
        self._total_stores = max(self._total_stores, int(old_stats.get("total_stores", 0)))
        for h, d in (old_stats.get("hourly") or {}).items():
            bucket = self._hourly.setdefault(str(h), {"lookups": 0, "hits": 0, "misses": 0})
            for k in ("lookups", "hits", "misses"):
                bucket[k] = max(bucket[k], int(d.get(k, 0)))

    def _save_if_due_locked(self, now: float) -> None:
        """批量落盘：dirty 且距上次保存超过间隔才真正写盘。"""
        if self._dirty and (now - self._last_save_ts) >= self._save_interval:
            self._save_locked()

    def _load(self) -> None:
        """加载磁盘缓存。模型指纹不匹配或解析失败时静默降级为空。"""
        try:
            if not self.cache_path.exists():
                return
            with open(self.cache_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            # 恢复全局监控计数（跨重启累计命中率；max 合并防多进程覆盖回退）
            stats_raw = payload.get("stats") or {}
            self._total_lookups = max(self._total_lookups, int(stats_raw.get("total_lookups", 0)))
            self._total_hits = max(self._total_hits, int(stats_raw.get("total_hits", 0)))
            self._total_misses = max(self._total_misses, int(stats_raw.get("total_misses", 0)))
            self._total_stores = max(self._total_stores, int(stats_raw.get("total_stores", 0)))
            hourly_raw = stats_raw.get("hourly") or {}
            for h, d in hourly_raw.items():
                bucket = self._hourly.setdefault(str(h), {"lookups": 0, "hits": 0, "misses": 0})
                for k in ("lookups", "hits", "misses"):
                    bucket[k] = max(bucket[k], int(d.get(k, 0)))
            # L0 全等快路径恢复
            exact_raw = payload.get("exact") or {}
            for k, v in exact_raw.items():
                skills = list(v.get("skills") or [])
                if skills:
                    self._exact[str(k)] = {
                        "skills": skills,
                        "ts": float(v.get("ts", time.time())),
                        "hash": str(v.get("hash", "")),
                    }
            entries = []
            for raw in payload.get("entries", []):
                try:
                    ctx = (
                        np.array(raw["ctx_emb"], dtype=np.float32)
                        if raw.get("ctx_emb") is not None else None
                    )
                    q = np.array(raw["query_emb"], dtype=np.float32)
                    entries.append(CacheEntry(
                        ctx_emb=ctx,
                        query_emb=q,
                        result=list(raw["result"]),
                        hit_count=int(raw.get("hit_count", 1)),
                        created_ts=float(raw.get("created_ts", time.time())),
                        last_hit_ts=float(raw.get("last_hit_ts", time.time())),
                        skill_set_hash=str(raw.get("skill_set_hash", "")),
                        intent=str(raw.get("intent", "")),
                    ))
                except Exception:  # noqa: BLE001
                    continue  # 单条损坏跳过
            self._entries = entries
            logger.info("Skill match cache loaded: %d entries from %s", len(entries), self.cache_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("Skill match cache load failed (start empty): %s", e)
            self._entries = []

    # ── env 辅助 ──

    @staticmethod
    def _env_float(key: str, default: float) -> float:
        v = get_env_float(key, default)
        return v

    @staticmethod
    def _env_int(key: str, default: int) -> int:
        v = get_env_int(key, default)
        return v

    @staticmethod
    def _env_path(key: str, default: Path) -> Path:
        v = get_env(key)
        return Path(v) if v else default


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度（已归一化向量直接点积）。"""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _normalize(v: np.ndarray) -> np.ndarray:
    """归一化向量（存储时预处理，查找时纯点积省去范数计算）。"""
    n = np.linalg.norm(v)
    if n == 0:
        return v.astype(np.float32)
    return (v / n).astype(np.float32)


def _dot(a: np.ndarray, b: np.ndarray) -> float:
    """预归一化向量的点积（= 余弦相似度）。"""
    return float(np.dot(a, b))


def _same_key(
    entry: CacheEntry,
    ctx_emb: np.ndarray | None,
    query_emb: np.ndarray,
    ctx_threshold: float,
    query_threshold: float,
    skill_set_hash: str = "",
) -> bool:
    """判断现有条目与 (ctx, query) 是否构成相同键（用于更新而非新增）。

    技能集指纹也参与判定：指纹不同视为不同键（技能库已变化，旧条目
    不应被新结果更新，交由 L1 失效机制在 lookup 侧跳过）。
    """
    if skill_set_hash and entry.skill_set_hash != skill_set_hash:
        return False
    if (ctx_emb is None) != (entry.ctx_emb is None):
        return False
    if ctx_emb is not None and entry.ctx_emb is not None:
        if _dot(_normalize(ctx_emb), entry.ctx_emb) < ctx_threshold:
            return False
    return _dot(_normalize(query_emb), entry.query_emb) >= query_threshold
