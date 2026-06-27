#!/usr/bin/env python3
"""
mark_memory.py — 为 Hindsight 记忆单元追加结构化标记 / Hermes Memory 协同修正

用法：
    # 搜索记忆单元（按关键词找 unit_id）
    python3 scripts/mark_memory.py search <关键词> [--limit 20] [--preview-len 120]

    # 标记记忆（可选同步 Hermes MEMORY.md）
    python3 scripts/mark_memory.py mark <unit_id> <类型> [说明] [--apply] [--apply-hermes --keyword <关键词>]

    # 取消标记（可选同步 Hermes MEMORY.md）
    python3 scripts/mark_memory.py unmark <unit_id> [--apply] [--apply-hermes --keyword <关键词>]

    # 检查标记状态
    python3 scripts/mark_memory.py check <unit_id>

    # 搜索 Hermes MEMORY.md（模糊匹配）
    python3 scripts/mark_memory.py hermes-search <关键词> [--preview-len 200]

标记类型：
    错误, 作废, 可疑, 已解决, 待验证

环境变量：
    CLUSTERING_DB_URL  PostgreSQL 连接字符串（必填）
"""
import argparse
import os
import re
import sys
from typing import Any

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

MARK_PREFIXES = {
    "[标记: 错误]",
    "[标记: 作废]",
    "[标记: 可疑]",
    "[标记: 已解决]",
    "[标记: 待验证]",
}

MARK_PATTERN = re.compile(r"\[标记: [^\]]+\]")


def _escape_like(s: str) -> str:
    """转义 PG ILIKE 通配符 % 和 _，保证字面匹配。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def validate_uuid(unit_id: str) -> None:
    """校验 UUID 格式，非法时直接退出。"""
    if not UUID_PATTERN.match(unit_id):
        print(f"错误: 无效的 UUID 格式 — {unit_id}", file=sys.stderr)
        print("  正确格式: 8-4-4-4-12 十六进制字符（如 09f472ff-...）", file=sys.stderr)
        sys.exit(1)


def get_connection():
    """从 CLUSTERING_DB_URL 环境变量获取数据库连接。"""
    db_url = os.environ.get("CLUSTERING_DB_URL")
    if not db_url:
        print("错误: 未设置 CLUSTERING_DB_URL 环境变量", file=sys.stderr)
        sys.exit(1)
    try:
        import psycopg2

        return psycopg2.connect(db_url)
    except ImportError:
        print("错误: 需要 psycopg2 库", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: 无法连接数据库 — {e}", file=sys.stderr)
        sys.exit(1)


def get_mark_state(conn, unit_id: str) -> str:
    """查询记忆标记状态。返回 'not_found' / 'no_mark' / 'marked'。"""
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM memory_units WHERE id = %s", (unit_id,))
        row = cur.fetchone()
        if row is None:
            return "not_found"
        text = row[0] or ""
        if MARK_PATTERN.search(text):
            return "marked"
        return "no_mark"


def mark_memory(conn, unit_id: str, mark_type: str, note: str = None, dry_run: bool = False, commit: bool = True) -> str:
    """为记忆追加标记，幂等保护。返回 'already_marked' / 'not_found' / 'success'。"""
    state = get_mark_state(conn, unit_id)
    if state == "marked":
        return "already_marked"
    if state == "not_found":
        return "not_found"

    mark_text = f"\n[标记: {mark_type}]"
    if note and note.strip():
        mark_text += f" {note}"

    if dry_run:
        print(f"  📄 [DRY-RUN] 将追加: {mark_text!r}")
        return "success"

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE memory_units SET text = text || %s WHERE id = %s",
            (mark_text, unit_id),
        )
        if cur.rowcount == 0:
            return "not_found"
        if commit:
            conn.commit()
        return "success"


def unmark_memory(conn, unit_id: str, dry_run: bool = False) -> str:
    """移除记忆中的标记文本，可逆恢复。返回 'not_found' / 'no_mark' / 'success'。"""
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM memory_units WHERE id = %s", (unit_id,))
        row = cur.fetchone()
        if row is None:
            return "not_found"
        text = row[0] or ""

        if dry_run:
            if MARK_PATTERN.search(text):
                print(f"  📄 [DRY-RUN] 将移除标记")
                return "success"
            return "no_mark"

        new_text = re.sub(r"\n\[标记: [^\]]+\][^\n]*", "", text)
        if new_text == text:
            return "no_mark"

        cur.execute("UPDATE memory_units SET text = %s WHERE id = %s", (new_text, unit_id))
        conn.commit()
        return "success"


HERMES_MEMORY_PATH = os.path.join(os.path.expanduser("~/.hermes"), "memories", "MEMORY.md")
SEPARATOR = "\n\u00a7\n"


def _keyword_matches(keyword: str, text: str) -> bool:
    """关键词匹配，避免全子串误标。"""
    if keyword.isascii() and keyword.isalpha():
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text, re.IGNORECASE))
    return keyword.lower() in text.lower()


def _read_hermes_entries(path: str) -> list[str]:
    """读取 MEMORY.md 并按 \\n§\\n 分隔为条目列表。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    entries = text.split(SEPARATOR)
    if entries and not entries[0].strip():
        entries = entries[1:]
    if entries and not entries[-1].strip():
        entries = entries[:-1]
    entries = [e.rstrip('\n') for e in entries]
    return entries


def _write_hermes_entries(path: str, entries: list[str]) -> None:
    """将条目列表用 \\n§\\n 拼接写回 MEMORY.md，保留尾换行。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(SEPARATOR.join(entries) + "\n")


def mark_hermes_memory(keyword: str, mark_type: str, note: str = None, dry_run: bool = False) -> int:
    """在 MEMORY.md 中模糊匹配含 keyword 的条目，追加标记。返回标记的条目数。"""
    if not os.path.exists(HERMES_MEMORY_PATH):
        print(f"  ⚠️  Hermes Memory 文件不存在: {HERMES_MEMORY_PATH}", file=sys.stderr)
        return 0
    entries = _read_hermes_entries(HERMES_MEMORY_PATH)
    changed = 0
    new_entries = []
    mark_text = f" [标记: {mark_type}]"
    if note and note.strip():
        mark_text += f" {note}"
    for entry in entries:
        if _keyword_matches(keyword, entry) and "[标记:" not in entry:
            if dry_run:
                preview = entry.strip()[:80].replace("\n", " ")
                print(f"    📄 将标记: {preview}...")
            else:
                entry += mark_text
            changed += 1
        new_entries.append(entry)
    if changed and not dry_run:
        _write_hermes_entries(HERMES_MEMORY_PATH, new_entries)
    return changed


def unmark_hermes_memory(keyword: str, dry_run: bool = False) -> int:
    """从 MEMORY.md 中移除匹配条目的标记。返回处理的条目数。"""
    if not os.path.exists(HERMES_MEMORY_PATH):
        return 0
    entries = _read_hermes_entries(HERMES_MEMORY_PATH)
    changed = 0
    new_entries = []
    for entry in entries:
        if _keyword_matches(keyword, entry) and "[标记:" in entry:
            if dry_run:
                preview = entry.strip()[:80].replace("\n", " ")
                print(f"    📄 将取消标记: {preview}...")
            else:
                entry = re.sub(r"\s*\[标记: [^\]]+\][^\n]*", "", entry)
            changed += 1
        new_entries.append(entry)
    if changed and not dry_run:
        _write_hermes_entries(HERMES_MEMORY_PATH, new_entries)
    return changed


def mark_keyword_memories(
    db_adapter: Any,
    dry_run: bool = False,
) -> dict[str, int]:
    """自动标记已知的错误/故障模式的记忆单元。

    供聚类 --apply 流程调用，在 Phase 3 后自动运行。
    使用关键词匹配，避免全子串误标（与 _keyword_matches 一致）。

    Returns:
        {"total_marked": int, "errors": int}
    """
    import re as _re

    RULES: list[tuple[str, str, str]] = [
        ("失败", "错误", "失败"),
        ("报错", "错误", "报错"),
        ("崩溃", "错误", "崩溃"),
        ("宕机", "错误", "宕机"),
        ("OOM", "错误", "OOM"),
        ("磁盘满", "错误", "磁盘满"),
        ("断连", "错误", "断连"),
        ("拒绝连接", "错误", "拒绝连接"),
        ("无法连接", "错误", "无法连接"),
        ("连接失败", "错误", "连接失败"),
        ("超时", "可疑", "超时"),
        ("异常", "可疑", "异常"),
        ("熔断", "可疑", "熔断"),
        ("降级", "可疑", "降级"),
        ("阻塞", "可疑", "阻塞"),
        ("未找到", "可疑", "未找到"),
    ]

    EXCLUDE_CONTEXT: list[str] = [
        "讨论", "研究", "探索",              # 概念性讨论
    ]

    # 宽泛关键词遇到这些上下文词时，可能是概念讨论而非实际故障事件
    # 如"超时时间设置"、"异常处理方案"、"失败原因分析"
    CONCEPT_REDUCER: list[str] = [
        "方案", "机制", "流程", "策略", "设计",
        "处理", "规范", "配置", "设置",
        "情况", "原因", "场景",
    ]

    # EXCLUDE_CONTEXT 仅在关键词附近 5 字符范围内才跳过（避免"故障讨论记录"被误排除）
    _EXCLUDE_PROXIMITY = 5

    EXCLUDE_PATTERNS: list[str] = [
        r'\b无(?:任何)?(?:bug|报错|失败|错误|异常|问题|故障)\b',
        r'\b0\s*[个]*(?:错误|bug|报错|失败|异常)',
        r'没有(?:任何)?(?:错误|bug|报错|失败|异常|问题)',
        r'已(?:修复|解决|更正|纠正)\b',
        r'验证\s*通过', r'部署\s*(?:通过|成功|完成)',
        r'运行\s*(?:正常|通过|成功)', r'流程\s*通过',
        r'测试\s*通过',
        r'(?:正常运行|稳定)', r'(?:无|没有)(?:任何)?异常',
        r'任务\s*正常', r'日志\s*无异常',
        r'(?:一切正常|系统正常|配置正常)',
        r'(?:服务正常|连接正常)',
        r'恢复(?:正常|成功)', r'启动(?:成功|正常)',
        r'写入(?:成功|完成)', r'删除(?:成功|完成)', r'更新(?:成功|完成)',
        r'创建(?:成功|完成)', r'初始化(?:成功|完成)',
        r'(?:操作成功|处理成功|成功[完成的])',
        r'所有.*(?:通过|成功|正常)', r'全部.*(?:通过|成功|正常)',
        r'0\s*个\s*问题', r'正确运行', r'(?:已[完成实现])',
    ]

    total_marked = 0
    errors = 0

    # 宽泛关键词（会被 CONCEPT_REDUCER 语境排除）
    _BROAD_KEYS = {"失败", "错误", "异常", "超时", "阻塞", "降级"}

    for keyword, mark_type, note in RULES:
        cur = db_adapter.conn.cursor()
        cur.execute(
            "SELECT id, text FROM memory_units "
            "WHERE text ILIKE %s "
            "AND text NOT LIKE %s "
            "ORDER BY created_at DESC LIMIT 500",
            (f"%{keyword}%", "%[标记: %"),
        )
        rows = cur.fetchall()
        if not rows:
            continue

        for row in rows:
            unit_id, text = row[0], row[1] or ""
            if keyword.isascii() and keyword.isalpha():
                if not _re.search(rf"\b{_re.escape(keyword)}\b", text, _re.I):
                    continue
            else:
                if keyword.lower() not in text.lower():
                    continue
            # EXCLUDE_CONTEXT 仅在关键词附近 _EXCLUDE_PROXIMITY 字符范围内才跳过
            _kw_pos = text.lower().find(keyword) if keyword.isascii() and keyword.isalpha() else text.lower().find(keyword)
            if _kw_pos >= 0:
                _excluded = False
                for ctx in EXCLUDE_CONTEXT:
                    _ctx_pos = text.lower().find(ctx)
                    if _ctx_pos >= 0 and abs(_ctx_pos - _kw_pos) <= _EXCLUDE_PROXIMITY:
                        _excluded = True
                        break
                if _excluded:
                    continue
            # 宽泛关键词被紧邻的概念语境包围时跳过（如"异常处理方案"、"失败原因分析"）
            # 只跳过关键词与概念词在 3 字符范围内相邻的情况
            if keyword in _BROAD_KEYS:
                kpos = text.lower().find(keyword)
                if kpos >= 0:
                    _skip = False
                    for cr in CONCEPT_REDUCER:
                        cpos = text.lower().find(cr, max(0, kpos - 3))
                        if cpos >= 0 and abs(cpos - kpos) <= max(len(keyword), len(cr)):
                            _skip = True
                            break
                        cpos2 = text.lower().find(cr, kpos + len(keyword))
                        if cpos2 >= 0 and cpos2 - kpos <= len(keyword) + 3:
                            _skip = True
                            break
                    if _skip:
                        continue
            if any(_re.search(p, text, _re.I) for p in EXCLUDE_PATTERNS):
                continue
            result = mark_memory(
                db_adapter.conn, unit_id, mark_type, note=note, dry_run=dry_run, commit=False,
            )
            if result == "success":
                total_marked += 1
            elif result == "not_found":
                errors += 1

    if not dry_run:
        db_adapter.conn.commit()

    return {"total_marked": total_marked, "errors": errors}


# ===== argparse CLI =====


def cmd_search(args: argparse.Namespace) -> None:
    """search 子命令：按关键词搜索记忆单元。"""
    conn = get_connection()
    try:
        import psycopg2.extras

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        like = f"%{_escape_like(args.keyword)}%"
        cur.execute(
            "SELECT id, text, created_at FROM memory_units "
            "WHERE text ILIKE %s "
            "ORDER BY created_at DESC LIMIT %s",
            (like, args.limit),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"🔍 未找到包含 '{args.keyword}' 的记忆")
            return
        print(f"🔍 找到 {len(rows)} 条包含 '{args.keyword}' 的记忆：\n")
        for row in rows:
            uid = row["id"]
            text = row.get("text", "") or ""
            preview = text[:args.preview_len].replace("\n", " ")
            state = "🔖 已标记" if MARK_PATTERN.search(text) else "📄 未标记"
            created = row.get("created_at", "?")
            print(f"  [{uid}]")
            print(f"    时间: {created}  |  状态: {state}")
            print(f"    预览: {preview}")
            print()
    finally:
        conn.close()


def cmd_check(args: argparse.Namespace) -> None:
    """check 子命令：查询记忆标记状态。"""
    validate_uuid(args.unit_id)
    conn = get_connection()
    try:
        state = get_mark_state(conn, args.unit_id)
        if state == "not_found":
            print(f"❌ 未找到: {args.unit_id}")
            sys.exit(1)
        elif state == "no_mark":
            print(f"📄 无标记: {args.unit_id}")
        else:
            print(f"🔖 已标记: {args.unit_id}")
    finally:
        conn.close()


def cmd_mark(args: argparse.Namespace) -> None:
    """mark 子命令：标记记忆。"""
    validate_uuid(args.unit_id)
    conn = get_connection()
    try:
        is_dry_run = not args.apply
        result = mark_memory(conn, args.unit_id, args.type, args.note, dry_run=is_dry_run)
        if result == "already_marked":
            print(f"⏭️  已标记，跳过: {args.unit_id}")
        elif result == "not_found":
            print(f"❌ 未找到记忆: {args.unit_id}")
            sys.exit(1)
        elif result == "success":
            if is_dry_run:
                print(f"📄 [DRY-RUN] 将标记: {args.unit_id} [{args.type}]")
            else:
                print(f"✅ 标记成功: {args.unit_id} [{args.type}]")

        # 同步 Hermes MEMORY.md
        if args.apply_hermes and args.keyword:
            h_count = mark_hermes_memory(args.keyword, args.type, args.note, dry_run=not args.apply)
            if h_count:
                print(f"   📝 Hermes MEMORY.md: 标记 {h_count} 条")
    finally:
        conn.close()


def cmd_unmark(args: argparse.Namespace) -> None:
    """unmark 子命令：取消标记。"""
    validate_uuid(args.unit_id)
    conn = get_connection()
    try:
        is_dry_run = not args.apply
        result = unmark_memory(conn, args.unit_id, dry_run=is_dry_run)
        if result == "not_found":
            print(f"❌ 未找到记忆: {args.unit_id}")
            sys.exit(1)
        elif result == "no_mark":
            print(f"📄 无标记可移除: {args.unit_id}")
        elif result == "success":
            if is_dry_run:
                print(f"📄 [DRY-RUN] 将取消标记: {args.unit_id}")
            else:
                print(f"✅ 取消标记成功: {args.unit_id}")

        if args.apply_hermes and args.keyword:
            h_count = unmark_hermes_memory(args.keyword, dry_run=not args.apply)
            if h_count:
                print(f"   📝 Hermes MEMORY.md: 取消标记 {h_count} 条")
    finally:
        conn.close()


def cmd_hermes_search(args: argparse.Namespace) -> None:
    """hermes-search 子命令：搜索 MEMORY.md。"""
    if not os.path.exists(HERMES_MEMORY_PATH):
        print(f"⚠️  Hermes Memory 文件不存在: {HERMES_MEMORY_PATH}", file=sys.stderr)
        sys.exit(1)
    entries = _read_hermes_entries(HERMES_MEMORY_PATH)
    matched = []
    for i, entry in enumerate(entries):
        if _keyword_matches(args.keyword, entry):
            matched.append((i, entry))
    if not matched:
        print(f"📝 未找到包含 '{args.keyword}' 的 Hermes Memory 条目")
        return
    print(f"📝 [Hermes Memory] 匹配 {len(matched)} 条:\n")
    for idx, entry in matched:
        preview = entry[:args.preview_len].replace("\n", " ")
        mark_tag = " 🔖" if "[标记:" in entry else ""
        print(f"  [条目{idx}]{mark_tag} {preview}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mark_memory",
        description="Hindsight 记忆标记工具 — 追加/移除/查询/搜索记忆标记",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # search
    p_search = sub.add_parser("search", help="按关键词搜索 Hindsight 记忆单元")
    p_search.add_argument("keyword", help="搜索关键词")
    p_search.add_argument("--limit", type=int, default=20, help="最大返回条数（默认 20）")
    p_search.add_argument("--preview-len", type=int, default=120, help="预览截断长度（默认 120）")
    p_search.set_defaults(func=cmd_search)

    # check
    p_check = sub.add_parser("check", help="查询一条记忆的标记状态")
    p_check.add_argument("unit_id", help="记忆 UUID")
    p_check.set_defaults(func=cmd_check)

    # mark
    p_mark = sub.add_parser("mark", help="追加标记")
    p_mark.add_argument("unit_id", help="记忆 UUID")
    p_mark.add_argument("type", choices=["错误", "作废", "可疑", "已解决", "待验证"], help="标记类型")
    p_mark.add_argument("note", nargs="?", default="", help="可选说明")
    p_mark.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    p_mark.add_argument("--apply-hermes", action="store_true", help="同步标记 Hermes MEMORY.md")
    p_mark.add_argument("--keyword", help="同步 MEMORY.md 时的关键词匹配")
    p_mark.set_defaults(func=cmd_mark)

    # unmark
    p_unmark = sub.add_parser("unmark", help="移除标记")
    p_unmark.add_argument("unit_id", help="记忆 UUID")
    p_unmark.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    p_unmark.add_argument("--apply-hermes", action="store_true", help="同步取消标记 Hermes MEMORY.md")
    p_unmark.add_argument("--keyword", help="同步 MEMORY.md 时的关键词匹配")
    p_unmark.set_defaults(func=cmd_unmark)

    # hermes-search
    ph = sub.add_parser("hermes-search", help="搜索 Hermes MEMORY.md（模糊匹配）")
    ph.add_argument("keyword", help="搜索关键词")
    ph.add_argument("--preview-len", type=int, default=200, help="预览截断长度（默认 200）")
    ph.set_defaults(func=cmd_hermes_search)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()