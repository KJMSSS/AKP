# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

한국 수학 시험지 PDF → HWPX(한글 문서) 자동 변환 파이프라인.  
학원 운영 도구로, 시스템이 만든 **1단 HWPX**를 학원장이 받아 타이퍼 양식(2단)으로 옮기는 것이 최종 목적이다.

## 주요 명령어

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
```

> Windows에서 `python`/`py` 별칭이 Microsoft Store 스텁으로 연결되어 exit 49로 실패하는 경우,  
> 전체 경로 `C:\Users\사용자\AppData\Local\Programs\Python\Python314\python.exe` 사용.

## 아키텍처

### 핵심 변환 흐름

```
PDF
 └─ OCR (Mathpix 또는 Claude) → raw.md ($...$ 인라인, $$...$$ 디스플레이)
      └─ apply_fallback()          ← 손상 감지 + 플레이스홀더 삽입
           └─ build_from_markdown() ← LaTeX→HWP Script + ZIP 패키징
                ├─ replace_condition_tables()   ← （가）（나）→ 1×1 hp:tbl
                └─ replace_boilerplate_tables() ← ㄱ/ㄴ/ㄷ → 1×1 hp:tbl
```

**`build_from_markdown(md, out_hwpx, template_hwpx)`** — 가장 중요한 진입점.  
`template_hwpx`의 `header.xml`(폰트·스타일)만 재사용하고 `section0.xml` 본문은 새로 생성한다.

### 수식 변환 규칙 (`src/common/latex_to_hwp.py`)

LaTeX → HWP Script (`hp:equation > hp:script`) 변환. 편집 가능한 수식 객체로 삽입됨.

| LaTeX | HWP Script |
|-------|-----------|
| `\frac{a}{b}`, `\dfrac{}{}` | `{a} over {b}` |
| `\sqrt{x}` | `sqrt {x}` |
| `\sqrt[n]{x}` | `nroot {n} {x}` |
| `\int_{a}^{b}` | `int from {a} to {b}` |
| `\sum_{k=1}^{n}` | `sum from {k=1} to {n}` |
| `\binom{n}{k}` | `LEFT ( {n} atop {k} RIGHT )` |

3단계 중첩 브레이스까지 처리 (`\frac{\sqrt{a^{2}+b^{2}}}{c}` 등).

### HWPX 구조

HWPX = ZIP 아카이브.  
- `Contents/header.xml` — 스타일·폰트 정의 (템플릿에서 복사)  
- `Contents/section0.xml` — 본문 전체 (`hp:p` 단락 + `hp:equation` 수식)  
- `BinData/BIN*.png` — 삽입 이미지

### 두 가지 파이프라인

| 방식 | 파일 | 용도 |
|------|------|------|
| **텍스트 기반 v5** (현행) | `src/text_only/text_builder.py` | 마크다운 → HWPX 신규 생성 |
| **템플릿 기반** (구) | `src/template_based/builder.py` | 기존 HWPX 슬롯 치환 |

현재 운영은 텍스트 기반 v5만 사용한다. 템플릿 기반 코드는 레거시.

### OCR 엔진

| 엔진 | 파일 | 특징 |
|------|------|------|
| Mathpix | `src/common/ocr/mathpix_client.py` | 수식 정확도 최고, 별도 구독 필요 |
| Claude | `src/ocr/claude_pdf_reader.py` | API 하나로 통합, `full_content` 모드로 해설 포함 |

두 엔진 모두 동일한 `$...$` / `$$...$$` 형식 출력 → 이후 파이프라인 공통.

### 문제 파서 (`src/text_only/problem_segmenter.py`)

`parse_problems(md)` → `(header, List[ProblemSegment])`  
`ProblemSegment`: number, problem_text, choices, conditions, boilerplate, images, is_subjective

- 객관식 번호: 1–22  
- 서술형 번호: 101–104 (`[단답형N]`/`[서술형N]` 접두사 필수)

### 비용 관리

`src/ocr/cost_guard.py` — 일일 $5 cap. API 호출 전 `guard.check_or_raise()`, 이후 `guard.record()`.

## 절대 정책 (이 규칙은 절대 위반 금지)

1. **학교 단위 순차 처리** — 여러 학교 병렬 빌드 금지
2. **LLM은 패턴 발견기** — `temperature=0`, 자동 적용 금지, 교정 사전 `approved` 항목만 자동 적용
3. **학원장 PDF 원본 = 진짜 정답** — LLM/OCR 결과보다 원본 PDF 우선
4. **크롭 OCR 표준 순서**: 전체 OCR 후 공란 발견해서 재빌드하는 방식 금지. 반드시 크롭 OCR 먼저 → raw.md 완성 → 빌드 1회

## 파일 네이밍 컨벤션

```
[연도_학기_회차_a/b_과목약어_학교명]
예: [2025_2_1_b_공수1_경신여고]
```

임시 마크다운: `output_text_temp.md` (루트에 덮어씀, git 무시).  
프로덕션 HWPX: `samples/11b_production/` 또는 `samples/2026/`.
