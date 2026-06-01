"""서강고 2026_1_1_a 공통수학1 → HWPX 빌드 (1회성).

이 raw.md는 손으로 입력해 선택지가 ①②③④⑤(원문자), 점수가 [N점] 형식이라
OCR용 v5 파이프라인(parse_problems/normalize_choices)이 전제하는 (1)~(5) 형식과
다르다. 그대로 parse→rebuild 경로를 태우면 _CHOICE_RE가 ①을 인식하지 못해
선택지가 통째로 사라진다. 그래서:

  · 본문·선택지·수식 → build_from_markdown으로 직접 변환 (원본 형식 그대로 보존)
  · 조건 박스만        → `< 조 건 >` 블록에 【★ 조건시작/끝:N번】 마커를 삽입한 뒤
                         replace_condition_tables로 1×1 표(박스)로 치환

→ 다른 학교 v5 빌드와 동일하게 조건이 박스 표로 출력되며, OCR 정제용 LLM 단계는
  손입력 raw에 불필요하므로 생략한다 (비용 $0).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.text_only.text_builder import build_from_markdown
from src.common.hwpx_table_inserter import (
    replace_condition_tables,
    replace_boilerplate_tables,
)

ROOT     = Path(__file__).resolve().parent.parent
RAW      = ROOT / "samples" / "2026" / "(광주)[2026_1_1_a_공수1_서강고]_raw.md"
OUT      = ROOT / "samples" / "2026" / "(광주)[2026_1_1_a_공수1_서강고].hwpx"
TEMPLATE = ROOT / "samples" / "11b" / "[2025_1_1_b_공수1_광주고].hwpx"

_COND_HEADER = re.compile(r"^<\s*조\s*건\s*>\s*$")
_SCORE       = re.compile(r"[\[［][\d.．]+점[\]］]")


def insert_condition_markers(md: str) -> tuple[str, int]:
    """`< 조 건 >` 블록을 【★ 조건시작:N번】 ~ 【★ 조건끝:N번】 마커로 감싼다.

    조건 영역 = 헤더 다음 줄 ~ 다음 [N점] 점수줄(문제 꼬리) 직전.
    (가)(나)(다) 마커 유무와 무관하게 헤더 기준으로 잡으므로 서술형6처럼
    단일·무마커 조건도 포함된다. 점수가 붙은 꼬리 문장은 본문으로 남긴다.
    """
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = 0
    while i < len(lines):
        if _COND_HEADER.match(lines[i].strip()):
            n += 1
            conds: list[str] = []
            j = i + 1
            while j < len(lines) and not _SCORE.search(lines[j]):
                if lines[j].strip():
                    conds.append(lines[j].strip())
                j += 1
            out.append(f"【★ 조건시작:{n}번】")
            out.extend(conds)
            out.append(f"【★ 조건끝:{n}번】")
            i = j  # 점수줄(꼬리 문장)부터 이어서 본문 처리
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out), n


md = RAW.read_text(encoding="utf-8")

# 학원장용 주석(blockquote `>`)은 학생 문서에서 제외
md = "\n".join(l for l in md.split("\n") if not l.lstrip().startswith(">"))

md, n_cond = insert_condition_markers(md)

r = build_from_markdown(md, OUT, TEMPLATE)
n_box  = replace_condition_tables(OUT)
n_bogi = replace_boilerplate_tables(OUT)  # 서강고엔 보기 ㄱㄴㄷ 없음 → 0 예상

print(f"완료: {OUT.name}")
print(f"  문단 {r['paragraphs']}개 / 수식 {r['equations']}개 / {OUT.stat().st_size//1024}KB")
print(f"  조건 마커 {n_cond}개 삽입 → 박스 표 {n_box}개 치환 (보기 {n_bogi}개)")
