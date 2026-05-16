# 야간 부트스트랩 결과 (2026-05-16)

samples/11b + samples/2024 통합 (124쌍) Mathpix OCR + 부트스트랩 실행.

## 실행 결과 요약

| 단계 | 결과 |
|---|---|
| batch_align (samples/2024 신규 OCR) | 105/106 성공, 1 skip, 0 오류 |
| batch_production (samples/2024) | 105 production HWPX 생성 |
| bootstrap_corrections --include-2024 | 124쌍 비교 → corrections.json 재생성 |

**비용 / 시간**: Mathpix $6.72 (cap $20의 33.6%) / 약 50분 (대부분 OCR)

## Baseline 검증 (A+B+C)

| # | 검증 항목 | 결과 |
|---|---|---|
| **A** | `samples/11b_production/2025_1_1_b_공수1_광주고_v10.hwpx` sha256 | ✅ PASS (변화 없음) |
| **B** | `corrections.json` approved 18 + blacklisted 2 | ✅ PASS (모두 보존) |
| **C** | `samples/11b/_aligned_dataset_v1.jsonl` sha256 | ✅ PASS (변화 없음) |

baseline 스냅샷: [reports/.baseline_20260516/](.baseline_20260516/)

## 교정 후보 분포 변화

| 지표 | 이전 | 이후 | 증감 |
|---|---:|---:|---:|
| 총 후보 | 316 | **1097** | +781 |
| 텍스트 | 299 | 1002 | +703 |
| 수식 | 17 | 95 | +78 |
| 2회 이상 반복 | 23 | **187** | +164 |
| 5회 이상 반복 | 2 | **15** | +13 |
| Approved (보존) | 18 | 18 | 0 |
| Blacklisted (보존) | 2 | 2 | 0 |

## 5+ 빈도 신규 패턴 — 학원장 검토 필요

전체 15개 중 **노이즈로 의심되는 8개를 *로 표시**:

| # | 빈도 | 타입 | 이전 | 이후 | 노이즈? |
|---|---:|---|---|---|---|
| 1 | 11 | text | `을` | `를` | |
| 2 | 11 | text | `학년도` | `년 1 학년` | |
| 3 | 10 | text | `를` | `을` | |
| 4 | 10 | text | `학기` | `a 수하 광주제일고` | ⚠ 학교명 누출 |
| 5 | 9 | text | `a` | `중간` | ⚠ 난이도 라벨 누출 |
| 6 | 8 | text | `1` | `쉬움` | ⚠ 난이도 라벨 누출 |
| 7 | 7 | text | `2` | `명 학년 학생` | ⚠ 헤더 텍스트 누출 |
| 8 | 7 | text | `학년` | `제` | |
| 9 | 6 | text | `보기` | `보 기` | ⚠ 띄어쓰기 역방향 (의심) |
| 10 | 6 | text | `오른쪽` | `다음` | |
| 11 | 5 | text | `1 2 3` | `보통` | ⚠ 난이도 라벨 누출 |
| 12 | 5 | text | `3` | `출처 광주 2024` | ⚠ 메타데이터 누출 |
| 13 | 5 | text | `대하 여` | `대하여` | |
| 14 | 5 | text | `라` | `라고` | |
| 15 | 5 | text | `아래` | `다음` | |

**핵심 인사이트**: 5+ 빈도 신규 패턴 중 절반이 시험지 헤더의 난이도 라벨/메타데이터 누출. 2024 시험지가 11b와 다른 헤더 구조를 가져서 본문과 align되며 잘못 매칭됨.

## ⚠️ 발견된 이슈 3건 — 후속 조치 필요

### 1. samples/2024 align rate 거의 0%
- batch_align 출력에서 모든 2024 시험지가 `정합: 0/0 (0%)`로 나옴
- pair_align.align()이 2024 시험지의 markdown↔HWPX 구조를 매칭 못함 (11b는 잘 동작)
- 결과: aligned_dataset.jsonl에 2024 레코드 14개만 수집됨 (목표는 800+)
- **원인 가능성**: 2024 시험지는 객관식/주관식 구분, 문제 번호 표기, 또는 HWPX paragraph 구조가 11b와 다를 가능성
- **권장**: pair_align의 매칭 휴리스틱을 2024 구조에 맞게 보강 (별도 작업)

### 2. 학교 메타데이터 부정확
- `_school_name()`이 파일명 마지막 토큰을 학교명으로 쓰는데, 2024 파일은 `..._문성고_도함수의_활용_정적분의_활용_v1.hwpx` 형태라 `활용`이 학교명으로 잡힘
- 보고서의 "학교별 발견 후보" 섹션에 `활용 100개`, `조건부확률 54개` 같은 가짜 학교 다수 등장
- **영향**: corrections 자체는 정확. schools 메타데이터만 부정확 → 추적/필터링 시 불편
- **권장 패치**: `_school_name()`에 `(고|여고|중|초)$` 패턴 인식 추가 (간단)

### 3. 양식별 분포 (타이퍼 vs 수학비서) — 데이터 없음
- corrections.json entry 필드: `[old, new, type, frequency, schools, first_seen, approved, context_examples]`
- **양식(form) 필드 없음** → 보고할 수 없음
- **권장**: 양식별 분포가 중요하다면 schools와 같이 form 메타데이터 추가 필요. PDF/HWPX 원본 또는 파일명에 양식 정보가 있는지 먼저 확인 필요

## 노이즈 의심 패턴 (1회만 발견된 것 중 표본)

수식 교정 후보 중 길이 격차 큰 케이스 다수 발견 — 수식 alignment 오류로 추정:

```
[equation] 'A=B' -> 'B`= left( rpile{ 2 ...'         (전혀 다른 수식)
[equation] 'A, P, Q' -> '{1} over {ax}+{1} over {by}` (점 좌표 → 분수)
[equation] '3!times 0!=6' -> '{}_{7}P_{3}=7times6...' (계승 → 순열)
```

수식 95개 중 명백한 의미 일치는 일부, 다수가 align 오류 가능성.

## 다음 단계 권장

1. **즉시 (학원장 검토)** — review_corrections.py로 5+ 빈도 15개 중 노이즈 8개 blacklist 처리
2. **단기 패치** — `_school_name()` 학교명 패턴 인식 (10분, [scripts/learn/bootstrap_corrections.py](../scripts/learn/bootstrap_corrections.py))
3. **중기 작업** — pair_align의 2024 시험지 매칭 로직 개선 (별도 사이클)
4. **장기 작업** — 양식(form) 메타데이터 도입 (필요 여부 확인 후)

## 변경된 파일 / 산출물

**코드 패치 (5개)**:
- [src/common/ocr/mathpix_client.py](../src/common/ocr/mathpix_client.py) — pdf_id 영구 캐시 추가
- [scripts/batch_align.py](../scripts/batch_align.py) — `--source-dir` + pdf_id 캐시 사용
- [scripts/batch_production.py](../scripts/batch_production.py) — `--source-dir`, `--out-dir`
- [scripts/learn/bootstrap_corrections.py](../scripts/learn/bootstrap_corrections.py) — approved/blacklisted 보존 + 다중 (prod, gold) 디렉토리

**생성된 산출물**:
- `samples/2024/_aligned_dataset.jsonl` (14 레코드 — align 이슈로 적음)
- `samples/2024/_*_raw.md` (Mathpix markdown 106개)
- `samples/2024_production/*_v1.hwpx` (105 production HWPX)
- `.mathpix_cache/pdf_ids.json` (pdf_id 영구 캐시, 재실행 시 재과금 방지)
- `src/learn/corrections.json` (재생성, 1097 후보)
- `src/learn/corrections.json.bak` (재생성 직전 백업)
- `reports/corrections_bootstrap_20260516.md` (자동 생성된 상세 보고서)

## Git 정책

- samples/, .mathpix_cache/는 .gitignore되어 추적 안 함
- 패치 5건 + 본 보고서 + corrections.json은 git 추적 대상이나 **commit/push 안 함** (학원장 OK 후 진행 — 정책)
