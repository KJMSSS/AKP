import zipfile
from pathlib import Path

import pytest

from src.common.latex_to_hwp import convert
from src.template_based.builder import (
    fill_template,
    count_empty_scripts,
    count_all_scripts,
    FillResult,
    _fill_empty,
    _fill_all,
    _formula_blocks,
)
from src.common.ocr.mathpix_client import OcrBlock, OcrResult


# ════════════════════════════════════════════════════════════════
# LaTeX → HWP Script 변환
# ════════════════════════════════════════════════════════════════

class TestLatexToHwp:

    def test_frac_simple(self):
        assert convert(r"\frac{a}{b}") == "{a} over {b}"

    def test_frac_numeric(self):
        assert convert(r"\frac{1}{2}") == "{1} over {2}"

    def test_frac_nested_numerator(self):
        result = convert(r"\frac{x^{2}}{n}")
        assert "over" in result
        assert "x^{2}" in result

    def test_frac_sqrt_in_numerator(self):
        # 3단계 중첩: \frac{\sqrt{a^{2}+b^{2}}}{c}
        result = convert(r"\frac{\sqrt{a^{2}+b^{2}}}{c}")
        assert "over" in result
        assert "sqrt" in result

    def test_frac_quadratic_formula(self):
        # 근의 공식: -b ± √(b²-4ac) / 2a
        result = convert(r"\frac{-b \pm \sqrt{b^{2}-4ac}}{2a}")
        assert "over" in result
        assert "sqrt" in result
        assert "+-" in result

    def test_sqrt_simple(self):
        assert convert(r"\sqrt{x}") == "sqrt {x}"

    def test_sqrt_nth(self):
        assert convert(r"\sqrt[3]{x}") == "nroot {3} {x}"

    def test_sqrt_nested_frac(self):
        # \sqrt{\frac{a}{b}} → sqrt {{a} over {b}}
        result = convert(r"\sqrt{\frac{a}{b}}")
        assert result.startswith("sqrt {")
        assert "over" in result

    def test_int_from_to(self):
        result = convert(r"\int_{0}^{1}")
        assert "int" in result
        assert "from" in result
        assert "to" in result

    def test_sum_from_to(self):
        result = convert(r"\sum_{k=1}^{n}")
        assert "sum from {k=1} to {n}" == result

    def test_lim(self):
        result = convert(r"\lim_{x \to 0}")
        assert result.startswith("lim_{")
        assert "->" in result

    def test_overline(self):
        assert convert(r"\overline{z}") == "bar {z}"

    def test_hat(self):
        assert convert(r"\hat{x}") == "hat {x}"

    def test_vec(self):
        assert convert(r"\vec{v}") == "vec {v}"

    def test_greek_alpha(self):
        assert convert(r"\alpha") == "alpha"

    def test_greek_omega(self):
        assert convert(r"\omega") == "omega"

    def test_greek_uppercase_sigma(self):
        assert convert(r"\Sigma") == "SIGMA"

    def test_leq(self):
        assert convert(r"\leq") == "<="

    def test_geq(self):
        assert convert(r"\geq") == ">="

    def test_neq(self):
        assert convert(r"\neq") == "<>"

    def test_times(self):
        assert convert(r"\times") == "times"

    def test_cdot(self):
        assert convert(r"\cdot") == "cdot"

    def test_infty(self):
        assert convert(r"\infty") == "inf"

    def test_to_arrow(self):
        assert convert(r"\to") == "->"

    def test_passthrough_power(self):
        # x^{2} 는 HWP에서도 동일 문법
        assert convert(r"x^{2}") == "x^{2}"

    def test_passthrough_subscript(self):
        assert convert(r"a_{n}") == "a_{n}"

    def test_passthrough_plain(self):
        assert convert("a+b+c=2") == "a+b+c=2"

    def test_real_exam_formula(self):
        # 실제 시험 수식: y=-2x^{2}+ax+4
        result = convert(r"y=-2 x^{2}+a x+4")
        assert "x^{2}" in result

    def test_left_right_to_delimiters(self):
        result = convert(r"\left( x+1 \right)")
        assert "\\left" not in result
        assert "\\right" not in result
        assert result == "( x+1 )"

    def test_left_brace(self):
        result = convert(r"\left\{ a_n \right\}")
        assert result == "{ a_n }"

    def test_left_abs(self):
        result = convert(r"\left| x \right|")
        assert result == "| x |"

    def test_left_dot_removed(self):
        result = convert(r"c\left(\frac{a}{b}\right.")
        assert "left" not in result
        assert "right" not in result
        assert result == "c({a} over {b}"  # \right. → empty (no closing delimiter)

    def test_left_bracket(self):
        result = convert(r"\left[ x \right]")
        assert result == "[ x ]"

    def test_text_unwrapped(self):
        result = convert(r"\text{단위}")
        assert result == "단위"

    # ── 삼각함수 / 로그 ────────────────────────────────────────────

    def test_sin(self):
        assert convert(r"\sin x") == "sin x"

    def test_cos_greek(self):
        result = convert(r"\cos\theta")
        assert result == "cos theta"

    def test_tan(self):
        assert convert(r"\tan\theta") == "tan theta"

    def test_ln(self):
        assert "ln" in convert(r"\ln(x)")

    def test_log_subscript(self):
        result = convert(r"\log_2 x")
        assert result.startswith("log")
        assert "2" in result

    def test_sin_in_frac(self):
        result = convert(r"\frac{\sin x}{\cos x}")
        assert "sin" in result
        assert "cos" in result
        assert "over" in result

    def test_arcsin(self):
        assert "arcsin" in convert(r"\arcsin x")

    # ── 집합 / 확통 패턴 ───────────────────────────────────────────

    def test_cap_uppercase(self):
        assert convert(r"A \cap B") == "A CAP B"

    def test_cup_uppercase(self):
        assert convert(r"A \cup B") == "A CUP B"

    def test_cdots_keyword(self):
        assert convert(r"\cdots") == "CDOTS"

    def test_ldots_keyword(self):
        assert convert(r"\ldots") == "LDOTS"

    # ── 기하 패턴 ──────────────────────────────────────────────────

    def test_bullet(self):
        assert convert(r"\bullet") == "bullet"

    def test_degree_circ(self):
        assert convert(r"\circ") == "DEG"

    def test_degree_keyword(self):
        assert convert(r"90\degree") == "90DEG"

    def test_escaped_braces(self):
        result = convert(r"\{x \in \mathbb{R}\}")
        assert "{" in result
        assert "}" in result


# ════════════════════════════════════════════════════════════════
# 내부 채우기 함수 (_fill_empty, _fill_all)
# ════════════════════════════════════════════════════════════════

_TEMPLATE_XML = """\
<root>
  <hp:equation><hp:script/></hp:equation>
  <hp:equation><hp:script>기존내용</hp:script></hp:equation>
  <hp:equation><hp:script></hp:script></hp:equation>
</root>"""

_TEMPLATE_XML_2 = """\
<root>
  <hp:equation><hp:script/></hp:equation>
  <hp:equation><hp:script/></hp:equation>
</root>"""


class TestFillEmpty:

    def test_fills_empty_only(self):
        xml, filled, skipped = _fill_empty(_TEMPLATE_XML, ["A", "B"])
        assert filled == 2
        assert skipped == 0
        assert ">A<" in xml
        assert ">B<" in xml
        assert "기존내용" in xml   # 기존 내용 보존

    def test_skips_when_no_formulas(self):
        xml, filled, skipped = _fill_empty(_TEMPLATE_XML, [])
        assert filled == 0
        assert skipped == 2      # 빈 슬롯 2개 못 채움

    def test_partial_fill(self):
        xml, filled, skipped = _fill_empty(_TEMPLATE_XML_2, ["X"])
        assert filled == 1
        assert skipped == 1


class TestFillAll:

    def test_replaces_all(self):
        xml, filled, skipped = _fill_all(_TEMPLATE_XML, ["A", "B", "C"])
        assert filled == 3
        assert skipped == 0
        assert "기존내용" not in xml

    def test_preserves_when_formula_exhausted(self):
        xml, filled, skipped = _fill_all(_TEMPLATE_XML, ["A"])
        assert filled == 1
        assert skipped == 2
        assert "기존내용" in xml   # 수식 부족시 원본 유지


# ════════════════════════════════════════════════════════════════
# _formula_blocks 추출
# ════════════════════════════════════════════════════════════════

def test_formula_blocks_extracts_and_converts():
    result = OcrResult(
        raw={},
        blocks=[
            OcrBlock(kind="text",           content="텍스트"),
            OcrBlock(kind="formula_inline", content=r"\frac{a}{b}"),
            OcrBlock(kind="formula_display",content=r"\sqrt{x}"),
        ],
    )
    formulas = _formula_blocks(result)
    assert len(formulas) == 2
    assert formulas[0] == "{a} over {b}"
    assert formulas[1] == "sqrt {x}"


# ════════════════════════════════════════════════════════════════
# fill_template (임시 .hwpx 파일 사용)
# ════════════════════════════════════════════════════════════════

def _make_mock_hwpx(tmp_path: Path, section_xml: str) -> Path:
    """테스트용 최소 .hwpx 파일 생성."""
    hwpx = tmp_path / "mock.hwpx"
    with zipfile.ZipFile(hwpx, "w") as zf:
        zf.writestr("mimetype", "application/haansofthwpx")
        zf.writestr("Contents/section0.xml", section_xml.encode("utf-8"))
    return hwpx


def _make_ocr_result(*latex_list: str) -> OcrResult:
    blocks = [OcrBlock(kind="formula_inline", content=f) for f in latex_list]
    return OcrResult(raw={}, blocks=blocks)


class TestFillTemplate:

    def test_fill_empty_mode(self, tmp_path):
        section = (
            '<root>'
            '<hp:equation><hp:script/></hp:equation>'
            '<hp:equation><hp:script>기존</hp:script></hp:equation>'
            '</root>'
        )
        template = _make_mock_hwpx(tmp_path, section)
        output   = tmp_path / "out.hwpx"
        ocr      = _make_ocr_result(r"x^{2}")

        fr = fill_template(template, ocr, output, replace_all=False)

        assert fr.filled == 1
        assert output.exists()
        with zipfile.ZipFile(output) as zf:
            xml = zf.read("Contents/section0.xml").decode()
        assert "x^{2}" in xml
        assert "기존" in xml        # 기존 내용 보존

    def test_fill_all_mode(self, tmp_path):
        section = (
            '<root>'
            '<hp:equation><hp:script>OLD_A</hp:script></hp:equation>'
            '<hp:equation><hp:script>OLD_B</hp:script></hp:equation>'
            '</root>'
        )
        template = _make_mock_hwpx(tmp_path, section)
        output   = tmp_path / "out.hwpx"
        ocr      = _make_ocr_result(r"\alpha", r"\beta")

        fr = fill_template(template, ocr, output, replace_all=True)

        assert fr.filled == 2
        with zipfile.ZipFile(output) as zf:
            xml = zf.read("Contents/section0.xml").decode()
        assert "OLD_A" not in xml
        assert "OLD_B" not in xml
        assert "alpha" in xml
        assert "beta"  in xml

    def test_other_files_preserved(self, tmp_path):
        section = '<root><hp:equation><hp:script/></hp:equation></root>'
        hwpx = tmp_path / "mock.hwpx"
        with zipfile.ZipFile(hwpx, "w") as zf:
            zf.writestr("mimetype", "application/haansofthwpx")
            zf.writestr("Contents/section0.xml", section.encode())
            zf.writestr("Contents/header.xml", b"<header/>")
            zf.writestr("META-INF/container.xml", b"<container/>")

        output = tmp_path / "out.hwpx"
        fill_template(hwpx, _make_ocr_result("x"), output)

        with zipfile.ZipFile(output) as zf:
            assert zf.read("Contents/header.xml") == b"<header/>"
            assert zf.read("META-INF/container.xml") == b"<container/>"

    def test_missing_section_raises(self, tmp_path):
        hwpx = tmp_path / "no_section.hwpx"
        with zipfile.ZipFile(hwpx, "w") as zf:
            zf.writestr("mimetype", "application/haansofthwpx")
        with pytest.raises(ValueError, match="section0.xml"):
            fill_template(hwpx, _make_ocr_result("x"), tmp_path / "out.hwpx")

    def test_xml_escape_in_script(self, tmp_path):
        section = '<root><hp:equation><hp:script/></hp:equation></root>'
        template = _make_mock_hwpx(tmp_path, section)
        output   = tmp_path / "out.hwpx"
        # 꺾쇠가 포함된 수식 (< ≥ 같은 기호)
        ocr = _make_ocr_result("a <= b")
        fill_template(template, ocr, output)
        with zipfile.ZipFile(output) as zf:
            xml = zf.read("Contents/section0.xml").decode()
        # < 는 &lt; 로 이스케이프되어야 함
        assert "&lt;" in xml or "a <= b" not in xml


# ════════════════════════════════════════════════════════════════
# count 함수
# ════════════════════════════════════════════════════════════════

def test_count_empty_scripts(tmp_path):
    section = (
        '<root>'
        '<hp:equation><hp:script/></hp:equation>'
        '<hp:equation><hp:script></hp:script></hp:equation>'
        '<hp:equation><hp:script>filled</hp:script></hp:equation>'
        '</root>'
    )
    hwpx = _make_mock_hwpx(tmp_path, section)
    assert count_empty_scripts(hwpx) == 2


def test_count_all_scripts(tmp_path):
    section = (
        '<root>'
        '<hp:equation><hp:script/></hp:equation>'
        '<hp:equation><hp:script>A</hp:script></hp:equation>'
        '<hp:equation><hp:script>B</hp:script></hp:equation>'
        '</root>'
    )
    hwpx = _make_mock_hwpx(tmp_path, section)
    assert count_all_scripts(hwpx) == 3
