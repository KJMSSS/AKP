# AKP 프로젝트 — 시험지 PDF → HWPX 자동 변환 시스템 (지침 v3.1)

> **버전**: v3.1 (2026-05-17 보완 — 학원장 워크플로우 명세 추가)
> **이전 버전**: v3 (Cycle 16 새 시작)
> **v3.1 변경**: 학원장 실제 워크플로우 + 1단 결과 형식 명세 + 우리 시스템 가치 지점

---

## 프로젝트 정체성

한국 수학 시험지 PDF를 타이퍼 양식 한글(HWPX) 시험지로 자동 변환하는 학원 운영 도구.
- GitHub: KJMSSS/AKP
- 작업 폴더: D:\f1\AKP
- 사용자: 광주 지역 학원 운영 (학원장)
- 운영 규모: 1주 30개 시험지 (광주 자가 15개 + 비서 결제 15개)

---

## 현재 단계 — Phase 1.5 (Cycle 16 새 시작)

### Phase 정의

| Phase | 정의 | 상태 |
|---|---|---|
| Phase 1 (이전) | 한글 정확 인식 (사전 등록 위주) | 한계 인정 |
| Phase 1.5 (Cycle 15h) | OCR 자체 개선 (LLM, 클로바, 그림) | 인프라 구축, LLM 비결정성 발견 |
| **Phase 1.5 (Cycle 16 — 현재)** | **PDF 품질 우선 + 정상 PDF baseline + LLM 격하** | **광주여고 baseline부터 새 시작** |
| Phase 2 | 타이퍼 양식 자동 변환 (1단 → 2단) | 대기 |
| Phase 3 | 해설 자동 생성 | 대기 |
| Phase 4 | 수학비서 → 타이퍼 자동 변환 | 대기 |
| Phase 5 | MCP 비서 연계 | 대기 |

### Cycle 16 새 시작 사유

**2026-05-17 학원장 핵심 발견 3가지**:

1. **LLM 단독 운영 불가 (비결정성)** — v16/v17 정확도 80% → 20% 역효과
2. **PDF 품질이 진짜 OCR 한계** — 광주고 PDF = 사진 찍어서 만든 것
3. **광주고 v10 baseline 가정 오류** — sha 일치 ≠ 시각 정합

**학원장 결정**: 첫 단추부터 다시 — 광주여고 baseline 시도

---

## ⭐ 학원장 실제 워크플로우 (v3.1 신규)

### 워크플로우 B (자가 제작분) — 우리 시스템 영역

```
[학원장 1주 15개 시험지 처리 흐름]

Step 1. 우리 시스템 (Cycle 16 작업 영역)
   - 입력: 학교 원본 PDF (광주 한정)
   - 출력: 1단 HWPX (한글에서 편집 가능)

Step 2. 학원장 한글에서 1단 HWPX 열기 + 검수
   - 본문/보기/수식/그림 정확성 확인
   - 시각 검수 우선

Step 3. 학원장: 타이퍼 양식 빈 템플릿 준비
   - 방식: 이전 시험지(타이퍼 양식) HWPX 복사
   - 학원장이 매번 새 파일 만들지 않고, 기존 파일 변형
   - 별도의 "깨끗한 빈 템플릿"은 없음 — 이전 시험지가 곧 템플릿

Step 4. 학원장: 1단 내용 복붙
   - 1단 HWPX에서 본문/보기/수식/그림 복사
   - 타이퍼 템플릿에 붙여넣기

Step 5. 학원장: 헤더 정보 수정
   - 학교명, 학기, 차수, 문제 번호 등

Step 6. 학원장: 최종 검수 후 학생 배포
```

### 우리 시스템의 가치 지점 ⭐

```
1단 HWPX의 정확도 = 학원장 복붙 작업의 효율

🟢 1단이 깨끗 → 학원장 복붙 빠르고 정확 → 시간 절약 큼
🔴 1단이 깨짐 → 학원장이 일일이 수정 → 가치 ↓

→ Cycle 16의 핵심: 1단 정확도 극대화
```

### 1단 결과 형식 명세 (Cycle 16 출력 표준)

CC가 만드는 1단 HWPX는 다음 형식을 따라야 함:

```
출력 위치: samples/11b_production/[학교명]_v{N}.hwpx

형식 요구사항:
- 1단 구성 (Cycle 16 작업)
- 학원장이 한글에서 열어서 복사 가능
- 한글 호환 (재구독 정상 가능)

요소별 보존 요구사항:
1. 본문 한글 텍스트
   - 자연스러운 한글 (자모 분리 X, 오자 최소화)
   - 단락 구조 유지
   - 헤더(학교/학년/차수)는 별도

2. 수식
   - 한글 hp:script 객체 (편집 가능)
   - 단순 텍스트 변환 금지
   - LaTeX 잔재 제거 (\right, RIGHT 등)
   - 부등호 양식: ≤ ≥ 한 줄 표현

3. 그림
   - HWPX BinData inline image
   - 위치 정확 (해당 문제 본문 근처)
   - 크기 적절

4. 보기
   - 원문자 ①②③④⑤
   - 순서 정확 (PDF 원본 순)
   - 5개 마커 보장

5. 문제 번호
   - 1~N 정상 순서
   - 번호 누락 없음
   - 페이지 break 적절
```

### Phase 2 — 타이퍼 양식 자동 변환 (미래)

```
목표: 학원장 Step 3~5를 자동화

작업:
1. 광주 선생님의 "이전 시험지 변형" 방식 학습
2. 1단 HWPX → 2단 타이퍼 양식 자동 매핑
3. 헤더/푸터 자동 채우기 (학교명/학기 등 OCR 또는 파일명에서)
4. 페이지당 4문제 압축
5. 로고/워터마크 자동 삽입

기대 효과:
- 학원장 Step 3~5 단계 자동화 → 시험지당 5~10분 추가 절약
- 1주 1.5~2.5시간 절약
- 1년 약 70~120시간 절약 가치
```

### 보존 우선순위 (학원장 결정 보류)

```
1단 → 2단 변환 시 어떤 정보를 가장 우선 보존할지:

후보:
- 수식 (한글 편집 가능 형태)
- 그림 (이미지/벡터)
- 보기 순서
- 문제 번호
- 페이지 break
- 다른 항목

상태: 학원장이 작업 진행하며 발견 (보류)
참고: Cycle 16 빌드 결과 검수하면서 가장 중요한 것 식별 가능
```

---

## 절대 정책 (위반 금지)

### ⭐ 핵심 원칙 — 작업 순서 절대

```
1️⃣ PDF 품질 (입력 정상성)
       ↓
2️⃣ OCR 인식 (Mathpix + LLM)
       ↓
3️⃣ 후처리/사전/Vision
       ↓
4️⃣ HWPX 빌드

→ 1단계 안 되면 나머지 다 무의미
```

### LLM 격하 정책 (Cycle 15h 결과)

```
이전: LLM 자동 교정 → 학원장 검수 → 사전 보조
새: 사전 우선 (approved만 자동 적용)
   → LLM은 패턴 발견기 (corrections.json approved=false 자동 등록)
   → 학원장 PDF 원본 확인 → approved=true 활성화
   → LLM temperature=0 (비결정성 차단, 필수)
```

### 환각 방지 — Vision Stage별 안전 정착

#### Stage A (활성)
- 그림/도형/그래프 정성적 설명 (★ 플레이스홀더 컨텍스트)
- 표 구조 인식 (행/열만)
- 레이아웃/양식 분석
- 환각 허용: 10% 미만 (비-수식 영역 한정)

#### Stage B (Cycle 15h부터 활성, LLM 격하 적용)
- 한글 텍스트 cross-check (Mathpix vs 클로바 vs LLM)
- LLM 결과 = 패턴 발견 (직접 적용 X, approved=false 등록)
- 3-way 불일치 → ★ 플레이스홀더

#### Stage C (영구 금지)
- 본문 수식 단독 OCR
- 구체적 숫자 (좌표, 길이, 우변, 답안) 단독 추출
- Mathpix 결과 덮어쓰기

#### 헤더 환각 방지 (Cycle 15h-1 추가)
- 헤더 단락 (## 시작 등) LLM 교정 거부
- "## 유의 사항 → ## - 확인 사항" 같은 의미 변경 차단
- 의미 변경 의심 → corrections.json 등록만, 자동 적용 X

#### 과거 환각 사례 — 영구 기억
- y=√a → y=√6 (수식 우변 환각)
- nroot126 → nroot46 (수식 숫자 환각)
- Vision 4문항 동일 marker_order ['①','④','②','⑤','③'] 환각 (Cycle 15f, SKIP 차단)
- v17 역교정: 다르니→다르며, 설정→설경, B,C→B.C (LLM 비결정성)

### 회귀 안전 정책 (재정의)

#### 이전 가정 오류 (인정)
- "광주고 v10 = 학원장 검수 통과 100% 정합 baseline" → 실제 v10도 시각 깨진 상태
- 핵심: sha 일치 ≠ 시각 정합

#### 새 baseline 정책 (이중 추적)
- **sha + 시각 검수 통과 학교 집합** 이중 추적
- 광주고 sha 유지 (자동 검증)
- 시각 baseline은 별도 학교 후보 (광주여고 검토 중)

### 학원장 검수 기반 (강화)

- CC: Ask before edits (모든 변경 사전 승인)
- git push는 학원장 명시 OK 후
- 사전 등록 모든 항목 학원장 y/n 검토
- 시각 검수만 신뢰
- **학원장 PDF 원본 확인 = 진짜 정답** (LLM/CC 추측보다 절대 우선)
- 야간 단독 작업 시 bypass-permissions 임시 모드 가능

### API 키 관리 (강화)

#### 절대 금지
- API 키 채팅 입력 (Claude에게 보내기)
- 코드에 직접 작성 (GitHub push)
- 스크린샷 노출
- 키 공유

#### 안전한 관리
- `.env` 파일에만 저장 (D:\f1\AKP\.env)
- `.gitignore`에 `.env` 포함 (필수)
- CC가 .env 읽어서 사용
- "API 키 발급 완료" 정도만 채팅 보고

#### 현재 등록된 API 키
- `CLOVA_OCR_SECRET` — 네이버 클로바 (재발급 완료, 환경 이슈로 보류)
- `CLOVA_OCR_INVOKE_URL` — APK_first 도메인 (KT DNS 환경 이슈)
- `ANTHROPIC_API_KEY` — Claude Sonnet 4.6 (LLM 후처리)
- `CLOVA_DISABLED=1` — 환경 우회 모드 (현재 활성)

---

## 두 가지 HWPX 양식

### 수학비서 양식
- 상용, 광주 외 지역 자료 기본
- 분류 작업 포함됨

### 타이퍼 양식 (우리 최종 출력 목표)
- 광주 선생님들과 협업 제작 (광주 한정)
- 2단 구성, "Gwang Ju Typer" 로고, 학교/학기 헤더, 가운데 녹색 T 워터마크, 페이지당 4문제, 저작권 푸터
- 18쌍 학습 데이터(samples/11b/*.hwpx) = 타이퍼 양식 (gold)
- **생성 방식**: 학원장이 이전 시험지를 변형해서 만듦 (v3.1 추가)

---

## 학습 데이터 자산

### 광주 18쌍 (samples/11b/)
- 형식: `[2025_1_1_b_공수1_학교명].pdf` + `.hwpx`
- 학교 17개 + 광주여고 (회전 보정 후) = 18쌍
- HWPX = 타이퍼 양식 (gold)
- **Cycle 16 baseline 후보**: 광주여고 (정상 PDF로 판단되는 후보)

### 광주 2024 (samples/2024/)
- 106쌍 (광주 2024 다과목 — 수상, 수하, 수1, 수2, 확통, 미적분)
- PDF + 수학비서 HWPX 페어 (학교 40개)
- 워드초벌 타이퍼 64건 (PDF 없음, Phase 4 자산)

### Production 결과 (samples/11b_production/)
- 17개 학교 v1 + 14개 v2 (Cycle 15b 교정 적용)
- 광주고 v17 (2차, sha `e1c77bae1921`, 헤더 보호 적용)
- 광주고 v17 (1차, sha `0704564984914`)
- 광주고 v16 (sha `8bc62331adf5`)
- 광주고 v15 (sha `0c95a49f296b`, \right 패치)
- 광주고 v10 (sha `2217502b5e83`, 시각 품질 한계 인정)

### Ground Truth 데이터 (절대 자산)

#### 등록 완료
- `광덕고_choices.json` — 광덕고 1~5번 PDF 원본 보기 (학원장 2026-05-17)

#### 등록 대기 (Cycle 16 Step 0 정정 후)
- 광주고 page 1 헤더: 쪼기 → **표기**
- 광주고 헤더: 유의 사항 → **확인 사항** (헤더 보호 예외)
- 광주고 #6: 다르며 → **다르니**
- 광주고 #88 script: B . C → **B, C**
- 광주고 #148: 설경 → **설정**

### 페어링 정책 (학원장 결정 — C 옵션)
같은 PDF에 두 양식 HWPX가 모두 있으면 **둘 다 학습 페어로 사용**.

### 교정 사전 (src/learn/corrections.json) — 재검토 대상

```
총 entries: 1112+ (Cycle 15h-1 추가 후)
- approved: 29건+ (학원장 검토 완료, Cycle 16에서도 보존)
- blacklisted: 8건
- pending: 1070건 → Cycle 16 Step 4에서 정상 PDF 기반 재검토
- CC 등록 (정정 필요): 5건 (Step 0에서 정정)
```

---

## 핵심 모듈

### Cycle 15h~15h-1 신규 (인프라, Cycle 16에서 보존)
- `src/ocr/clova_ocr.py` — 클로바 OCR wrapper + CLOVA_DISABLED=1 우회
- `src/ocr/llm_postprocess.py` — Sonnet 4.6 한글 교정 + 수식 플레이스홀더 + 헤더 보호 + 롤백 검증
- `src/ocr/multi_engine.py` — Mathpix + 클로바 + LLM 3-way cross-check
- `src/ocr/cost_guard.py` — 일일 $5 비용 cap
- `src/text_only/jamo_normalize.py` — 자모 분리 감지 + LLM 힌트
- `src/common/image_extractor.py` — pymupdf 그림 캡쳐
- `src/common/hwpx_image_inserter.py` — HWPX BinData 그림 삽입

### Cycle 16 신규 예정
- `src/preprocessing/pdf_quality_analyzer.py` — PDF 품질 자동 판정
- `src/preprocessing/image_enhancer.py` — AI 이미지 향상 (Cycle 15h-3)
- `scripts/preprocessing/analyze_pdf_quality.py` — CLI

### 변환 파이프라인
- `src/text_only/ocr_fallback.py` — 손상 영역 ★ + 사전 적용 + Vision 보조 + multi_engine 통합
- `src/common/latex_to_hwp.py` — LaTeX → HWP 수식 (\right 처리, Cycle 15g 패치)
- `src/text_only/handwriting_filter.py` — 학생 마킹 제거
- `src/text_only/layout_filter.py` — 6종 휴리스틱 필터 (재검토 대상)
- `src/text_only/vision_stage_a.py` — Vision Stage A
- `scripts/text/pdf_to_text.py` — 메인 파이프라인

### 페이지 단위 도구 (Cycle 15g)
- `src/text_only/page_extractor.py`
- `scripts/text/build_by_page.py`

### 학습 시스템
- `src/learn/apply_corrections.py`
- `src/learn/corrections.json`
- `src/learn/correction_capture.py`
- `src/learn/pair_align.py`
- `scripts/learn/bootstrap_corrections.py`
- `scripts/learn/review_corrections.py`

### 전처리
- `src/preprocessing/pdf_rotation.py`

---

## 사전 적용 안전 정책

| 패턴 | 적용 방식 |
|---|---|
| 공백 포함 또는 3자+ | 단순 replace |
| 단일 글자 (을, 를) | 컨텍스트 앵커 매칭 |
| 수식 (type=equation) | 미적용 (HWPX hp:script 레벨 통합 필요) |
| 헤더 영역 (## 시작) | 보호 (LLM 교정 거부, 학원장 명시 승인만) |

---

## 페이지 단위 워크플로우 (Cycle 15g+ 유지)

### 학원장 결정 (2026-05-17)
> "한페이지씩 수정하는 것이 좋아보임 페이지씩 해결하자"

### 작업 사이클

```
[학원장] 1페이지 검수 → 발견 문제 보고
       ↓
[Claude] 진단 + CC 작업 지시안
       ↓
[CC] 페이지 단위 빌드 도구로 진단 + 패치
       ↓
[학원장] 패치 OK
       ↓
[CC] 코드 변경 + 페이지 단위 재빌드 + 회귀 검증
       ↓
[학원장] 1페이지 재검수
       ↓
OK → 다음 페이지로 / 부분 OK → 부분 패치 / 실패 → 롤백
```

### 장점
- 학원장 검수 부담 ↓
- 회귀 위험 ↓ (작은 단위 격리)
- 디버깅 정밀도 ↑

### 단점 인지
- 전체 처리 시간 ↑
- 페이지 경계 이슈 별도 처리

---

## Cycle 16 — 새 시작 (현재 진행)

### 목표
정상 PDF baseline 확립 + PDF 품질 자동 판정 + 사전/시스템 재정리.

### Step 0 — corrections.json 정정 (CC 즉시, 30분)

```json
{
  "정정": {
    "쪼기 → 쓰기 (CC 잘못)": "쪼기 → 표기로 수정",
    "학익 사항 → 유의 사항 (CC 혼동)": "유의 사항 → 확인 사항으로 재등록 (헤더 보호 예외)"
  },
  "신규 등록 (학원장 PDF 확인, approved=true)": [
    "쪼기 → 표기",
    "유의 사항 → 확인 사항 (헤더 보호 예외)",
    "다르며 → 다르니 (#6)",
    "B . C → B, C (#88 script)"
  ]
}
```

### Step 1 — 광주여고 baseline 빌드 ⭐ (CC 야간 작업)

**목적**: 정상 PDF에서 현재 시스템 작동 검증

```
입력: samples/11b/[2025_1_1_b_공수1_광주여고].pdf
페어: samples/11b/[2025_1_1_b_공수1_광주여고].hwpx (타이퍼 양식 gold, 비교용)

작업:
1. 현재 시스템 그대로 빌드:
   - Mathpix OCR
   - 사전 approved만
   - LLM 후처리 (temperature=0)
   - layout_filter, latex_to_hwp, 그림 캡쳐, Vision Stage A

2. 출력: samples/11b_production/[2025_1_1_b_공수1_광주여고]_v1.hwpx
   ⭐ 1단 결과 형식 명세 준수 (v3.1)

3. HWPX 페어 자동 비교:
   - log/cycle_16/광주여고_baseline_diff.md

4. 회귀 안전:
   - 광주고 v17 (2차) sha `e1c77bae1921` 유지

5. 학원장 시각 검수

비용 cap: $5, 시간: CC 1~2h
```

### Step 2 — PDF 품질 자동 판정 시스템

```
src/preprocessing/pdf_quality_analyzer.py (신규)

판정 지표:
- 평균 DPI (300+ = 양호)
- 기울어짐 (1도 이하 = 양호)
- 페이지 균일성
- 노이즈 레벨
- Mathpix OCR confidence 평균

자동 분류:
🟢 정상 → 바로 OCR
🟡 보통 → AI 향상 (Cycle 15h-3)
🔴 사진 → 학원장 재스캔 권유
```

### Step 3 — 광주고 별도 트랙

Step 1 결과 따라 광주고는 AI 향상 또는 재스캔.

### Step 4 — 사전 pending 1070건 재검토

광주여고 baseline 후 정상 PDF에 유효한 것만 보존.

### Step 5 — 14개교 \right 패치 일괄 적용

---

## 미래 사이클 로드맵

### Phase 1.5 (Cycle 16 진행 중)
- **Cycle 16 Step 0~5** ⭐
- Cycle 15h-3 (AI 이미지 향상) — 진행 중

### Phase 1.5 잔여 (Cycle 16 후)
- 클로바 OCR 환경 해결
- 광주고 v17 page 1 잔여 미해결 (2번 누락, 보기 8줄, 부등호 양식)
- LLM 안정성 추가

### Phase 2 — 타이퍼 양식 자동 변환
- 1단 → 2단 자동 매핑
- 광주 선생님 "이전 시험지 변형" 방식 학습
- 헤더/푸터 자동
- 그림 벡터 변환 (옵션 4 Hybrid)

### Phase 3 — 해설 자동 생성

### Phase 4 — 수학비서 → 타이퍼 자동 변환

### Phase 5 — MCP 비서 연계

---

## 그림 처리 단계적 계획

### Phase 1.5 (Cycle 16) — 로컬 PDF 캡쳐
- pymupdf로 PDF 그림 자동 감지
- PNG → HWPX BinData 삽입
- 비용: $0 (로컬)
- 정확도: 90% (도형/그래프) / 70~80% (복잡)
- 부족분: 학원장 한글에서 수동 보정

### Phase 2 — 타이퍼 양식 그림 자동 생성
- 표준 도형 라이브러리 + AI 파라미터 추출 (방법 4 Hybrid)
- 광주 선생님 그림 양식 통합
- 시험지당 약 300~500원 추가

---

## 비용 모델 (1주 30개 기준)

| 워크플로우 | 비용/개 | 1주 비용 | 1년 비용 |
|---|---|---|---|
| 비서 (해설 없이) | 1만원 | 30만원 | 1440만원 |
| 비서 (해설 포함) | 7만원 | 210만원 | 1억800만원 |
| 수학비서 → 타이퍼 수동 | 5천원 인건비 | 15만원 | 720만원 |
| PDF → 타이퍼 (Cycle 16, 광주 자가) | **약 195원** | **약 3,000원** | **약 16만원** |

### 우리 시스템 가치
- **Phase 1.5 완성 (Cycle 16)**: 광주 자가 1만5천원/개 절약 = 1년 1064만원 순익
- **Phase 2 완성**: 학원장 Step 3~5 자동화, 시험지당 5~10분 추가 절약
- **Phase 4 완성**: 전 지역 5천원/개 인건비 절약 = 720만원/년
- **Phase 3 완성**: 해설 비용 6만원/개 → 약 0원

### Cycle 16 안전 cap
- 일일 cap: $5
- 월 cap: $30
- 도달 시 자동 차단

---

## 외부 한계 (받아들임)
- 학생 마킹 PDF는 OCR로 복구 불가 → 재스캔 필요
- 한글 첫 로드 시 수식 객체 부분 렌더링 (더블클릭+저장으로 정규화)
- 한글 Ctrl+F 【】특수괄호 분리 (수정 불가)
- 광주고 v10 시각 품질 한계 인정
- **사진 PDF는 AI 향상 한계** — 학원장 재스캔 권유
- **클로바 OCR 학원장 환경 이슈** — KT DNS 사설 IP 반환

---

## 작업 스타일

### Claude (전략/방향)
- 한국어 응답
- 옵션 제시 + 추천 + 사유 명확
- 표/리스트 적극 활용
- 위험 가능성 사전 경고
- 야간 작업 시 핸드오프 보고

### CC (Claude Code, 코드)
- Ask before edits
- 야간 단독 시 bypass-permissions 가능
- 사이클 단위 작업
- 작업 후 보고서
- 학교명 매핑 정확성 (로그 디렉토리 경로 검증)
- **환각 진단 시 학원장 PDF 원본 비교 우선** ⭐
- **1단 결과 형식 명세 준수** ⭐ (v3.1)

### 학원장 (의사결정)
- 시각 검수 우선
- 작은 검토 즉석 y/n
- 큰 결정 옵션 비교
- "할 수 있을 만큼씩" — 작업 단위 유연
- **PDF 원본 확인 자산화** — 학원장 시간이 곧 사전 자산

---

## 한 줄 정리

> 현재 Phase 1.5 — Cycle 16 새 시작 (광주여고 baseline부터).
> 작업 순서 절대: PDF → OCR → 후처리 → 빌드.
> LLM 격하 (패턴 발견기, temperature=0).
> 학원장 PDF 원본 확인 = 진짜 정답.
> 1단 결과 정확도 = 학원장 복붙 효율 (시스템 가치 지점).
> Phase 2에서 1단 → 2단 자동 변환 (이전 시험지 변형 방식 학습).
> 시험지당 약 195원, 1년 16만원 비용으로 1064만원 절약.
> 회귀 안전 + 환각 방지 + 학원장 검수 절대 우선순위.

---

## API 키 유출되지 않게 항상 조심
- `.env` 파일에만 저장
- `.gitignore`에 `.env` 포함 확인
- 채팅에 키 직접 입력 금지
- 스크린샷에 키 노출 금지
- 의심 시 즉시 API 키 재발급
