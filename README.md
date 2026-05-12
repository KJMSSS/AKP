# AKP — 수학 시험지 PDF → 한글(.hwpx) 자동 변환

## 프로젝트 목표

수학 시험지 스캔 PDF를 한글 문서(.hwpx)로 **자동** 변환한다.

| 항목 | 목표 |
|------|------|
| 단가 절감 | 외주 1만원/장 → 자동화로 대체 |
| 정확도 | 95% 이상 |
| 검수 시간 | 변환 후 5~10분 이내 |

## 기술 스택

| 역할 | 도구 |
|------|------|
| 런타임 | Python 3.14 |
| 수식 OCR | [Mathpix API](https://mathpix.com/) |
| 문서 구조화 | Claude API / Gemini API |
| 한글 문서 조작 | HWPX 직접 파싱 (ZIP + XML) |

## 변환 파이프라인

```
PDF 입력
  └─► PDF → 이미지 (pdf2image)
        └─► Mathpix API → LaTeX 수식 + 텍스트 블록
              └─► Claude/Gemini → 단락 구조화 JSON
                    └─► HWPX XML 생성 (수식 객체 포함)
                          └─► .hwpx 패키징 (zipfile)
                                └─► output/
```

## HWPX 구조 요약

`.hwpx`는 ZIP 아카이브다. 내부 XML을 직접 편집해 수식·텍스트·표를 삽입한다.

```
document.hwpx
├── Contents/
│   ├── content.hml      ← 본문 XML (단락·수식 객체)
│   └── header.xml       ← 스타일·폰트 정의
└── META-INF/
    └── container.xml
```

## 진행 상황

- [x] 프로젝트 셋업 (폴더 구조, 가상환경, README, .env.example)
- [x] Mathpix API 연동 (`src/ocr/mathpix_client.py`)
- [x] HWPX 템플릿 분석 (`docs/hwpx_structure.md`) — 실제 파일 검증 후 보완
- [ ] 변환 파이프라인 구현 (`src/pipeline.py`)
- [ ] 수식 객체 자동 생성 (`src/hwpx/formula_builder.py`)

## 디렉터리 구조 (예정)

```
AKP/
├── src/
│   ├── ocr/            # Mathpix 연동
│   ├── llm/            # Claude/Gemini 구조화
│   └── hwpx/           # HWPX XML 생성
├── tests/
├── samples/            # 테스트용 샘플 PDF
├── output/             # 변환 결과물 (.gitignore)
├── docs/
├── .env                # API 키 (절대 커밋 금지)
├── .env.example        # 키 템플릿 (커밋 가능)
└── requirements.txt
```

## 빠른 시작

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# .env 설정 (아래 보안 섹션 필독)
copy .env.example .env   # Windows
# cp .env.example .env   # Mac/Linux
# 에디터로 .env 열어 실제 API 키 입력

python src/pipeline.py samples/test.pdf
```

---

## ⚠️ 보안 — API 키 관리 필독

### 기본 원칙

1. **`.env` 파일에 모든 API 키를 저장한다.** `.gitignore`에 의해 Git 추적에서 완전 제외된다.
2. **`.env`는 절대 GitHub에 커밋하지 않는다.** 실수로 푸시하면 키를 즉시 폐기하고 재발급해야 한다.
3. **코드 내 하드코딩 절대 금지.** 키가 코드에 직접 들어가면 커밋 히스토리에 영구 기록된다.

### 올바른 키 사용 방법 (Python)

```python
import os
from dotenv import load_dotenv

load_dotenv()  # .env 파일 자동 로드

MATHPIX_APP_ID  = os.getenv("MATHPIX_APP_ID")   # 항상 이 방식
MATHPIX_APP_KEY = os.getenv("MATHPIX_APP_KEY")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")

# ❌ 절대 금지
# MATHPIX_APP_KEY = "abc123realkey..."
```

### 첫 셋업 절차

```bash
copy .env.example .env   # 템플릿 복사
# .env 열어서 각 항목에 실제 키 입력 후 저장
```

### API 키 발급

| 서비스 | 발급 링크 |
|--------|-----------|
| Mathpix | https://accounts.mathpix.com |
| Anthropic (Claude) | https://console.anthropic.com |

### 키가 유출됐을 때

1. 해당 서비스 콘솔에서 **즉시 키 폐기(Revoke)**
2. 새 키 발급
3. `.env` 업데이트
4. `git log`로 커밋 히스토리 점검 (`git filter-repo`로 히스토리 정리 고려)
