"""
Claude(Anthropic) 연동 — 구조화 + 레이아웃/그림 추론 레이어.

키(env): ANTHROPIC_API_KEY  (모델: 기본 CLAUDE_MODEL 또는 아래 DEFAULT_MODEL)

역할:
  1) structure_problems(mmd)  : Mathpix Markdown(평면 텍스트)을 문제 단위 JSON 으로 구조화
                                (번호/배점/지문/보기). 수식은 \( ... \) LaTeX 로 유지.
  2) detect_figures(image)    : 페이지 이미지에서 그림/그래프/도형 영역 후보 bbox(정규화) 반환.
                                사용자가 드래그로 보정할 초기 후보.

* REST API 직접 호출(requests). SDK 의존 없음.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")


class ClaudeError(RuntimeError):
    pass


# 실측 청구 토큰 측정용 — _call 이 각 API 응답의 usage 를 누적한다(측정 전 reset_usage).
_USAGE_LOG: list = []


def reset_usage() -> None:
    _USAGE_LOG.clear()


def get_usage() -> dict:
    def _s(k):
        return sum(int(u.get(k, 0) or 0) for u in _USAGE_LOG)
    return {"calls": len(_USAGE_LOG),
            "input_tokens": _s("input_tokens"),
            "output_tokens": _s("output_tokens"),
            "cache_read_input_tokens": _s("cache_read_input_tokens"),
            "cache_creation_input_tokens": _s("cache_creation_input_tokens")}


def _api_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        raise ClaudeError("ANTHROPIC_API_KEY 가 없습니다.")
    return k


def _call(content: list, *, max_tokens: int = 4096, model: str | None = None,
          timeout: int = 120) -> str:
    import requests
    import time
    headers = {
        "x-api-key": _api_key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    last = None
    for attempt in range(3):   # 일시적 네트워크 타임아웃/연결오류 재시도(파이프라인 보호)
        try:
            resp = requests.post(API_URL, headers=headers, json=body, timeout=timeout)
            if resp.status_code != 200:
                raise ClaudeError(f"Anthropic HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if isinstance(data.get("usage"), dict):
                _USAGE_LOG.append(data["usage"])
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        except requests.exceptions.RequestException as e:
            last = e
            if attempt < 2:
                time.sleep(3)
    raise ClaudeError(f"Anthropic API 연결 실패(3회 재시도): {last}")


def _extract_json(s: str):
    """모델 출력에서 첫 JSON 객체/배열을 추출.

    JSON 문자열 안의 LaTeX(\\(, \\frac 등)는 JSON 으로는 invalid escape 이므로,
    원본 → 백슬래시 일괄 이중화 → invalid escape 만 보정 순으로 시도한다.
    raw_decode 를 써서 문자열 내부의 {}(LaTeX 중괄호)도 정상 처리한다.
    """
    s = s.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    start = min([i for i in (s.find("{"), s.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ClaudeError(f"JSON 을 찾을 수 없음: {s[:200]}")
    body = s[start:]
    dec = json.JSONDecoder()
    # LaTeX 백슬래시만 안전 보정. 유효 JSON 이스케이프(\" \\ \/ \uXXXX, 그리고 뒤에 영문자가
    # 없는 \b\f\n\r\t)는 보존하고, 그 외 백슬래시(= LaTeX 명령 / \( \) 델리미터)만 이중화한다.
    # \frac 의 \f, \times 의 \t 처럼 'JSON 이스케이프 글자 + 영문자'는 LaTeX 명령이므로 반드시
    # 이중화한다(안 하면 \f→폼피드 로 \frac→'rac' 처럼 깨진다). 실제 \n 개행은 보존된다.
    # (기존 body.replace('\\','\\\\') 전면 이중화는 정상 \n·이미 이스케이프된 LaTeX까지 파괴 → 폐기)
    _lat = re.compile(r'\\(?!(?:["\\/]|u[0-9a-fA-F]{4}|[bfnrt](?![A-Za-z])))')
    candidates = [
        body,                                          # 1) 이미 올바르게 이스케이프된 정상 JSON
        _lat.sub(r"\\\\", body),                       # 2) LaTeX 단일 백슬래시만 선택 보정
        _lat.sub(r"\\\\", body.replace("\\\\", "\\")), # 3) 이중백슬래시 정규화 후 재보정(혼합 대비)
    ]
    for cand in candidates:
        try:
            return dec.raw_decode(cand)[0]             # 첫 객체만(뒤 trailing 무시)
        except json.JSONDecodeError:
            continue
    raise ClaudeError(f"JSON 파싱 실패: {body[:200]}")


# --- 1) 구조화 ---------------------------------------------------------------
_STRUCT_PROMPT = """너는 한국 수학 시험지 OCR 결과를 구조화하는 도우미다.
아래는 시험지 한 페이지를 Mathpix 로 OCR 한 Markdown 이다(수식은 \\( \\) 또는 \\[ \\]).

이걸 '문제 단위'로 분해해서 JSON 으로만 출력해라. 형식:
{"problems":[
  {"number":"문제번호(예 1)","points":"배점(예 3.4점, 없으면 빈문자열)",
   "stem":"발문 전체. 수식은 반드시 \\( ... \\) LaTeX 그대로 유지",
   "choices":["①보기","②보기","③보기","④보기","⑤보기"]  // 객관식 아니면 []
  }
]}

규칙:
- 머리말/저작권/시험안내/표지(교과부장·교감·교장, 출제자 등)는 문제가 아니므로 제외.
- 보기 기호(①②③④⑤ 또는 (1)~(5))는 choices 항목에서 제거하고 내용만.
- 그림/그래프가 있던 자리는 stem 안에 "[그림]" 토큰으로 표시.
- 수식 LaTeX 를 절대 풀어쓰지 말고 원형 유지. JSON 외 텍스트 출력 금지.

OCR Markdown:
---
{MMD}
---"""


def structure_problems(mmd: str, *, model: str | None = None) -> list[dict]:
    out = _call([{"type": "text", "text": _STRUCT_PROMPT.replace("{MMD}", mmd)}],
                max_tokens=8000, model=model)
    data = _extract_json(out)
    return data.get("problems", []) if isinstance(data, dict) else data


_STRUCT_VISION_PROMPT = """이 이미지는 한국 수학 시험지 한 페이지다(2단 신문형일 수 있음).
인쇄된 문제들을 '문제 단위'로 분해해 JSON 으로만 출력하라.

규칙:
- 학생 손글씨/연필 풀이/밑줄/동그라미/낙서는 완전히 무시. 인쇄된 '원본 문제'만 읽어라.
- 한글은 정확히(절대 한자/일본어로 바꾸지 말 것). 숫자·기호도 정확히.
- 수식은 LaTeX 로 \\( ... \\) 안에 정확히 유지(풀어쓰기 금지).
- 머리말/저작권/시험안내/표지(출제자·교감·교장 등)는 문제가 아니므로 제외.
- 보기 기호(①②③④⑤ 또는 (1)~(5))는 choices 에서 빼고 내용만.
- 그림/그래프 자리는 stem 에 "[그림]", 표 자리는 "[표]" 토큰으로 표시(별도 처리됨).
- 2단이면 읽기 순서는 왼쪽 단 위→아래, 그 다음 오른쪽 단.

[<보기> 상자 vs 선택지 — 반드시 구분. 자주 틀리니 특히 주의]
- "다음 <보기> 중 … 모두 고른 것은?" 유형의 <보기> 상자(ㄱ.ㄴ.ㄷ.ㄹ.ㅁ 또는 a.b.c 로
  나열된 '보기 지문' 묶음)는 발문의 일부다 → 그 항목들을 **stem 안에** 포함하라
  (예: stem 끝에 "<보기> ㄱ. \\(x^2=0\\)  ㄴ. \\(-x^2+4x-10\\)  ㄷ. …").
- choices(선택지)에는 **오직 ①②③④⑤(또는 (1)~(5))로 표시된 정답 후보만** 넣어라. 이 유형의
  선택지는 대개 "ㄱ, ㄹ" · "ㄴ, ㄷ" · "ㄱ, ㄷ, ㅁ" 처럼 <보기> 기호들의 조합이다 —
  그 **조합 문자열**을 choices 에 넣어라(예: choices=["ㄱ, ㄹ","ㄴ, ㄷ","ㄷ, ㅁ","ㄱ, ㄴ, ㄹ","ㄱ, ㄷ, ㅁ"]).
- ⚠️ <보기> 상자의 ㄱ/ㄴ/ㄷ 항목(수식 자체)을 choices 로 넣지 마라. 그건 보기 지문이지 선택지가 아니다.

[보기 기호 자모 정확도 — ㄹ 을 ㅎ/ㅁ 으로 오독 금지. 자주 틀리니 특히 주의]
- 보기 기호는 인쇄된 한글 자음이며 항상 ㄱ→ㄴ→ㄷ→ㄹ→ㅁ→ㅂ→ㅅ '사전순으로 빠짐없이 연속'해서 붙는다.
  4번째 기호는 반드시 ㄹ 이다(ㅎ 도 ㅁ 도 아니다). 스캔이 흐리면 ㄹ 이 ㅎ(위 꼭지+ㅁ)이나 ㅁ(사각형)으로
  잘못 보이기 쉽다 — 절대 혼동하지 마라.
- 원문자 라벨(㉠㉡㉢㉣㉤…)도 완전히 동일한 규칙이다 — 항상 ㉠→㉡→㉢→㉣→㉤ 사전순으로 빠짐없이
  연속하며 4번째는 반드시 ㉣(원 안의 ㄹ)이다. ㉣ 을 ㉤(원 안의 ㅁ)이나 ㉢(원 안의 ㄷ)으로 오독하지
  마라. 한 상자 안에 같은 원문자 라벨이 두 번 나오는 일은 없다 — 중복이 보이면 그건 오독이다.
- <보기> 상자의 라벨 순서(ㄱㄴㄷㄹㅁ… / ㉠㉡㉢㉣㉤…)와 '개수'를 먼저 확정한 뒤, choices 에 쓰인
  기호를 그 집합으로 교차검증해 교정하라. 상자에 ㄱㄴㄷㄹ 만 있으면 choices 에 ㅁ·ㅂ·ㅅ·ㅎ 이 나올
  수 없다(원문자도 동일).
- ㅎ 은 이 유형의 보기 라벨로 거의 쓰이지 않는다 — choices 에서 ㅎ 이 보이면 십중팔구 ㄹ 의 오독이다.
- 학생 손글씨가 아니라 '인쇄된 기호'만 읽어라(재강조).

[서술형(주관식) — 빠뜨리기 매우 쉬우니 특히 주의]
- 객관식뿐 아니라 '서술형/주관식' 문항도 **하나도 빠짐없이 모두** 추출하라.
- "□ 서술형 문항은 답안지에 번호를 명시하고 풀이과정과 답을 쓰시오" 같은 '안내 문구'와
  "★ 확인사항" 같은 끝맺음 박스는 그 자체로는 문제가 아니므로 제외한다. 그러나 그 아래로
  이어지는 실제 서술형 문항(서술형1, 서술형2, …)은 **반드시 각각 별도 문제로** 추출하라
  (안내문이 보인다고 그 아래 서술형 본문까지 통째로 건너뛰면 절대 안 된다).
- 서술형 문항의 number 는 시험지에 적힌 그대로 넣어라(예 "서술형1", "서술형2"; 시험지가
  '서술형 1' 표기면 그대로). 객관식은 숫자 번호 그대로("1","2",…).
- 서술형은 선택지가 없으므로 choices 는 []. 배점([7점],[8점] 등)은 points 에 넣어라.

형식:
{"problems":[{"number":"1","points":"3.4점(없으면 빈문자열)","stem":"발문 전체(수식 \\(..\\))","choices":["보기1","보기2",...]}]}
객관식이 아니면(서술형·주관식) choices 는 []. JSON 외 출력 금지."""


_BOGI_ORDER = "ㄱㄴㄷㄹㅁㅂㅅ"
_CIRCLED_ORDER = "㉠㉡㉢㉣㉤㉥㉦㉧"
_COMBO_CHOICE_RE = re.compile(r"^\s*[ㄱ-ㅎ](?:\s*,\s*[ㄱ-ㅎ])*\s*$")
_COMBO_CIRCLED_RE = re.compile(r"^\s*[㉠-㉳](?:\s*,\s*[㉠-㉳])*\s*$")
_BOKI_HDR_RE = re.compile(r"[<〈]\s*보\s*기\s*[>〉]")
# 라벨 후보: 원문자(구두점 없음) / 자모+구두점('ㄱ.'·'ㄴ·') — build_exam._BOKI_ITEM 과 동일 두 꼴
_CIRCLED_LABEL_RE = re.compile(r"([㉠-㉳])")
_JAMO_LABEL_RE = re.compile(r"([ㄱ-ㅎ])\s*[.·]")


def _bogi_label_count(stem: str) -> int:
    """stem 의 <보기> 상자 라벨(ㄱㄴㄷㄹㅁ…)이 몇 개까지 '연속으로' 등장하는지 추정.
    라벨은 '기호 + 마침표/가운뎃점' 형태(예 'ㄱ.', 'ㄴ·'). 못 파싱하면 0(→ㅁ 교정 skip)."""
    n = 0
    for sym in _BOGI_ORDER:
        if re.search(sym + r"\s*[.·]", stem):
            n += 1
        else:
            break
    return n


def _reseq_labels(text: str, pat: re.Pattern, order: str) -> str:
    """text 안의 라벨 시퀀스를 사전순 정식 시퀀스로 위치 기반 강제 교정.

    <보기> 상자 라벨은 관례상 무조건 사전순 연속(ㄱㄴㄷㄹ… / ㉠㉡㉢㉣…)이므로
    n번째로 등장한 라벨은 order 의 n번째 기호여야 한다. 아니면 오독 → 교체.
    보수 가드: 라벨 2개 이상 + 첫 라벨이 order[0] 일 때만. 이미 정식이면 no-op.
    """
    ms = list(pat.finditer(text))
    if len(ms) < 2 or len(ms) > len(order):
        return text
    found = [m.group(1) for m in ms]
    if found[0] != order[0]:
        return text
    want = list(order[:len(ms)])
    if found == want:
        return text
    out, last = [], 0
    for m, w in zip(ms, want):
        out.append(text[last:m.start(1)])
        out.append(w)
        last = m.end(1)
    out.append(text[last:])
    return "".join(out)


def _fix_boki_stem_labels(problems):
    """stem 의 <보기> 상자 '라벨 자체' 오독을 순서 규칙으로 교정.

    2026-07-06 실사고: opus 가 원문자 4번째 라벨 ㉣ 을 ㉤ 으로, 5번째 ㉤ 을 ㉢ 으로
    오독해 빌드본에 ㉠㉡㉢㉤㉢ 이 찍혔다(프롬프트 규칙만으로는 부족). 상자 라벨은
    사전순 연속이 절대 관례이므로 헤더 이후 영역의 라벨을 위치로 강제 교정한다.
    잔여 위험: 상자 항목 본문이 다른 라벨을 역참조하는 경우(드묾) 오교정 가능 —
    원문자는 헤더 뒤부터, 자모는 구두점('ㄱ.') 요구로 오탐을 줄인다.
    """
    if not isinstance(problems, list):
        return problems
    for p in problems:
        if not isinstance(p, dict):
            continue
        stem = p.get("stem")
        if not isinstance(stem, str):
            continue
        m = _BOKI_HDR_RE.search(stem)
        if not m:
            continue
        head, box = stem[:m.end()], stem[m.end():]
        box = _reseq_labels(box, _CIRCLED_LABEL_RE, _CIRCLED_ORDER)
        box = _reseq_labels(box, _JAMO_LABEL_RE, _BOGI_ORDER)
        if head + box != stem:
            p["stem"] = head + box
    return problems


def _bogi_circled_count(stem: str) -> int:
    """stem 의 <보기> 상자 원문자 라벨(㉠㉡㉢…)이 몇 개까지 '연속으로' 등장하는지 추정."""
    n = 0
    for sym in _CIRCLED_ORDER:
        if sym in stem:
            n += 1
        else:
            break
    return n


def _fix_bogi_jamo(problems):
    """<보기> 조합형 choices 의 자모 오독(ㄹ→ㅎ/ㅁ)을 위치 규칙으로 '보수적' 교정.

    - 모든 선택지가 순수 자모 기호 조합(예 'ㄱ, ㄹ')일 때만 이 유형으로 간주(일반 객관식 불건드림).
    - stem 의 상자 라벨 집합(valid)을 알면: ㅎ→ㄹ(ㄹ 이 유효할 때만), 상자에 ㅁ 없고 ㄹ 있으면 ㅁ→ㄹ.
    - 라벨 집합을 못 구하면(valid=None): ㅎ→ㄹ 만(ㅎ 은 이 유형 라벨로 사실상 불가). ㅁ 은 과교정 위험이라 skip.
    """
    if not isinstance(problems, list):
        return problems
    for p in problems:
        if not isinstance(p, dict):
            continue
        ch = p.get("choices")
        if not isinstance(ch, list) or len(ch) < 2:
            continue
        stem = p.get("stem", "") or ""
        if all(isinstance(c, str) and _COMBO_CHOICE_RE.match(c) for c in ch):
            n = _bogi_label_count(stem)
            valid = set(_BOGI_ORDER[:n]) if n else None
            for i, c in enumerate(ch):
                out = []
                for s in (t.strip() for t in c.split(",")):
                    if s == "ㅎ" and (valid is None or "ㄹ" in valid):
                        s = "ㄹ"
                    elif s == "ㅁ" and valid is not None and "ㅁ" not in valid and "ㄹ" in valid:
                        s = "ㄹ"
                    out.append(s)
                ch[i] = ", ".join(out)
        elif all(isinstance(c, str) and _COMBO_CIRCLED_RE.match(c) for c in ch):
            # 원문자 조합형("㉠, ㉣") — 자모형과 대칭 규칙: ㉭(원 ㅎ)은 라벨로 사실상
            # 불가 → ㉣, 상자에 ㉤ 없고 ㉣ 있으면 ㉤→㉣ (2026-07-06 원문자 오독 사고).
            n = _bogi_circled_count(stem)
            valid = set(_CIRCLED_ORDER[:n]) if n else None
            for i, c in enumerate(ch):
                out = []
                for s in (t.strip() for t in c.split(",")):
                    if s == "㉭" and (valid is None or "㉣" in valid):
                        s = "㉣"
                    elif s == "㉤" and valid is not None and "㉤" not in valid and "㉣" in valid:
                        s = "㉣"
                    out.append(s)
                ch[i] = ", ".join(out)
    return problems


def structure_problems_vision(image_path: str, *, model: str | None = None) -> list[dict]:
    """페이지 이미지 → 문제별 JSON(번호/배점/지문/보기). Mathpix OCR 대체.

    Claude 비전이 한글+수식을 직접 읽어 Mathpix 한글 깨짐(한자화)을 해결한다.
    손글씨는 무시하고 인쇄 원본만 구조화한다.
    """
    content = [_encode_image(image_path), {"type": "text", "text": _STRUCT_VISION_PROMPT}]
    out = _call(content, max_tokens=8000, model=model)
    data = _extract_json(out)
    problems = data.get("problems", []) if isinstance(data, dict) else data
    # 상자 라벨을 먼저 정식 시퀀스로 교정 → choices 교차검증이 교정된 라벨 집합을 쓴다
    return _fix_bogi_jamo(_fix_boki_stem_labels(problems))


# --- 2) 그림/낙서 영역 감지 --------------------------------------------------
# 좌표는 '픽셀'로 받고(모델이 정규화 float 을 자주 틀림) 코드에서 0~1 정규화한다.
_FIG_PROMPT = """이 이미지는 수학 시험지 한 페이지(가로 {W}px, 세로 {H}px)다.
'시험 문제에 속한 수학적 그림'만 찾아라: 함수 그래프, 평면도형(삼각형·원 등),
입체도형(정사면체·정육면체·원기둥 등), 좌표평면/좌표공간, 기하 다이어그램.

다음은 **절대 포함하지 마라**(오검출이 매우 잦으니 특히 주의):
- 로고·배너·광고·캐릭터·마스코트, "정답 및 해설"·"선생님이 직접 풀어주신" 등 해설지/출판사
  안내 그림, 표지·워터마크·열쇠/리본 같은 장식 이미지
- 수식, 일반 텍스트, 보기 ①~⑤, 격자형 표(별도 처리), 학생 손글씨/낙서, 도장·직인,
  머리말·꼬리말, 페이지 번호
확실히 '수학 문제 풀이에 필요한 도형/그래프'인 것만 포함하라. 조금이라도 애매하면 제외하라.

각 그림이 '어느 문제에 속하는지' 문제 번호도 판단하라(그림 주변/위의 문항 번호).
JSON 으로만 출력: {"figures":[{"label":"간단설명","bbox":[x0,y0,x1,y1],"problem":"문제번호(모르면 빈문자열)"}]}
- **bbox 정확도가 매우 중요**: 그림(곡선·도형·좌표축·점·라벨)을 빠짐없이 '딱 맞게' 감싸라.
  학생 손글씨 풀이가 그림 위/옆에 겹쳐 있어도 인쇄된 그림 본체를 기준으로 감싸되, 여백의 손글씨
  계산식까지 넓게 잡지 마라. 그림이 작거나 흐릿해도 그 그림 영역을 정확히 지정하라.
  bbox 안에 그림이 없고 텍스트/여백만 있으면 잘못된 것이다.
- bbox 는 픽셀 정수 좌표(좌상단 x0,y0 / 우하단 x1,y1).
- 수학 그림이 없으면 {"figures":[]}. JSON 외 출력 금지."""

_SCRIBBLE_PROMPT = """이 이미지는 수학 시험지에서 잘라낸 그림 영역(가로 {W}px, 세로 {H}px)이다.
인쇄된 '원래 도형' 위에 덧그려지거나 끼어든 '지워야 할 것'을 모두 찾아라:
학생 연필/펜 손글씨, 풀이 낙서, 밑줄·동그라미·체크·화살표 표시, 도장·직인, 스캔 얼룩·번짐.
인쇄된 원본 도형의 선·좌표축·점·라벨은 제외하라. 조금이라도 덧그린 흔적이면 포함(적극적으로).
JSON 으로만 출력: {"marks":[{"label":"설명","bbox":[x0,y0,x1,y1]}]}
- bbox 는 픽셀 정수 좌표. 없으면 {"marks":[]}. JSON 외 출력 금지."""


def _encode_image(image_path: str) -> dict:
    with open(image_path, "rb") as f:
        raw = f.read()
    # media type 은 확장자가 아니라 매직바이트로 판별 — Gemini 재작도가 JPEG 데이터를
    # .png 로 저장하는 경우가 있어(2026-07-02 실측) 확장자 기반은 API 400 을 낸다.
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        media = "image/png"
    elif raw[:2] == b"\xff\xd8":
        media = "image/jpeg"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        media = "image/webp"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        media = "image/gif"
    else:
        ext = os.path.splitext(image_path)[1].lstrip(".").lower()
        media = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
    b64 = base64.b64encode(raw).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}


def _image_wh(image_path: str) -> tuple[int, int]:
    from PIL import Image
    with Image.open(image_path) as im:
        return im.size


def _normalize_bbox(b: list, w: int, h: int) -> list:
    """픽셀 또는 정규화 bbox 를 모두 0~1 정규화로 통일하고 클램프."""
    if not b or len(b) < 4:
        return []
    x0, y0, x1, y1 = b[:4]
    # 이미 0~1 정규화로 보이면 그대로
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.5:
        nb = [x0, y0, x1, y1]
    else:
        nb = [x0 / w, y0 / h, x1 / w, y1 / h]
    nb = [min(1.0, max(0.0, v)) for v in nb]
    return [round(v, 4) for v in nb]


@dataclass
class Region:
    label: str
    bbox: list           # [x0,y0,x1,y1] normalized 0~1
    problem: str = ""    # 이 영역이 속한 문제 번호(그림 감지에서만 채워짐)


def _detect(image_path: str, prompt: str, key: str, model: str | None) -> list[Region]:
    w, h = _image_wh(image_path)
    text = prompt.replace("{W}", str(w)).replace("{H}", str(h))
    content = [_encode_image(image_path), {"type": "text", "text": text}]
    out = _call(content, max_tokens=2000, model=model)
    data = _extract_json(out)
    items = data.get(key, []) if isinstance(data, dict) else data
    res = []
    for it in items:
        nb = _normalize_bbox(it.get("bbox", []), w, h)
        if nb:
            res.append(Region(label=it.get("label", ""), bbox=nb,
                              problem=str(it.get("problem", "") or "")))
    return res


def detect_figures(image_path: str, *, model: str | None = None) -> list[Region]:
    """진짜 그림(그래프/도형/다이어그램) 영역 후보. 사용자가 드래그로 보정."""
    return _detect(image_path, _FIG_PROMPT, "figures", model)


def detect_scribbles(image_path: str, *, model: str | None = None) -> list[Region]:
    """지워야 할 낙서/손글씨/직인 영역 후보. 인페인팅 대상."""
    return _detect(image_path, _SCRIBBLE_PROMPT, "marks", model)

_MPL_PROMPT = """이 이미지는 수학 시험지에서 잘라낸 '그림'(함수 그래프/평면·입체 도형/좌표평면/다이어그램)이다.
학생 손글씨·낙서·스캔 얼룩은 무시하고, '인쇄된 원래 도형'을 matplotlib 로 **깔끔하고 정확하게**
재현하는 파이썬 코드를 작성하라(목표: 출판 품질의 단정한 선화).

정확성:
- 곡선/직선의 모양·교점·증감·점근선, 도형의 종류·꼭짓점·각·변 길이 비율, 점과 좌표, 화살표를 정확히.
- **원본에 실제로 보이는 것만 그려라**: 원본에 없는 도형(오각형·별·격자 등)을 임의로 추가하거나
  추측으로 채우지 마라. 타원이면 타원, 화살표면 화살표 — 원본 형태를 그대로 따르고 새 도형을 지어내지 마라.
- 이차곡선(쌍곡선·포물선·타원)은 그 방정식으로부터 np 로 정확히 plot 하라. 점근선만 그리거나
  직선으로 대체하는 것은 절대 금지. 원은 plt Circle 또는 매개변수 plot 으로. 원본에 있는
  곡선·원·도형·점·직선을 **하나도 빠뜨리지 마라**(쌍곡선이면 양쪽 가지 모두).
- **원본에 있는 모든 텍스트·숫자를 빠짐없이 정확한 위치에 넣어라**: 점 라벨(A,B,P,Q,M,O…),
  좌표값((4,0,0),(0,4,0) 등), 길이·수치(8, 3:1, √6 등), 각도 표시, 변수명. 숫자 하나도 누락 금지.
  원본에 보이는 라벨인데 빠뜨리면 실패다. 한글 라벨은 그대로.
깔끔함(중요):
- 불필요한 격자/배경 제거(ax.grid(False)), 선은 검정 단색(color='k'), 군더더기 없이.
- 좌표축이 있으면 화살표 달린 x/y 축만 그리고 눈금은 필요한 점만. 좌표축이 없는 순수 도형이면 ax.axis('off').
- 입체도형(정사면체·정육면체·원기둥 등)은 2D 투영 선화로: 보이는 모서리는 실선, 숨은 모서리는 점선(linestyle='--').
형식:
- `fig, ax = plt.subplots(figsize=(...))` 형태. set_aspect('equal') 등 비율 적절히.
- **import 금지**(np, plt, math 제공됨). plt.savefig/plt.show 호출 금지.
- 파일/네트워크/시스템 접근 금지. 코드만 출력(설명·마크다운 펜스 금지).

[입체도형 그리기 참고] 정육면체 비스듬 투영 패턴(직육면체·원기둥·원뿔·정사면체도 이 방식으로 응용 — 앞면을 그린 뒤 offset 만큼 평행이동한 뒷면, 숨은 모서리는 점선 '--'):
  s, off = 1.0, 0.4
  f = [(0,0),(s,0),(s,s),(0,s)]; bk = [(x+off, y+off) for x,y in f]
  for a,c in [(0,1),(1,2),(2,3),(3,0)]: ax.plot([f[a][0],f[c][0]],[f[a][1],f[c][1]],'k-')
  for a,c in [(1,2),(2,3)]: ax.plot([bk[a][0],bk[c][0]],[bk[a][1],bk[c][1]],'k-')
  for i in [1,2,3]: ax.plot([f[i][0],bk[i][0]],[f[i][1],bk[i][1]],'k-')
  ax.plot([bk[0][0],bk[1][0]],[bk[0][1],bk[1][1]],'k--'); ax.plot([bk[0][0],bk[3][0]],[bk[0][1],bk[3][1]],'k--'); ax.plot([f[0][0],bk[0][0]],[f[0][1],bk[0][1]],'k--')
원기둥: 위·아래 타원 + 양옆 세로 모선, 뒤쪽 타원호만 점선. 원뿔: 밑면 타원 + 꼭짓점에서 두 모선. 정사면체: 밑면 삼각형 + 꼭짓점에서 세 모서리(뒤 모서리 점선)."""


_MPL_RETRY = """

[이전 시도] 아래 코드로 그렸으나 문제가 있었다. 지적된 문제점을 고쳐 전체 코드를 다시 작성하라.
--- 이전 코드 ---
{CODE}
--- 지적된 문제점 ---
{ISSUES}
수정된 전체 matplotlib 코드만 출력(설명·마크다운 펜스 금지)."""


_SPEC_PROMPT = """이 이미지는 수학 시험지의 그림이다(학생 손글씨·낙서는 무시, 인쇄된 그림만).
matplotlib 로 정확히 다시 그릴 수 있도록 그림을 '구조 데이터(JSON)'로 추출하라.
AI 가 그림을 통째로 상상해 그리는 게 아니라, 원본에 실제로 있는 요소를 데이터로만 뽑는다.

좌표계: 좌표축이 보이면 그 좌표값을, 없으면 그림 비율에 맞춰 적당한 좌표를 정해 모든 요소를 표현.
**원본에 실제로 있는 것만**. 없는 도형(오각형·별 등)을 지어내지 마라.

JSON 형식(해당 없는 키는 생략):
{
 "axes": {"show": true/false, "xlabel": "x", "ylabel": "y"},
 "ellipses": [{"cx":0,"cy":0,"a":(가로반지름),"b":(세로반지름),"style":"solid|dashed"}],
 "circles": [{"cx":,"cy":,"r":,"style":}],
 "conics": [{"kind":"hyperbola|parabola","cx":0,"cy":0,"a":,"b":,"axis":"x|y","style":}],
 "polylines": [{"points":[[x,y],[x,y],...],"closed":true/false,"style":}],
 "segments": [{"x1":,"y1":,"x2":,"y2":,"style":,"arrow":true/false}],
 "points": [{"x":,"y":,"marker":"o|*","label":"A"}],
 "texts": [{"x":,"y":,"text":"결석"}],
 "dims": [{"text":"16 cm","x1":,"y1":,"x2":,"y2":}]
}
- conics: hyperbola 는 x²/a² − y²/b² = 1 (axis="x") 또는 y²/a² − x²/b² = 1 (axis="y") 형태의 a,b.
- 한글 텍스트(결석·전극·반사 장치 등)는 texts 에 그대로 넣어라. 모든 숫자·치수·라벨 포함.
- 점 옆 글자(A,B,P…)는 points 의 label 로. JSON 외 출력 금지."""


def figure_to_spec(image_path: str, *, model: str | None = None) -> dict:
    """크롭된 그림 → 구조 데이터(JSON). spec_to_matplotlib 로 정확히 렌더(환각 방지)."""
    content = [_encode_image(image_path), {"type": "text", "text": _SPEC_PROMPT}]
    out = _call(content, max_tokens=3000, model=model)
    data = _extract_json(out)
    return data if isinstance(data, dict) else {}


def figure_to_matplotlib(image_path: str, *, model: str | None = None,
                         prev_code: str | None = None, issues: list | None = None) -> str:
    """크롭된 그림 -> 재현용 matplotlib 코드(문자열). 렌더는 figure_ai 가 담당.

    prev_code/issues 가 주어지면 '이전 시도 + 지적된 차이점'을 함께 보내 교정 재생성한다
    (재작도 검증 루프용).
    """
    text = _MPL_PROMPT
    if prev_code and issues:
        text = _MPL_PROMPT + _MPL_RETRY.replace("{CODE}", prev_code).replace(
            "{ISSUES}", "\n".join(f"- {x}" for x in issues))
    content = [_encode_image(image_path), {"type": "text", "text": text}]
    out = _call(content, max_tokens=2500, model=model)
    out = re.sub(r"^```(?:python)?|```$", "", out.strip(), flags=re.M).strip()
    return out


_VERIFY_PROMPT = """두 이미지를 비교한다.
[이미지 A] = 원본 시험지 그림(손글씨·낙서·스캔얼룩이 섞여 있을 수 있음).
[이미지 B] = 그 그림을 코드로 깔끔하게 재작도한 결과.

B 가 A 의 '핵심 수학 구조'를 올바르게 재현했는지 채점하라.
통과(ok=true): 도형의 종류·구성(정육면체/원기둥/그래프 등)이 맞고, 꼭짓점·라벨(A,B,P,Q 등)이
정확하며, 곡선의 모양·교점·증감과 점·선의 '대략적' 배치가 원본과 일치한다.
허용(감점·탈락 아님): 점·라벨의 미세한 위치 차이, 크기/비율의 작은 차이, 색·선두께·배경 스타일,
원본의 손글씨/낙서/가장자리 자국이 B 에서 지워진 것(오히려 바람직함).
탈락(ok=false): 도형 종류가 다름, 원본의 핵심 곡선·도형·원이 누락(예: 쌍곡선 문제인데 쌍곡선이
없고 직선만 있음, 원이 빠짐, 포물선이 직선으로 됨), 꼭짓점·라벨·점이 누락/오기, 곡선 모양·교점·
증감이 명백히 다름, 필요한 숫자·좌표·길이 라벨이 빠짐. 핵심 요소가 빠지면 관대하게 보지 말고 탈락.
탈락(ok=false, 추가 생성): B 에 A 에 없는 내용이 '추가'된 경우도 반드시 탈락 —
특히 원본에 없는 글자 줄/문자 나열(예: 한글 자모 나열 "ㄱㄴㄷㄹ…", 알파벳 나열 "ABCD…"),
범례·캡션·제목·설명 문장·워터마크, 없는 도형·눈금·수치. 읽을 수 없는 자국을 임의의 글자로
'복원'해 넣은 것도 추가 생성이다. 추가된 내용은 issues 에 그대로 옮겨 적어라.
JSON 으로만 출력: {"score": 0~100, "ok": true/false, "issues": ["구체적 차이점", ...]}
- 핵심 구조·라벨이 맞고 추가 생성이 없으면 score>=80, ok=true. 구조/라벨 오류나 추가 생성이
  있으면 ok=false. JSON 외 금지."""


def verify_redraw(original_path: str, redrawn_path: str, *, model: str | None = None) -> dict:
    """원본 그림 vs 재작도 결과를 Claude 비전이 충실도 채점. {score, ok, issues} 반환."""
    content = [
        {"type": "text", "text": "[이미지 A] 원본 그림:"},
        _encode_image(original_path),
        {"type": "text", "text": "[이미지 B] 재작도 결과:"},
        _encode_image(redrawn_path),
        {"type": "text", "text": _VERIFY_PROMPT},
    ]
    out = _call(content, max_tokens=1500, model=model)
    data = _extract_json(out)
    return {"score": int(data.get("score", 0) or 0),
            "ok": bool(data.get("ok", False)),
            "issues": list(data.get("issues", []) or [])}


_TABLE_VERIFY_PROMPT = """[이미지] = 원본 시험지의 표.
[추출 데이터] = 그 표에서 인식한 행별 셀 내용(파이프 | 로 셀 구분):
{TABLE}

추출 데이터가 원본 표와 정확히 일치하는지 채점하라. 셀 내용(한글/숫자/수식),
행·열 개수, 병합 여부를 본다. 학생 손글씨는 무시(원본의 인쇄 내용 기준).
JSON 으로만: {"score": 0~100, "ok": true/false, "issues": ["구체적 불일치", ...]}
- score>=85 이고 내용·구조 오류가 없으면 ok=true. JSON 외 출력 금지."""


def verify_table(original_path: str, grid: list, *, model: str | None = None) -> dict:
    """원본 표 이미지 vs 추출 grid 를 Claude 비전이 대조 채점. {score, ok, issues}."""
    def _ct(c): return c.get("text", "") if isinstance(c, dict) else str(c)
    tbl = "\n".join(" | ".join(_ct(c) for c in row) for row in (grid or []))
    content = [_encode_image(original_path),
               {"type": "text", "text": _TABLE_VERIFY_PROMPT.replace("{TABLE}", tbl)}]
    out = _call(content, max_tokens=1200, model=model)
    data = _extract_json(out)
    return {"score": int(data.get("score", 0) or 0),
            "ok": bool(data.get("ok", False)),
            "issues": list(data.get("issues", []) or [])}


# --- 3) 표 영역 감지 + 구조화 추출 (Mathpix 한글 약점 → Claude 비전 담당) ----
_TABLE_DETECT_PROMPT = """이 이미지는 수학 시험지 한 페이지(가로 {W}px, 세로 {H}px)다.
'인쇄된 표(격자 형태: 도수분포표·확률분포표·자료표·참거짓표 등)' 영역만 모두 찾아라.
다음은 표가 아니므로 **절대 포함하지 마라**: 일반 텍스트, 수식, 보기①~⑤,
그림/그래프/도형, 학생 손글씨·연필 낙서, 2단 레이아웃의 단 경계 세로줄, 문제 묶음 박스.
JSON 으로만 출력: {"tables":[{"label":"설명","bbox":[x0,y0,x1,y1],"problem":"문제번호(모르면 빈문자열)"}]}
- bbox 는 픽셀 정수 좌표(좌상단 x0,y0 / 우하단 x1,y1).
- 인쇄된 표가 없으면 {"tables":[]}. JSON 외 출력 금지."""


def detect_tables(image_path: str, *, model: str | None = None) -> list[Region]:
    """인쇄된 표(격자) 영역 후보. 2단 경계/문제박스/손글씨는 제외하도록 지시."""
    return _detect(image_path, _TABLE_DETECT_PROMPT, "tables", model)


_TABLE_GRID_PROMPT = """이 이미지는 수학 시험지에서 잘라낸 '표'다.
인쇄된 표의 행/열 구조와 각 셀 내용을 정확히 읽어라.

규칙:
- 학생 손글씨·연필 낙서·밑줄·동그라미는 **무시**하고, 인쇄된 표의 원래 내용만 추출.
- 한글은 정확히(절대 한자/일본어로 바꾸지 말 것). 숫자·기호도 정확히.
- 가로로 병합된 셀은 colspan, 세로로 병합된 셀은 rowspan 으로 표기.
- 수식이 든 셀은 LaTeX 를 \\( ... \\) 로 감싸 text 에 넣어라. 빈 셀은 text:"".
JSON 으로만 출력:
{"rows":[[{"text":"셀내용","colspan":1,"rowspan":1}, ...], ...]}
표가 아니거나 읽을 수 없으면 {"rows":[]}. JSON 외 출력 금지."""


def table_to_grid(image_path: str, *, model: str | None = None) -> list[list[dict]]:
    """크롭된 표 이미지 -> table.py 가 받는 rows([[{text,colspan,rowspan}...]...]).

    손글씨는 무시하고 인쇄된 표 구조·내용만 한글 정확하게 추출한다.
    """
    content = [_encode_image(image_path), {"type": "text", "text": _TABLE_GRID_PROMPT}]
    out = _call(content, max_tokens=3000, model=model)
    data = _extract_json(out)
    rows = data.get("rows", []) if isinstance(data, dict) else data
    norm: list[list[dict]] = []
    for row in (rows or []):
        nr = []
        for cell in row:
            if isinstance(cell, dict):
                nr.append({"text": str(cell.get("text", "")),
                           "colspan": int(cell.get("colspan", 1) or 1),
                           "rowspan": int(cell.get("rowspan", 1) or 1)})
            else:
                nr.append({"text": str(cell), "colspan": 1, "rowspan": 1})
        if nr:
            norm.append(nr)
    return norm


# --- 발문↔표 중복 제거 (패턴 무관, 의미 기반) -------------------------------
# 표를 이미지로 넣으면 같은 내용이 발문에 텍스트로도 남아 중복된다. '(가)/(나)' 같은
# 마커 패턴으로 거르는 건 a,b·①② 등 다른 형식 표를 못 잡으므로, 표 이미지와 발문을
# Claude 에 함께 줘서 '내용이 겹치는 부분'을 의미적으로 제거한다.
_CLEAN_STEM_PROMPT = """위 이미지는 어떤 수학 문제에 들어가는 '표(또는 조건 상자)'다.
아래 [발문]에는 이 표의 내용이 '텍스트로 그대로 중복 서술'된 부분이 있을 수 있다.
표가 그 내용을 대신 보여주므로, 발문에서 '표와 겹치는 부분만' 삭제하라.

규칙:
- 표 안에 있는 조건/항목과 같은 내용이 발문에 또 있으면 그 부분을 제거한다.
  ((가)/(나) 든 a/b 든 ①②든, 표 형식과 무관하게 '내용이 같으면' 제거.)
- 문제가 무엇을 구하라는지(질문)와 발문의 도입 문장은 절대 삭제하지 않는다.
- 표에 없는 내용은 그대로 둔다.
- 수식은 $ ... $ 로 표기돼 있다. 남기는 수식의 $ ... $ 안 내용은 절대 바꾸지 마라.
- 삭제로 생긴 어색한 접속/빈 줄은 자연스럽게 다듬되 새 내용을 지어내지 마라.
- 정리된 발문 '텍스트만' 출력한다(설명·따옴표·코드블록 없이).

[발문]
"""


def _runs_to_text(runs: list) -> str:
    """runs([{type,text|latex}...]) -> 편집용 텍스트(수식은 $latex$)."""
    parts = []
    for r in runs or []:
        if r.get("type") == "eqn":
            parts.append("$" + (r.get("latex") or r.get("hwp_script") or "") + "$")
        else:
            parts.append(r.get("text", ""))
    return "".join(parts)


def _text_to_runs(t: str) -> list:
    """$latex$ 포함 텍스트 -> runs(frontend textToRuns 와 동일 규칙)."""
    out, last = [], 0
    for m in re.finditer(r'\$([^$]+)\$', t):
        if m.start() > last and t[last:m.start()]:
            out.append({"type": "text", "text": t[last:m.start()]})
        out.append({"type": "eqn", "latex": m.group(1)})
        last = m.end()
    if last < len(t) and t[last:]:
        out.append({"type": "text", "text": t[last:]})
    return out


def clean_stem_against_table(stem_runs: list, table_image_path: str, *,
                             model: str | None = None) -> list:
    """표 이미지와 발문(runs)을 대조해 발문에 중복된 표 내용을 의미적으로 제거한다.

    '(가)/(나)' 같은 패턴에 의존하지 않아 a,b·①② 등 어떤 표 형식이든 처리한다.
    표가 담당하는 조건은 빼고 질문·도입 문장은 보존한다. 실패 시 원본 runs 반환(안전).
    """
    text = _runs_to_text(stem_runs)
    if not text.strip():
        return stem_runs
    try:
        content = [_encode_image(table_image_path),
                   {"type": "text", "text": _CLEAN_STEM_PROMPT + text}]
        cleaned = (_call(content, max_tokens=2048, model=model) or "").strip()
        cleaned = cleaned.strip("`").strip()
        if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
            cleaned = cleaned[1:-1].strip()
        return _text_to_runs(cleaned) if cleaned else stem_runs
    except Exception:  # noqa: BLE001 (네트워크/파싱 실패 → 원본 유지)
        return stem_runs


# --- 발문↔그림(<보기> 상자) 중복 제거 (의미 기반, 오탐 방지) -----------------
# "다음 <보기> 중 옳은 것을 모두 고른 것은?" 유형에서 그림이 '그래프+<보기> 상자'를 함께
# 크롭해 그림 안에 ㄱㄴㄷㄹ 보기 지문이 들어간 경우, 같은 지문이 발문에도 남아 중복된다.
# 그림 이미지와 발문을 Claude 에 함께 줘, '그림이 이미 보여주는 <보기> 항목 묶음'만 발문에서
# 제거한다. 그림에 <보기> 상자가 없으면(일반 그래프/도형) 발문을 그대로 반환한다(오탐 방지).
_CLEAN_STEM_FIG_PROMPT = """위 이미지는 어떤 수학 문제에 들어가는 '그림(그래프/도형)'이다.
이 그림 안에 <보기> 상자(ㄱ.ㄴ.ㄷ.ㄹ … 로 나열된 '보기 지문' 묶음)가 함께 들어있을 수 있다.
아래 [발문]에도 같은 <보기> 항목이 텍스트로 중복돼 있으면, 그림이 그 내용을 대신 보여주므로
발문에서 '그림과 겹치는 <보기> 항목 묶음만' 삭제하라.

규칙:
- 그림 안에 <보기> 상자가 '없으면'(일반 그래프/도형뿐이면) 발문을 '한 글자도 바꾸지 말고
  그대로' 출력하라. (그림에 없는 보기 지문을 임의로 지우면 안 된다.)
- 그림 안의 <보기> 항목과 '같은 항목'이 발문에 있을 때만, 그 <보기> 항목 묶음을 제거한다.
- 질문 문장("… 옳은 것을 모두 고른 것은?" 등)과 도입부("다음 <보기> 중" 포함)는 절대 삭제하지 않는다.
- 수식은 $ ... $ 로 표기돼 있다. 남기는 수식의 $ ... $ 안 내용은 절대 바꾸지 마라.
- 삭제로 생긴 어색한 빈 줄만 다듬되 새 내용을 지어내지 마라.
- 정리된 발문 '텍스트만' 출력한다(설명·따옴표·코드블록 없이).

[발문]
"""


def clean_stem_against_figure(stem_runs: list, figure_image_path: str, *,
                              model: str | None = None) -> list:
    """그림(<보기> 상자 포함 가능)과 발문을 대조해, 그림이 보여주는 <보기> 항목이
    발문에 중복되면 그 묶음만 제거한다. 그림에 <보기> 가 없으면 원본 유지(오탐 방지).
    choices 는 인자에 없으므로 절대 건드리지 않는다. 실패 시 원본 runs 반환(안전).
    """
    text = _runs_to_text(stem_runs)
    if not text.strip():
        return stem_runs
    try:
        content = [_encode_image(figure_image_path),
                   {"type": "text", "text": _CLEAN_STEM_FIG_PROMPT + text}]
        cleaned = (_call(content, max_tokens=2048, model=model) or "").strip()
        cleaned = cleaned.strip("`").strip()
        if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
            cleaned = cleaned[1:-1].strip()
        return _text_to_runs(cleaned) if cleaned else stem_runs
    except Exception:  # noqa: BLE001 (네트워크/파싱 실패 → 원본 유지)
        return stem_runs


# --- 페이지 방향 자동 감지 (회전된 스캔 보정) -------------------------------
_ORIENT_PROMPT = """이 이미지는 학교 시험지를 스캔/촬영한 것이다(손글씨가 섞여 있을 수 있다).
인쇄된 '본문 글자'가 똑바로(왼→오, 위→아래로 정상적으로 읽히는 방향)가 되게 하려면
이미지를 '시계방향'으로 몇 도 돌려야 하는가?
0, 90, 180, 270 중 '숫자 하나만' 출력하라(설명·단위·다른 문자 금지)."""


def detect_orientation(image_path: str, *, model: str | None = None) -> int:
    """페이지가 똑바로 읽히도록 '시계방향'으로 회전할 각도(0/90/180/270).

    회전된 스캔이면 OCR 이 발문을 못 읽으므로 이 값으로 페이지를 미리 바로 세운다.
    방향만 판단하면 되므로 썸네일로 축소해 보낸다(속도·비용↓). 실패 시 0(회전 안 함).
    """
    try:
        from PIL import Image
        import io
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            im.thumbnail((1024, 1024))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        content = [{"type": "image", "source": {"type": "base64",
                    "media_type": "image/jpeg", "data": b64}},
                   {"type": "text", "text": _ORIENT_PROMPT}]
        out = _call(content, max_tokens=12, model=model) or ""
    except Exception:  # noqa: BLE001 (이미지/네트워크 오류 → 회전 안 함)
        return 0
    m = re.search(r'(270|180|90|0)', out)
    return int(m.group(1)) if m else 0


# 하위호환
Figure = Region
