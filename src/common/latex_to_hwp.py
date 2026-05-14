"""
LaTeX -> HWP 수식 편집기(hp:script) 변환기

HWP 수식 표기는 LaTeX와 유사하지만 몇 가지 핵심 차이가 있다:
  \\frac{a}{b}          ->  {a} over {b}
  \\sqrt{x}             ->  sqrt {x}
  \\sqrt[n]{x}          ->  nroot {n} {x}
  \\int_{a}^{b}         ->  int from {a} to {b}
  \\sum_{k=1}^{n}       ->  sum from {k=1} to {n}
  \\overline{z}         ->  bar {z}
  \\alpha               ->  alpha
  \\leq / \\geq          ->  <= / >=
  x^{2}, a_{n}          ->  그대로 (동일 문법)
"""
import re

# ── 내부 헬퍼 ─────────────────────────────────────────────────────

# 중첩 3단계까지 허용하는 브레이스 그룹
# \frac{\sqrt{a^{2}+b^{2}}}{c} 같은 수식을 한 패스에서 처리하기 위해 3단계 필요
_BG = r'\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}'

# ── 분수 (\frac / \dfrac / \tfrac / \cfrac) ──────────────────────
_FRAC_RE = re.compile(r'\\(?:d|t|c)?frac\s*' + _BG + r'\s*' + _BG)

def _sub_frac(m: re.Match) -> str:
    return f'{{{m.group(1)}}} over {{{m.group(2)}}}'

# ── 제곱근 ────────────────────────────────────────────────────────
_SQRT_N_RE = re.compile(r'\\sqrt\[([^\]]+)\]\s*' + _BG)
_SQRT_RE   = re.compile(r'\\sqrt\s*' + _BG)
# \nroot{n}{x} — Mathpix가 간혹 이 표기를 사용함
_NROOT_RE  = re.compile(r'\\nroot\s*' + _BG + r'\s*' + _BG)

def _sub_sqrt_n(m: re.Match) -> str:
    return f'nroot {{{m.group(1)}}} {{{m.group(2)}}}'

def _sub_sqrt(m: re.Match) -> str:
    return f'sqrt {{{m.group(1)}}}'

def _sub_nroot(m: re.Match) -> str:
    return f'nroot {{{m.group(1)}}} {{{m.group(2)}}}'

# ── 적분·합·곱 (from/to 방식) ─────────────────────────────────────
_FROM_TO_RE = re.compile(
    r'\\(int|oint|sum|prod)\s*'
    r'(?:_\s*' + _BG + r')?\s*'
    r'(?:\^\s*' + _BG + r')?'
)

def _sub_from_to(m: re.Match) -> str:
    cmd   = m.group(1)
    lower = m.group(2)
    upper = m.group(3)
    if lower and upper:
        return f'{cmd} from {{{lower}}} to {{{upper}}}'
    if lower:
        return f'{cmd} from {{{lower}}}'
    if upper:
        return f'{cmd} to {{{upper}}}'
    return cmd

# ── 극한 (lim_{x→0} 은 subscript 방식 유지) ──────────────────────
_LIM_RE = re.compile(r'\\lim\s*(?:_\s*' + _BG + r')?')

def _sub_lim(m: re.Match) -> str:
    sub = m.group(1)
    return f'lim_{{{sub}}}' if sub else 'lim'

# ── 장식자 ────────────────────────────────────────────────────────
_DECO_MAP = {
    r'\overline': 'bar',   r'\bar':       'bar',
    r'\underline': 'under',
    r'\hat':  'hat',       r'\widehat':   'hat',
    r'\tilde': 'tilde',    r'\widetilde': 'tilde',
    r'\vec':  'vec',
    r'\dot':  'dot',       r'\ddot':      'ddot',
}
_DECO_RE = re.compile(
    r'(' + '|'.join(re.escape(k) for k in sorted(_DECO_MAP, key=len, reverse=True))
    + r')\s*' + _BG
)

def _sub_deco(m: re.Match) -> str:
    hwp_cmd = _DECO_MAP[m.group(1)]
    return f'{hwp_cmd} {{{m.group(2)}}}'

# ── 그리스 문자 ───────────────────────────────────────────────────
_GREEK: dict[str, str] = {
    r'\alpha': 'alpha',   r'\beta': 'beta',     r'\gamma': 'gamma',
    r'\delta': 'delta',   r'\epsilon': 'epsilon', r'\varepsilon': 'epsilon',
    r'\zeta': 'zeta',     r'\eta': 'eta',       r'\theta': 'theta',
    r'\vartheta': 'theta', r'\iota': 'iota',    r'\kappa': 'kappa',
    r'\lambda': 'lambda', r'\mu': 'mu',          r'\nu': 'nu',
    r'\xi': 'xi',         r'\pi': 'pi',          r'\varpi': 'pi',
    r'\rho': 'rho',       r'\varrho': 'rho',    r'\sigma': 'sigma',
    r'\varsigma': 'sigma', r'\tau': 'tau',       r'\upsilon': 'upsilon',
    r'\phi': 'phi',       r'\varphi': 'phi',    r'\chi': 'chi',
    r'\psi': 'psi',       r'\omega': 'omega',
    r'\Gamma': 'GAMMA',   r'\Delta': 'DELTA',   r'\Theta': 'THETA',
    r'\Lambda': 'LAMBDA', r'\Xi': 'XI',          r'\Pi': 'PI',
    r'\Sigma': 'SIGMA',   r'\Upsilon': 'UPSILON', r'\Phi': 'PHI',
    r'\Psi': 'PSI',       r'\Omega': 'OMEGA',
}
# 긴 것 우선 매칭을 위해 길이 내림차순 정렬
_GREEK_RE = re.compile(
    '(' + '|'.join(re.escape(k) for k in sorted(_GREEK, key=len, reverse=True)) + r')(?![a-zA-Z])'
)

def _sub_greek(m: re.Match) -> str:
    return _GREEK.get(m.group(1), m.group(1))

# ── 기호·연산자 (순서 중요 — 긴 것 먼저) ─────────────────────────
_SYMBOLS: list[tuple[str, str]] = [
    # 이스케이프된 중괄호 처리 (\{ → {, \} → })
    (r'\\\{', '{'), (r'\\\}', '}'),
    # 삼각함수 · 지수/로그 (백슬래시 제거 + 뒤따르는 공백 유지)
    # ※ \b 대신 (?![a-zA-Z])를 써야 \log_2 처럼 _ 뒤에 오는 경우도 매칭된다.
    # ※ 그리스 변환보다 먼저 실행되어야 \cos\theta 같은 연결이 올바르게 처리된다.
    (r'\\arcsin(?![a-zA-Z])', 'arcsin '), (r'\\arccos(?![a-zA-Z])', 'arccos '), (r'\\arctan(?![a-zA-Z])', 'arctan '),
    (r'\\sinh(?![a-zA-Z])',   'sinh '),   (r'\\cosh(?![a-zA-Z])',   'cosh '),   (r'\\tanh(?![a-zA-Z])',   'tanh '),
    (r'\\sin(?![a-zA-Z])',    'sin '),    (r'\\cos(?![a-zA-Z])',    'cos '),    (r'\\tan(?![a-zA-Z])',    'tan '),
    (r'\\cot(?![a-zA-Z])',    'cot '),    (r'\\sec(?![a-zA-Z])',    'sec '),    (r'\\csc(?![a-zA-Z])',    'csc '),
    (r'\\ln(?![a-zA-Z])',     'ln '),     (r'\\log(?![a-zA-Z])',    'log '),    (r'\\exp(?![a-zA-Z])',    'exp '),
    (r'\\max(?![a-zA-Z])',    'max '),    (r'\\min(?![a-zA-Z])',    'min '),
    # 화살표 · 논리
    (r'\\Leftrightarrow\b', '<=>'),
    (r'\\Rightarrow\b',     '=>'),
    (r'\\Leftarrow\b',      '<='),
    (r'\\rightarrow\b|\\to\b', '->'),
    (r'\\leftarrow\b',      '<-'),
    (r'\\longrightarrow\b', '->'),
    # 부등호
    (r'\\leq\b|\\le\b',     '<='),
    (r'\\geq\b|\\ge\b',     '>='),
    (r'\\neq\b|\\ne\b',     '<>'),
    # 연산자
    (r'\\bullet\b',         'bullet'),
    (r'\\cdot\b',           'cdot'),
    (r'\\times\b',          'times'),
    (r'\\div\b',            'div'),
    (r'\\pm\b',             '+-'),
    (r'\\mp\b',             '-+'),
    (r'\\infty\b',          'inf'),
    # 점줄임 — HWP는 CDOTS/LDOTS 키워드 사용
    (r'\\cdots\b|\\dots\b', 'CDOTS'),
    (r'\\ldots\b',          'LDOTS'),
    # 논리/집합
    (r'\\because\b',        'because'),
    (r'\\therefore\b',      'therefore'),
    (r'\\notin\b',          'notin'),
    (r'\\in\b',             'in'),
    (r'\\subseteq\b',       'subseteq'),
    (r'\\supseteq\b',       'supseteq'),
    (r'\\subset\b',         'subset'),
    (r'\\supset\b',         'supset'),
    (r'\\cup\b',            'CUP'),
    (r'\\cap\b',            'CAP'),
    (r'\\emptyset\b|\\varnothing\b', 'emptyset'),
    (r'\\forall\b',         'for all'),
    (r'\\exists\b',         'exists'),
    # 기하
    (r'\\parallel\b',       '//'),
    (r'\\perp\b',           'perp'),
    (r'\\angle\b',          'angle'),
    (r'\\triangle\b',       'triangle'),
    # 각도 기호
    (r'\\degree\b|\\circ\b', 'DEG'),
    (r'°',                   'DEG'),
    # 공백 명령
    (r'\\,|\\;|\\!|\\quad\b|\\qquad\b', '~'),
    (r'\\ ',                '~'),
    # \left / \right delimiter → 실제 기호 (\{ 는 앞 단계에서 이미 { 로 변환됨)
    (r'\\left\s*\(',     '('),    (r'\\right\s*\)',    ')'),
    (r'\\left\s*\[',     '['),    (r'\\right\s*\]',    ']'),
    (r'\\left\s*\{',     '{'),    (r'\\right\s*\}',    '}'),
    (r'\\left\s*\|',     '|'),    (r'\\right\s*\|',    '|'),
    (r'\\left\s*\.',     ''),     (r'\\right\s*\.',     ''),
    (r'\\left\s*<',      '<'),    (r'\\right\s*>',      '>'),
    # \text{...} 언래핑
    (r'\\text\{([^}]*)\}',  r'\1'),
    (r'\\mathrm\{([^}]*)\}', r'\1'),
    (r'\\mathbf\{([^}]*)\}', r'\1'),
    (r'\\mathit\{([^}]*)\}', r'\1'),
]
_SYMBOL_PATS = [(re.compile(p), r) for p, r in _SYMBOLS]

# ── LaTeX 환경 (\begin{...}...\end{...}) ────────────────────────────
# 시험 OCR에서 나타나는 환경: array, gathered, aligned, cases, *matrix 계열
# 반드시 다른 변환보다 먼저 처리해야 내부 수식이 이후 패스에서 올바르게 변환된다.

_ENV_RE = re.compile(
    r'\\begin\{(array|gathered|aligned|cases|(?:p|b|v)?matrix)\}'
    r'(?:\{[^}]*\})?'    # array 열 스펙 {lll}, {ll|l} 등 선택적 무시
    r'([\s\S]*?)'
    r'\\end\{\1\}',
)


def _sub_env(m: re.Match) -> str:
    """LaTeX 환경 블록 → HWP script (행/열 구조 보존)."""
    env  = m.group(1)
    body = m.group(2).strip()

    # \\ 기준 행 분리
    rows = [r.strip() for r in re.split(r'\\\\', body) if r.strip()]
    if not rows:
        return ''

    if env in ('gathered', 'aligned'):
        # 정렬 마커 & 제거 후 atop 적층
        cleaned = []
        for row in rows:
            row = re.sub(r'^\s*&+\s*', '', row)   # 행 앞 & 제거
            row = re.sub(r'\s*&+\s*', ' ', row)   # 나머지 & → 공백
            row = row.strip()
            if row:
                cleaned.append(row)
        return ' atop '.join(cleaned) if cleaned else ''

    elif env == 'cases':
        cleaned = []
        for row in rows:
            row = re.sub(r'\s*&+\s*', ' ', row).strip()
            if row:
                cleaned.append(row)
        return 'lbrace cases{ ' + ' ## '.join(cleaned) + ' }'

    else:  # array, matrix, pmatrix, bmatrix, vmatrix
        has_cols = any('&' in row for row in rows)

        if not has_cols:
            # 단일 열 → atop 적층
            return ' atop '.join(rows)

        # 다중 열 → matrix{} 표현
        matrix_rows = []
        for row in rows:
            cells = [c.strip() for c in row.split('&')]
            matrix_rows.append(' # '.join(cells))
        inner = ' ## '.join(matrix_rows)

        if env == 'pmatrix':
            return f'lparen matrix{{ {inner} }} rparen'
        elif env == 'bmatrix':
            return f'lbracket matrix{{ {inner} }} rbracket'
        elif env == 'vmatrix':
            return f'lline matrix{{ {inner} }} rline'
        else:
            return f'matrix{{ {inner} }}'


# ── 공개 API ─────────────────────────────────────────────────────

def convert(latex: str) -> str:
    """LaTeX 수식 문자열을 HWP hp:script 표기로 변환한다."""
    s = latex.strip()
    # 환경 블록을 먼저 처리 (내부 수식은 이후 패스가 담당)
    s = _ENV_RE.sub(_sub_env, s)
    for _ in range(3):          # 중첩 표현을 위한 다중 패스
        prev = s
        s = _FRAC_RE.sub(_sub_frac, s)
        s = _SQRT_N_RE.sub(_sub_sqrt_n, s)
        s = _NROOT_RE.sub(_sub_nroot, s)
        s = _SQRT_RE.sub(_sub_sqrt, s)
        s = _LIM_RE.sub(_sub_lim, s)
        s = _FROM_TO_RE.sub(_sub_from_to, s)
        s = _DECO_RE.sub(_sub_deco, s)
        # 심볼 패턴을 그리스 변환보다 먼저 실행:
        # \cos\theta 처럼 삼각함수 바로 뒤에 그리스 문자가 붙을 때
        # 심볼을 먼저 치환해야 \b 경계가 제대로 작동한다.
        for pat, repl in _SYMBOL_PATS:
            s = pat.sub(repl, s)
        s = _GREEK_RE.sub(_sub_greek, s)
        if s == prev:
            break
    # 중복 공백 정리 (삼각함수 변환 시 생길 수 있는 연속 공백)
    return re.sub(r' {2,}', ' ', s).strip()
