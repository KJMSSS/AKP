# 인계 노트 — 2026-05-12

다음 세션 또는 다른 작업자를 위한 현재 상태 요약.

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
