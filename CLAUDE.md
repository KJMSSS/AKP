# CLAUDE.md

Claude Code 작업 시 적용되는 **운영 정책 + 진입점**.
프로젝트 아키텍처는 [docs/PLAN.md](docs/PLAN.md)를 단일 진실 출처로 참조한다.

## 한 줄 정의

한국 수학 시험지 PDF → HWPX 자동 변환 파이프라인 (학원 운영 도구).

**핵심 방향**: 중간 검수·재빌드 루프 없이 **한 번에 최고 품질**로 출력.
OCR 파이프라인 자체의 품질이 전부 — 사람이 고쳐줄 거라는 전제로 설계하지 않는다.

## 절대 정책 (위반 금지)

1. **학교 단위 순차 처리** — 여러 학교 병렬 빌드 금지
2. **LLM은 패턴 발견기** — `temperature=0`. **AI 고확신 패턴(확신 ≥ 0.8)은 자동 등록·적용 허용**,
   저확신은 관리자 승인. 등록된 패턴은 패턴 목록에서 언제든 끄거나 삭제 가능(되돌리기 쉬움).
3. **학원장 PDF 원본 = 진짜 정답** — LLM/OCR 결과보다 원본 PDF 우선
4. **크롭 OCR 표준 순서** — 전체 OCR 후 공란 발견해서 재빌드 금지.
   반드시 `크롭 OCR → raw.md 완성 → 빌드 1회`
5. **두 대 동기화 = main 직접** — 집 데스크톱 ↔ 노트북을 GitHub `main`으로 직접 동기화한다
   (학원장 결정 2026-06-24). 양쪽 `git pull`(시작) → 작업 → `git push`(끝).
   `main` push = Railway 라이브 배포이므로 **push 전 반드시 `pytest` 통과 확인**.
   push는 학원장 "끝내자"/"배포해" 신호 때 실행(깨진 코드 자동 배포 방지).
6. **HWPX 수식 직접 편집 금지** — XML 조립으로 `hp:equation` 만들지 않는다.
   항상 `build_from_markdown()` 파이프라인 경유. 상세: [docs/PLAN.md](docs/PLAN.md) 7-1절
7. **단순 replace 금지** — 한국어 조사(을/를 등) 단순 치환은 안전 정책 위반

## 진입점

```powershell
# 테스트
pytest tests/
pytest tests/test_builder.py::TestLatexToHwp::test_frac_simple -v

# PDF → HWPX (Mathpix, 문제만)
py scripts/text/pdf_to_text.py "samples/시험지.pdf"

# PDF → HWPX (Claude, 정답·해설 포함)
py scripts/text/pdf_to_text.py "samples/시험지.pdf" --ocr-engine claude --full-content

# 재실행 (같은 PDF는 자동 캐시로 불필요한 재과금만 회피 — 품질·효율상 필요하면 --force-ocr로 재과금)
py scripts/text/pdf_to_text.py "samples/시험지.pdf"
# 캐시 무시하고 강제로 새 OCR (의도적 재과금)
py scripts/text/pdf_to_text.py "samples/시험지.pdf" --force-ocr

# 웹 서버
py -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8080
```

> Windows에서 `py` / `python` 별칭이 Microsoft Store 스텁으로 연결되어 exit 49로 실패하면,
> 전체 경로 `C:\Users\사용자\AppData\Local\Programs\Python\Python314\python.exe` 사용.

## 프로젝트 상세 → [docs/PLAN.md](docs/PLAN.md)

다음은 모두 PLAN.md에 있다 — 이 파일에서 중복 작성하지 않는다.

- 아키텍처 (변환 흐름, HWPX 구조, 텍스트 기반 v5 vs 템플릿 기반)
- OCR 엔진 비교 (Mathpix / Claude / OCR A+B+C 개선)
- LaTeX → HWP Script 변환 규칙
- 문제 파서 (`problem_segmenter.py`)
- 비용 관리 (`cost_guard.py`)
- 로드맵 (STEP 1~4)
- 알려진 버그 / 미결 이슈
- HWPX 수식 직접 편집 시 실전 교훈 (7-1절)
- 핵심 파일 구조

## 파일 네이밍

```
[연도_학년_학기_a(중간)/b(기말)_과목약어_학교명]
예: [2025_1_1_b_공수1_경신여고]
```

- 임시 마크다운: `output_text_temp.md` (루트, git 무시)
- 프로덕션 HWPX: `samples/11b_production/` 또는 `samples/2026/`
