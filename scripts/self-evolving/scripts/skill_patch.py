"""skill_patch.py — 安全的 SKILL.md 写回（self-evolving 专用，自包含）。

设计目标（见 docs/architecture/flywheel-full-optimization-2026-08-11.md F-5 + B）：
  把 Self-Evolving 的 refine 产出（refined_content）写回对应 skill 的 SKILL.md，
  闭环能力飞轮。安全特性对齐 skillopt_runner.patch_skill_hermes：
    - 保留 frontmatter；只把修正内容 append 到正文（不覆盖原 skill）。
    - 安全护栏 HARD_BLOCK（命令注入 / AWS key / prompt injection 等），拦截即拒绝。
    - 按 task_id 去重：同一失败任务重复跑只保留一份应用记录，避免无限堆积。
    - 原子写：先备份，写盘失败回滚备份。

注意：本模块刻意放在 self-evolving 本地（scripts/self-evolving/scripts/），
不依赖未部署到生产机的 scripts/common，保证 self-evolving 自包含可独立运行。
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Tuple


def security_scan(content: str) -> Tuple[bool, str]:
    """语义安全护栏：区分「高置信危险」与「需代码语境才危险」。

    Returns:
        (is_safe, reason) — is_safe=True 通过；reason 为失败原因。
    """
    # 高置信危险：无论上下文，始终拦截
    HARD_BLOCK: list[Tuple[re.Pattern[str], str]] = [
        (re.compile(r'rm\s+-rf\s+/', re.IGNORECASE), '危险的 rm -rf 路径'),
        (re.compile(r'curl\s+[^|]*\|\s*bash', re.IGNORECASE), 'curl | bash 远程执行'),
        (re.compile(r'wget\s+[^|]*\|\s*bash', re.IGNORECASE), 'wget | bash 远程执行'),
        (re.compile(r'AKIA[0-9A-Z]{16}'), '疑似 AWS access key'),
        (re.compile(r'(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions?'),
         '疑似 prompt injection: ignore instructions'),
        (re.compile(r'(?i)you\s+are\s+now\s+(a|an)\s+'),
         '疑似 prompt injection: 角色重定义'),
        (re.compile(r'(?i)disregard\s+(all\s+)?(safety|security|ethical)'),
         '疑似 prompt injection: 绕过安全'),
    ]
    for pattern, reason in HARD_BLOCK:
        if pattern.search(content):
            return False, f'安全扫描失败: {reason}'

    # 语境相关：仅在「代码块 / shell 行」中拦截 sudo/eval(/exec(，降低 prose 误杀
    has_code_fence = '```' in content or '~~~' in content
    has_shell = bool(re.search(r'(?m)^\s*[\$>]\s*\S', content)) or bool(
        re.search(r'(?m)^\s*(sudo|rm\b|curl|wget|bash|sh|python3?|eval|exec)\b', content))
    if has_code_fence or has_shell:
        CODE_CONTEXT: list[Tuple[re.Pattern[str], str]] = [
            (re.compile(r'(?m)^\s*sudo\b', re.IGNORECASE),
             '代码/命令语境中的 sudo'),
            (re.compile(r'\b(eval|exec)\s*\(\s*', re.IGNORECASE),
             '代码/命令语境中的 eval(/exec('),
        ]
        for pattern, reason in CODE_CONTEXT:
            if pattern.search(content):
                return False, f'安全扫描失败: {reason}'
    return True, 'OK'


def find_skill_md(skill_name: str, home: Optional[str] = None) -> Optional[Path]:
    """定位 skill 的 SKILL.md（兼容分类嵌套目录 + plugins/*/skills 结构）。"""
    root = Path(home or os.environ.get('HERMES_HOME') or '/root/.hermes')
    # 搜索根：顶层 skills + 各 plugin 下的 skills
    candidate_roots = [root / 'skills']
    plugins_root = root / 'plugins'
    if plugins_root.is_dir():
        for entry in plugins_root.iterdir():
            if entry.is_dir():
                candidate_roots.append(entry / 'skills')
    for base in candidate_roots:
        if not base.is_dir():
            continue
        for candidate in base.rglob(f'{skill_name}/SKILL.md'):
            return candidate
    return None


_SE_BLOCK_RE = re.compile(
    r'<!-- SE-APPLIED id=([^>\s]+).*?-->\n.*?\n<!-- /SE-APPLIED -->',
    re.DOTALL,
)


def patch_skill_md(
    skill_name: str,
    new_content: str,
    *,
    task_id: str = 'n/a',
    home: Optional[str] = None,
    backup_dir: Optional[str] = None,
) -> bool:
    """将 refined_content 安全写回 skill 的 SKILL.md。

    行为：
      1) 安全扫描（HARD_BLOCK）→ 不通过直接拒绝。
      2) 定位 SKILL.md（rglob）。
      3) 保留 frontmatter，把修正内容 append 到正文，写入一个带 task_id 的
         去重块；同一 task_id 旧块先被清除。
      4) 先备份再原子写，失败回滚。

    Returns:
        True 表示成功写回；False 表示被拒绝/未找到/写盘失败。
    """
    is_safe, reason = security_scan(new_content)
    if not is_safe:
        print(f'SECURITY: 拒绝写回 {skill_name}: {reason}')
        return False

    p = find_skill_md(skill_name, home)
    if not p:
        print(f'ERROR: 找不到 SKILL.md: {skill_name}')
        return False

    try:
        existing = p.read_text(encoding='utf-8')
    except OSError as e:
        print(f'ERROR: 读取 SKILL.md 失败 {skill_name}: {e}')
        return False

    # 解析 frontmatter
    fm_end = None
    if existing.lstrip().startswith('---'):
        stripped = existing.lstrip()
        first_end = stripped.find('---', 3)
        if first_end > 0:
            fm_end = len(existing) - len(stripped) + first_end + 3
    if fm_end is None:
        print(f'ERROR: 无法解析 frontmatter: {skill_name}')
        return False

    frontmatter = existing[:fm_end]
    body = existing[fm_end:]

    # 去除该 task_id 的旧块（去重）
    def _replace(m: re.Match) -> str:
        return '' if m.group(1) == task_id else m.group(0)
    body = _SE_BLOCK_RE.sub(_replace, body)
    body = body.rstrip('\n')

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    block = (
        f'\n\n<!-- SE-APPLIED id={task_id} ts={ts} -->\n'
        f'### 🔄 Self-Evolving 修正（task {task_id}，待人工复核）\n\n'
        f'{new_content.strip()}\n'
        f'<!-- /SE-APPLIED -->'
    )
    merged = frontmatter + '\n\n' + body.strip() + block + '\n'

    # 备份 + 原子写
    resolved_home = home or os.environ.get('HERMES_HOME') or '/root/.hermes'
    bdir = Path(backup_dir or (Path(resolved_home) / 'skills_backup'))
    bdir.mkdir(parents=True, exist_ok=True)
    safe_name = skill_name.replace('/', '-')
    bak = bdir / f'{safe_name}_{datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H-%M-%S")}.md.bak'
    try:
        shutil.copy2(p, bak)
        p.write_text(merged, encoding='utf-8')
    except Exception as e:  # noqa: BLE001
        print(f'ERROR: 写回失败，尝试回滚: {e}')
        try:
            shutil.copy2(bak, p)
        except Exception:
            pass
        return False

    print(f'SUCCESS: 已写回 {p}（备份 {bak}）')
    return True
