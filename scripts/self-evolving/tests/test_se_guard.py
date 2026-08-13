import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from skill_patch import security_scan, patch_skill_md

cases = [
    ("sudo 在代码块", "```\nsudo reboot now\n```", False),
    ("eval( 在代码块 中行", "```\nresult = eval('1+1')\n```", False),
    ("exec( 在代码块", "```\ndata = exec(payload)\n```", False),
    ("prose 提 eval（应放行）", "请用 eval 评估一下效果，不要 sudo", True),
    ("纯良性正文", "这里补充一条知识要点。", True),
]
allok = True
for name, text, expect in cases:
    ok, reason = security_scan(text)
    passed = (ok == expect)
    allok &= passed
    print(f"  [{'OK' if passed else 'FAIL'}] {name}: safe={ok} ({reason}) expect={expect}")

home = tempfile.mkdtemp()
sk = Path(home) / "skills" / "s" / "SKILL.md"
sk.parent.mkdir(parents=True, exist_ok=True)
sk.write_text("---\nname: s\n---\n# s\nbody\n", encoding="utf-8")
r1 = patch_skill_md("s", "```\nval = eval(user_input)\n```", task_id="x1", home=home)
print(f"  eval( 写回 -> {r1} (应 False)")
print("GUARD_TEST", "ALL_OK" if (allok and r1 is False) else "HAS_FAILURE")
