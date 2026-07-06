# CLAUDE.md

Claude Code 작업 시 적용되는 **운영 정책 + 진입점**.
아키텍처는 [docs/PLAN.md](docs/PLAN.md), 엔진 절대규칙은 [docs/ENGINE_RULES.md](docs/ENGINE_RULES.md)가 단일 진실 출처.

## 한 줄 정의

한국 수학 시험지 PDF → HWPX 자동 변환 파이프라인 (학원 운영 도구).
**2026-07-06 대전환**: 구엔진(src/, 텍스트 기반 v5) 전면 폐기 → examconv 엔진(`backend/`,
템플릿 복제 + Claude 비전 구조화 + React 검수 UI) 이식. 흐름:
**매트릭스 셀 → 업로드 → 회전 맞추기 → 분석(opus) → 검수(그림 크롭·낙서 지우개·Gemini 재작도) → 빌드 → Drive 업로드**.

## 절대 정책 (위반 금지)

1. **학교 단위 순차 처리** — 여러 학교 병렬 빌드 금지
2. **학원장 PDF 원본 = 진짜 정답** — LLM/OCR 결과보다 원본 PDF 우선
3. **두 대 동기화 = main 직접** — 집 데스크톱 ↔ 노트북을 GitHub `main`으로 직접 동기화
   (학원장 결정 2026-06-24). 양쪽 `git pull`(시작) → 작업 → `git push`(끝).
   `main` push = Railway 라이브 배포이므로 **push 전 반드시 `pytest` 통과 확인**.
   push는 학원장 "끝내자"/"배포해" 신호 때 실행(깨진 코드 자동 배포 방지).
4. **HWPX 조립은 `build_exam()` 경유만** — XML 수작업으로 `hp:equation`/배치 만들지 않는다.
   페이지 배치·수식·그림 규칙은 [docs/ENGINE_RULES.md](docs/ENGINE_RULES.md) 필수 숙지
   (높이 추정 패킹 금지, columnBreak 고정 규칙, x^{2} 중괄호 등 실사고 이력 규칙).
5. **변환 결과물은 디스크에 쌓지 않는다** — 작업 폴더(`DATA_DIR/work/{job}`)는 3일 후 자동
   삭제(2026-07-06 학원장 결정). 영속 보관처는 Google Drive(빌드 시 자동 업로드).
6. **구조화·검증 모델 = opus** (`claude-opus-4-8`, 충실도>비용). 그림 재작도 = Gemini.

## 진입점

```bash
# 서버 (로컬 맥)
.venv/bin/python -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8080
# ⚠️ --reload 운영 금지 (인메모리 JOBS 소실 → 검수 중 작업 날아감)

# 테스트 (main push 전 필수)
.venv/bin/python -m pytest tests/

# 프론트(검수 UI) 수정 후 재빌드 — dist 는 커밋 대상(배포에 node 없음)
cd frontend && npm run build

# 접속
# http://localhost:8080/           ← 매트릭스 (로그인 필요)
# http://localhost:8080/converter/ ← 변환·검수 UI (매트릭스 셀에서 진입)
# http://localhost:8080/admin      ← 사용자 관리
```

## 구조 요약

```
backend/            변환 엔진 (examconv 이식 — 무수정 유지 원칙)
  pipeline/         ingest·vision_claude·build_exam·assemble_hwpx·figure·redraw_gemini…
  mathconv/         LaTeX → 한글 수식
  templates/base.hwpx   빌드 골격 (A3 2단 신문형 — 구조 의존, 교체 금지)
frontend/           React 검수 UI (Vite) → dist 커밋
scripts/web/        웹 셸: app.py(라우트) · engine_api.py(엔진 라우터+Drive/레지스트리)
                    · auth.py · store.py · users.py · usage_log.py · gdrive_uploader.py
scripts/web/static/ matrix.html(메인) · admin.html · login.html · guide.html
```

## 환경변수 (.env)

`ANTHROPIC_API_KEY`(필수) · `GEMINI_API_KEY`(재작도) · `GOOGLE_CLIENT_ID/SECRET`(로그인+Drive)
· `SECRET_KEY` · `ADMIN_EMAIL` · `DAILY_COST_CAP`(기본 5.0) · `DATA_DIR`(Railway 볼륨)

## 파일 네이밍 (레지스트리 키)

```
연도_학년_학기_a(중간)/b(기말)_과목약어_학교명
예: 2025_1_1_b_공수1_경신여고  →  Drive 파일명 [2025_1_1_b_공수1_경신여고].hwpx
```
