# Cycle 16 통합 작업 로그

---

## STAGE 번호 체계 (2026-05-28 확정 — CC 초안 정정)

| 번호 | 의미 |
|------|------|
| STAGE 1   | exam-studio HWPX 검증 도구 차용 (완료) |
| STAGE 1.1 | pdf_to_text.py 검증 통합 |
| STAGE 1.2 | 광주고 v17 + 14개교 v5 일괄 validate |
| STAGE 1.3 | 미빌드 4개교 빌드 (경신/광덕/광주여/대성) |
| STAGE 2   | corrections.json 카테고리화 |
| STAGE 3-N | equation.py R-N 규칙 적용 (N=1~10) |

> CC가 "STAGE 3-1", "STAGE 3-2"로 잘못 제안한 번호를 STAGE 1.1 / 1.2로 정정.
> 이후 모든 commit message에 이 번호 사용. 임의 번호 생성 금지.

---

## baseline 상태 (2026-05-28 확정)

| 기준 | sha / 상태 |
|------|-----------|
| 시각 baseline | 동신여고 v_PASS (광주여고 미빌드, 지침 v3 가정 어긋남) |
| sha baseline | 광주고 v17 `e1c77bae1921` |
| 미빌드 4개교 | 경신여고, 광덕고, 광주여고, 대성여고 |

---

## STAGE 1 — exam-studio HWPX 검증 도구 차용 (2026-05-28 완료)

**커밋**: `6e57996`

### 차용 출처

```
저장소: D:/f1/exam-studio
commit: 4a96778 (fix(equation): 복잡 \frac·순열조합·호 변환 실버그 4건 수정)
```

### 차용 자산

| exam-studio 원본 | AKP 대상 | 패턴 |
|-----------------|---------|------|
| `resources/hwpx_scripts/validate.py` | `src/common/hwpx_validator.py` | A (직접 이식) |
| `resources/hwpx_scripts/fix_namespaces.py` | `src/common/hwpx_namespace_fixer.py` | A (직접 이식) |
| `docs/hwpx-pitfalls.md` | `docs/HWPX_PITFALLS.md` | D (AKP 코드 라인 매핑 추가) |

### 충돌 점검 결과

| 항목 | 결과 |
|------|------|
| AKP 기존 HWPX 검증 함수 | 없음 → 충돌 없음 |
| fix_namespaces.py — 네임스페이스 | AKP text_builder.py가 이미 hp:/hh:/hc:/hs: 직접 사용 → no-op, 충돌 없음 |
| validate.py — lxml 의존성 | AKP requirements.txt에 lxml 없음 → stdlib ET fallback 사용, 충돌 없음 |
| fix_namespaces.py — 의존성 | 순수 stdlib (zipfile, re, os) → 충돌 없음 |

### 통합 위치 (crop_ocr_builder.py)

```
Step 8:   HWPX 빌드 (build_from_markdown)
Step 9:   표 삽입 (replace_condition_tables, replace_boilerplate_tables)
Step 9.5: ★ HWPX 구조 검증 ← STAGE 1 신규
            fix_hwpx_namespaces (ns0: → hp: 정규화)
            validate_hwpx (XML/수식이스케이프/cellAddr/zOrder/태그균형/매니페스트)
Step 10:  골드 manifest 정합 검증 (_verify)
```

역할 분리: `_verify()` = 골드정합(의미) / `validate_hwpx()` = XML구조(구조)

### 제약

- `fix_hwpx()` 자동 수정: **비활성** (학원장 승인 후 활성화 예정)
- `fix_hwpx_namespaces()`: **활성** (의미 변경 없음, 안전)

### 회귀 검증

동신여고: 구조 PASS ✓ + 골드 PASS ✓ 95/95

---

## STAGE 1.1 — pdf_to_text.py 검증 통합 (2026-05-28)

### 통합 위치 (pdf_to_text.py)

```
[ 2단계 ]   HWPX 생성 (build_from_markdown)
[ 2.5단계 ] ★ HWPX 구조 검증 ← STAGE 1.1 신규
              fix_hwpx_namespaces()
              validate_hwpx() → 실패 시 HWPXValidationError 발생
[ 완료 ]
```

### 오류 처리

- `HWPXValidationError` (hwpx_validator.py에 정의)
- 구조 이슈 발견 시 상세 출력 후 raise → 학원장 보고
- fix=False (학원장 승인 후만 fix 활성화)

---

## STAGE 1.2 — 19개 HWPX 일괄 validate (2026-05-28 완료)

**커밋**: (STAGE 1.1과 통합)

### 대상 (19개)

v5 파일 18개 + 광주고 v17

### 결과: PASS 19개 / FAIL 0개

| 학교 | 파일 sha (sha1 앞 12자) | 구조 검증 |
|------|----------------------|---------|
| 경신여고_v5 | `268f3353e8fa` | ✓ PASS |
| 고려고_v5 | `95db6530e656` | ✓ PASS |
| 광덕고_v5 | `ba246a3c7076` | ✓ PASS |
| 광주고_v5 | `bba9136f96ea` | ✓ PASS |
| 광주여고_v5 | `c0aa47f14d09` | ✓ PASS |
| 광주제일고_v5 | `857a110991d8` | ✓ PASS |
| 국제고_v5 | `8aeeaa956dfc` | ✓ PASS |
| 금호고_v5 | `ff857c10c5ac` | ✓ PASS |
| 금호중앙여고_v5 | `277061e1ae3a` | ✓ PASS |
| 대광여고_v5 | `fd439b02ee96` | ✓ PASS |
| 대동고_v5 | `6b536563c1f1` | ✓ PASS |
| 대성여고_v5 | `99e3a9a316e3` | ✓ PASS |
| 동명고_v5 | `fdf677dcabe6` | ✓ PASS |
| 동성고_v5 | `c5429c5e8201` | ✓ PASS |
| **동신여고_v5 (시각 baseline)** | `84ed78cbb5be` | ✓ PASS |
| 동아여고_v5 | `afbe3e47f785` | ✓ PASS |
| 명진고_v5 | `da4ca6f3fbaf` | ✓ PASS |
| 문성고_v5 | `089b1351a6b3` | ✓ PASS |
| **광주고_v17 (sha baseline)** | `e98cfc5d88d8` | ✓ PASS |

> **sha 주의**: 이전 기록의 `e1c77bae1921`은 git 커밋 sha (파일 생성 시점),
> 위 `e98cfc5d88d8`는 파일 내용 sha1. samples/는 .gitignore 대상이므로 파일 sha로 추적.
> **광주고 v17 파일이 변경되지 않았음을 확인.**

### fix dry-run 결과

```
수정 대상: 0건
escape=0  celladdr=0  zorder=0
→ --fix 활성화 시 원본 변경 없음. 현재 AKP 출력물 구조 이상 없음.
```

### 결론

- `--fix` 활성화 불필요 (현재 출력물 구조 이상 없음)
- AKP 파이프라인 XML 생성 로직 정상 (`_xe()` 이스케이프, `_ez()` zOrder 카운터)

---

## STAGE 1.3 — 미빌드 4개교 빌드 (예정)

대상: 경신여고, 광덕고, 광주여고, 대성여고

---

## STAGE 2 — corrections.json 카테고리화 (예정)

---

## STAGE 3-N — equation.py R-N 규칙 적용 (예정)

R-01: 통수식 split (분수/루트 한 줄 표현 강제)
