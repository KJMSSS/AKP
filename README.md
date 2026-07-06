# AKP — 수학 시험지 PDF → HWPX 변환기

학원 운영 도구. 시험지 PDF를 업로드하면 Claude 비전으로 문제를 구조화하고,
검수 화면에서 그림 선택·낙서 지우기·AI 재작도를 거쳐 한글(HWPX) 시험지로 빌드한다.
결과물은 Google Drive 에 자동 업로드된다.

## 빠른 시작

```bash
# 의존성
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 환경변수
cp .env.example .env   # 실제 키로 교체

# 서버
.venv/bin/python -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8080
# → http://localhost:8080 (Google 로그인)
```

## 문서

- 운영 정책·진입점: [CLAUDE.md](CLAUDE.md)
- 아키텍처: [docs/PLAN.md](docs/PLAN.md)
- 엔진 절대규칙(실사고 이력): [docs/ENGINE_RULES.md](docs/ENGINE_RULES.md)

## 구조

```
backend/     변환 엔진 (Claude 비전 구조화 · Gemini 재작도 · HWPX 템플릿 조립)
frontend/    React 검수 UI (회전 → 분석 → 그림검수 → 빌드)
scripts/web/ FastAPI 웹 셸 (OAuth 로그인 · 학교×과목 매트릭스 · Drive 업로드)
tests/       pytest — main push 전 필수 통과
```
