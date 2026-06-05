# AKP 프로젝트 전체 플랜

> 한국 수학 시험지 PDF → HWPX(한글 문서) 자동 변환 파이프라인  
> 학원 운영 도구 — 학원장이 타이퍼 양식(2단 HWPX)으로 직원에게 배포하는 것이 최종 목표  
> 최종 수정: 2026-06-05

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [현재 완성된 기능](#2-현재-완성된-기능)
3. [아키텍처](#3-아키텍처)
4. [웹 매트릭스 UI](#4-웹-매트릭스-ui)
5. [로드맵](#5-로드맵)
6. [OCR 품질 개선 로드맵](#6-ocr-품질-개선-로드맵)
7. [알려진 버그 / 미결 이슈](#7-알려진-버그--미결-이슈)
8. [절대 정책](#8-절대-정책-위반-금지)
9. [파일 네이밍 컨벤션](#9-파일-네이밍-컨벤션)
10. [배포 환경](#10-배포-환경)
11. [주요 명령어](#11-주요-명령어)
12. [핵심 파일 구조](#12-핵심-파일-구조)

---

## 1. 프로젝트 개요

학원에서 수집한 수학 시험지 PDF를 직원이 직접 타이핑하는 대신,  
OCR + LLM으로 자동 추출하고 한글(HWPX) 문서로 변환하는 시스템이다.

### 최종 목표 흐름

```
학원장이 PDF 업로드
    ↓
웹 매트릭스 UI에서 자동 변환 (OCR → HWPX)
    ↓
직원이 웹 검수 인터페이스에서 오류 수정
    ↓
2단 타이퍼 양식 HWPX 자동 생성
    ↓
직원이 한글에서 편집·인쇄
```

### 두 가지 사용자

| 역할 | 책임 |
|------|------|
| **학원장** | PDF 업로드, 최종 승인, 관리자 설정 |
| **직원** | 웹 검수 (오류 표시·수정 텍스트 입력) |

---

## 2. 현재 완성된 기능

### ✅ PDF OCR
- **Mathpix**: 수식 정확도 최고. 별도 구독 필요. `src/common/ocr/mathpix_client.py`
- **Claude**: API 하나로 통합. `full_content` 모드로 해설 포함. `src/ocr/claude_pdf_reader.py`
- 두 엔진 모두 `$...$` / `$$...$$` 통일 포맷 출력 → 이후 파이프라인 공통

### ✅ LaTeX → HWP Script 변환
파일: `src/common/latex_to_hwp.py`

| LaTeX | HWP Script |
|-------|-----------|
| `\frac{a}{b}`, `\dfrac{}{}` | `{a} over {b}` |
| `\sqrt{x}` | `sqrt {x}` |
| `\sqrt[n]{x}` | `nroot {n} {x}` |
| `\int_{a}^{b}` | `int from {a} to {b}` |
| `\sum_{k=1}^{n}` | `sum from {k=1} to {n}` |
| `\binom{n}{k}` | `LEFT ( {n} atop {k} RIGHT )` |
| `\lim_{x\to a}` | `lim from {x to a}` |

3단계 중첩 브레이스까지 처리 (`\frac{\sqrt{a^{2}+b^{2}}}{c}` 등).

### ✅ HWPX 빌더 v5 (텍스트 기반)
파일: `src/text_only/text_builder.py`

- 마크다운 → `section0.xml` 새 생성 (템플릿 본문 재사용 안 함)
- `header.xml`(폰트·스타일)만 템플릿에서 복사
- 조건표 `（가）（나）` → `1×1 hp:tbl` (`src/common/hwpx_table_inserter.py`)
- 보기표 `ㄱ/ㄴ/ㄷ` → `1×1 hp:tbl`
- HWPX 네임스페이스 자동 수정 (`src/common/hwpx_namespace_fixer.py`)
- HWPX 구조 검증 (`src/common/hwpx_validator.py`)

### ✅ 그림 삽입
파일: `src/common/image_extractor.py`, `src/common/hwpx_image_inserter.py`

- PyMuPDF로 PDF 이미지 영역 추출
- Claude OCR이 `【★ 그림:N번】` 마커 출력 → 세그먼트에서 감지
- PyMuPDF 실패 시 Vision 폴백 (Claude Haiku)
- BinData PNG 삽입 + 위치 플레이스홀더

### ✅ 웹 검수 인터페이스
파일: `scripts/web/static/review.html`

- FastAPI SSE 스트리밍으로 변환 진행 실시간 표시
- 문제별 텍스트 편집 → 재빌드 → 검수완 HWPX 다운로드
- 검수 완료 시 레지스트리에 `review_status: completed` 반영

### ✅ 2단 타이퍼 양식 자동 변환
파일: `src/text_only/typer_builder.py`

- 1단 HWPX → A3 2단 자동 변환 (`build_typer_hwpx()`)
- 문제별 그룹화 → 1×6 메타 표 + 본문 단락 조립
- BinData 이미지 보존
- 웹: `POST /api/pipeline/{key}/typer/generate` — 자동 생성/재생성 버튼

### ✅ 웹 매트릭스 UI
파일: `scripts/web/static/matrix.html`

- 학교 × 과목 매트릭스 (행: 학교, 탭: 과목)
- 잡 이동 (연도·학교·과목·학년·학기·중간/기말 변경 가능)
- 단계별 파일 업로드 (한글완성본·타이퍼·해설)
- Google Drive 자동 업로드
- Railway 배포 (GitHub push → 자동 재배포)

### ✅ 인증 & 보안
- Google OAuth2 (이메일 허용 목록 + 관리자 구분)
- 일일 비용 캡 ($5 전체, 사용자별 설정 가능)
- `_require_login` / `_require_admin` 전 엔드포인트 적용
- `_validate_safe_key()` — 경로 탈출(`../`) 방지
- `/download/{job_id}` 인증 필수

---

## 3. 아키텍처

### 핵심 변환 흐름

```
PDF
 └─ OCR (Mathpix 또는 Claude) → raw.md  ($...$ 인라인, $$...$$ 디스플레이)
      └─ apply_fallback()           ← 손상 감지 + 플레이스홀더 삽입
           └─ parse_problems()      ← 문제 단위 세그먼트 분리
                └─ rebuild_markdown()
                     └─ build_from_markdown() ← LaTeX→HWP Script + ZIP 패키징
                          ├─ replace_condition_tables()    ← （가）（나）→ 1×1 hp:tbl
                          └─ replace_boilerplate_tables()  ← ㄱ/ㄴ/ㄷ → 1×1 hp:tbl
```

### HWPX 내부 구조

```
파일명.hwpx  (ZIP 아카이브)
├── Contents/
│   ├── header.xml      ← 스타일·폰트 정의 (템플릿에서 복사)
│   └── section0.xml    ← 본문 전체 (hp:p 단락 + hp:equation 수식)
└── BinData/
    └── BIN*.png        ← 삽입 이미지
```

### 두 가지 파이프라인

| 방식 | 파일 | 용도 |
|------|------|------|
| **텍스트 기반 v5** (현행) | `src/text_only/text_builder.py` | 마크다운 → HWPX 신규 생성 |
| **템플릿 기반** (레거시) | `src/template_based/builder.py` | 기존 HWPX 슬롯 치환 (미사용) |

### 문제 파서

파일: `src/text_only/problem_segmenter.py`

- `parse_problems(md)` → `(header, List[ProblemSegment])`
- `ProblemSegment`: `number, problem_text, choices, conditions, boilerplate, images, is_subjective`
- 객관식 번호: 1–22
- 서술형 번호: 101–104 (`[단답형N]`/`[서술형N]` 접두사 필수)

### 비용 관리

파일: `src/ocr/cost_guard.py`

- 일일 $5 캡. API 호출 전 `guard.check_or_raise()`, 이후 `guard.record()`

---

## 4. 웹 매트릭스 UI

### 레지스트리 키 형식

```
연도_학년_학기_a(중간)/b(기말)_과목_학교
```

예시: `2026_1_1_a_공수1_경신여고`

| 위치 | 의미 | 값 |
|------|------|-----|
| 0 | 연도 | 2026, 2025 ... |
| 1 | 학년 | 1, 2, 3 |
| 2 | 학기 | 1, 2 |
| 3 | 시험 종류 | `a` (중간), `b` (기말) |
| 4 | 과목 ID | 공수1, 공수2, 대수, 확통, 기하, 미적1, 미적2 |
| 5~ | 학교 | 경신여고, 광주제일고 ... |

### 기본 과목 목록

| ID | 전체명 | 학년 | 학기 |
|----|--------|------|------|
| 공수1 | 공통수학1 | 1 | 1 |
| 공수2 | 공통수학2 | 1 | 2 |
| 대수 | 대수 | 2 | 1 |
| 확통 | 확률과 통계 | 2 | 2 |
| 기하 | 기하 | 2 | 2 |
| 미적1 | 미적분1 | 2 | 2 |
| 미적2 | 미적분2 | 3 | 1 |

### 파이프라인 단계 (STEP)

| STEP | 이름 | 방식 | 설명 |
|------|------|------|------|
| 1 | PDF | 자동 | PDF 업로드 + OCR + HWPX 변환 |
| 2 | HWPX 검수전 | 자동 | 변환 완료 HWPX |
| 3 | HWPX 검수완 | 자동 | 웹 검수 후 재빌드 HWPX |
| 4 | 한글완성본 | 수동 | 학원장이 편집한 최종본 업로드 |
| 5 | 타이퍼 양식 | 자동/수동 | 2단 A3 HWPX (자동 생성 가능) |
| 6 | 해설 | 수동 | 해설 파일 업로드 |

### 잡 이동 기능

이동 모달에서 변경 가능한 항목:
- **연도** (2020~2030)
- **학교** (등록된 전체 학교)
- **과목** (등록된 전체 과목)
- **학년** (1~3)
- **학기** (1~2)
- **중간/기말** (a/b)

이동 시 자동 처리:
- 레지스트리 키 변경
- `_review.json`의 `custom_filename` 갱신 (다운로드 파일명 동기화)
- stages 디렉토리 이동 (한글완성본·타이퍼·해설 파일 포함)

---

## 5. 로드맵

### STEP 1 — 그림 파이프라인 `미착수`

**목표**: PDF의 그림 영역을 자동 감지해 HWPX에 삽입

**작업 항목**:
- PDF에서 이미지 영역 자동 감지 (Vision + PyMuPDF)
- 문제별 크롭에 그림 포함
- HWPX BinData 삽입 + 위치 지정
- 기준: `samples/11b/*.hwpx` 골드 18쌍의 그림 위치 참고

**관련 파일**:
- `src/common/image_extractor.py` — PyMuPDF 그림 추출 (부분 구현)
- `src/common/hwpx_image_inserter.py` — BinData 삽입 (부분 구현)

---

### STEP 2 — 웹 검수 인터페이스 강화 `미착수`

**목표**: 직원이 PDF와 HWPX를 창 전환 없이 검수할 수 있는 인터페이스

**작업 항목**:
- 문제별 크롭 PNG + 추출 텍스트 나란히 표시
- 오류 표시 및 수정 텍스트 입력
- 학원장 승인 화면 (모바일 대응)
- 수정 사항 → HWPX 재생성 트리거

**현재 상태**: 기본 검수 인터페이스 존재 (`review.html`), 크롭 PNG 표시 미구현

---

### STEP 3 — 2단 타이퍼 양식 자동 변환 `✅ 완료` (2026-06-04)

- `src/text_only/typer_builder.py` — `build_typer_hwpx()` 함수
- 1단 `section0.xml` 파싱 → 문제별 그룹화 → 1×6 메타 표 + 본문 단락 조립
- A3 2단 HWPX 생성, BinData 이미지 보존, 구조 검증 PASS
- 웹: `POST /api/pipeline/{key}/typer/generate` 엔드포인트
- 매트릭스 UI: 자동 생성/재생성 버튼

---

### STEP 4 — 통합 배포 `미착수`

**목표**: 전체 파이프라인 연결 + 직원 계정 구분

**작업 항목**:
- 전체 흐름 연결: PDF 업로드 → 웹 검수 → 타이퍼 양식 다운로드
- 직원 계정 구분 (검수자 / 학원장)
- 결과물 Google Drive 자동 업로드
- 모바일 반응형 UI

---

## 6. OCR 품질 개선 로드맵

### STEP A — 프롬프트 수식 예시 추가 `미착수`

파일: `src/ocr/claude_pdf_reader.py`

`_SYSTEM` / `_SYSTEM_FULL` 두 프롬프트 모두에 `[수식 예시]` 섹션 추가.

추가할 예시:
```
- x^{n+1}      (두 자리 지수 중괄호 필수)
- a_{n}, S_{10}  (첨자 중괄호)
- \sqrt{a+b}   (백슬래시 필수)
- \lim_{x \to a}, \sin, \cos, \log  (LaTeX 명령어 백슬래시)
- \frac{분자}{분모}  (분수 구조)
- 인라인 $...$ vs 독립 $$...$$ 구분
```

---

### STEP B — 과목별 출제 범위 주입 `미착수`

파일: `src/ocr/claude_pdf_reader.py`, `scripts/web/app.py`

```python
SUBJECT_HINTS = {
    "공수1": "다항식, 방정식과 부등식, 도형의 방정식",
    "공수2": "집합, 명제, 함수, 경우의 수",
    "대수":  "지수·로그, 수열",
    "확통":  "경우의 수, 확률, 통계",
    "기하":  "이차곡선, 벡터, 공간도형",
    "미적1": "극한, 미분, 적분 (다항함수)",
    "미적2": "수열의 극한, 미분법, 적분법 (초월함수)",
}
```

- `read_pdf_as_markdown()`에 `subject: str = ""` 파라미터 추가
- `app.py` `_run_conversion`에서 `custom_filename` 파싱해 subject 전달
  (school 파싱 로직 이미 있음)

---

### STEP C — 2차 LaTeX 교정 패스 `미착수`

신규 파일: `src/ocr/latex_corrector.py`

```python
def correct_latex(md: str, subject: str = "") -> str:
    """OCR 마크다운의 LaTeX 수식만 검토해서 오류 수정."""
```

- 프롬프트: "다음 마크다운의 LaTeX 수식 오류만 수정하세요. 텍스트 내용은 절대 변경 금지."
- 예상 추가 비용: 1차의 10~20%
- 파이프라인: `read_pdf_as_markdown()` → `correct_latex()` → 기존 파이프라인

---

## 7. 알려진 버그 / 미결 이슈

| 심각도 | 항목 | 내용 |
|--------|------|------|
| 🔴 | 서강고 선택지 마커 초과 | 75건 검출, 기대 70건 — 파서 선택지 인식 로직 검토 필요 |
| 🟠 | 웹 검수 크롭 PNG | 문제별 크롭 PNG + 텍스트 나란히 표시 미구현 |
| 🟡 | 서강고 HWPX D안 | 첫 실전 결과물 — 다음 세션 시작 시 확인 필요 |

---

## 8. 절대 정책 (위반 금지)

1. **학교 단위 순차 처리** — 여러 학교 병렬 빌드 금지
2. **LLM은 패턴 발견기** — `temperature=0`, 자동 적용 금지, `approved` 항목만 자동 적용
3. **학원장 PDF 원본 = 진짜 정답** — LLM/OCR 결과보다 원본 PDF 우선
4. **크롭 OCR 표준 순서**: 전체 OCR 후 공란 발견해서 재빌드하는 방식 금지.  
   반드시 `크롭 OCR 먼저 → raw.md 완성 → 빌드 1회`
5. **push 정책**: 로컬 확인 후 명시적 요청 시에만 push.  
   단, 자동 pre-compact 커밋 생성 시는 push 포함.

---

## 9. 파일 네이밍 컨벤션

```
레지스트리 키 = 연도_학년_학기_a(중간)/b(기말)_과목_학교
예시: 2026_1_1_a_공수1_경신여고
```

| 파일 종류 | 이름 형식 | 예시 |
|-----------|----------|------|
| HWPX (자동) | `{레지스트리키}.hwpx` | `2026_1_1_a_공수1_경신여고.hwpx` |
| HWPX (검수완) | `{레지스트리키}_검수.hwpx` | `2026_1_1_a_공수1_경신여고_검수.hwpx` |
| 타이퍼 양식 | `{레지스트리키}_타이퍼양식.hwpx` | `2026_1_1_a_공수1_경신여고_타이퍼양식.hwpx` |
| 임시 마크다운 | `output_text_temp.md` | 루트에 덮어씀, git 무시 |

---

## 10. 배포 환경

### Railway

- GitHub `main` 브랜치 push → 자동 재배포
- Volume 마운트: `/data` 영속 저장 (`matrix_config.json`, `matrix_registry.json`, `uploads/`)
- 환경 변수:

| 변수 | 설명 |
|------|------|
| `GOOGLE_CLIENT_ID` | Google OAuth2 |
| `GOOGLE_CLIENT_SECRET` | Google OAuth2 |
| `SECRET_KEY` | 세션 서명 키 |
| `ADMIN_EMAIL` | 관리자 이메일 |
| `ANTHROPIC_API_KEY` | Claude OCR |
| `DAILY_COST_CAP` | 전체 일일 비용 한도 (기본 5.0) |
| `DATA_DIR` | 데이터 저장 경로 (Railway Volume) |

### 로컬 실행

```powershell
# 서버 시작
py -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8080

# 접속
http://localhost:8080
```

---

## 11. 주요 명령어

```powershell
# 테스트 전체 실행
pytest tests/

# 테스트 단일 실행
pytest tests/test_builder.py::TestLatexToHwp::test_frac_simple -v

# PDF → HWPX (Mathpix OCR, 문제만)
py scripts/text/pdf_to_text.py "samples/시험지.pdf"

# PDF → HWPX (Claude OCR, 정답·해설 포함)
py scripts/text/pdf_to_text.py "samples/시험지.pdf" --ocr-engine claude --full-content

# 재실행 (Mathpix 재과금 방지)
py scripts/text/pdf_to_text.py "samples/시험지.pdf" --pdf-id <이전_pdf_id>

# 플랜 PDF 재생성
py docs/_gen_plan_pdf.py
```

---

## 12. 핵심 파일 구조

```
AKP/
├── scripts/
│   └── web/
│       ├── app.py                    ← FastAPI 서버 (OCR·변환·검수·매트릭스 API)
│       ├── static/
│       │   ├── matrix.html           ← 매트릭스 UI (잡 관리)
│       │   ├── review.html           ← 검수 인터페이스
│       │   ├── admin.html            ← 관리자 화면
│       │   └── login.html            ← 로그인 화면
│       ├── data/
│       │   ├── matrix_config.json    ← 학교·과목 설정
│       │   └── matrix_registry.json  ← 잡 레지스트리
│       ├── usage_log.py              ← 비용 기록
│       ├── corrections_log.py        ← 검수 교정 로그
│       ├── users.py                  ← 사용자 관리
│       └── gdrive_uploader.py        ← Google Drive 업로드
│
├── src/
│   ├── text_only/
│   │   ├── text_builder.py           ← 마크다운 → HWPX (v5 현행)
│   │   ├── typer_builder.py          ← 1단 → 2단 타이퍼 양식
│   │   ├── problem_segmenter.py      ← 문제 파서
│   │   └── ocr_fallback.py           ← 손상 감지·플레이스홀더
│   ├── common/
│   │   ├── latex_to_hwp.py           ← LaTeX → HWP Script
│   │   ├── hwpx_table_inserter.py    ← 조건표·보기표 → hp:tbl
│   │   ├── hwpx_image_inserter.py    ← BinData PNG 삽입
│   │   ├── hwpx_namespace_fixer.py   ← 네임스페이스 수정
│   │   ├── hwpx_validator.py         ← HWPX 구조 검증
│   │   ├── image_extractor.py        ← PDF 이미지 추출
│   │   ├── pdf_utils.py              ← PDF 회전 정상화 등
│   │   └── ocr/
│   │       └── mathpix_client.py     ← Mathpix OCR 엔진
│   └── ocr/
│       ├── claude_pdf_reader.py      ← Claude OCR 엔진
│       └── cost_guard.py             ← 일일 비용 캡
│
├── docs/
│   ├── PLAN.md                       ← 이 파일
│   ├── AKP_프로젝트_플랜.pdf         ← PDF 버전 (3페이지)
│   └── _gen_plan_pdf.py              ← PDF 생성 스크립트
│
├── samples/
│   ├── 11b_production/               ← 배포용 HWPX
│   ├── 2026/                         ← 2026년 결과물
│   └── *.hwpx                        ← 템플릿 HWPX
│
├── tests/                            ← pytest 테스트
├── log/                              ← 사이클별 로그
└── CLAUDE.md                         ← Claude Code 설정
```

---

*이 문서는 `docs/_gen_plan_pdf.py`로 PDF 버전을 생성할 수 있습니다.*
