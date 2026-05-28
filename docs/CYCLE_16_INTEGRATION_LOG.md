# Cycle 16 통합 작업 로그

---

## STAGE 1 — exam-studio HWPX 검증 도구 차용 (2026-05-28)

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

### 통합 위치

**crop_ocr_builder.py**:
- imports 추가: `fix_hwpx_namespaces`, `validate_hwpx as _hwpx_struct_validate`
- Step 9.5 신규 (표 삽입 후, 골드 검증 전):
  ```
  Step 8: HWPX 빌드 (build_from_markdown)
  Step 9: 표 삽입 (replace_condition_tables, replace_boilerplate_tables)
  Step 9.5: ★ HWPX 구조 검증 ← 신규
    - fix_hwpx_namespaces (ns0: → hp: 정규화)
    - validate_hwpx (XML/수식이스케이프/cellAddr/zOrder/태그균형/매니페스트)
  Step 10: 골드 manifest 정합 검증 (_verify)
  ```

**역할 분리**:
```
_verify()           = 골드 manifest 정합 (의미 검증: 선택지 마커 수)
validate_hwpx()     = HWPX 구조 (구조 검증: XML 파싱, 이스케이프, cellAddr 등)
```

### 제약 사항

- `fix_hwpx()` 자동 수정: **비활성** (학원장 승인 후 활성화 예정)
- `fix_hwpx_namespaces()`: **활성** (의미 변경 없음, 안전)

### 회귀 검증

동신여고 빌드로 Step 9.5 동작 확인:

```
결과: HWPX 구조 검증 ✓ PASS
      골드 manifest PASS ✓ 95/95
```

---

## 다음 단계 제안

| 단계 | 내용 |
|------|------|
| STAGE 2 | fix_hwpx() --fix 활성화 (학원장 승인 후) |
| STAGE 3-1 | pdf_to_text.py에 동일 구조 검증 통합 |
| STAGE 3-2 | 전 학교 v5 HWPX 일괄 구조 검증 배치 스크립트 |
