# AKP 프로젝트 전체 플랜

> 한국 수학 시험지 PDF → HWPX(한글 문서) 자동 변환 파이프라인  
> 학원 운영 도구 — 학원장이 타이퍼 양식(2단 HWPX)으로 직원에게 배포하는 것이 최종 목표  
> 최종 수정: 2026-06-05 (OCR A+B+C 완료)

> **핵심 방향 (2026-06-05 확정)**  
> 중간 검수·재빌드 루프 없이 **한 번에 최고 품질**로 출력하는 것이 목표.  
> OCR 품질이 전부 — 사람이 고쳐줄 거라는 전제로 파이프라인을 설계하지 않는다.

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
OCR + 자동 변환 (1회, 고품질)
    ↓
2단 타이퍼 양식 HWPX 자동 생성
    ↓
학원장이 결과물 확인 (최종 1회)
    ↓
직원이 한글에서 편집·인쇄
```

> 이전 목표였던 "직원 검수 → 수정 → 재빌드" 반복 루프는 제거.  
> 대신 **OCR 파이프라인 자체의 품질을 높여** 첫 출력이 곧 최종본이 되는 구조를 목표로 한다.

### 사용자

| 역할 | 책임 |
|------|------|
| **학원장** | PDF 업로드, 결과물 최종 확인 |
| **직원** | 타이퍼 양식 수령 후 한글 편집·인쇄 |

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

> **우선순위 원칙**: OCR 품질이 모든 것의 전제. 파이프라인 품질이 충분히 높아야 나머지 STEP이 의미가 있다.

---

### ★ OCR 품질 개선 (A+B+C) `✅ 완료` (2026-06-05)

기존 플랜의 "6번 부가 작업"에서 **전체 1순위**로 격상 → 완료.

→ 상세 내용은 [6절 OCR 품질 개선 로드맵](#6-ocr-품질-개선-로드맵) 참조

---

### STEP 1 — 그림 파이프라인 `미착수`

**목표**: PDF의 그림 영역을 자동 감지해 HWPX에 삽입 (첫 빌드에서 올바른 위치에)

**작업 항목**:
- PDF에서 이미지 영역 자동 감지 (Vision + PyMuPDF)
- 문제별 크롭에 그림 포함
- HWPX BinData 삽입 + 위치 지정
- 기준: `samples/11b/*.hwpx` 골드 18쌍의 그림 위치 참고

**관련 파일**:
- `src/common/image_extractor.py` — PyMuPDF 그림 추출 (부분 구현)
- `src/common/hwpx_image_inserter.py` — BinData 삽입 (부분 구현)

---

### STEP 2 — 결과물 확인 뷰어 (최소화) `방향 변경`

~~웹 검수 인터페이스 강화~~ → **단순 결과 확인 뷰어**로 방향 변경.

**변경 이유**: "직원이 오류 수정 → 재빌드" 반복 루프는 이 프로젝트의 목표가 아님.  
OCR 품질이 높아지면 검수 자체가 필요 없어지는 구조를 목표로 한다.

**남길 것**: 학원장이 변환 결과를 빠르게 훑어볼 수 있는 최소 뷰어  
**제거할 것**: 수정 텍스트 입력 → 재빌드 루프, 직원 검수 워크플로우

---

### STEP 3 — 2단 타이퍼 양식 자동 변환 `✅ 완료` (2026-06-04)

- `src/text_only/typer_builder.py` — `build_typer_hwpx()` 함수
- 1단 `section0.xml` 파싱 → 문제별 그룹화 → 1×6 메타 표 + 본문 단락 조립
- A3 2단 HWPX 생성, BinData 이미지 보존, 구조 검증 PASS
- 웹: `POST /api/pipeline/{key}/typer/generate` 엔드포인트
- 매트릭스 UI: 자동 생성/재생성 버튼

---

### STEP 4 — 통합 배포 `미착수`

**목표**: PDF 업로드 → 1회 변환 → 타이퍼 양식 다운로드 (검수 루프 없음)

**작업 항목**:
- 전체 흐름 연결: PDF 업로드 → OCR+변환 → 타이퍼 양식 자동 생성 → 다운로드
- 결과물 Google Drive 자동 업로드
- 모바일 반응형 UI

---

## 6. OCR 품질 개선 로드맵

> **이 섹션이 전체 프로젝트의 핵심.**  
> commit: `0541c89` (2026-06-05)

### STEP A — 프롬프트 수식 예시 추가 `✅ 완료`

파일: `src/ocr/claude_pdf_reader.py`

`_SYSTEM` 프롬프트에 `[수식 정확도]` + `[수식 오류 주의]` 섹션 추가:
- 지수·첨자 중괄호 필수: `x^{2}`, `3^{-x}`, `a_{n}`
- 백슬래시 필수: `\sin`, `\cos`, `\log`, `\lim`, `\frac`, `\sqrt`
- ❌/✅ 대비 예시로 자주 틀리는 패턴 명시

---

### STEP B — 과목별 출제 범위 주입 `✅ 완료`

파일: `src/ocr/claude_pdf_reader.py`, `scripts/web/app.py`

- `SUBJECT_HINTS` 딕셔너리 (공수1·공수2·대수·확통·기하·미적1·미적2)
- `read_pdf_as_markdown(subject=)` 파라미터 추가
- `_run_conversion`에서 레지스트리 키 파싱 → subject 자동 전달

---

### STEP C — 2차 LaTeX 교정 패스 `✅ 완료`

신규 파일: `src/ocr/latex_corrector.py`

- **Claude Haiku** 로 수식만 교정 (텍스트 불변, 비용 절감)
- 파이프라인: `read_pdf_as_markdown()` → `correct_latex()` → `apply_fallback()` → 빌드
- 수식 없는 마크다운은 API 호출 없이 원문 반환

---

## 7. 알려진 버그 / 미결 이슈

| 심각도 | 항목 | 내용 |
|--------|------|------|
| 🔴 | 서강고 선택지 마커 초과 | 75건 검출, 기대 70건 — 파서 선택지 인식 로직 검토 필요 |
| 🟠 | 웹 검수 크롭 PNG | 문제별 크롭 PNG + 텍스트 나란히 표시 미구현 |
| 🟡 | 서강고 HWPX D안 | 3번 집합기호·7번 손글씨 OCR 잡음 수정 완료 (2026-06-05) |

---

## 7-1. HWPX 수식 직접 편집 시 주의사항 (실전 교훈)

서강고 vD 수정 작업에서 수식을 직접 XML로 조작했더니 **수식이 전부 빈 박스로 표시**되는 현상 발생.  
4번 시도 실패 후 파악한 3대 원인:

### 원인 1: `<hp:outMargin>` 누락
```xml
<!-- 필수 구조 (이 태그 없으면 HWP에서 빈 박스) -->
<hp:equation id="..." ...>
  <hp:sz .../>
  <hp:pos .../>
  <hp:outMargin left="0" right="0" top="0" bottom="0"/>  ← 필수!
  <hp:script>HWP Script</hp:script>
</hp:equation>
```

### 원인 2: LaTeX를 HWP Script로 변환하지 않음
`hp:script`에는 LaTeX가 아니라 **HWP Script**가 들어가야 한다.

| LaTeX | HWP Script |
|-------|-----------|
| `\times` | `times` |
| `\frac{a}{b}` | `{a} over {b}` |
| `\sqrt{x}` | `sqrt {x}` |

`from src.common.latex_to_hwp import convert as latex_to_hwp`로 변환.

### 원인 3: 단락/수식 ID 충돌
다른 HWPX에서 단락을 가져올 때, `hp:p id`, `hp:equation id`, `zOrder`가  
기존 문서 값과 겹치면 HWP가 오작동한다.

### 올바른 수정 절차
```
1. 올바른 마크다운 작성 ($...$로 수식 감싸기)
2. build_from_markdown()으로 임시 HWPX 생성
3. 임시 HWPX에서 목표 단락 추출
4. 대상 문서의 최대 id/zOrder 파악 후 오프셋 적용
5. 교체 범위 통째로 교체 (문제 단락 + 선택지 단락들)
```

> **규칙**: 수식 포함 단락은 절대 직접 XML 조합하지 않는다. **항상 build_from_markdown 파이프라인 경유.**

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
