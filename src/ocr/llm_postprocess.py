"""
LLM 후처리 — Mathpix MD의 한글 텍스트를 Claude Sonnet 4.6으로 교정.

핵심 전략:
  - 수식($...$)을 【수식N】 플레이스홀더로 치환 후 LLM 전달
  - LLM이 한글 OCR 오자만 교정 → 수식 복원
  - 자모 분리 패턴 감지 + 마킹 (LLM에 힌트)
  - 교정 후 수식 수 보존 검증 → 감소 시 롤백

★ Stage C 불변: 수식 내용, 숫자, 답안 번호 변경 절대 금지

비용 cap: DAILY_COST_CAP_USD (기본 $5, 환경변수 LLM_COST_CAP_USD로 변경)
로깅: log/cycle_15h/llm/{stem}_{ts}_{status}.json
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.text_only.jamo_normalize import has_jamo, mark_jamo_for_llm

load_dotenv()

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 8192
_DAILY_COST_CAP_USD = float(os.environ.get("LLM_COST_CAP_USD", "5.0"))
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "log" / "cycle_15h" / "llm"
_COST_FILE = _LOG_DIR / "daily_cost.json"

_INPUT_COST_PER_TOKEN  = 3.0  / 1_000_000
_OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000

# 【수식N】 플레이스홀더 패턴
_PH_RE = re.compile(r"【수식(\d+)】")

_SYSTEM = """당신은 수학 시험지 OCR 교정 전문가입니다.

입력 형식:
  줄번호|텍스트 (수식은 【수식N】 플레이스홀더로 표시됨)

출력 규칙:
  교정이 필요한 줄만 '줄번호|교정된텍스트' 형식으로 출력
  교정 없으면 해당 줄은 출력하지 않음
  설명/주석 없이 결과만 출력

## 교정 대상 (한글 OCR 오자)
- 자음/모음 오인식: 문제분→문제를, 베점→배점, 누등식→부등식, 사인편→사인펜
- 받침 오인식: 근율→근을, 조건율→조건을, 만폭→만족, 개수롤→개수를
- 전체 음절 오인식: 시협→시험, 저작권넙→저작권법, 처벌둰→처벌될
- 자모 분리 [자모:X] 마커: 주변 문맥으로 원래 음절 복원 (마커 자체는 제거)
  예) 핟[자모:ㄷ]톨 히→하도록 하  (존재하도록 하)

## 절대 금지
- 【수식N】 플레이스홀더 변경/삭제
- 숫자 변경 (점수, 배점, 계수)
- ①②③④⑤ 선택지 번호 변경
- 문장 추가/삭제 (교정만)
- 확신이 없는 경우 원본 유지
- 학교명, 고유명사 변경
- ## 또는 # 로 시작하는 헤더 줄 변경 (헤더는 교정 대상 아님)"""

# 수식 패턴 (display $$...$$ 먼저, 그다음 inline $...$)
_FORMULA_RE = re.compile(r"\$\$[\s\S]+?\$\$|\$[^$\n]+\$")


def _mask_formulas(text: str) -> tuple[str, list[str]]:
    """수식을 【수식N】 플레이스홀더로 치환. 원래 수식 목록 반환."""
    formulas: list[str] = []
    counter = [0]

    def repl(m: re.Match) -> str:
        formulas.append(m.group(0))
        ph = f"【수식{counter[0]}】"
        counter[0] += 1
        return ph

    masked = _FORMULA_RE.sub(repl, text)
    return masked, formulas


def _restore_formulas(text: str, formulas: list[str]) -> str:
    """【수식N】 플레이스홀더를 원래 수식으로 복원."""
    def repl(m: re.Match) -> str:
        idx = int(m.group(1))
        return formulas[idx] if idx < len(formulas) else m.group(0)
    return _PH_RE.sub(repl, text)


def _load_daily_cost() -> float:
    if not _COST_FILE.exists():
        return 0.0
    try:
        data = json.loads(_COST_FILE.read_text(encoding="utf-8"))
        return float(data.get(time.strftime("%Y-%m-%d"), 0.0))
    except (json.JSONDecodeError, OSError):
        return 0.0


def _save_daily_cost(cost: float) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    data: dict = {}
    if _COST_FILE.exists():
        try:
            data = json.loads(_COST_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    data[today] = round(float(data.get(today, 0.0)) + cost, 6)
    _COST_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * _INPUT_COST_PER_TOKEN + output_tokens * _OUTPUT_COST_PER_TOKEN


def _count_formulas(md: str) -> int:
    return len(_FORMULA_RE.findall(md))


def _has_hallucination_risk(orig_masked: str, corr_masked: str) -> bool:
    """플레이스홀더 포함 텍스트 기준 환각 검사."""
    # 플레이스홀더 수 변화 = 수식 삭제 의심
    orig_ph = len(_PH_RE.findall(orig_masked))
    corr_ph = len(_PH_RE.findall(corr_masked))
    if corr_ph < orig_ph:
        return True
    # 길이가 70% 미만으로 줄면 의심
    if len(orig_masked) > 0 and len(corr_masked) / len(orig_masked) < 0.7:
        return True
    return False


def _extract_korean_lines(md: str) -> list[tuple[int, str]]:
    """
    한글(가-힣) 2자 이상이거나 독립 자모(ㄱ-ㅣ) 포함 줄 추출.

    수식만 있는 줄 제외. 헤더(##...)도 포함.
    반환: [(line_idx, original_line_text), ...]
    """
    result = []
    for i, line in enumerate(md.split("\n")):
        stripped = line.strip()
        if not stripped:
            continue
        # 수식 전용 줄은 제외
        if re.match(r"^\$\$[\s\S]*\$\$$", stripped):
            continue
        kor_chars = len(re.findall(r"[가-힣]", stripped))
        jamo_flag = has_jamo(stripped)
        if kor_chars >= 2 or (kor_chars >= 1 and jamo_flag):
            result.append((i, line))  # 원본 line(indent 포함) 저장
    return result


def postprocess_markdown(
    md: str,
    log_stem: str = "",
    dry_run: bool = False,
) -> tuple[str, dict]:
    """
    Mathpix MD의 한글 줄을 LLM으로 교정.

    수식 플레이스홀더 치환 → LLM 교정 → 수식 복원 → 수식 수 보존 검증.
    반환: (교정된_md, 메타정보)
    """
    daily_cost = _load_daily_cost()
    if daily_cost >= _DAILY_COST_CAP_USD:
        return md, {
            "skipped": True,
            "reason": f"일일 비용 상한 (${daily_cost:.3f} ≥ ${_DAILY_COST_CAP_USD})",
            "cost_usd": 0.0,
        }
    if dry_run:
        return md, {"skipped": True, "reason": "dry_run", "cost_usd": 0.0}

    kor_lines = _extract_korean_lines(md)
    if not kor_lines:
        return md, {"skipped": True, "reason": "한글 줄 없음", "cost_usd": 0.0}

    # 수식 마스킹 + 자모 마킹 후 LLM 입력 블록 생성
    # line_formula_map: {line_idx: [formula0, formula1, ...]}
    line_formula_map: dict[int, list[str]] = {}
    input_rows: list[str] = []
    for line_idx, line_text in kor_lines:
        stripped = line_text.strip()
        masked, formulas = _mask_formulas(stripped)
        line_formula_map[line_idx] = formulas
        if has_jamo(masked):
            masked = mark_jamo_for_llm(masked)
        input_rows.append(f"{line_idx}|{masked}")

    input_block = "\n".join(input_rows)
    user_prompt = (
        "아래는 시험지 마크다운의 한글 포함 줄입니다. 수식은 【수식N】으로 표시.\n"
        "형식: 줄번호|텍스트\n"
        "OCR 오자가 있는 줄만 '줄번호|교정된텍스트' 로 출력하세요.\n"
        "교정 없는 줄은 출력 금지. 설명 금지.\n\n"
        + input_block
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    t0 = time.time()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0,
    )
    elapsed = time.time() - t0

    raw_reply    = response.content[0].text.strip()
    input_tok    = response.usage.input_tokens
    output_tok   = response.usage.output_tokens
    cost         = _estimate_cost(input_tok, output_tok)
    _save_daily_cost(cost)

    meta: dict = {
        "skipped":       False,
        "cost_usd":      round(cost, 6),
        "input_tokens":  input_tok,
        "output_tokens": output_tok,
        "elapsed_s":     round(elapsed, 2),
        "korean_lines":  len(kor_lines),
        "corrections":   0,
    }

    # 교정 적용: "줄번호|교정된텍스트(플레이스홀더 포함)"
    md_lines = md.split("\n")
    corrections_applied = 0
    rejected = 0

    for reply_line in raw_reply.split("\n"):
        reply_line = reply_line.strip()
        if "|" not in reply_line:
            continue
        parts = reply_line.split("|", 1)
        try:
            line_idx = int(parts[0])
        except ValueError:
            continue
        corrected_masked = parts[1].strip()
        orig_line = md_lines[line_idx]
        orig_masked, _ = _mask_formulas(orig_line.strip())

        # 헤더 보존: ##/# 로 시작하는 줄은 교정 거부
        if orig_line.strip().startswith("#"):
            rejected += 1
            continue

        # 환각 검사 (플레이스홀더 기준)
        if _has_hallucination_risk(orig_masked, corrected_masked):
            rejected += 1
            continue

        # 수식 복원
        formulas = line_formula_map.get(line_idx, [])
        restored = _restore_formulas(corrected_masked, formulas)

        # 수식 수 최종 확인 (복원 후)
        orig_fcount = _count_formulas(orig_line)
        corr_fcount = _count_formulas(restored)
        if corr_fcount < orig_fcount:
            rejected += 1
            continue

        md_lines[line_idx] = restored
        corrections_applied += 1

    meta["corrections"] = corrections_applied
    meta["rejected"]    = rejected
    corrected_md = "\n".join(md_lines)

    # 전체 수식 수 보존 최종 검증
    orig_total = _count_formulas(md)
    corr_total = _count_formulas(corrected_md)
    if corr_total < orig_total:
        meta["formula_regression"] = True
        meta["reason"] = f"수식 수 감소 ({orig_total}→{corr_total}) — 전체 롤백"
        meta["corrections"] = 0
        if log_stem:
            _log(log_stem, input_block, raw_reply, meta, "formula_rollback")
        return md, meta

    if log_stem:
        _log(log_stem, input_block, raw_reply, meta, "applied")

    return corrected_md, meta


def _log(stem: str, input_block: str, raw_reply: str, meta: dict, status: str) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    path = _LOG_DIR / f"{stem}_{ts}_{status}.json"
    path.write_text(json.dumps({
        "status":     status,
        "meta":       meta,
        "input":      input_block,
        "output":     raw_reply,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
