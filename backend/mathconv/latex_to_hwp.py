"""
LaTeX -> 한글(HWP) 수식 스크립트 변환기 (v1).

Mathpix/LLM 이 내놓는 LaTeX 를 한글 수식편집기 스크립트로 바꾼다.
출력은 **원시(raw) 스크립트**: 연산자 `>`,`<`,`&` 를 그대로 둔다(저장 시 XML 이스케이프됨).

한글 수식 스크립트의 LaTeX 와의 주요 차이(루트 샘플에서 확인):
  - 명령에 백슬래시 없음:  \alpha -> alpha,  \le -> le,  \ge -> ge,  \times -> times
  - 분수:  \frac{a}{b} -> {a} over {b}
  - 제곱근: \sqrt{a} -> sqrt {a} ,  \sqrt[n]{a} -> root {n} of {a}
  - 위/아래첨자 ^{}, _{} 는 동일
  - 묶음:  \left( ... \right) -> left ( ... right )
  - 행렬/cases:  \\ -> # (행 구분),  cases{... # ...}
  - 예) cases {x > 1 # x le 2} ,  left ( a , b right )

신뢰도가 낮으면(미해결 백슬래시·중괄호 불균형) ok=False 를 돌려 호출측이
'수식-이미지 폴백'으로 전환하도록 한다(하이브리드 전략).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- 토큰 매핑 (백슬래시 명령 -> 한글 수식 토큰) -----------------------------
_SIMPLE = {
    # 관계/연산
    "le": "le", "leq": "le", "ge": "ge", "geq": "ge", "ne": "<>", "neq": "<>",
    "times": "times", "div": "div", "cdot": "cdot", "pm": "+-", "mp": "-+",
    "approx": "approx", "equiv": "equiv", "sim": "sim", "propto": "prop",
    "ll": "<<", "gg": ">>", "subset": "subset", "supset": "supset",
    "in": "in", "notin": "notin", "cup": "cup", "cap": "cap",
    "rightarrow": "->", "to": "->", "leftarrow": "<-", "Rightarrow": "=>",
    "leftrightarrow": "<->", "mapsto": "mapsto",
    # 큰 연산자
    "sum": "sum", "prod": "prod", "int": "int", "lim": "lim",
    "infty": "inf", "partial": "partial", "nabla": "nabla",
    # 함수
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec",
    "csc": "csc", "log": "log", "ln": "ln", "exp": "exp", "max": "max",
    "min": "min", "gcd": "gcd", "deg": "deg",
    # 점/생략
    "cdots": "cdots", "ldots": "dots", "dots": "dots", "vdots": "vdots",
    "ddots": "ddots", "angle": "angle", "perp": "perp", "parallel": "parallel",
    # 도형/논리 기호 (한국 시험지 빈출 — 미지원 폴백 방지)
    "square": "□", "triangle": "△", "therefore": "∴", "because": "∵",
    "bigcirc": "○", "bigtriangleup": "△", "bigtriangledown": "▽",
    # 그리스 소문자
    "alpha": "alpha", "beta": "beta", "gamma": "gamma", "delta": "delta",
    "epsilon": "epsilon", "varepsilon": "varepsilon", "zeta": "zeta",
    "eta": "eta", "theta": "theta", "vartheta": "vartheta", "iota": "iota",
    "kappa": "kappa", "lambda": "lambda", "mu": "mu", "nu": "nu", "xi": "xi",
    "pi": "pi", "rho": "rho", "sigma": "sigma", "tau": "tau", "phi": "phi",
    "varphi": "varphi", "chi": "chi", "psi": "psi", "omega": "omega",
    # 그리스 대문자
    "Gamma": "GAMMA", "Delta": "DELTA", "Theta": "THETA", "Lambda": "LAMBDA",
    "Xi": "XI", "Pi": "PI", "Sigma": "SIGMA", "Phi": "PHI", "Psi": "PSI",
    "Omega": "OMEGA",
    # 공백/기타
    "quad": "`", "qquad": "``", "left": "left", "right": "right",
    "cdotp": "cdot", "ast": "*", "star": "star", "circ": "circ",
}

# 환경 -> (열기, 닫기, 행구분처리)
_ENVS = {
    "cases": ("cases {", "}", True),
    "matrix": ("matrix {", "}", True),
    "pmatrix": ("left ( matrix {", "} right )", True),
    "bmatrix": ("left [ matrix {", "} right ]", True),
    "vmatrix": ("left | matrix {", "} right |", True),
    "array": ("matrix {", "}", True),
    "aligned": ("", "", True),
    "align": ("", "", True),
}


@dataclass
class MathResult:
    script: str
    ok: bool
    reason: str = ""


def _convert_frac(s: str) -> str:
    """\\frac{a}{b} / \\dfrac / \\tfrac -> {a} over {b}. 중첩 지원."""
    pat = re.compile(r"\\[dt]?frac\s*")
    while True:
        m = pat.search(s)
        if not m:
            break
        i = m.end()
        a, i = _read_group(s, i)
        b, i = _read_group(s, i)
        s = s[: m.start()] + "{" + a + "} over {" + b + "}" + s[i:]
    return s


def _convert_sqrt(s: str) -> str:
    # \sqrt[n]{a} -> root {n} of {a}
    # ⚠️ 치환 토큰이 영문자로 시작하므로 반드시 앞에 공백을 넣는다. 없으면 직전의
    # 미치환 명령과 붙어 가짜 명령이 된다: q\pm\sqrt{37} -> q\pmsqrt {37} (실사고).
    pat = re.compile(r"\\sqrt\s*(\[)?")
    while True:
        m = pat.search(s)
        if not m:
            break
        i = m.end()
        if m.group(1):  # 있을 경우 [n]
            n, j = _read_bracket(s, i - 1)
            a, j = _read_group(s, j)
            repl = " root {" + n + "} of {" + a + "}"
            s = s[: m.start()] + repl + s[j:]
        else:
            a, j = _read_group(s, i)
            s = s[: m.start()] + " sqrt {" + a + "}" + s[j:]
    return s


def _read_group(s: str, i: int):
    """위치 i 부터 {..} 그룹 본문과 다음 위치 반환. {가 없으면 한 토큰."""
    while i < len(s) and s[i].isspace():
        i += 1
    if i < len(s) and s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1 : j], j + 1
            j += 1
        return s[i + 1 :], len(s)
    # 단일 토큰(문자 또는 \cmd)
    if i < len(s) and s[i] == "\\":
        m = re.match(r"\\[A-Za-z]+", s[i:])
        if m:
            return s[i : i + m.end()], i + m.end()
    return (s[i] if i < len(s) else ""), i + 1


def _read_bracket(s: str, i: int):
    j = s.find("]", i)
    return s[i + 1 : j], j + 1


def _convert_envs(s: str) -> str:
    for name, (op, cl, rowsep) in _ENVS.items():
        pat = re.compile(r"\\begin\s*\{" + name + r"\}(.*?)\\end\s*\{" + name + r"\}", re.S)
        while True:
            m = pat.search(s)
            if not m:
                break
            body = m.group(1)
            body = body.replace("\\\\", " # ")  # 행 구분
            if name == "cases":
                body = body.replace("&", " ")   # cases 는 정렬기호 제거
            s = s[: m.start()] + " " + op + body + cl + s[m.end():]  # 공백: 명령 붙음 방지
    return s


# 단항 명령 \cmd{x} -> token {x} (벡터/모자/윗줄 등 — 시험지 빈출)
_UNARY = {
    "vec": "vec", "overrightarrow": "vec", "overleftarrow": "vec",
    "overline": "overline", "bar": "bar", "underline": "underline",
    "hat": "hat", "widehat": "hat", "tilde": "tilde", "widetilde": "tilde",
    "dot": "dot", "ddot": "ddot", "acute": "acute", "grave": "grave",
    "boxed": "",  # 테두리는 무시
}


def _convert_unary(s: str) -> str:
    pat = re.compile(r"\\(" + "|".join(sorted(_UNARY, key=len, reverse=True)) + r")\b\s*")
    while True:
        m = pat.search(s)
        if not m:
            break
        token = _UNARY[m.group(1)]
        a, j = _read_group(s, m.end())
        repl = (" " + token + " {" + a + "}") if token else ("{" + a + "}")  # 공백: 명령 붙음 방지
        s = s[: m.start()] + repl + s[j:]
    return s


def _brace_scripts(s: str) -> str:
    """비중괄호 위/아래첨자 인자를 {}로 감싼다: x^2+3x -> x^{2}+3x.

    한글 수식은 ^/_ 뒤 인자를 공백·중괄호 경계까지 탐욕적으로 흡수한다
    (실측 2026-07-02: x^2+3x+2=0 이 통째로 위첨자 렌더). LaTeX 의 '한 토큰'
    결합 규칙을 중괄호로 명시해야 하며, base.hwpx 원본 수식도 전부 x^{2} 꼴이다.
    frac/sqrt 변환 뒤에 호출할 것(^\frac 같은 미변환 명령 흡수 방지).
    """
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        out.append(c)
        i += 1
        if c in "^_":
            j = i
            while j < n and s[j].isspace():
                j += 1
            if j < n and s[j] != "{":
                tok, k = _read_group(s, j)
                out.append("{" + tok + "}")
                i = k
    return "".join(out)


def latex_to_hwp(latex: str) -> MathResult:
    s = latex.strip()
    # 인라인 수식 구분자 제거
    s = re.sub(r"^\$+|\$+$", "", s).strip()
    s = re.sub(r"^\\\(|\\\)$", "", s).strip()
    s = re.sub(r"^\\\[|\\\]$", "", s).strip()

    s = _convert_envs(s)
    s = _convert_frac(s)
    s = _convert_sqrt(s)
    s = _convert_unary(s)
    s = _brace_scripts(s)

    # \left( \right) 등 구분자 (치환 토큰 앞 공백: 직전 명령과 붙음 방지)
    s = re.sub(r"\\left\s*", " left ", s)
    s = re.sub(r"\\right\s*", " right ", s)
    s = s.replace("\\{", "{ lbrace }").replace("\\}", "{ rbrace }")
    s = s.replace("\\|", "|").replace("\\,", " ").replace("\\;", " ").replace("\\!", "")
    s = s.replace("\\ ", " ")

    # \text{..} / \mathrm{..} -> rm { .. }
    s = re.sub(r"\\(?:text|mathrm|operatorname)\s*\{([^{}]*)\}", r" rm {\1}", s)
    s = re.sub(r"\\(?:mathbf|bf)\s*\{([^{}]*)\}", r" bold {\1}", s)
    s = re.sub(r"\\(?:mathit|it)\s*\{([^{}]*)\}", r" it {\1}", s)

    # 남은 \\ -> #
    s = s.replace("\\\\", " # ")

    # 단순 명령 치환 — 토큰 경계 공백 보장 (a\times b -> a times b, x\alpha -> x alpha)
    def repl_cmd(m):
        name = m.group(1)
        return " " + _SIMPLE.get(name, "\x00" + name) + " "  # 미지원이면 마킹
    s = re.sub(r"\\([A-Za-z]+)", repl_cmd, s)
    s = re.sub(r"[ \t]{2,}", " ", s)  # 치환으로 생긴 다중 공백 정리

    # 미지원 명령 잔존 여부
    unresolved = re.findall(r"\x00([A-Za-z]+)", s)
    s = s.replace("\x00", "\\")  # 표시용으로 복원

    # 중괄호 균형 체크
    balanced = s.count("{") == s.count("}")

    ok = (not unresolved) and balanced
    reason = ""
    if unresolved:
        reason = "미지원 명령: " + ", ".join(sorted(set(unresolved)))
    elif not balanced:
        reason = "중괄호 불균형"
    return MathResult(script=s.strip(), ok=ok, reason=reason)


if __name__ == "__main__":
    tests = [
        r"x^{2}+y^{2}=1",
        r"\frac{a}{b}",
        r"\sqrt{x^2+1}",
        r"\sqrt[3]{x}",
        r"\begin{cases} x>1 \\ x \le 2 \end{cases}",
        r"\left( a, b \right)",
        r"\sum_{k=1}^{n} k = \frac{n(n+1)}{2}",
        r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}",
        r"\alpha + \beta = \gamma",
        r"\lim_{x \to \infty} \frac{1}{x} = 0",
        r"\overrightarrow{AB}",
        # 2026-07-02 실사고 회귀 케이스
        r"x=\dfrac{q\pm\sqrt{37}}{6}",   # \pm+sqrt 붙음 -> 'pmsqrt' 미지원 오류였음
        r"2x^2=x^2+x",                   # 비중괄호 ^: 한글이 '2=x^2+x' 전체를 위첨자로
        r"x^2+3x+2=0",
        r"x^2+2=2x(x+1)",
        r"3x^3+x^2+1=3x^3-2",
        r"a_1+a_n=10",
        r"x^{2}+y^\alpha",
        # 2026-07-04 실사고 회귀 케이스: 빈칸 동그라미 \bigcirc 미지원 -> 폴백
        r"a=\boxed{\bigcirc}",
        r"b\neq \boxed{\bigcirc}",
        r"\bigcirc",
    ]
    for t in tests:
        r = latex_to_hwp(t)
        flag = "OK " if r.ok else "IMG"
        print(f"[{flag}] {t}\n      -> {r.script}" + (f"   ({r.reason})" if r.reason else ""))
