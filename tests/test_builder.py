import zipfile
from pathlib import Path

import pytest

from src.common.latex_to_hwp import convert
from src.text_only.text_builder import _to_circled, _postprocess_lines
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


# ════════════════════════════════════════════════════════════════
# 선택지 원문자 변환 (_to_circled)
# ════════════════════════════════════════════════════════════════

class TestToCircled:

    # ── 변환되어야 할 케이스 ─────────────────────────────────────

    def test_ascii_single_at_start(self):
        assert _to_circled("(1) -2<x<4") == "① -2<x<4"

    def test_ascii_all_five(self):
        for n, c in [("1","①"),("2","②"),("3","③"),("4","④"),("5","⑤")]:
            assert _to_circled(f"({n}) text") == f"{c} text"

    def test_ascii_six_to_ten(self):
        for n, c in [("6","⑥"),("7","⑦"),("8","⑧"),("9","⑨"),("10","⑩")]:
            assert _to_circled(f"({n}) text") == f"{c} text"

    def test_ascii_multiple_on_one_line(self):
        result = _to_circled("(1) foo (2) bar (3) baz")
        assert result == "① foo ② bar ③ baz"

    def test_ascii_at_end_of_string(self):
        assert _to_circled("답: (3)") == "답: ③"

    def test_ascii_choice_only(self):
        assert _to_circled("(5)") == "⑤"

    def test_fullwidth_basic(self):
        for n, c in [("1","①"),("2","②"),("3","③"),("4","④"),("5","⑤")]:
            assert _to_circled(f"（{n}） text") == f"{c} text"

    def test_fullwidth_at_line_start(self):
        assert _to_circled("（3） 9") == "③ 9"

    # ── 변환되지 않아야 할 케이스 ────────────────────────────────

    def test_no_space_after_not_converted(self):
        # 수식 내 (3)-\sqrt{b} 패턴: 뒤에 공백 없음
        assert _to_circled(r"(3)-\sqrt{b}=x+1") == r"(3)-\sqrt{b}=x+1"

    def test_preceded_by_dash_not_converted(self):
        # 수직선 레이블 (ㄹ)-(1)-(ㄴ): 앞이 -
        assert _to_circled("(ㄹ)-(1)-(ㄴ)") == "(ㄹ)-(1)-(ㄴ)"

    def test_out_of_range_not_converted(self):
        # 6자리 이상 번호 or 0
        assert _to_circled("(676)") == "(676)"
        assert _to_circled("(0) text") == "(0) text"
        assert _to_circled("(11) text") == "(11) text"

    def test_embedded_in_word_not_converted(self):
        # 앞뒤에 공백 없이 단어에 붙어있는 경우
        assert _to_circled("abc(2)def") == "abc(2)def"

    def test_korean_jamo_not_converted(self):
        # (ㄱ), (ᄂ) 등 한글 자모 — 숫자가 아님
        assert _to_circled("(ᄀ) text") == "(ᄀ) text"

    def test_math_latex_untouched(self):
        # LaTeX 수식 내용은 _to_circled 호출 대상이 아니지만
        # text 세그먼트가 수식이 아닐 때도 수식처럼 보이는 문자열 보호 확인
        assert _to_circled("f(3) = 0") == "f(3) = 0"  # 앞이 f(비공백)


# ════════════════════════════════════════════════════════════════
# 줄 후처리 (_postprocess_lines)
# ════════════════════════════════════════════════════════════════

class TestPostprocessLines:

    # ── 문항 번호 앞 빈 줄 ──────────────────────────────────────

    def test_blank_before_second_question(self):
        lines = ['1. 첫 문제', '답', '2. 두 번째 문제']
        out = _postprocess_lines(lines)
        idx = out.index('2. 두 번째 문제')
        assert out[idx - 1].strip() == ''

    def test_no_blank_before_first_question(self):
        lines = ['제목', '1. 첫 문제']
        out = _postprocess_lines(lines)
        idx = out.index('1. 첫 문제')
        # 첫 문항 앞엔 빈 줄 삽입 없음 → 바로 앞이 '제목'
        assert out[idx - 1].strip() != ''

    def test_blank_between_consecutive_questions(self):
        lines = ['1. A', '2. B', '3. C']
        out = _postprocess_lines(lines)
        non_empty = [l for l in out if l.strip()]
        assert non_empty == ['1. A', '2. B', '3. C']
        # 각 문항 사이에 빈 줄 존재
        assert out.count('') == 2

    # ── 점수 앞 빈 줄 ───────────────────────────────────────────

    def test_blank_before_score(self):
        lines = ['1. 문제 본문', '[4.5점]', '(1) 답']
        out = _postprocess_lines(lines)
        idx = out.index('[4.5점]')
        assert out[idx - 1].strip() == ''

    def test_blank_before_integer_score(self):
        lines = ['본문', '[5점]']
        out = _postprocess_lines(lines)
        idx = out.index('[5점]')
        assert out[idx - 1].strip() == ''

    # ── display math 앞뒤 빈 줄 ─────────────────────────────────

    def test_blank_around_display_math(self):
        lines = ['본문', '$$x+1$$', '다음 줄']
        out = _postprocess_lines(lines)
        idx = out.index('$$x+1$$')
        assert out[idx - 1].strip() == ''
        assert out[idx + 1].strip() == ''

    def test_no_double_blank_if_already_blank(self):
        lines = ['본문', '', '$$x$$', '', '다음']
        out = _postprocess_lines(lines)
        # 연속 빈 줄은 최대 1개
        for i in range(len(out) - 1):
            assert not (out[i].strip() == '' and out[i + 1].strip() == '')

    # ── 복수 선택지 줄 분리 ─────────────────────────────────────

    def test_split_two_choices_on_one_line(self):
        lines = ['(1) $4\\sqrt{13}$ (2) $4\\sqrt{14}$']
        out = _postprocess_lines(lines)
        non_empty = [l for l in out if l.strip()]
        assert len(non_empty) == 2
        assert non_empty[0].startswith('(1)')
        assert non_empty[1].startswith('(2)')

    def test_split_three_choices(self):
        lines = ['(1) 가 (2) 나 (3) 다']
        out = _postprocess_lines(lines)
        non_empty = [l for l in out if l.strip()]
        assert len(non_empty) == 3

    def test_no_split_single_choice(self):
        lines = ['(1) $-2<x<4$']
        out = _postprocess_lines(lines)
        assert out == ['(1) $-2<x<4$']

    def test_fullwidth_choice_split(self):
        lines = ['（1） 7 （2） 8 （3） 9']
        out = _postprocess_lines(lines)
        non_empty = [l for l in out if l.strip()]
        assert len(non_empty) == 3

    def test_no_split_dash_before_choice(self):
        # (ㄹ)-(1)-(ㄴ) — 앞이 '-', 공백 아님
        lines = ['(ㄹ)-(1)-(ㄴ)']
        out = _postprocess_lines(lines)
        assert out == ['(ㄹ)-(1)-(ㄴ)']

    # ── 연속 빈 줄 압축 ─────────────────────────────────────────

    def test_consecutive_blanks_compressed(self):
        lines = ['a', '', '', 'b']
        out = _postprocess_lines(lines)
        blank_count = sum(1 for l in out if not l.strip())
        assert blank_count <= 1
