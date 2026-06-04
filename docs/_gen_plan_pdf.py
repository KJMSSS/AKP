"""AKP 프로젝트 전체 플랜 PDF 생성 (PyMuPDF 1.27+)"""
import fitz
from pathlib import Path

OUT  = Path(__file__).parent / "AKP_프로젝트_플랜.pdf"
REG  = "C:/Windows/Fonts/malgun.ttf"
BOLD = "C:/Windows/Fonts/malgunbd.ttf"
W, H = 595, 842  # A4

GRAY   = (0.5,  0.5,  0.5)
BLACK  = (0.1,  0.1,  0.1)
BLUE   = (0.08, 0.35, 0.75)
GREEN  = (0.10, 0.55, 0.30)
ORANGE = (0.80, 0.40, 0.05)
RED    = (0.75, 0.15, 0.15)
LIGHT  = (0.93, 0.95, 1.0)
DIV    = (0.82, 0.84, 0.88)
WHITE  = (1.0,  1.0,  1.0)

doc = fitz.open()

# ── 헬퍼 ──────────────────────────────────────────────────────────

def new_page():
    p = doc.new_page(width=W, height=H)
    p.insert_font(fontname="R", fontfile=REG)
    p.insert_font(fontname="B", fontfile=BOLD)
    # 헤더 바
    p.draw_rect(fitz.Rect(0, 0, W, 44), color=None, fill=(0.10, 0.25, 0.55), width=0)
    # 푸터 바
    p.draw_rect(fitz.Rect(0, H-26, W, H), color=None, fill=(0.93, 0.94, 0.96), width=0)
    return p

def footer(p, num):
    p.insert_text((40, H-10), "AKP 프로젝트 플랜  |  학원 내부 자료",
                  fontname="R", fontsize=7.5, color=GRAY)
    p.insert_text((530, H-10), f"{num} / 3",
                  fontname="R", fontsize=7.5, color=GRAY)

def t(p, text, x, y, size=10, fn="R", color=BLACK):
    p.insert_text((x, y), text, fontname=fn, fontsize=size, color=color)
    return y + size * 1.6

def tb(p, text, rect, size=10, fn="R", color=BLACK, align=0):
    return p.insert_textbox(rect, text, fontname=fn, fontsize=size,
                            color=color, align=align)

def hline(p, y):
    p.draw_line((40, y), (555, y), color=DIV, width=0.7)

def sec(p, text, y):
    p.draw_rect(fitz.Rect(40, y-13, 44, y+3), color=None, fill=BLUE, width=0)
    p.insert_text((50, y), text, fontname="B", fontsize=12, color=BLUE)
    hline(p, y+8)
    return y + 24

def badge_inline(p, text, x, y, bg=BLUE, fg=WHITE, size=8):
    tw = fitz.Font(fontfile=BOLD).text_length(text, fontsize=size)
    pad = 5
    r = fitz.Rect(x, y-size+1, x+tw+pad*2, y+3)
    p.draw_rect(r, color=None, fill=bg, width=0)
    p.insert_text((x+pad, y), text, fontname="B", fontsize=size, color=fg)
    return r.x1 + 7

def card(p, step, title, status, lines, y):
    colors = {"완료": GREEN, "미착수": GRAY, "진행중": ORANGE}
    bg = colors.get(status, GRAY)
    labels = {"완료": "✓ 완료", "미착수": "○ 미착수", "진행중": "→ 진행중"}
    lbl = labels.get(status, status)
    fill = (0.96, 0.99, 0.97) if status == "완료" else (0.99, 0.99, 1.0)
    p.draw_rect(fitz.Rect(40, y, 555, y+75), color=DIV, fill=fill, width=0.7)
    p.draw_rect(fitz.Rect(40, y, 68, y+75), color=None, fill=bg, width=0)
    p.insert_text((47, y+25), "STEP", fontname="B", fontsize=7, color=WHITE)
    p.insert_text((48, y+40), step,  fontname="B", fontsize=15, color=WHITE)
    p.insert_text((76, y+22), title, fontname="B", fontsize=11, color=BLACK)
    xb = badge_inline(p, lbl, 76, y+40, bg=bg)
    tb(p, "  ".join(lines), fitz.Rect(76, y+44, 548, y+73), size=8.5, color=GRAY)
    return y + 84


# ══════════════════════════════════════════════════════════════════════
# PAGE 1
# ══════════════════════════════════════════════════════════════════════
p = new_page()
p.insert_text((40, 30), "AKP 프로젝트 전체 플랜",
              fontname="B", fontsize=16, color=WHITE)
p.insert_text((432, 30), "2026년 6월",
              fontname="R", fontsize=9, color=(0.8, 0.85, 1.0))
footer(p, 1)

y = 66
y = t(p, "한국 수학 시험지 PDF → HWPX(한글 문서) 자동 변환 파이프라인", 40, y, 10.5, "B", BLUE)
y = t(p, "학원 운영 도구 — 학원장이 타이퍼 양식(2단 HWPX)으로 직원에게 배포하는 것이 최종 목표",
      40, y, 9, color=GRAY)
y += 4

y = sec(p, "현재 완성된 기능", y)
done = [
    ("PDF OCR",           "Mathpix·Claude 두 엔진 지원. $...$ / $$...$$ 통일 포맷 출력"),
    ("LaTeX→HWP Script",  r"\frac·\sqrt·\int·\sum·\binom 등 3단계 중첩 지원"),
    ("HWPX 빌더 v5",      "마크다운 → section0.xml 새 생성. 조건표·보기표 자동 삽입"),
    ("그림 삽입",          "PyMuPDF 추출 + Vision 폴백. BinData PNG 삽입"),
    ("웹 검수 UI",         "FastAPI + SSE. 문제별 편집 → 재빌드 → 다운로드"),
    ("2단 타이퍼 양식",    "1단 HWPX → A3 2단 자동 변환 (typer_builder.py)"),
    ("웹 매트릭스 UI",     "학교×과목 표. 잡 이동·삭제·단계별 업로드·Google Drive 연동"),
    ("Google OAuth",       "이메일 허용 목록 + 관리자 구분. 일일 비용 캡($)"),
    ("Railway 배포",       "GitHub push → 자동 재배포. Volume 마운트 영속 저장"),
]
for name, desc in done:
    xb = badge_inline(p, name, 46, y + 1)
    tb(p, desc, fitz.Rect(xb, y-9, 550, y+8), size=8.8, color=GRAY)
    y += 17

y += 4; hline(p, y); y += 14

y = sec(p, "레지스트리 키 형식", y)
y = t(p, "연도 _ 학년 _ 학기 _ a(중간)/b(기말) _ 과목 _ 학교", 40, y, 11, "B", BLACK)
y = t(p, "예시:  2026_1_1_a_공수1_경신여고", 40, y, 9.5, color=GRAY)
y += 4; hline(p, y); y += 14

y = sec(p, "핵심 변환 흐름", y)
flow = ["PDF", "OCR", "apply_fallback()", "parse_problems()",
        "build_from_markdown()", "replace_tables()", "insert_figure()", "HWPX"]
desc_map = {
    "PDF": "원본 입력",
    "OCR": "Mathpix·Claude → raw.md",
    "apply_fallback()": "손상 감지·플레이스홀더",
    "parse_problems()": "문제 단위 세그먼트",
    "build_from_markdown()": "LaTeX→HWP Script",
    "replace_tables()": "조건표·보기표",
    "insert_figure()": "BinData PNG",
    "HWPX": "최종 산출물",
}
fx = 40
for i, step in enumerate(flow):
    bw = 66
    p.draw_rect(fitz.Rect(fx, y-12, fx+bw, y+8), color=BLUE,
                fill=LIGHT, width=0.8)
    p.insert_text((fx+3, y), step, fontname="B", fontsize=7, color=BLUE)
    p.insert_text((fx+3, y+9), desc_map[step], fontname="R", fontsize=6.5, color=GRAY)
    if i < len(flow)-1:
        ax = fx + bw + 1
        p.draw_line((ax, y-2), (ax+8, y-2), color=BLUE, width=0.9)
        p.draw_line((ax+6, y-5), (ax+8, y-2), color=BLUE, width=0.9)
        p.draw_line((ax+6, y+1), (ax+8, y-2), color=BLUE, width=0.9)
    fx += bw + 10
    if i == 3:
        fx = 40; y += 28


# ══════════════════════════════════════════════════════════════════════
# PAGE 2 — 로드맵
# ══════════════════════════════════════════════════════════════════════
p = new_page()
p.insert_text((40, 30), "AKP 프로젝트 전체 플랜  —  로드맵",
              fontname="B", fontsize=14, color=WHITE)
p.insert_text((432, 30), "2026년 6월", fontname="R", fontsize=9, color=(0.8,0.85,1.0))
footer(p, 2)

y = 62
y = sec(p, "전체 파이프라인 로드맵", y)
steps = [
    ("1", "그림 파이프라인", "미착수",
     ["PDF에서 이미지 영역 자동 감지 (Vision + PyMuPDF)",
      "HWPX BinData 삽입 + 위치 지정  |  기준: 골드 HWPX 18쌍"]),
    ("2", "웹 검수 인터페이스 강화", "미착수",
     ["직원 화면: 크롭 PNG + 추출 텍스트 나란히 표시",
      "오류 표시·수정 → HWPX 재생성  |  학원장 승인 화면 (모바일 대응)"]),
    ("3", "2단 타이퍼 양식 자동 변환", "완료",
     ["src/text_only/typer_builder.py  —  build_typer_hwpx()",
      "A3 2단 HWPX, BinData 이미지 보존  |  웹: 자동 생성/재생성 버튼"]),
    ("4", "통합 배포", "미착수",
     ["전체 파이프라인 연결 (PDF → 웹검수 → 타이퍼 HWPX)",
      "직원·학원장 계정 구분  |  결과물 Google Drive 자동 업로드"]),
]
for args in steps:
    y = card(p, *args, y)

y += 6
y = sec(p, "OCR 품질 개선 로드맵", y)
ocr = [
    ("A", "프롬프트 수식 예시 추가", "미착수",
     [r"claude_pdf_reader.py 프롬프트에 [수식 예시] 섹션 추가",
      r"x^{n+1}, \sqrt{a+b}, \frac{분자}{분모}, \lim_{x\to a} 등 반복 오류 패턴 명시"]),
    ("B", "과목별 출제 범위 주입", "미착수",
     ["과목 ID → 범위/수식 힌트 딕셔너리  |  read_pdf_as_markdown()에 subject 파라미터",
      "공수1: 다항식·방정식  |  기하: 이차곡선·벡터  |  미적2: 초월함수 미적분"]),
    ("C", "2차 LaTeX 교정 패스", "미착수",
     ["src/ocr/latex_corrector.py  —  correct_latex(md, subject)",
      "1차 OCR 결과를 별도 Claude 호출로 수식만 집중 교정  |  추가 비용 10~20%"]),
]
for args in ocr:
    y = card(p, *args, y)


# ══════════════════════════════════════════════════════════════════════
# PAGE 3 — 이슈 & 제약 & 구조
# ══════════════════════════════════════════════════════════════════════
p = new_page()
p.insert_text((40, 30), "AKP 프로젝트 전체 플랜  —  이슈 & 구조",
              fontname="B", fontsize=14, color=WHITE)
p.insert_text((432, 30), "2026년 6월", fontname="R", fontsize=9, color=(0.8,0.85,1.0))
footer(p, 3)

y = 62
y = sec(p, "알려진 버그 / 미결 이슈", y)
issues = [
    (RED,    "서강고 선택지 마커 초과", "75건 검출, 기대 70건 — 파서 선택지 인식 로직 검토 필요"),
    (ORANGE, "웹 검수 크롭 PNG",       "HTML 검수 리포트: 문제별 크롭 PNG + 텍스트 나란히 표시 — 미착수"),
    (GRAY,   "서강고 HWPX D안",        "첫 실전 결과물 — 다음 세션 시작 시 확인 필요"),
]
for color, title, desc in issues:
    p.draw_rect(fitz.Rect(46, y-10, 50, y+4), color=None, fill=color, width=0)
    p.insert_text((56, y), title, fontname="B", fontsize=9.5, color=color)
    tb(p, desc, fitz.Rect(56, y+4, 552, y+20), size=8.5, color=GRAY)
    y += 30

y += 4; hline(p, y); y += 16
y = sec(p, "절대 정책 (위반 금지)", y)
policies = [
    "학교 단위 순차 처리 — 여러 학교 병렬 빌드 금지",
    "LLM은 패턴 발견기 — temperature=0, 자동 적용 금지, approved 항목만 자동 적용",
    "학원장 PDF 원본 = 진짜 정답 — LLM/OCR 결과보다 원본 PDF 우선",
    "크롭 OCR 표준 순서: 전체 OCR → 공란 발견 → 크롭 OCR → raw.md 완성 → 빌드 1회",
]
for pol in policies:
    p.draw_line((47, y-1), (52, y-1), color=BLUE, width=1.2)
    tb(p, pol, fitz.Rect(58, y-10, 552, y+6), size=9, color=BLACK)
    y += 17

y += 6; hline(p, y); y += 16
y = sec(p, "핵심 파일 구조", y)
files = [
    ("scripts/web/app.py",                   "FastAPI 서버 — OCR·변환·검수·매트릭스 API"),
    ("scripts/web/static/matrix.html",        "매트릭스 UI — 잡 이동·삭제·단계 업로드"),
    ("scripts/web/static/review.html",        "검수 UI — 문제별 편집 + 재빌드"),
    ("src/text_only/text_builder.py",         "마크다운 → HWPX 신규 생성 (v5)"),
    ("src/text_only/typer_builder.py",        "1단 HWPX → 2단 타이퍼 양식"),
    ("src/common/latex_to_hwp.py",            "LaTeX → HWP Script 변환 규칙"),
    ("src/ocr/claude_pdf_reader.py",          "Claude OCR 엔진"),
    ("src/common/ocr/mathpix_client.py",      "Mathpix OCR 엔진"),
    ("src/text_only/problem_segmenter.py",    "문제 파서 — parse_problems()"),
    ("src/common/hwpx_table_inserter.py",     "조건표·보기표 → 1×1 hp:tbl"),
    ("src/common/image_extractor.py",         "PDF 이미지 추출 (PyMuPDF + Vision)"),
    ("scripts/web/data/matrix_registry.json", "잡 레지스트리 영속 저장"),
]
for i, (path, desc) in enumerate(files):
    cx = 40 if i % 2 == 0 else 300
    ry = y + (i // 2) * 20
    p.insert_text((cx, ry), path, fontname="R", fontsize=7.5, color=BLUE)
    tb(p, desc, fitz.Rect(cx, ry+2, cx+248, ry+15), size=7.5, color=GRAY)

y += (len(files) // 2 + 1) * 20 + 6
hline(p, y); y += 14

y = sec(p, "배포 환경", y)
deploy = [
    ("Railway",      "GitHub main push → 자동 재배포. Volume /data 마운트 영속 저장"),
    ("인증",          "Google OAuth2. 허용 이메일 목록 + 관리자 구분"),
    ("비용 제한",      "일일 $5 캡 (전체). 사용자별 캡 설정 가능 (관리자 화면)"),
    ("로컬 실행",      "py -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8080"),
]
for label, desc in deploy:
    xb = badge_inline(p, label, 46, y+1)
    tb(p, desc, fitz.Rect(xb, y-9, 550, y+8), size=8.8, color=GRAY)
    y += 17

doc.save(str(OUT))
print(f"저장 완료: {OUT}  ({OUT.stat().st_size//1024} KB)")
