"""数据血缘模块 — 记录知识节点的来源、处理步骤和版本变化。

提供 LineageRecord 数据类和 LineageTracker 管理器，支持：
- 记录每个知识点的来源文章、提取方式、处理步骤
- 支持 basic/full 两种详细程度
- JSON 格式的导入导出
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LineageRecord:
    """单条知识点血缘记录。

    Attributes:
        node_id: 知识点ID（字符串形式，支持数字ID或UUID）
        source_article: 来源文章路径/标题
        source_text: 原文片段（basic 级别不记录，full 级别记录）
        extraction_method: 提取方式（llm_extract / manual / import）
        processing_steps: 处理步骤列表（["analyze", "split", "admit", "place"]）
        created_at: 创建时间（ISO 格式）
        updated_at: 更新时间（ISO 格式）
        version: 版本号（int，递增）
        metadata: 额外元数据（灵活扩展用）
    """

    node_id: str
    source_article: str
    source_text: str = ""
    extraction_method: str = "llm_extract"
    processing_steps: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_step(self, step: str, detail: dict[str, Any] | None = None) -> None:
        """添加处理步骤。

        Args:
            step: 步骤名称（如 "analyze", "split", "admit", "place"）
            detail: 步骤详情（可选，存入 metadata）
        """
        if step not in self.processing_steps:
            self.processing_steps.append(step)
        if detail:
            step_key = f"step_{step}_detail"
            if step_key not in self.metadata:
                self.metadata[step_key] = []
            self.metadata[step_key].append(detail)
        self.updated_at = _now_iso()

    def increment_version(self) -> None:
        """版本号递增。"""
        self.version += 1
        self.updated_at = _now_iso()

    def to_dict(self, detail_level: str = "basic") -> dict[str, Any]:
        """转换为字典。

        Args:
            detail_level: 详细程度，"basic" 或 "full"

        Returns:
            字典形式的记录
        """
        data = asdict(self)
        if detail_level == "basic":
            data.pop("source_text", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LineageRecord":
        """从字典创建 LineageRecord。

        Args:
            data: 字典数据

        Returns:
            LineageRecord 实例
        """
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


class LineageTracker:
    """血缘记录追踪器。

    管理多个知识点的血缘记录，支持记录、查询、导入导出。
    保留历史版本：同一 node_id 的多次更新会保存所有版本。
    """

    def __init__(self, detail_level: str = "basic") -> None:
        """初始化追踪器。

        Args:
            detail_level: 详细程度，"basic" 或 "full"
        """
        self.detail_level = detail_level
        # 用 (node_id, version) 作为 key 保留历史版本
        self._records: dict[tuple[str, int], LineageRecord] = {}
        # node_id -> 最新版本号的映射
        self._latest_versions: dict[str, int] = {}

    def _make_key(self, node_id: str, version: int) -> tuple[str, int]:
        """生成记录 key。"""
        return (node_id, version)

    def _get_latest_version(self, node_id: str) -> int:
        """获取指定节点的最新版本号。"""
        return self._latest_versions.get(node_id, 0)

    def create_record(
        self,
        node_id: str,
        source_article: str,
        source_text: str = "",
        extraction_method: str = "llm_extract",
    ) -> LineageRecord:
        """创建一条新的血缘记录。

        如果节点已存在，会创建新版本而非覆盖。

        Args:
            node_id: 知识点ID
            source_article: 来源文章
            source_text: 原文片段
            extraction_method: 提取方式

        Returns:
            新建的 LineageRecord
        """
        latest_version = self._get_latest_version(node_id)
        new_version = latest_version + 1

        record = LineageRecord(
            node_id=node_id,
            source_article=source_article,
            source_text=source_text if self.detail_level == "full" else "",
            extraction_method=extraction_method,
            version=new_version,
        )
        self._records[self._make_key(node_id, new_version)] = record
        self._latest_versions[node_id] = new_version
        return record

    def get_record(self, node_id: str, version: int | None = None) -> LineageRecord | None:
        """获取指定节点的最新版本或指定版本。

        Args:
            node_id: 知识点ID
            version: 版本号，None 时获取最新版本

        Returns:
            LineageRecord 或 None
        """
        if version is None:
            version = self._get_latest_version(node_id)
        if version <= 0:
            return None
        return self._records.get(self._make_key(node_id, version))

    def get_all_versions(self, node_id: str) -> list[LineageRecord]:
        """获取指定节点的所有历史版本。

        Args:
            node_id: 知识点ID

        Returns:
            按版本号排序的 LineageRecord 列表（ oldest -> newest）
        """
        latest = self._get_latest_version(node_id)
        if latest <= 0:
            return []
        return [
            self._records[self._make_key(node_id, v)]
            for v in range(1, latest + 1)
            if self._make_key(node_id, v) in self._records
        ]

    def add_step(self, node_id: str, step: str, detail: dict[str, Any] | None = None) -> bool:
        """为指定节点的最新版本添加处理步骤。

        Args:
            node_id: 知识点ID
            step: 步骤名称
            detail: 步骤详情

        Returns:
            是否成功添加（节点不存在返回 False）
        """
        record = self.get_record(node_id)
        if record is None:
            return False
        record.add_step(step, detail)
        return True

    def all_records(self, latest_only: bool = False) -> list[LineageRecord]:
        """获取所有血缘记录。

        Args:
            latest_only: 是否只返回最新版本

        Returns:
            记录列表
        """
        if latest_only:
            return [
                self._records[k]
                for k, v in self._records.items()
                if v.version == self._latest_versions.get(k[0], 0)
            ]
        return list(self._records.values())

    def count(self) -> tuple[int, int]:
        """获取记录统计。

        Returns:
            (总版本数, 唯一节点数)
        """
        return len(self._records), len(self._latest_versions)

    def to_json(self, indent: int = 2, latest_only: bool = False) -> str:
        """导出为 JSON 字符串。

        Args:
            indent: 缩进空格数
            latest_only: 是否只导出最新版本

        Returns:
            JSON 字符串
        """
        records = self.all_records(latest_only=latest_only)
        records_list = [r.to_dict(self.detail_level) for r in records]
        return json.dumps(records_list, ensure_ascii=False, indent=indent)

    def save_to_file(self, file_path: str | Path, latest_only: bool = False) -> None:
        """保存到 JSON 文件。

        Args:
            file_path: 文件路径
            latest_only: 是否只保存最新版本
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(latest_only=latest_only), encoding="utf-8")

    @classmethod
    def from_json(cls, json_str: str, detail_level: str = "basic") -> "LineageTracker":
        """从 JSON 字符串加载。

        Args:
            json_str: JSON 字符串
            detail_level: 详细程度

        Returns:
            LineageTracker 实例
        """
        tracker = cls(detail_level=detail_level)
        try:
            records_data = json.loads(json_str)
            for data in records_data:
                record = LineageRecord.from_dict(data)
                tracker._records[tracker._make_key(record.node_id, record.version)] = record
                tracker._latest_versions[record.node_id] = max(
                    tracker._latest_versions.get(record.node_id, 0),
                    record.version,
                )
        except (json.JSONDecodeError, TypeError):
            pass
        return tracker

    @classmethod
    def load_from_file(cls, file_path: str | Path, detail_level: str = "basic") -> "LineageTracker":
        """从 JSON 文件加载。

        Args:
            file_path: 文件路径
            detail_level: 详细程度

        Returns:
            LineageTracker 实例
        """
        path = Path(file_path)
        if not path.exists():
            return cls(detail_level=detail_level)
        try:
            json_str = path.read_text(encoding="utf-8")
            return cls.from_json(json_str, detail_level=detail_level)
        except Exception:
            return cls(detail_level=detail_level)


__all__ = [
    "LineageRecord",
    "LineageTracker",
]
