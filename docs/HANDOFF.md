# 인계 노트 — 2026-05-12 (2026-05-14 추가)

다음 세션 또는 다른 작업자를 위한 현재 상태 요약.

---

## 2026-05-14 업데이트 — OCR fallback 정책 전환

### 배경
이전 세션에서 `src/text_only/ocr_fallback.py`가 손상 영역을 Claude
Vision으로 자동 재처리하는 방식이었으나, 환각 사례가 발견됨:
- `y=√a` → `y=√6`
- `nroot126` → `nroot46`

학원 운영에서 수식 환각은 치명적이므로 "자동 복구" 대신 "감지 + 사람이
PDF 보고 직접 확인" 정책으로 전환.

### 현재 동작
1. **`src/text_only/ocr_fallback.py`**
   - `ENABLE_VISION_FALLBACK = False` (Vision 비활성, 코드는 보존)
   - 4가지 손상 패턴 감지: Mathpix CDN 이미지, 인라인 수식 한글, 디스플레이 한글 ≥30%, 빈 함수 정의
   - 손상 영역마다 `[★ OCR 실패 영역 — 원본 PDF 참조]` 플레이스홀더 삽입
   - 페이지 마커(`\page{N}`)가 있으면 `[★ OCR 실패 영역 — 원본 PDF p.{N} 참조]` 형태

2. **`src/text_only/handwriting_filter.py`** (Claude AI 기반)
   - 시스템 프롬프트 최상위에 "★ 플레이스홀더 절대 보존" 규칙 추가
   - 강화 후에도 60% 정도(6/10)만 자연 보존됨

3. **`reinforce_placeholders(filtered_md, raw_md)`** (신규)
   - 필터가 누락한 ★ 마커를 코드 로직으로 강제 재삽입
   - 문항 단위(선택형 N., 서술형 N)로 손상 카운트 vs ★ 카운트 비교
   - 부족분만큼 해당 문항 블록 끝에 ★ 삽입 → 위치 정밀도는 떨어지지만 "어느 문항에 OCR 실패가 있었는가" 정보는 100% 보존

4. **`scripts/text/pdf_to_text.py`**
   - 흐름: Mathpix OCR → `apply_fallback` → `filter_handwriting` → `reinforce_placeholders` → HWPX 빌드
   - filter 후 보강 로그: `[reinforce] 필터가 누락한 ★ 마커 N건 재삽입`

### 광주고 공수1 (2026-1-1) 검증 결과
- raw OCR: 10,508자, Mathpix CDN 이미지 9건, 빈 함수 정의 1건
- fallback 후: 플레이스홀더 10건 삽입
- 손글씨 필터 후: ★ 6건 자연 보존 (4건 누락)
- reinforce 후: **★ 10건 완전 복원** (서술형 2에 1건, 서술형 3에 3건 재삽입)
- 수식 내 한글 잔재: 0건 ✅
- 출력: `samples/output_text_(광주)[2026_1_1_a_공수1_광주고]_v2.hwpx` (문단 202, 수식 123)

### 알려진 한계
- **HandwritingFilter도 LLM 추론을 함** — 환각 위험 존재 (이번 세션에서 발견)
  - 예: 광주고 문제 19 ②: raw 선택지에 `-58`이 없는데 필터가 수열 추론으로 채워넣음
  - 예: 광주고 문제 12: raw 조건 4개 → 필터가 2개만 남기고 재배치
  - 다음 작업: 필터 프롬프트에 "추론 금지" 강화 또는 필터 자체 비활성 옵션
- ★ 재삽입은 문항 끝에 들어감 — 그림이 본문 중간에 있던 경우 위치 손실

### 재활성화 시 체크리스트 (Vision fallback)
1. Vision 응답에 환각 검사 단계 추가 (raw OCR과 비교, 새로 등장한 수식 토큰 자동 점검)
2. 의심 영역만 골라 부분 Vision 호출하는 인터페이스 (전체 재처리 X)
3. 사람이 검토하는 diff 리포트
4. 그 뒤 `ENABLE_VISION_FALLBACK = True` 전환

---

## 오늘 완성된 것

### 코어 파이프라인
- `src/ocr/pdf_parser.py` — PDF 마크다운 → 문항별 토큰 그룹핑 (선택형/서술형)
- `src/hwpx/slot_analyzer.py` — HWPX section0.xml → 문항별 슬롯 그룹핑
- `src/hwpx/pdf_filler.py` — 토큰-슬롯 매칭 + 채우기 (내용 기반 + answer_num 위치 대응)
- `scripts/pdf_to_hwpx.py` — 5단계 통합 파이프라인 CLI

### 검수 메커니즘
- `src/hwpx/change_log.py` — ChangeRecord + JSON 로그 + Markdown 리포트
- `scripts/remove_highlights.py` — hp:equation textColor 초기화
- `--highlight`, `--min-confidence`, `--changes`, `--report` 플래그

### 핵심 알고리즘 결정사항
1. **순서 폴백 없음**: 내용 기반 정확 매칭만 사용 (오배치 방지)
2. **answer_num 위치 기반**: OCR에서 특정 번호 누락 시 나머지 밀림 방지
3. **신뢰도 분류**: norm(원본) == norm(적용) → 1.0, 다름 → 0.3 (하드코딩 휴리스틱)
4. **_norm 함수**: 백틱 제거 + 연속쉼표 정규화 + 단일 토큰 중괄호 제거 + XML 엔티티 디코딩

### 검증 결과 (확통 경신여고 2025-2학기 1회)
- 전체 슬롯: 181개
- 실제 변경: 22개 (형식 21개 + 내용 1개)
- `--min-confidence 0.5` 사용 시: 22개 → 21개 (의심 변경 1개 차단)
- OCR 한계: 11·12·15·16·17번 미매칭 (2단 레이아웃 + 서술형 레이블 누락)
- 알려진 오류: 13번 ④ 답지 `7/27` → OCR이 `2/25`로 잘못 읽음 (신뢰도 0.5 차단됨)

---

## 다음 세션에서 할 만한 것

### 우선순위 높음
1. **Cowork 환경 셋업** — 클라우드 PC에 Python + 의존성 설치, .env 설정
2. **다른 과목 시험지 테스트** — 공수1, 기하, 미적분 각 1장씩 실행해보기
3. **서술형 레이블 OCR 개선** — `서술형1`이 없을 때 다른 패턴으로 보완 탐색

### 우선순위 중간
4. **Mathpix lines.json 활용** — per-formula 실제 confidence 연동 (현재는 0.3 고정)
5. **임시 스크립트 정리** — `scripts/_analyze_*.py`, `scripts/_reparse_pdf.py` 삭제
6. **tests/ 보강** — pdf_parser, slot_analyzer, pdf_filler 단위 테스트 추가

### 장기
7. **웹 UI** — 파일 드래그앤드롭으로 실행하는 간단한 인터페이스
8. **배치 처리** — 여러 시험지를 한 번에 처리

---

## 알아둘 점

### 보안
- `.env` 절대 커밋 금지 (`.gitignore` 설정됨)
- Mathpix API 키는 `.env` 파일에만 저장
- `samples/` 폴더는 gitignore (시험지 PDF·HWPX 포함)

### Mathpix pdf_id 재사용
- `pdf_id` 기록해두면 재과금 없이 재실행 가능
- 확통 경신여고: `0202d1c2-9ca9-4d2f-8f51-ae117bc15d8c`
- 다른 시험지 실행 후 터미널에서 `pdf_id:` 줄 기록

### HWPX 구조
- 수식 슬롯 = `<hp:equation>` 안의 `<hp:script>` 태그
- 문항 경계 = `<hp:t>N번</hp:t>` 패턴
- 답지 경계 = `<hp:t>① </hp:t>` ~ `<hp:t>⑤ </hp:t>` 원문자 마커
- 하이라이트 = `hp:equation` 의 `textColor` 속성 변경

### 주요 파일 위치
```
d:\f1\AKP\              ← 프로젝트 루트
d:\f1\AKP\.env          ← API 키 (절대 공유 금지)
d:\f1\AKP\samples\      ← 시험지 파일 (gitignore)
d:\f1\AKP\docs\         ← 문서 및 검수 리포트
```

---

## 참고: 주요 정규식 패턴

```python
# PDF 선택형 문항 분리
r'(?:^|\n)(\d{1,2})[.．]\s*'

# PDF 서술형 문항 분리 (OCR 오타 서술헝 포함)
r'(?:^|\n)(?:##\s*)?서술[형헝]\s*(\d+)\s*[.,，．\s]'

# 답지 추출
r'[（(]([1-5])[）)]\s*([^\n（(]+)'

# HWPX 토큰 스캔
r'<hp:t[^>]*>([^<]+)</hp:t>|<hp:script>(.*?)</hp:script>'
```
