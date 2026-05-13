"""구조 재편 마이그레이션 — import 경로 일괄 치환 후 복사"""
import shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 치환 규칙: (old, new) ─────────────────────────────────────────
RULES = [
    # src 패키지
    ("from src.hwpx.latex_to_hwp",   "from src.common.latex_to_hwp"),
    ("from src.ocr.mathpix_client",   "from src.common.ocr.mathpix_client"),
    ("from src.hwpx.builder",         "from src.template_based.builder"),
    ("from src.hwpx.change_log",      "from src.template_based.change_log"),
    ("from src.hwpx.slot_analyzer",   "from src.template_based.slot_analyzer"),
    ("from src.hwpx.pdf_filler",      "from src.template_based.pdf_filler"),
    ("from src.ocr.pdf_parser",       "from src.template_based.pdf_parser"),
    # clone_template 임포트 (auto.py 전용)
    ("from clone_template import clone_template as do_clone",
     "from src.template_based.clone_template import clone_template as do_clone"),
]

# sys.path 깊이 수정
SYSPATH_1DEEP  = 'sys.path.insert(0, str(Path(__file__).parent.parent))'
SYSPATH_2DEEP  = 'sys.path.insert(0, str(Path(__file__).parent.parent.parent))'

def transform(text: str, extra_rules: list[tuple[str,str]] | None = None, deeper: bool = False) -> str:
    for old, new in RULES:
        text = text.replace(old, new)
    if extra_rules:
        for old, new in extra_rules:
            text = text.replace(old, new)
    if deeper:
        text = text.replace(SYSPATH_1DEEP, SYSPATH_2DEEP)
    return text

def copy_transformed(src: Path, dst: Path, extra_rules=None, deeper=False):
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding='utf-8')
    text = transform(text, extra_rules, deeper)
    dst.write_text(text, encoding='utf-8')
    print(f"  {src.relative_to(ROOT)}  →  {dst.relative_to(ROOT)}")

print("=== src/ 이동 ===")

# 1. common/ocr/mathpix_client.py  (변경 없음)
copy_transformed(
    ROOT / "src/ocr/mathpix_client.py",
    ROOT / "src/common/ocr/mathpix_client.py",
)

# 2. common/latex_to_hwp.py  (변경 없음)
copy_transformed(
    ROOT / "src/hwpx/latex_to_hwp.py",
    ROOT / "src/common/latex_to_hwp.py",
)

# 3. template_based/builder.py
copy_transformed(
    ROOT / "src/hwpx/builder.py",
    ROOT / "src/template_based/builder.py",
)

# 4. template_based/pdf_filler.py
copy_transformed(
    ROOT / "src/hwpx/pdf_filler.py",
    ROOT / "src/template_based/pdf_filler.py",
)

# 5. template_based/change_log.py  (변경 없음)
copy_transformed(
    ROOT / "src/hwpx/change_log.py",
    ROOT / "src/template_based/change_log.py",
)

# 6. template_based/unpacker.py  (변경 없음)
copy_transformed(
    ROOT / "src/hwpx/unpacker.py",
    ROOT / "src/template_based/unpacker.py",
)

# 7. template_based/slot_analyzer.py  (변경 없음)
copy_transformed(
    ROOT / "src/hwpx/slot_analyzer.py",
    ROOT / "src/template_based/slot_analyzer.py",
)

# 8. template_based/pdf_parser.py
copy_transformed(
    ROOT / "src/ocr/pdf_parser.py",
    ROOT / "src/template_based/pdf_parser.py",
)

# 9. template_based/clone_template.py  (scripts/에서 이동)
copy_transformed(
    ROOT / "scripts/clone_template.py",
    ROOT / "src/template_based/clone_template.py",
)

print()
print("=== scripts/ 이동 ===")

# 10. scripts/template/pdf_to_hwpx.py
copy_transformed(
    ROOT / "scripts/pdf_to_hwpx.py",
    ROOT / "scripts/template/pdf_to_hwpx.py",
    deeper=True,
)

# 11. scripts/template/auto.py
# ROOT 계산: parent.parent → parent.parent.parent
# CONVERT_SCRIPT: scripts/pdf_to_hwpx.py → scripts/template/pdf_to_hwpx.py
copy_transformed(
    ROOT / "scripts/auto.py",
    ROOT / "scripts/template/auto.py",
    extra_rules=[
        ("ROOT          = Path(__file__).resolve().parent.parent\n",
         "ROOT          = Path(__file__).resolve().parent.parent.parent\n"),
        ('CONVERT_SCRIPT = SCRIPTS_DIR / "pdf_to_hwpx.py"',
         'CONVERT_SCRIPT = ROOT / "scripts" / "template" / "pdf_to_hwpx.py"'),
        # clone import는 RULES에서 처리됨
        # sys.path 불필요 (직접 src.template_based 참조)
        ('sys.path.insert(0, str(SCRIPTS_DIR))\n        from src.template_based.clone_template import clone_template as do_clone',
         'sys.path.insert(0, str(ROOT))\n        from src.template_based.clone_template import clone_template as do_clone'),
    ],
)

# 12. scripts/template/remove_highlights.py
copy_transformed(
    ROOT / "scripts/remove_highlights.py",
    ROOT / "scripts/template/remove_highlights.py",
    deeper=True,
)

# 13. scripts/template/batch_convert.py
copy_transformed(
    ROOT / "scripts/batch_convert.py",
    ROOT / "scripts/template/batch_convert.py",
    extra_rules=[
        ("ROOT          = Path(__file__).resolve().parent.parent\n",
         "ROOT          = Path(__file__).resolve().parent.parent.parent\n"),
        ('CONVERT_SCRIPT = SCRIPTS_DIR / "pdf_to_hwpx.py"',
         'CONVERT_SCRIPT = ROOT / "scripts" / "template" / "pdf_to_hwpx.py"'),
    ],
)

# 14. scripts/template/run_batch.py
copy_transformed(
    ROOT / "scripts/run_batch.py",
    ROOT / "scripts/template/run_batch.py",
    extra_rules=[
        ('SCRIPT = ROOT / "scripts" / "batch_convert.py"',
         'SCRIPT = ROOT / "scripts" / "template" / "batch_convert.py"'),
    ],
)

# 15. scripts/shared/test_mathpix_real.py
copy_transformed(
    ROOT / "scripts/test_mathpix_real.py",
    ROOT / "scripts/shared/test_mathpix_real.py",
    deeper=True,
)

# 16. scripts/shared/ocr_pdf.py
copy_transformed(
    ROOT / "scripts/ocr_pdf.py",
    ROOT / "scripts/shared/ocr_pdf.py",
    deeper=True,
)

print()
print("=== scripts/ 루트 잔류 파일 import 수정 ===")
# 루트에 남는 디버그/분석 스크립트 — sys.path는 parent.parent(=ROOT)로 그대로, import만 수정
for name in [
    "test_full_pipeline.py",
    "_analyze_markdown.py",
    "_match_analysis.py",
    "_reparse_pdf.py",
]:
    p = ROOT / "scripts" / name
    if p.exists():
        text = p.read_text(encoding='utf-8')
        new_text = transform(text)
        if new_text != text:
            p.write_text(new_text, encoding='utf-8')
            print(f"  {name}  (import 수정)")
        else:
            print(f"  {name}  (변경 없음)")

print()
print("=== tests/ import 수정 ===")
for name in ["test_builder.py", "test_mathpix_client.py"]:
    p = ROOT / "tests" / name
    if p.exists():
        text = p.read_text(encoding='utf-8')
        new_text = transform(text)
        if new_text != text:
            p.write_text(new_text, encoding='utf-8')
            print(f"  {name}  (import 수정)")
        else:
            print(f"  {name}  (변경 없음)")

print()
print("완료.")
