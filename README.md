# AKP — 시험지 자동 편집 파이프라인

> **A**uto **K**orean exam **P**aper → HWPX  
> PDF 시험지 + 한글 워드초벌 → 자동으로 채워진 최종 HWPX

---

## 🔄 변환 워크플로우 (현행 · 2026-06-01)

현재 운영되는 변환 흐름은 **텍스트(raw.md) 기반 v5 파이프라인**이다.
크게 **① 시스템이 1단 HWPX를 만드는 단계**와 **② 학원장이 그 한글문서를 받아 타이퍼 양식으로 옮기는 단계**로 나뉜다.

```
[학교 원본 PDF]
      │  ① 시스템 변환 (Claude Code 영역)
      ▼
  raw.md  ──(텍스트 타이핑 / Mathpix OCR)
      │
      ├─ (필요시) 공란 복원: 문제 단위 크롭 OCR + Vision 보완
      ├─ (필요시) LLM 후처리: 한글 오자 교정 (temperature=0, 패턴 발견기)
      ▼
  build_from_markdown()      ← LaTeX → HWP 수식 변환 + ZIP 패키징 (로컬, 무료)
      │
      ├─ replace_condition_tables()   ← 조건 (가)(나)(다) → 1×1 표
      ├─ replace_boilerplate_tables() ← 보기 ㄱ/ㄴ/ㄷ → 1×1 표
      ▼
  [1단 HWPX]  =  "한글문서"  (samples/.../[학교].hwpx)
      │
      │  ② 학원장 영역 (한글문서 넣고 난 뒤)  ⬇⬇⬇
      ▼
  Step A. 한글에서 1단 HWPX 열기 + 시각 검수
            · 본문/보기/수식/그림 정확성 확인
            · 🔴 손상(★)·의심 표시 직접 수정
            · 수식 객체 부분 렌더링 시 더블클릭+저장으로 정규화
      ▼
  Step B. 타이퍼 양식 템플릿 준비
            · 이전 시험지(타이퍼 양식) HWPX를 복사해서 변형
            · 별도 빈 템플릿 없음 — 직전 시험지가 곧 템플릿
      ▼
  Step C. 1단 내용 → 타이퍼 양식에 복붙
            · 본문/보기/수식/그림을 2단 양식에 붙여넣기
      ▼
  Step D. 헤더 정보 수정
            · 학교명 · 학기 · 차수 · 문제 번호 등
      ▼
  Step E. 최종 검수 → 인쇄 / 학생 배포
```

### ① 시스템 변환 단계 상세

| 순서 | 작업 | 도구 / 함수 | 비용 |
|------|------|-------------|------|
| 1 | PDF → `raw.md` 텍스트화 | 직접 타이핑 또는 Mathpix OCR | 타이핑 $0 / OCR ~$0.005·p |
| 2 | 공란(`N. （미인식）`) 복원 | `crop_problems.py` + 크롭 Mathpix + Claude Vision | ~$0.05 |
| 3 | 한글 오자 교정 (선택) | `llm_postprocess.py` (temperature=0) | ~$0.02~0.05 |
| 4 | 점수 형식 정규화 | `(N점)` → `[N점]` 전처리 (소괄호 학교 필수) | $0 |
| 5 | **HWPX 빌드** | `build_from_markdown(md, out, template)` | **$0 (로컬)** |
| 6 | 조건/보기 표 삽입 | `replace_condition_tables` / `replace_boilerplate_tables` | $0 |

- `build_from_markdown`은 **템플릿 HWPX의 header.xml**(폰트·스타일)만 재사용하고 본문은 새로 생성한다.
- LaTeX 수식 `$...$`은 한글 편집 가능한 `hp:script` 객체로 변환된다 (단순 텍스트 변환 아님).
- **그림(도형·그래프)은 텍스트 변환에 포함되지 않음** → 한글에서 직접 삽입하거나 `image_extractor`로 PDF 캡처 후 삽입.

### ② "한글문서 넣고 난 뒤" — 학원장 단계가 핵심 가치 지점

```
1단 HWPX의 정확도  =  학원장 복붙(Step C) 작업의 효율

🟢 1단이 깨끗  → 복붙 빠르고 정확 → 시간 절약 큼
🔴 1단이 깨짐  → 학원장이 일일이 수정 → 시스템 가치 ↓
```

> 시스템이 만든 1단 HWPX는 **그 자체가 최종본이 아니라**, 학원장이 타이퍼 양식으로
> 옮길 때 쓰는 "깨끗한 복사 원본"이다. 따라서 변환 목표는 100% 자동 완성이 아니라
> **복붙 가능한 정확한 1단**을 만드는 것이다.

#### 단일 파일 빠른 변환 예시 (이번 서강고 케이스)

```bash
# raw.md를 직접 만든 뒤 일회성 빌드 스크립트로 HWPX 생성
py scripts/build_seogang_2026.py
# → samples/2026/(광주)[...서강고].hwpx  (문단 197 / 수식 218, 약 16KB)
```

핵심 호출부:

```python
from src.text_only.text_builder import build_from_markdown
md = RAW.read_text(encoding="utf-8")
build_from_markdown(md, OUT_HWPX, TEMPLATE_HWPX)   # 템플릿: 같은 과목 hwpx (폰트 참조용)
```

> ⚠️ 이 머신의 실행 파이썬은 `C:\Users\사용자\AppData\Local\Programs\Python\Python314\python.exe`.
> (`python`/`py` 별칭은 Microsoft Store 스텁이라 exit 49로 실패하므로 풀 경로 사용)

---

## 현재 상태 (2026-05-12)

✅ **파이프라인 완성 + 검수 메커니즘 적용**

| 단계 | 상태 | 비고 |
|------|------|------|
| Mathpix PDF OCR | ✅ 완성 | 마크다운 + 수식 추출 |
| LaTeX → HWP Script 변환 | ✅ 완성 | `latex_to_hwp.py` |
| HWPX 슬롯 분석 | ✅ 완성 | 문항·답지별 자동 그룹핑 |
| PDF → 슬롯 매칭 | ✅ 완성 | 내용 기반 + answer_num 위치 대응 |
| 검수 메커니즘 | ✅ 완성 | 변경 로그 + 하이라이트 + 신뢰도 임계값 |
| 확통 경신여고 검증 | ✅ 통과 | 22개 변경, 의심 1개 차단 성공 |
| Cowork 배포 | 🔜 예정 | |
| 다과목 확장 | 🔜 예정 | |

---

## 핵심 기능

### 1. 자동 채우기
PDF 시험지를 Mathpix로 OCR해서 한글 워드초벌의 수식 슬롯을 자동으로 채운다.

```
[시험지 PDF]  →  Mathpix OCR  →  LaTeX 수식 추출
                                        ↓
[워드초벌.hwpx] → 슬롯 분석 → 매칭 → [완성본.hwpx]
```

### 2. 검수 메커니즘
OCR은 100% 신뢰 불가 — 자동 변경 전 사람이 확인할 수 있는 안전장치.

- **변경 로그** (`samples/changes_*.json`): 모든 변경 슬롯 기록
- **검수 리포트** (`docs/review_*.md`): 슬롯별 원본 vs 변경값 비교표
- **하이라이트** (`--highlight`): 변경된 수식을 한글에서 색상으로 표시
  - 🔵 파란색: 형식만 변경 (수학적으로 동일, 안전)
  - 🔴 빨간색: 내용 변경 (OCR 값이 워드초벌과 다름 — 직접 확인 필요)
- **신뢰도 임계값** (`--min-confidence 0.5`): 의심 변경은 자동 적용 안 함

### 3. 학습된 교훈
- **13번 ④번 케이스**: OCR이 `(4) 2/25`로 읽었지만 실제는 `7/27` → OCR보다 워드초벌이 더 정확한 경우 존재, 검수 필수
- **2단 레이아웃**: OCR이 열 경계를 넘나들어 11번·12번·15번 오인식 가능
- **서술형 레이블 누락**: `서술형1`, `서술형2`가 OCR에서 빠지면 해당 문항 전체 미매칭
- **표기 정규화**: 한글 수식 `` a,``b `` ↔ latex_to_hwp 출력 `a, b` 는 수학적으로 동등

---

## 폴더 구조

```
AKP/
├── scripts/
│   ├── pdf_to_hwpx.py          ← 메인 실행 스크립트
│   └── remove_highlights.py    ← 검수 후 하이라이트 제거
├── src/
│   ├── ocr/
│   │   ├── mathpix_client.py   ← Mathpix API 클라이언트
│   │   └── pdf_parser.py       ← PDF 마크다운 → 문항별 토큰
│   └── hwpx/
│       ├── builder.py          ← HWPX ZIP 조작 유틸
│       ├── latex_to_hwp.py     ← LaTeX → HWP Script 변환
│       ├── slot_analyzer.py    ← HWPX 슬롯 그룹 분석
│       ├── pdf_filler.py       ← 매칭 + 채우기 + 하이라이트
│       └── change_log.py       ← 변경 로그 + 검수 리포트 생성
├── docs/
│   ├── hwpx_structure.md       ← HWPX XML 구조 레퍼런스
│   ├── USAGE.md                ← 운영 단계별 가이드
│   └── review_*.md             ← 시험지별 검수 리포트 (gitignore 제외)
├── tests/
│   └── test_builder.py
├── samples/                    ← .gitignore (PDF·HWPX 파일, API 결과)
├── .env                        ← .gitignore (API 키 — 절대 커밋 금지)
├── .env.example                ← 커밋 가능 (키 없는 템플릿)
└── requirements.txt
```

---

## 빠른 시작

### 환경 설정

```bash
pip install -r requirements.txt
cp .env.example .env
# .env 에 MATHPIX_APP_ID, MATHPIX_APP_KEY 입력
```

### 기본 실행

```bash
py scripts/pdf_to_hwpx.py \
    "samples/시험지.pdf" \
    "samples/워드초벌.hwpx" \
    "samples/output.hwpx"
```

### 권장 실행 (검수 메커니즘 포함)

```bash
py scripts/pdf_to_hwpx.py \
    "samples/시험지.pdf" \
    "samples/워드초벌.hwpx" \
    "samples/output_v2.hwpx" \
    --pdf-id <Mathpix_pdf_id>       # 재과금 방지: 이미 처리한 ID 재사용
    --highlight                      # 변경 수식 색상 표시
    --min-confidence 0.5             # 의심 변경 자동 차단
    --changes "samples/changes_이름.json"
    --report  "docs/review_이름.md"
```

### 검수 후 색상 제거

```bash
py scripts/remove_highlights.py "samples/output_v2.hwpx"
# 덮어쓰기 대신 새 파일로:
py scripts/remove_highlights.py "samples/output_v2.hwpx" "samples/output_final.hwpx"
```

---

## 옵션 전체 목록

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--pdf-id <id>` | (없음) | 기존 Mathpix 결과 재사용 (과금 방지) |
| `--dry-run` | off | 파일 저장 없이 분석만 출력 |
| `--highlight` | off | 변경 수식에 색상 표시 |
| `--min-confidence <0~1>` | `0.0` | 이 값 미만 신뢰도 변경 건너뜀 (권장: `0.5`) |
| `--changes <path>` | `samples/changes_<출력명>.json` | 변경 로그 JSON 저장 경로 |
| `--report <path>` | `docs/review_<출력명>.md` | 검수 리포트 MD 저장 경로 |

---

## 새 학교 시험지 추가 방법

1. `samples/`에 PDF와 워드초벌 HWPX 복사
2. 첫 실행 (Mathpix 과금 발생, pdf_id 기록):
   ```bash
   py scripts/pdf_to_hwpx.py "samples/NEW.pdf" "samples/NEW_초벌.hwpx" \
       "samples/output_NEW.hwpx" --highlight --min-confidence 0.5
   ```
3. 출력된 `pdf_id` 메모 → 재실행 시 `--pdf-id` 사용
4. `docs/review_NEW.md` 열어 검수 필요 슬롯 확인
5. 한글에서 `output_NEW.hwpx` 열고 🔴 빨간 수식 직접 수정
6. 검수 완료 후 색상 제거:
   ```bash
   py scripts/remove_highlights.py samples/output_NEW.hwpx
   ```

---

## API 비용 안내

| 서비스 | 단가 | 확통 1회분 (4페이지) |
|--------|------|-------------------|
| Mathpix PDF OCR | ~$0.005/페이지 | ~$0.02 |

- `--pdf-id` 재사용 시 추가 과금 없음
- 동일 시험지를 다시 실행할 땐 반드시 `--pdf-id` 사용

---

## 운영 모델

- **사용자**: 학원장 단독 (Cowork 환경)
- **입력**: 교육청/학교 제공 PDF + 자체 제작 워드초벌 HWPX
- **출력**: 수식이 자동 채워진 HWPX → 한글에서 최종 검수 후 인쇄

---

## 관련 문서

- [USAGE.md](docs/USAGE.md) — 단계별 운영 가이드 (학원 현장용)
- [hwpx_structure.md](docs/hwpx_structure.md) — HWPX XML 구조 레퍼런스
