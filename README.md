# AKP — 시험지 자동 편집 파이프라인

> **A**uto **K**orean exam **P**aper → HWPX  
> PDF 시험지 + 한글 워드초벌 → 자동으로 채워진 최종 HWPX

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
