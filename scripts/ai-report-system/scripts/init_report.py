# -*- coding: utf-8 -*-
"""
新报告初始化 — 创建目录结构，准备环境。

用法:
    python3 scripts/init_report.py "报告主题" [报告类型]

示例:
    python3 scripts/init_report.py "央企智能化转型建设规划" tech
"""

from __future__ import annotations

import sys
from pathlib import Path

REPORT_TYPES = {"tech", "research", "market", "product"}


def init_report(topic: str, report_type: str = "tech") -> dict:
    """创建新报告的目录结构和初始文件。

    Args:
        topic: 报告主题
        report_type: 报告类型（tech/research/market/product）

    Returns:
        {"topic": topic, "report_dir": Path, "inputs_dir": Path, "type": report_type}
    """
    if report_type not in REPORT_TYPES:
        print(f"⚠️ 未知报告类型 '{report_type}'，使用默认 'tech'")
        report_type = "tech"

    # 目录名：取主题前40字，替换空格
    dir_name = topic.replace(" ", "_")[:40]
    report_dir = Path("reports") / dir_name
    inputs_dir = report_dir / "inputs"

    # 创建目录
    inputs_dir.mkdir(parents=True, exist_ok=True)

    print(f"📁 报告目录: {report_dir}/")
    print(f"📁 素材目录: {inputs_dir}/")
    print(f"📋 报告类型: {report_type}")
    print()
    print("📌 接下来：")
    print(f"   1. 把源素材文件拖到 {inputs_dir}/ 目录下")
    print(f"   2. 告诉我文件已放好，开始提取目标")
    print()
    print(f"   支持格式: .md .txt .docx")

    return {
        "topic": topic,
        "report_dir": str(report_dir),
        "inputs_dir": str(inputs_dir),
        "type": report_type,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python3 scripts/init_report.py \"报告主题\" [报告类型]")
        print("示例: python3 scripts/init_report.py \"央企智能化转型建设规划\" tech")
        sys.exit(1)

    topic = sys.argv[1]
    report_type = sys.argv[2] if len(sys.argv) > 2 else "tech"
    init_report(topic, report_type)


if __name__ == "__main__":
    main()
