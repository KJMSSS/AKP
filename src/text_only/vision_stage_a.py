"""
Vision Stage A — 레이아웃 분석 전용 (수식 추출 금지).

역할:
  - PDF 페이지 영역 분류 (본문 / 선택지 / 결재선 / 페이지 푸터)
  - 선택지 정상 순서 ①②③④⑤ 식별 및 재배열
  - 문제 경계 분석 (크로스블리드 힌트 보강)
  - 답안 영역 노이즈 패턴 제거
  - cases/aligned 환경 압축 해제
  - Mathpix OCR 결과 중 비-본문 노이즈 라인 식별

절대 금지:
  - 수식·숫자 단독 추출 (Stage C 전용)
  - 수식 재처리·재전사
  - 환각 허용 10% 초과 시 결과 폐기

안전 정책:
  - Vision 출력에 LaTeX 패턴 포함 → 해당 항목 무효화
  - 식별된 노이즈 라인이 전체의 20% 초과 → 결과 폐기 (환각 과다)
  - Mathpix 마커 개수 < 5 → 선택지 재정렬 SKIP
  - 적용 가능 항목만 선별 적용, 나머지 원본 유지
"""
import json
import os
import re
import base64
import time
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 2048
_COST_PER_M_INPUT  = 3.0
_COST_PER_M_OUTPUT = 15.0

# Vision 호출 로그 디렉토리
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "log" / "vision_calls" / "cycle_15f"

# 수식 존재 여부 탐지 (Vision 응답 안전 검사용)
_LATEX_PATTERN = re.compile(r"\\[a-zA-Z]{2,}|[\$]{1,2}|\{.*\}")

# 선택지 알파벳 패턴 (layout_filter가 놓친 것)
_ALPHA_CHOICE_LINE = re.compile(r"^\s*\(([a-e])\)\s", re.IGNORECASE | re.MULTILINE)

# 문항 번호 탐지
_ITEM_NO = re.compile(r"^(\d{1,2})[.．]", re.MULTILINE)

# 선택지 원문자
_CIRCLE_MARKERS = ["①", "②", "③", "④", "⑤"]
_CIRCLE_SET = set(_CIRCLE_MARKERS)
_CIRCLE_LINE_RE = re.compile(r"^[ \t]*[①②③④⑤][ \t]")
_CIRCLE_RE = re.compile(r"[①②③④⑤]")

# 문항 경계 (문제 번호)
_PROB_BOUNDARY_RE = re.compile(r"^\s*(\d{1,2})[.．]", re.MULTILINE)

# 답안 영역 노이즈: "[N점] 숫자" 형태 — 꼬리 숫자만 제거
_ANSWER_SUFFIX_RE = re.compile(r"(\[\d+\.?\d*점\])\s+\d+\s*$", re.MULTILINE)

# cases/aligned 인라인 압축 패턴
_INLINE_ENV_RE = re.compile(
    r'(\$\$)\s*\\begin\{(cases|aligned)\}(.*?)\\end\{\2\}\s*(\$\$)',
    re.DOTALL,
)

_VISION_LAYOUT_PROMPT = """\
이 시험지 페이지에서 다음만 보고하세요. 절대 텍스트/수식/숫자 자체는 추출하지 마세요.

[분석 목표]
1. 선택지 마커 (①②③④⑤) 등장 순서 — 문제 번호별로, 실제 PDF의 위쪽→아래쪽 순.
   정상 순서(①②③④⑤)인 문항은 보고 생략해도 됩니다.
2. 문제 번호 경계 — 각 문제가 차지하는 y 범위 (0.0=페이지 상단, 1.0=페이지 하단)
3. 표/결재선 영역 위치 (y 범위)
4. 답안 기입란/페이지 메타 영역 위치 (y 범위)
5. 수식이 두 줄 이상에 걸친 문제 번호와 위치 (y 범위)
6. 결재선·손글씨·페이지 메타에 해당하는 노이즈 줄 텍스트 (수식 제외)

[절대 금지]
- 수식, 수학 기호, 숫자를 새로 전사(transcription)하지 마세요
- LaTeX, MathML, 수식 표기 일체 출력 금지
- 선택지의 텍스트/숫자 내용 출력 금지 (①②③④⑤ 기호만 허용)

[응답 형식 — JSON만 출력, 설명 없이]
{
  "choice_order": [{"problem": 2, "markers_in_order": ["①","③","②","④","⑤"]}],
  "problem_boundaries": [{"problem": 5, "y_range": [0.4, 0.5]}],
  "table_regions": [{"y_range": [0.3, 0.4]}],
  "answer_noise_regions": [{"y_range": [0.8, 0.9]}],
  "multiline_formula_regions": [{"problem": 3, "y_range": [0.2, 0.3]}],
  "noise_lines": ["결재선 텍스트만 (수식 제외)"],
  "confidence": "high|medium|low"
}

noise_lines: Mathpix OCR 출력에서 결재선·메타·손글씨에 해당하는 줄의 정확한 텍스트.
수식이 포함된 줄은 noise_lines에 넣지 마세요.\
"""


def _needs_vision(md: str) -> bool:
    """
    Vision 분석이 필요한 복잡 레이아웃 여부 판단.
    layout_filter 실행 AFTER 상태에서 호출됨.
    """
    if "【★ 크로스블리드" in md:
        return True
    if _ALPHA_CHOICE_LINE.search(md):
        return True
    circles = len(re.findall(r"[①②③④⑤]", md))
    if circles > 20:
        items = _ITEM_NO.findall(md)
        if circles < len(items) * 3:
            return True
    return False


def _safe_parse_vision_json(raw: str) -> dict | None:
    """Vision 출력에서 JSON 추출 및 안전 검사."""
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

    # 안전 검사: LaTeX 패턴 포함 noise_lines 항목 제거
    if "noise_lines" in data:
        data["noise_lines"] = [
            line for line in data.get("noise_lines", [])
            if not _LATEX_PATTERN.search(str(line))
        ]

    # choice_order: 리스트 타입 보장 + 원문자만 포함된 항목만 허용
    raw_co = data.get("choice_order", [])
    if isinstance(raw_co, dict):
        # 이전 형식(dict) → 새 형식(list) 변환
        raw_co = [
            {"problem": int(k), "markers_in_order": v}
            for k, v in raw_co.items()
        ]
    # 원문자만 포함된 항목만 허용
    safe_co = [
        e for e in raw_co
        if all(c in _CIRCLE_SET for c in e.get("markers_in_order", []))
    ]
    # 정상 순서(①②③④⑤) 항목 제거 — Vision이 모든 문항 보고 시 오탐 방지
    safe_co = [
        e for e in safe_co
        if e.get("markers_in_order", _CIRCLE_MARKERS) != _CIRCLE_MARKERS
    ]
    # 환각 과다: 비정상 순서 항목이 30개 초과하면 폐기
    if len(safe_co) > 30:
        return None
    data["choice_order"] = safe_co

    return data


def _log_vision_call(pdf_path: Path, layout: dict | None, cost: float, raw_response: str) -> None:
    """Vision 호출 결과를 log/vision_calls/cycle_15f/ 에 기록."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        school = pdf_path.stem.strip("[]").replace(" ", "_")
        log_path = _LOG_DIR / f"{ts}_{school}.json"
        log_data = {
            "timestamp": ts,
            "pdf": str(pdf_path.name),
            "cost_usd": round(cost, 6),
            "layout": layout,
            "raw_preview": raw_response[:600] if raw_response else "",
        }
        log_path.write_text(
            json.dumps(log_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"  [vision_a] 로그 기록 실패: {e}")


def analyze_layout(pdf_path: Path) -> tuple[dict | None, float, str]:
    """
    PDF를 Claude Vision으로 분석, 레이아웃 JSON 반환.

    반환: (layout_dict or None, cost_usd, raw_response)
    """
    if not pdf_path.exists():
        return None, 0.0, ""

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  [vision_a] ANTHROPIC_API_KEY 없음 — Vision 스킵")
        return None, 0.0, ""

    pdf_bytes = pdf_path.read_bytes()
    b64 = base64.standard_b64encode(pdf_bytes).decode()

    client = anthropic.Anthropic(api_key=api_key)
    t0 = time.time()

    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": _VISION_LAYOUT_PROMPT},
                    ],
                }
            ],
        )
    except Exception as e:
        print(f"  [vision_a] API 오류: {e}")
        return None, 0.0, ""

    elapsed = time.time() - t0
    in_tok  = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost    = (in_tok * _COST_PER_M_INPUT + out_tok * _COST_PER_M_OUTPUT) / 1_000_000

    print(
        f"  [vision_a] 완료: {elapsed:.1f}s  "
        f"in={in_tok:,}tok out={out_tok:,}tok  ${cost:.4f}"
    )

    raw_text = resp.content[0].text
    layout   = _safe_parse_vision_json(raw_text)

    if layout is None:
        print("  [vision_a] JSON 파싱 실패 또는 안전 검사 탈락")
    else:
        conf     = layout.get("confidence", "?")
        n_noise  = len(layout.get("noise_lines", []))
        n_choice = len(layout.get("choice_order", []))
        n_bounds = len(layout.get("problem_boundaries", []))
        print(
            f"  [vision_a] 신뢰도={conf}  noise={n_noise}줄  "
            f"choice_order={n_choice}문항  boundaries={n_bounds}문항"
        )

    return layout, cost, raw_text


# ── 적용 함수 1: 노이즈 라인 제거 (기존 기능) ─────────────────────────

def _apply_noise_lines(md: str, layout: dict) -> tuple[str, list[dict]]:
    """noise_lines → 플레이스홀더 교체."""
    log: list[dict] = []
    noise_lines = layout.get("noise_lines", [])
    if not noise_lines:
        return md, log

    lines = md.split("\n")
    total = len(lines)
    noise_set = set(nl.strip() for nl in noise_lines)

    removed_idx = [
        i for i, line in enumerate(lines)
        if line.strip() in noise_set and line.strip()
    ]

    # 환각 과다 방어: 20% 초과 제거 시 거부
    if removed_idx and len(removed_idx) / max(total, 1) > 0.20:
        log.append({
            "action": "noise_removal_rejected",
            "reason": f"제거 대상 {len(removed_idx)}/{total}줄 (20% 초과)",
        })
        return md, log

    for i in removed_idx:
        lines[i] = "【★ Vision-A 노이즈 제거 — 원본 PDF 참조】"

    log.append({"action": "noise_removed", "count": len(removed_idx)})
    return "\n".join(lines), log


# ── 적용 함수 2: 선택지 순서 재배열 ───────────────────────────────────

def _apply_choice_reorder(md: str, choice_order_list: list) -> tuple[str, list[dict]]:
    """
    Vision 보고 선택지 순서 기반 마커 재할당.

    안전 조건:
    - Vision이 5개 미분 원문자를 보고한 경우만 동작
    - Mathpix 현재 마커가 Vision 보고와 정확히 일치해야 실행
    """
    if not choice_order_list:
        return md, []

    log = []
    lines = md.split("\n")

    for entry in choice_order_list:
        prob = str(entry.get("problem", ""))
        reported = entry.get("markers_in_order", [])

        # Safety: must have exactly 5 distinct circle markers
        if sorted(reported) != sorted(_CIRCLE_MARKERS):
            log.append({"action": "choice_reorder_skip", "problem": prob,
                        "reason": "markers not 5 distinct"})
            continue
        if reported == _CIRCLE_MARKERS:
            continue  # Already correct

        # Find problem start/end line
        prob_re = re.compile(rf"^\s*{re.escape(prob)}[.．]")
        prob_start = next(
            (i for i, ln in enumerate(lines) if prob_re.match(ln)), None
        )
        if prob_start is None:
            continue

        prob_end = len(lines)
        for i in range(prob_start + 1, len(lines)):
            m = _PROB_BOUNDARY_RE.match(lines[i])
            if m and m.group(1) != prob:
                prob_end = i
                break

        # Collect choice line indices
        choice_indices = [
            i for i in range(prob_start, prob_end)
            if _CIRCLE_LINE_RE.match(lines[i])
        ]

        if len(choice_indices) != 5:
            log.append({"action": "choice_reorder_skip", "problem": prob,
                        "reason": f"found {len(choice_indices)} choice lines ≠ 5"})
            continue

        # Verify current markers match Vision's report exactly
        current = []
        for idx in choice_indices:
            cm = _CIRCLE_RE.search(lines[idx])
            if cm:
                current.append(cm.group(0))

        if current != reported:
            log.append({"action": "choice_reorder_skip", "problem": prob,
                        "reason": f"Mathpix={current} ≠ Vision={reported}"})
            continue

        # Apply remapping
        changed = 0
        for i, (line_idx, correct) in enumerate(zip(choice_indices, _CIRCLE_MARKERS)):
            wrong = reported[i]
            if wrong != correct:
                lines[line_idx] = lines[line_idx].replace(wrong, correct, 1)
                changed += 1

        if changed:
            log.append({
                "action": "choice_reorder",
                "problem": prob,
                "changed": changed,
                "from": list(reported),
                "to": _CIRCLE_MARKERS[:],
            })

    return "\n".join(lines), log


# ── 적용 함수 3: 문제 경계 (크로스블리드 힌트 보강) ────────────────────

def _apply_problem_boundary(md: str, boundaries: list) -> tuple[str, list[dict]]:
    """
    문제 경계 정보 로그 + 크로스블리드 마커에 Vision 출처 힌트 추가.
    실제 텍스트 이동은 하지 않음 (안전 우선).
    """
    if not boundaries:
        return md, []

    def _prob_int(e: dict) -> int:
        try:
            return int(e.get("problem", 0))
        except (TypeError, ValueError):
            return 0

    prob_nums = sorted(_prob_int(e) for e in boundaries)
    log = [{"action": "problem_boundaries_noted", "problems": prob_nums}]

    if "【★ 크로스블리드" in md:
        bleed_re = re.compile(r"【★ 크로스블리드 — ([^】]+)】")
        md = bleed_re.sub(
            lambda m: f"【★ 크로스블리드 — {m.group(1)} (Vision 경계: {prob_nums})】",
            md,
        )
        log.append({"action": "bleed_hint_added"})

    return md, log


# ── 적용 함수 4: 답안 영역 노이즈 ────────────────────────────────────

def _apply_answer_noise(md: str, answer_regions: list) -> tuple[str, list[dict]]:
    """
    답안 영역 노이즈 제거 — "[N점] 단독숫자" 꼬리 숫자만 안전 제거.
    Vision answer_noise_regions가 있을 때만 실행.
    """
    if not answer_regions:
        return md, []

    result, n = _ANSWER_SUFFIX_RE.subn(r"\1", md)
    log = []
    if n:
        log.append({"action": "answer_noise_removed", "count": n})
    return result, log


# ── 적용 함수 5: cases/aligned 압축 해제 ─────────────────────────────

def _apply_cases_split(md: str, formula_regions: list) -> tuple[str, list[dict]]:
    """
    cases/aligned 환경이 한 줄에 압축된 것을 여러 줄로 전개.
    Vision multiline_formula_regions가 있을 때만 실행.
    """
    if not formula_regions:
        return md, []

    counter = [0]

    def expand_env(m: re.Match) -> str:
        env  = m.group(2)
        body = m.group(3)

        # \\ 없으면 단일 행 — 건드리지 않음
        if r'\\' not in body:
            return m.group(0)

        body_stripped = body.strip()
        # 이미 개행이 있으면 정상 포맷 — 건드리지 않음
        if "\n" in body_stripped:
            return m.group(0)

        rows = [r.strip() for r in re.split(r"\\\\", body_stripped) if r.strip()]
        formatted = f"$$\n\\begin{{{env}}}\n"
        for row in rows:
            formatted += f"{row} \\\\\n"
        formatted += f"\\end{{{env}}}\n$$"
        counter[0] += 1
        return formatted

    result = _INLINE_ENV_RE.sub(expand_env, md)
    log = []
    if counter[0]:
        log.append({"action": "cases_expanded", "count": counter[0]})
    return result, log


# ── 적용 함수 6: 본문 복원 (로그 전용) ───────────────────────────────

def _apply_body_restoration(md: str, layout: dict) -> tuple[str, list[dict]]:
    """
    본문 vs 선택지 구분 분석 결과 로그 기록.
    자동 이동 미구현 — 수동 확인 권장.
    """
    bounds = layout.get("problem_boundaries", [])
    if not bounds:
        return md, []

    return md, [{
        "action": "body_restoration_skipped",
        "note": "자동 본문 이동 미구현 — 수동 확인 권장",
        "problems_with_boundaries": len(bounds),
    }]


# ── 통합 적용 ─────────────────────────────────────────────────────────

def apply_vision_layout(md: str, layout: dict) -> tuple[str, list[dict]]:
    """
    Vision 레이아웃 분석 결과를 MD에 적용 (6가지 기능).
    """
    log: list[dict] = []
    if not layout:
        return md, log

    # 1. noise_lines 제거
    md, step_log = _apply_noise_lines(md, layout)
    log.extend(step_log)

    # 2. 선택지 순서 재배열
    md, step_log = _apply_choice_reorder(md, layout.get("choice_order", []))
    log.extend(step_log)

    # 3. 문제 경계 힌트 보강
    md, step_log = _apply_problem_boundary(md, layout.get("problem_boundaries", []))
    log.extend(step_log)

    # 4. 답안 영역 노이즈
    md, step_log = _apply_answer_noise(md, layout.get("answer_noise_regions", []))
    log.extend(step_log)

    # 5. cases/aligned 압축 해제
    md, step_log = _apply_cases_split(md, layout.get("multiline_formula_regions", []))
    log.extend(step_log)

    # 6. 본문 복원 (로그 전용)
    md, step_log = _apply_body_restoration(md, layout)
    log.extend(step_log)

    return md, log


def run_vision_stage_a(
    md: str, pdf_path: Path, cost_cap_usd: float = 5.0
) -> tuple[str, list[dict], float]:
    """
    Vision Stage A 전체 실행 진입점.

    반환: (수정된 md, 로그, 소비 비용)
    Vision 불필요하면 원본 md 그대로 반환.
    """
    if not _needs_vision(md):
        return md, [{"action": "skipped", "reason": "복잡 레이아웃 없음"}], 0.0

    print(f"  [vision_a] 복잡 레이아웃 감지 — Vision 분석 시작: {pdf_path.name}")
    layout, cost, raw_text = analyze_layout(pdf_path)

    _log_vision_call(pdf_path, layout, cost, raw_text)

    if cost > cost_cap_usd:
        return md, [{"action": "cost_cap_exceeded", "cost": cost}], cost

    if layout is None:
        return md, [{"action": "analysis_failed"}], cost

    md_out, apply_log = apply_vision_layout(md, layout)
    return md_out, apply_log, cost
