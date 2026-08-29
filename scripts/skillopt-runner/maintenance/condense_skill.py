#!/usr/bin/env python3
"""一次性 SKILL.md 瘦身（LLM 压缩重写）。

背景：skillopt 的无标记 append 让 33 个活跃 skill 文档膨胀超限（>30k 字符），
重复章节 2~3 份、配置 dump 数十行、待复核块堆积 —— 文档塞进上下文后信号被稀释。

用法（WSL 内执行）:
  python3 condense_skill.py --top 3              # dry-run：按体积取前 3，写预览
  python3 condense_skill.py --skill mlops/hindsight-memory --apply
  python3 condense_skill.py --all --apply        # 全部超限 skill

安全机制：
  - 默认 dry-run，预览写到 HERMES_HOME/backups/condense-preview-<tag>/
  - --apply 前把原文备份到 HERMES_HOME/backups/condense-<tag>/<skill>/SKILL.md
  - 产物校验：长度上限 target*1.3、非空、保留 markdown 结构；失败不写盘
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

HERMES_HOME = pathlib.Path(os.environ.get('HERMES_HOME', '/root/.hermes'))
SKILLS_DIR = HERMES_HOME / 'skills'
TAG = '20260829'
TARGET_DEFAULT = 11000          # 压缩目标（字符），略低于 soft limit 12000
MAX_INPUT_CHARS = 400_000       # 单文档输入上限（保险丝）
HARD_MIN_CHARS = 800            # 产物最小长度：低于此视为 LLM 输出异常

MODEL = os.environ.get('CONDENSE_MODEL', 's-glm-5.2')
ENDPOINT = os.environ.get('CONDENSE_ENDPOINT', 'http://127.0.0.1:4142/v1')

PROMPT = """你是一个技能文档（SKILL.md）压缩专家。下面这份文档因为反复追加而严重膨胀，\
请把它重写为一份精炼、可直接使用的技能文档。

## 硬性要求
1. 输出必须是完整的 markdown 文档，不带任何解释、前后缀或代码围栏包裹。
2. 若开头有 YAML frontmatter（--- 包裹的元数据），保留在最前面；\
description 字段如有重复拼接的片段请清理成一句通顺描述。
3. 目标长度：{target} 字符以内。信息密度优先，宁缺毋滥。
4. 合并重复章节：同一主题出现多次（如同名标题出现 2~3 次），合并为一份，取信息并集。
5. 删除：标记为"待人工复核/待确认"的追加块、历史迁移记录、Self-Evolving 修正块、\
与正文重复的示例、过时的日期性说明。
6. 压缩超长配置/代码 dump（>15 行的代码块）：保留关键命令与参数为简短片段，\
其余概括为一行说明。
7. 逐条保留：所有独有的坑（pitfall）、报错与解法、命令行、路径、参数名、规则性结论。\
这些是文档的核心价值，一条都不能丢。
8. 保持原有语言（中文写的内容用中文，英文的用英文）。

## 原文档
"""


def load_env():
    """把 HERMES_HOME/.env 的 KEY=VALUE 注入 os.environ（不覆盖已有值）。"""
    env_file = HERMES_HOME / '.env'
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def get_client():
    from openai import OpenAI
    key = os.environ.get('LITELLM_MASTER_KEY', '')
    if not key:
        sys.exit('错误：LITELLM_MASTER_KEY 未设置（检查 .env）')
    return OpenAI(base_url=ENDPOINT, api_key=key, timeout=900, max_retries=3)


def llm_condense(client, text: str, target: int, *, strict: bool = False) -> str:
    prompt = PROMPT.format(target=f'{target:,}')
    if strict:
        prompt += (f'\n\n【上一稿仍超长】这次必须压到 {target:,} 字符以内，'
                   f'进一步删除次要细节，保留所有坑与命令。\n\n## 原文档\n')
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{'role': 'user', 'content': prompt + text}],
        max_completion_tokens=32768,
    )
    out = (resp.choices[0].message.content or '').strip()
    # 剥掉可能的围栏包裹
    if out.startswith('```'):
        out = out.split('\n', 1)[1] if '\n' in out else out
        if out.rstrip().endswith('```'):
            out = out.rstrip()[:-3]
    return out.strip()


def find_skill_md(name: str) -> pathlib.Path | None:
    """按 变体名/裸名 在 skills 下解析 SKILL.md（排除 .archive）。"""
    direct = SKILLS_DIR / name / 'SKILL.md'
    if direct.exists():
        return direct
    bare = name.split('/')[-1]
    for p in SKILLS_DIR.rglob('SKILL.md'):
        if '.archive' in p.parts:
            continue
        if p.parent.name == bare:
            return p
    return None


def oversized(top: int | None) -> list[pathlib.Path]:
    docs = [p for p in SKILLS_DIR.rglob('SKILL.md')
            if '.archive' not in p.parts and p.stat().st_size > 30000]
    docs.sort(key=lambda p: -p.stat().st_size)
    return docs[:top] if top else docs


def validate(orig_len: int, new: str, target: int) -> tuple[bool, str]:
    if len(new) < HARD_MIN_CHARS:
        return False, f'产物仅 {len(new)} 字符，疑似 LLM 输出异常'
    if '#' not in new:
        return False, '产物无 markdown 标题结构'
    if len(new) > target * 1.3:
        return False, f'产物 {len(new):,} 字符仍超上限（target={target:,}）'
    if len(new) > orig_len * 0.9:
        return False, f'产物 {len(new):,} 几乎没有压缩（原文 {orig_len:,}）'
    return True, 'OK'


def process(client, path: pathlib.Path, target: int, apply: bool) -> dict:
    rel = path.parent.relative_to(SKILLS_DIR)
    orig = path.read_text(encoding='utf-8')
    name = path.parent.name
    if len(orig) <= target:
        return {'skill': str(rel), 'status': 'skip', 'note': f'仅 {len(orig):,} 字符无需瘦身'}

    t0 = time.time()
    new = llm_condense(client, orig, target)
    ok, note = validate(len(orig), new, target)
    if not ok:
        # 第二稿：更激进
        new2 = llm_condense(client, orig, target, strict=True)
        ok2, note2 = validate(len(orig), new2, target)
        if ok2:
            new, ok, note = new2, True, note2

    dt = time.time() - t0
    if not ok:
        return {'skill': str(rel), 'status': 'reject',
                'note': f'{note}（{dt:.0f}s）', 'orig': len(orig), 'new': len(new)}

    if apply:
        bkdir = HERMES_HOME / 'backups' / f'condense-{TAG}' / rel
        bkdir.mkdir(parents=True, exist_ok=True)
        bk_file = bkdir / 'SKILL.md'
        if not bk_file.exists():       # 重复 apply 不覆盖原始备份
            bk_file.write_text(orig, encoding='utf-8')
        path.write_text(new, encoding='utf-8')
        status = 'applied'
    else:
        pvdir = HERMES_HOME / 'backups' / f'condense-preview-{TAG}' / rel
        pvdir.mkdir(parents=True, exist_ok=True)
        (pvdir / 'SKILL.md').write_text(new, encoding='utf-8')
        status = 'preview'

    return {'skill': str(rel), 'status': status, 'note': f'{note}（{dt:.0f}s）',
            'orig': len(orig), 'new': len(new),
            'ratio': f'{len(new) / len(orig) * 100:.0f}%'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skill', action='append', default=[],
                    help='指定 skill（相对 skills/ 的名字或裸名，可重复）')
    ap.add_argument('--top', type=int, default=None, help='按体积取前 N 个')
    ap.add_argument('--all', action='store_true', help='所有超限 skill')
    ap.add_argument('--apply', action='store_true', help='真写盘（默认 dry-run 预览）')
    ap.add_argument('--target', type=int, default=TARGET_DEFAULT)
    args = ap.parse_args()

    load_env()
    client = get_client()

    if args.skill:
        paths = []
        for s in args.skill:
            p = find_skill_md(s)
            if p is None:
                print(f'!! 找不到 skill: {s}')
                sys.exit(2)
            paths.append(p)
    elif args.all:
        paths = oversized(None)
    else:
        paths = oversized(args.top or 3)

    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print(f'== SKILL.md 瘦身 [{mode}] model={MODEL} target={args.target:,} '
          f'待处理 {len(paths)} 个 ==\n')
    results = []
    for i, p in enumerate(paths, 1):
        rel = p.parent.relative_to(SKILLS_DIR)
        print(f'[{i}/{len(paths)}] {rel} ({p.stat().st_size:,} chars) ...', flush=True)
        try:
            r = process(client, p, args.target, args.apply)
        except Exception as e:                     # noqa: BLE001
            r = {'skill': str(rel), 'status': 'error', 'note': repr(e)[:160]}
        print(f"    → {r['status']}: {r.get('note', '')}", flush=True)
        results.append(r)

    print(f'\n{"=" * 64}\n汇总：')
    ok_rows = [r for r in results if r['status'] in ('applied', 'preview')]
    for r in results:
        line = f"  [{r['status']:>7}] {r['skill']}"
        if 'orig' in r:
            line += f"  {r['orig']:,} → {r['new']:,} ({r.get('ratio', '')})"
        print(line)
    total_before = sum(r.get('orig', 0) for r in ok_rows)
    total_after = sum(r.get('new', 0) for r in ok_rows)
    print(f'\n成功 {len(ok_rows)}/{len(results)}；'
          f'压缩量 {total_before:,} → {total_after:,} 字符'
          + (f'（释放 {total_before - total_after:,}）' if total_before else ''))
    if not args.apply and ok_rows:
        print(f'预览位于: {HERMES_HOME / "backups" / f"condense-preview-{TAG}"}')


if __name__ == '__main__':
    main()
