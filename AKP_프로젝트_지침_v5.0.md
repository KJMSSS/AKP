# AKP 프로젝트 — 시험지 PDF → HWPX 자동 변환 시스템 (지침 v5.0)

> **버전**: v5.0 (2026-06-02)
> **이전 버전**: v4.0 (2026-05-22 — 골드 HWPX 분석 시스템 완성)
> **v5.0 변경**: Claude OCR 엔진 추가 + 웹 서비스 배포(Railway) + 토큰 인증 + 검수 데이터 수집 워크플로우 + 표/그림 파이프라인 완성

---

## 프로젝트 정체성

한국 수학 시험지 PDF를 타이퍼 양식 한글(HWPX) 시험지로 자동 변환하는 학원 운영 도구.

- GitHub: KJMSSS/AKP
- 작업 폴더: D:\f1\AKP
- 웹 서비스: Railway 배포 (24시간, PC 꺼도 동작)
- 운영 규모: 1주 30개 시험지 (광주 자가 15개 + 비서 결제 15개)

---

## 현재 단계 — Phase 2 진입 (웹 서비스 + 검수 시스템)

### Phase 정의

| Phase | 정의 | 상태 |
|---|---|---|
| Phase 1 (이전) | 한글 정확 인식 (사전 등록 위주) | 한계 인정 |
| Phase 1.5 (Cycle 16) | v5 파이프라인 완성 + 골드 분석 시스템 | ✅ 완료 |
| **Phase 2 (현재)** | **웹 서비스 + 직원 검수 + 데이터 축적** | **진행 중** |
| Phase 3 | 해설 자동 생성 | 대기 |
| Phase 4 | 수학비서 → 타이퍼 자동 변환 | 대기 |
| Phase 5 | MCP 비서 연계 | 대기 |

---

## ⭐ 웹 서비스 (v5.0 신규)

### 구조

```
scripts/web/
├── app.py              # FastAPI 서버 (Railway 배포)
├── tokens.py           # 토큰 발급/검증/한도 관리
├── usage_log.py        # 변환별 비용·토큰 로그
├── corrections_log.py  # 직원 검수 수정 이력
└── static/
    ├── index.html      # 변환기 (직원/학원장 공용)
    ├── review.html     # 검수 페이지 (직원용)
    └── admin.html      # 관리자 대시보드 (학원장 전용)
```

### 서버 실행

```powershell
# 로컬 실행
py -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8000

# Railway (자동 배포 — GitHub push 시 자동 반영)
```

### 환경변수 (.env 또는 Railway Variables)

```
ANTHROPIC_API_KEY=sk-ant-xxx    # Claude API 키 (필수)
ADMIN_PASSWORD=원하는비밀번호    # 관리자 페이지 비밀번호
DAILY_COST_CAP=5.0              # 일일 비용 한도 USD
DATA_DIR=/data                   # Railway Volume 마운트 경로
TMP_DIR=/tmp/akp                 # 임시 파일 경로
```

---

## ⭐ 토큰 인증 (v5.0 신규)

### 구조

```
학원장 → admin 페이지에서 토큰 발급
   "선생님A": {"token": "akp-abc123", "cap_usd": 2.0, "active": true}
   "관리자":  {"token": "akp-xxxxxx", "cap_usd": 0.0}  ← 0=무제한

직원 → 브라우저에 토큰 한 번 입력 (localStorage 저장)
     → 변환 시 자동 전송
     → 자기 한도 내에서만 사용 가능
```

### 관리자 화면 (`/admin`)

- 비밀번호 로그인 (ADMIN_PASSWORD)
- **내 토큰**: 학원장 본인 토큰 (무제한) + [변환기 열기] 버튼
- **직원 토큰 발급**: 이름 + 한도 설정
- **수정 내역 테이블**: 누가 어떤 문제를 수정했는지 + [되돌리기]

### 토큰 데이터

- `scripts/web/tokens.json` — `.gitignore` 포함 (GitHub에 올라가지 않음)
- Railway 배포 시 Volume(`/data`)에 저장 → 재배포해도 유지

---

## ⭐ 검수 워크플로우 (v5.0 신규)

### 목적

직원 검수 → 수정 이력 축적 → AKP 개선

```
변환 완료 HWPX
     ↓
직원: 한글에서 열어 PDF와 비교
     ↓
웹 검수 페이지 (/review/{job_id})
  - PDF 원본 이미지 vs 변환 텍스트 나란히 표시
  - 문제별 [✓ 정확] / [✗ 오류] 체크
  - 오류 시: 짧은 한글 메모 입력 (LaTeX 불필요)
     ↓
[검수 완료 제출]
  - corrections.jsonl에 즉시 저장 (직원 권한)
  - 별도 승인 대기 없음
     ↓
학원장: admin 페이지에서 수정 내역 확인
  - 이번 주 N건 / 직원별 건수
  - 이상한 수정만 [되돌리기]
```

### 수정 데이터 형식 (`scripts/web/logs/corrections.jsonl`)

```json
{
  "id": "abc123",
  "ts": "2026-06-03T14:32:11",
  "employee": "김선생",
  "pdf_name": "경신여고.pdf",
  "problem_number": 3,
  "correction_note": "분수가 7/27이 맞음",
  "corrected_text": "$\\frac{7}{27}$",
  "status": "applied"
}
```

### 역할 분담

| 역할 | 권한 |
|------|------|
| 직원 | 검수 + 오류 표시 + 즉시 적용 |
| 학원장 | 수정 내역 확인 + 되돌리기 |

---

## ⭐ OCR 엔진 (v5.0 업데이트)

### 두 가지 엔진

| 엔진 | 명령 | 특징 | 비용 |
|------|------|------|------|
| **Mathpix** (기존) | `--ocr-engine mathpix` | 수식 정확도 최고, 별도 구독 필요 | ~$0.005/p |
| **Claude** (신규) | `--ocr-engine claude` | API 하나로 통합, 정답·해설 포함 가능 | ~$0.13/시험지 |

### 사용법

```powershell
# Mathpix (기존 방식)
py scripts/text/pdf_to_text.py 시험지.pdf

# Claude — 문제만
py scripts/text/pdf_to_text.py 시험지.pdf --ocr-engine claude

# Claude — 정답·해설 포함
py scripts/text/pdf_to_text.py 시험지.pdf --ocr-engine claude --full-content
```

### Claude OCR 핵심 파일

- `src/ocr/claude_pdf_reader.py` — `read_pdf_as_markdown(pdf_path, full_content=False)`
- 15페이지 초과 시 자동 청크 분할
- 수식 포맷: `$...$` (인라인), `$$...$$` (디스플레이) — Mathpix와 동일

---

## ⭐ v5 파이프라인 구조 (v5.0 업데이트)

```
PDF
  ↓ OCR (Mathpix 또는 Claude)
  ↓ raw.md ($...$ 형식)
  ↓ apply_fallback()              손상 감지 + 플레이스홀더
  ↓ parse_problems()              → (header, segments)        ← 필수 (v5.0)
  ↓ extract_images()              → figure_map               ← 필수 (v5.0)
  ↓ rebuild_markdown(figure_items=...)   조건·보기·그림 마커 삽입  ← 필수 (v5.0)
  ↓ build_from_markdown()         HWPX 빌드 (마크다운 테이블 → hp:tbl 포함)
  ↓ replace_condition_tables()    （가）/（나）/（다） → 1×1 박스
  ↓ replace_boilerplate_tables()  ㄱ/ㄴ/ㄷ → 1×1 박스
  ↓ insert_figure_placeholder()   PDF 추출 이미지 삽입
  ↓ fix_hwpx_namespaces()
  ↓ validate_hwpx()
  → HWPX
```

### v4 → v5 파이프라인 변경점

| 항목 | v4 | v5 |
|------|----|----|
| 조건/보기 표 | `replace_condition_tables()` 호출하지만 마커 없어서 동작 안 함 | `parse_problems()` + `rebuild_markdown()` 추가로 마커 삽입 후 동작 ✅ |
| 그림 삽입 | 미구현 | `extract_images()` + `insert_figure_placeholder()` 추가 ✅ |
| 마크다운 표 | `text_builder.py`에서 무시 | `\| ... \|` 형식 → `<hp:tbl>` 변환 ✅ |

---

## ⭐ 크롭 OCR 워크플로우 (v4.0 유지)

```
크롭 OCR 먼저 → raw.md 1회 완성 → 빌드 1회

❌ 잘못: 전체 OCR → 빌드 → 공란 발견 → 재빌드 반복
✅ 올바름: 크롭 OCR → raw.md 완성 → 빌드 (1회 끝)
```

### 점수 형식 전처리 (소괄호 학교 필수)

```python
md_raw = re.sub(r'\((\d+(?:\.\d+)?)점\)', r'[\1점]', md_raw)
```

---

## ⭐ 절대 정책 (위반 금지)

### 작업 순서 절대

```
PDF 품질 → OCR → 후처리 → 빌드
항상 하나씩 (학교 단위 순차)
```

### LLM 격하 정책

```
사전 우선 (approved만 자동 적용)
LLM = 패턴 발견기 (temperature=0, 자동 적용 X)
학원장 PDF 원본 확인 = 진짜 정답
```

### API 키 관리

```
.env 파일에만 저장 (GitHub에 절대 커밋 금지)
tokens.json도 .gitignore 포함 (직원 토큰 정보 보호)
Railway Variables에 키 입력 (서버에서 사용)
```

---

## 학원장 워크플로우 (v5.0 업데이트)

### 변환 방법 2가지

**방법 A — CLI (로컬, 기존 방식)**
```powershell
py scripts/text/pdf_to_text.py 시험지.pdf --ocr-engine claude
```

**방법 B — 웹 서비스 (Railway, PC 꺼도 가능)**
1. `https://[railway-url]` 접속
2. 관리자 토큰으로 로그인
3. PDF 업로드 → HWPX 다운로드

### 검수 흐름

```
HWPX 다운로드 → 한글에서 열기 → PDF와 비교
     ↓
웹 검수 페이지 (/review/{job_id})
  → 문제별 ✓/✗ 체크 + 오류 메모
  → 제출 → corrections.jsonl 누적
     ↓
학원장: admin 페이지에서 이번 주 수정 내역 확인
```

---

## 핵심 모듈

### v5.0 신규

| 파일 | 역할 |
|------|------|
| `src/ocr/claude_pdf_reader.py` | Claude API PDF OCR 엔진 |
| `scripts/web/app.py` | FastAPI 웹 서버 |
| `scripts/web/tokens.py` | 토큰 발급/검증 |
| `scripts/web/usage_log.py` | 비용·토큰 로그 |
| `scripts/web/corrections_log.py` | 검수 수정 이력 |
| `scripts/web/static/index.html` | 변환기 UI |
| `scripts/web/static/review.html` | 검수 UI |
| `scripts/web/static/admin.html` | 관리자 대시보드 |
| `railway.toml` | Railway 배포 설정 |

### v5.0 업데이트 (기존 파일)

| 파일 | 변경 내용 |
|------|----------|
| `src/text_only/text_builder.py` | 마크다운 테이블(`\|...\|`) → `<hp:tbl>` 변환 추가 |
| `scripts/text/pdf_to_text.py` | `--ocr-engine`, `--full-content` 옵션 + 그림/표 파이프라인 통합 |

### v4.0 유지 (핵심 파이프라인)

- `src/text_only/problem_segmenter.py` — `parse_problems()`, `rebuild_markdown()`
- `src/text_only/text_builder.py` — `build_from_markdown()`
- `src/common/latex_to_hwp.py` — LaTeX → HWP Script
- `src/common/hwpx_table_inserter.py` — 조건/보기/데이터 표 삽입
- `src/common/image_extractor.py` — PDF 그림 추출
- `src/common/hwpx_image_inserter.py` — HWPX 이미지 삽입
- `src/ocr/cost_guard.py` — 일일 $5 비용 cap

---

## 골드 HWPX 분석 시스템 (v4.0 유지)

- `scripts/analyze_gold_hwpx.py` — 18개 학교 골드 분석
- `data/gold_manifest/[학교].json` — 학교별 문제 메타데이터 (18개 생성 완료)

18개 학교 현황 → v4.0 참조

---

## 단답형/서술형 레이블 규칙 (v4.0 유지)

```
29. [단답형1] 다음 중에서 ...
32. [서술형1] 두 양수 a, b에 대하여 ...
```

| 값 | 감지 방법 |
|----|----------|
| `multiple_choice` | 선택지 ①~⑤ 있음 |
| `short_answer` | `[단답형N]` 텍스트 |
| `essay` | `[서술형N]` 텍스트 |

---

## 비용 모델 (v5.0 업데이트)

| 항목 | 비용 |
|------|------|
| Claude OCR (문제만) | ~$0.11/시험지 |
| Claude OCR (정답·해설 포함) | ~$0.20/시험지 |
| Mathpix OCR | ~$0.005/페이지 |
| 웹 서비스 (Railway Hobby) | $5/월 + Volume ~$0.25/GB |
| **1년 시스템 총비용** | **약 16만원 (OCR) + 6만원 (Railway) = 22만원** |

---

## 파일 네이밍 컨벤션

```
[연도_학기_회차_a/b_과목약어_학교명]
예: [2025_2_1_b_공수1_경신여고]

임시 마크다운: output_text_temp.md (루트, git 무시)
프로덕션 HWPX: samples/11b_production/ 또는 samples/2026/
```

---

## 한 줄 정리

> Phase 2 진입 — 웹 서비스(Railway) + 토큰 인증 + 직원 검수 데이터 수집 시스템 완성.
> Claude OCR 엔진 추가 (Mathpix 대체 가능). 표/그림 파이프라인 완성 (조건/보기 표 + PDF 그림 자동 삽입).
> 검수 데이터 축적 → AKP 자동 개선 기반 구축.
> 작업 순서 절대: PDF → OCR → 후처리 → 빌드. 항상 하나씩 (학교 단위).
> 시험지당 약 200원, 1년 22만원으로 운영.
