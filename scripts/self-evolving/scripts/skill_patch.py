"""skill_patch.py — 安全的 SKILL.md 写回（self-evolving 专用，自包含）。

设计目标（见 docs/architecture/flywheel-full-optimization-2026-08-11.md F-5 + B）：
  把 Self-Evolving 的 refine 产出（refined_content）写回对应 skill 的 SKILL.md，
  闭环能力飞轮。安全特性对齐 skillopt_runner.patch_skill_hermes：
    - 保留 frontmatter；只把修正内容 append 到正文（不覆盖原 skill）。
    - 安全护栏 HARD_BLOCK（命令注入 / AWS key / prompt injection 等），拦截即拒绝。
    - 按 task_id 去重：同一失败任务重复跑只保留一份应用记录，避免无限堆积。
    - 原子写：先备份，写盘失败回滚备份。

2026-08-29 A+B 改造补充（与 skillopt 的护栏对齐）：
    - 长度上限：软 12k / 硬 30k 字符，超硬上限拒绝写回（提示需人工整合）。
      此前本模块无上限，导致 hindsight-memory 被堆到 12 万字符（全库中位数 8.6k）。
    - 空变更跳过：新内容与已有 SE-APPLIED 块逐字相同时不写盘、不产生备份。
      此前每晚重跑同一批 task 都会重写文件并留下备份。

注意：本模块刻意放在 self-evolving 本地（scripts/self-evolving/scripts/）。
LLM 护栏统一来自部署到生产机的 hermes_common（路径 /root/.hermes/lib/hermes_common，
经 `deploy.sh deploy hermes-common` 部署）；self-evolving 的各 LLM 客户端通过
_load_common_llm_guard() 加载该唯一来源，hermes_common 是稳定且必须部署的依赖。
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

# ── SKILL.md 长度上限（与 skillopt_runner 的护栏对齐）──────────────────────
# 软上限：超过只 WARN，仍允许写回。
# 硬上限：超过直接拒绝写回。此时该 skill 需要人工整合，而不是继续机器追加。
# 可用环境变量 SE_SKILL_SOFT_MAX / SE_SKILL_HARD_MAX 覆盖。
SOFT_MAX_CHARS = 12000
HARD_MAX_CHARS = 30000

# 「待人工复核」的 SE 块累积数量上限。
# 绝对长度会误伤「本来就大」的文档（如 hermes-agent 8.6 万字符、0 个 SE 块）；
# 占比规则会误伤小文档（163 字符的文档加一个 80 字符的块就是 50%）。
# 块数最能表达真实意图：这些块标题都写着「待人工复核」，若累积到 8 个仍无人
# 复核，继续追加只会加重欠账，应当停下等人。
# 可用环境变量 SE_MAX_BLOCK_COUNT 覆盖。
MAX_BLOCK_COUNT = 8


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


def _skill_dir_exists(skill_name: str, home: Optional[str] = None) -> bool:
    """判断 skill 目录是否存在（区分「未安装」与「已安装但缺 SKILL.md」）。"""
    root = Path(home or os.environ.get('HERMES_HOME') or '/root/.hermes')
    candidate_roots = [root / 'skills']
    plugins_root = root / 'plugins'
    if plugins_root.is_dir():
        for entry in plugins_root.iterdir():
            if entry.is_dir():
                candidate_roots.append(entry / 'skills')
    for base in candidate_roots:
        if base.is_dir() and (base / skill_name).is_dir():
            return True
    return False


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


@dataclass
class PatchResult:
    """写回结果。ok=True 表示目标状态已达成（含「内容未变、无需重写」）。"""
    ok: bool
    status: str = ''      # applied | unchanged | rejected
    reason: str = ''
    size: int = 0         # 写回后（或未变更时的）文件总字符数


def get_char_limits() -> Tuple[int, int]:
    """SKILL.md 长度软/硬上限，可用环境变量覆盖。"""
    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name, '')
        if raw:
            try:
                v = int(raw)
                if v > 0:
                    return v
            except ValueError:
                pass
        return default
    return (_int('SE_SKILL_SOFT_MAX', SOFT_MAX_CHARS),
            _int('SE_SKILL_HARD_MAX', HARD_MAX_CHARS))


def get_block_count_limit() -> int:
    """「待人工复核」SE 块的累积数量上限，可用环境变量覆盖。"""
    raw = os.environ.get('SE_MAX_BLOCK_COUNT', '')
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return MAX_BLOCK_COUNT


def count_blocks(text: str) -> int:
    """全文里 SE-APPLIED 块的数量。"""
    return sum(1 for _ in _SE_BLOCK_RE.finditer(text))


def _find_block(task_id: str, body: str) -> Optional[re.Match]:
    """定位 body 中属于该 task_id 的 SE-APPLIED 块。"""
    for m in _SE_BLOCK_RE.finditer(body):
        if m.group(1) == task_id:
            return m
    return None


def _block_payload(block: str) -> str:
    """剥掉块的标记行与标题行，返回可比对的正文。"""
    keep: list[str] = []
    for ln in block.split('\n'):
        s = ln.strip()
        if s.startswith('<!-- SE-APPLIED') or s.startswith('<!-- /SE-APPLIED -->'):
            continue
        if s.startswith('### '):
            continue
        keep.append(ln)
    return '\n'.join(keep).strip()


def patch_skill_md_detailed(
    skill_name: str,
    new_content: str,
    *,
    task_id: str = 'n/a',
    home: Optional[str] = None,
    backup_dir: Optional[str] = None,
) -> PatchResult:
    """将 refined_content 安全写回 skill 的 SKILL.md（详细结果版）。

    行为：
      0) 空变更短路：若与已有 SE-APPLIED 块内容逐字相同，直接返回 unchanged，
         不写盘、不产生备份。
      1) 安全扫描（HARD_BLOCK）→ 不通过直接拒绝。
      2) 定位 SKILL.md（rglob）。
      3) 保留 frontmatter，把修正内容 append 到正文，写入一个带 task_id 的
         去重块；同一 task_id 旧块先被清除。
      4) 长度护栏：软上限只 WARN，超硬上限拒绝写回。
      5) 先备份再原子写，失败回滚。

    Returns:
        PatchResult — ok 表示目标状态已达成（applied 或 unchanged）。
    """
    payload = new_content.strip()
    if not payload:
        return PatchResult(False, 'rejected', '内容为空', 0)

    is_safe, reason = security_scan(new_content)
    if not is_safe:
        print(f'SECURITY: 拒绝写回 {skill_name}: {reason}')
        return PatchResult(False, 'rejected', reason, 0)

    p = find_skill_md(skill_name, home)
    if not p:
        # 目标缺失：降级为 WARN（不计入 ERROR 监控），避免污染健康报告错误统计；
        # driver 已将该跳过计入 ledger blocked（属预期跳过，非故障吞任务）。
        if _skill_dir_exists(skill_name, home):
            print(f'WARN: 跳过写回（目标 SKILL.md 缺失）: {skill_name}')
        else:
            print(f'WARN: 跳过写回（目标 skill 未安装）: {skill_name}')
        return PatchResult(False, 'rejected', 'SKILL.md 未找到', 0)

    try:
        existing = p.read_text(encoding='utf-8')
    except OSError as e:
        print(f'ERROR: 读取 SKILL.md 失败 {skill_name}: {e}')
        return PatchResult(False, 'rejected', f'读取失败: {e}', 0)

    # 解析 frontmatter
    fm_end = None
    if existing.lstrip().startswith('---'):
        stripped = existing.lstrip()
        first_end = stripped.find('---', 3)
        if first_end > 0:
            fm_end = len(existing) - len(stripped) + first_end + 3
    if fm_end is None:
        print(f'ERROR: 无法解析 frontmatter: {skill_name}')
        return PatchResult(False, 'rejected', '无法解析 frontmatter', 0)

    frontmatter = existing[:fm_end]
    body = existing[fm_end:]

    # ── 空变更短路：与已有块逐字相同则不写盘 ──
    old_block = _find_block(task_id, body)
    if old_block is not None and _block_payload(old_block.group(0)) == payload:
        print(f'SKIP: 内容未变化，跳过写回 {skill_name} (task {task_id})')
        return PatchResult(True, 'unchanged', '内容未变化', len(existing))

    # 去除该 task_id 的旧块（去重）
    def _replace(m: re.Match) -> str:
        return '' if m.group(1) == task_id else m.group(0)
    body = _SE_BLOCK_RE.sub(_replace, body)
    body = body.rstrip('\n')

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    block = (
        f'\n\n<!-- SE-APPLIED id={task_id} ts={ts} -->\n'
        f'### 🔄 Self-Evolving 修正（task {task_id}，待人工复核）\n\n'
        f'{payload}\n'
        f'<!-- /SE-APPLIED -->'
    )
    merged = frontmatter + '\n\n' + body.strip() + block + '\n'

    # ── 长度护栏（与 skillopt 的软/硬上限对齐）──
    soft_max, hard_max = get_char_limits()
    total = len(merged)
    if total > hard_max:
        print(f'LIMIT: 拒绝写回 {skill_name} — 写回后 {total} 字符 > 硬上限 '
              f'{hard_max}，该 skill 需人工整合后再启用自动写回')
        return PatchResult(False, 'rejected',
                           f'超硬上限 {total}>{hard_max}', total)
    if total > soft_max:
        print(f'WARN: {skill_name} 写回后 {total} 字符 > 软上限 {soft_max}，'
              f'接近需人工整合的临界点')

    # ── 待复核块数护栏：累积过多「待人工复核」块则停止追加 ──
    block_limit = get_block_count_limit()
    n_blocks = count_blocks(merged)
    if n_blocks > block_limit:
        print(f'BLOCKS: 拒绝写回 {skill_name} — 待复核的 SE 块已达 '
              f'{n_blocks} 个 > 上限 {block_limit}，先人工复核再启用自动写回')
        return PatchResult(False, 'rejected',
                           f'待复核块过多 {n_blocks}>{block_limit}', total)

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
        return PatchResult(False, 'rejected', f'写盘失败: {e}', total)

    print(f'SUCCESS: 已写回 {p}（备份 {bak}）')
    return PatchResult(True, 'applied', 'ok', total)


def patch_skill_md(
    skill_name: str,
    new_content: str,
    *,
    task_id: str = 'n/a',
    home: Optional[str] = None,
    backup_dir: Optional[str] = None,
) -> bool:
    """向后兼容的 bool 包装：True 表示目标状态已达成（含「内容未变」）。

    需要区分 applied / unchanged / rejected 的调用方请用 patch_skill_md_detailed。
    """
    return patch_skill_md_detailed(
        skill_name, new_content, task_id=task_id,
        home=home, backup_dir=backup_dir,
    ).ok
