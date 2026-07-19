"""验证 CLI 端到端功能

用法:
    python tests/verify_cli.py [docx1] [docx2]
    不传参数则使用系统 tempdir 下的默认文件（与脚本 export_docx.py 的输出路径一致）。
"""
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def read_docx_text(docx_path: Path) -> str:
    with zipfile.ZipFile(docx_path, "r") as z:
        tree = ET.parse(z.open("word/document.xml"))
    return "\n".join(
        t.text or "" for t in tree.getroot().findall(".//w:t", NS)
    )


def has_toc_field(docx_path: Path) -> bool:
    with zipfile.ZipFile(docx_path, "r") as z:
        xml = z.read("word/document.xml").decode("utf-8")
    return "TOC" in xml


def main() -> int:
    tmp_dir = Path(tempfile.gettempdir())

    # 允许通过命令行参数覆盖路径
    docx1 = Path(sys.argv[1]) if len(sys.argv) > 1 else tmp_dir / "test_sub.docx"
    docx2 = Path(sys.argv[2]) if len(sys.argv) > 2 else tmp_dir / "test_notoc.docx"

    tests: list[tuple[str, bool]] = []

    # 测试 1：subtitle 进入 docx
    if docx1.exists():
        text = read_docx_text(docx1)
        tests.append(("subtitle 进入 docx", "副标题XYZ" in text))
        tests.append(("title 进入 docx", "主标题ABC" in text))
    else:
        print(f"⚠️  跳过测试 1：文件不存在 {docx1}")
        tests.append(("subtitle docx 存在", False))

    # 测试 2：--no-toc 不生成 TOC
    if docx2.exists():
        tests.append(("--no-toc 不生成 TOC", not has_toc_field(docx2)))
    else:
        print(f"⚠️  跳过测试 2：文件不存在 {docx2}")
        tests.append(("--no-toc docx 存在", False))

    # 打印结果
    print("\n=== CLI 端到端验证结果 ===")
    passed = 0
    for name, ok in tests:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")
        if ok:
            passed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
