# AKP 프로젝트 플랜 (단일 진실 출처)

> 2026-07-06 대전환 반영. 이전 플랜(구엔진 v5 텍스트 파이프라인)은 git 이력 참조.
> 엔진 내부 절대규칙(배치·수식·그림·재작도)은 [ENGINE_RULES.md](ENGINE_RULES.md).

## 1. 개요

한국 수학 시험지 PDF → HWPX 자동 변환. 학원 운영 도구(직원 사용).
2026-07-06, 별도 프로젝트(examconv, ~/Desktop/test)에서 실전 검증된 엔진을 통째로 이식하고
구엔진(src/ 51파일 13,300줄 + CLI 70여 개)을 전면 삭제했다.

- **유지(AKP 셸)**: Google OAuth 로그인·사용자 관리, 학교×과목 매트릭스·레지스트리,
  Google Drive 업로드, Railway 배포
- **이식(examconv)**: 변환 엔진 전체 + React 검수 UI (회전·그림 크롭·낙서 지우개·Gemini 재작도)

## 2. 변환 흐름

```
매트릭스 셀 클릭 ─→ /converter/?key=…&school=…&subject=…
  1) 업로드           POST /api/analyze (registry_key 포함, 레지스트리 converting 등록)
  2) 페이지 렌더       PyMuPDF → page-*.png  (무과금)
  3) 회전 맞추기       사용자 수동 (PreviewRotate — 자동 OSD 없음)
  4) 분석             POST /api/jobs/{id}/run → Claude 비전 구조화 (opus, 과금)
  5) 검수             Review UI — 문제 편집 + 그림 드래그 크롭 / 지우개 / Gemini 재작도
                      (재작도는 Claude 검증 게이트 통과해야 채택, 실패 시 반려)
  6) 빌드             POST /api/jobs/{id}/build → base.hwpx 템플릿에 주입
                      → Drive 업로드(AKP/{연도}/{과목}/[key].hwpx) + 레지스트리 done
  7) 다운로드          GET /api/jobs/{id}/download
```

## 3. 모듈 구조

| 경로 | 역할 |
|---|---|
| `backend/pipeline/ingest.py` | PDF → 페이지 PNG (fitz) |
| `backend/pipeline/vision_claude.py` | Claude REST — 구조화·검증·발문/표 중복제거 (usage 누적) |
| `backend/pipeline/figure.py` | 크롭·배경 흰색화·deskew·지우개 인페인팅(OpenCV) |
| `backend/pipeline/redraw_gemini.py` | Gemini image-to-image 재작도 (Flash/Pro) |
| `backend/pipeline/build_exam.py` | 오케스트레이터 — 배치(2문제/단 columnBreak)·수식·그림 삽입 |
| `backend/pipeline/assemble_hwpx.py` | 순수 stdlib HWPX 엔진 (템플릿 열고 본문 주입) |
| `backend/mathconv/latex_to_hwp.py` | LaTeX → 한글 수식 스크립트 |
| `backend/templates/base.hwpx` | 빌드 골격 (A3 2단 신문형 — 구조 의존, 임의 교체 금지) |
| `scripts/web/app.py` | 웹 셸 라우트 (인증·매트릭스·레지스트리·수동 업로드 슬롯) |
| `scripts/web/engine_api.py` | 엔진 라우터 — 인증 가드·비용 로깅·Drive/레지스트리 연동·3일 정리 |
| `scripts/web/store.py` | 경로(DATA_DIR/WORK_DIR)·config/registry I/O 단일 출처 |
| `scripts/web/auth.py` | 세션 인증 헬퍼 |
| `frontend/` | React 검수 UI — 수정 시 `npm run build` 후 dist 커밋 |

## 4. 데이터·비용

- **작업 파일**: `DATA_DIR/work/{job}/` (원본·페이지 PNG·크롭·state.json·result.hwpx).
  3일 보관 후 자동 삭제. 매트릭스에서 잡 삭제 시 즉시 삭제(+Drive 파일).
- **영속 데이터**: matrix_config.json(시드 커밋) · matrix_registry.json · users.json ·
  usage.jsonl · gdrive_token.json — 전부 DATA_DIR(Railway 볼륨).
- **비용**: opus 단가로 usage.jsonl 기록(analyze/build/redraw). 일일 캡
  `DAILY_COST_CAP`(전체) + 사용자별 cap_usd. 초과 시 429.
- **모델**: 구조화·검증 `claude-opus-4-8` / 재작도 `gemini-3.1-flash-image`(기본),
  `gemini-3-pro-image`(pro).

## 5. 검증 한계 (절대 잊지 말 것)

hwpx 레이아웃은 **한글 없이 검증 불가**. 코드로 확인 가능한 것은 XML 사실뿐 —
"XML이 올바르다" ≠ "한글에서 잘 보인다". 실제 배치는 반드시 한글에서 열어 확인.
(ENGINE_RULES.md 검증 절 참조)

## 6. 배포 (Railway)

- `railway.toml`: uvicorn 8080, healthcheck `/api/usage`
- 시스템 패키지 불필요 (tesseract 제거 — nixpacks.toml 삭제됨. opencv 는 headless 휠)
- frontend/dist 커밋 필수 (배포 환경에 node 없음)
- 볼륨: `RAILWAY_VOLUME_MOUNT_PATH` 최우선 → DATA_DIR env → scripts/web/data

## 7. 미결 이슈 / 다음 작업

- [ ] 검수 중 새로고침 시 프론트 상태(그림 배정) 소실 — job_id localStorage + 재구성 API
- [ ] 학기 칸 표기 형식 — 현재 코드값(registry key) 그대로. "2024년 3학년 1학기 기말" 형식
      원하면 별도 입력 필드 필요 (examconv 2026-07-05 세션 미결 그대로 승계)
- [ ] guide.html 내용이 구엔진 흐름 기준 — 새 흐름으로 갱신 필요
- [ ] hwpx → hwp 자동 변환은 이 환경(맥)에서 불가 결론 — 한글에서 다른 이름으로 저장
