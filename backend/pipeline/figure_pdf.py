"""
디지털 PDF 구조 기반 그림·데이터표 추출 (VLM 눈대중 bbox 대체).

기존 detect_figures()/detect_tables() 는 '렌더된 페이지 이미지'를 Claude 비전에
보내 bbox 를 눈대중으로 받았다 → 수십~수백 px 오차로 그림이 아닌 텍스트/표 테두리
영역이 크롭되는 근본 결함(2026-07-18: image1=표조각, image2=1번 보기텍스트 …).

디지털 PDF(스캔 아님)는 그림·표가 정확한 좌표를 가진 임베디드 래스터/벡터 객체다.
PyMuPDF(fitz) 로 구조를 직접 파싱해 '결정적(deterministic)'으로 그림 영역을 잡고
고해상도 clip 렌더한다. 핵심 판별:

  그래픽 rect(래스터 + 벡터 격자) 중,
    · 내부 텍스트가 problems.json 본문(지문/발문/보기/선택지)과 매칭되면 → 제외
      (보기 상자·자료 상자·발문·선택지는 build_exam 이 native 로 그린다)
    · 매칭 안 되면(그림 라벨·데이터표 셀뿐) → 크롭
  + 페이지 상단 헤더(성명/수험번호/시험명)와 페이지 전체 테두리는 배제.

스캔/이미지 입력은 이 경로를 타지 않고(is_digital_pdf_page=False) 기존 VLM 폴백.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _merge(rects, gap):
    """근접(gap pt 이내) 사각형들을 합집합으로 병합. rects: [(x0,y0,x1,y1), ...]."""
    boxes = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        for r in boxes:
            hit = None
            for o in out:
                if (r[0] <= o[2] + gap and o[0] <= r[2] + gap
                        and r[1] <= o[3] + gap and o[1] <= r[3] + gap):
                    hit = o
                    break
            if hit is not None:
                hit[0] = min(hit[0], r[0]); hit[1] = min(hit[1], r[1])
                hit[2] = max(hit[2], r[2]); hit[3] = max(hit[3], r[3])
                changed = True
            else:
                out.append(r[:])
        boxes = out
    return boxes


def is_digital_pdf_page(pg) -> bool:
    """스캔(=텍스트 없음/전면 단일 래스터) 이 아니라 디지털 PDF 페이지인지."""
    if len(pg.get_text().strip()) < 50:
        return False
    area = pg.rect.width * pg.rect.height
    for x in pg.get_images(full=True):
        for r in pg.get_image_rects(x[0]):
            if r.width * r.height > 0.8 * area:
                return False
    return True


def _raster_rects(pg):
    """임베디드 래스터 이미지 사각형(사진·모식도). 거의 항상 '그림'."""
    out = []
    for x in pg.get_images(full=True):
        for r in pg.get_image_rects(x[0]):
            out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def _vector_rects(pg):
    """벡터 드로잉(박스/선/곡선) 사각형. 페이지 전면 테두리는 제외."""
    PW, PH = pg.rect.width, pg.rect.height
    out = []
    for d in pg.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        w, h = r.width, r.height
        if w < 5 and h < 5:
            continue
        if w > PW * 0.9 and h > PH * 0.9:   # 페이지 전면 테두리
            continue
        out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def _column_x(pg):
    """2단 컬럼 분리선 x(세로로 매우 긴 얇은 드로잉). 없으면 페이지 중앙."""
    PW, PH = pg.rect.width, pg.rect.height
    divs = [d["rect"] for d in pg.get_drawings()
            if d.get("rect") is not None and d["rect"].height > PH * 0.3
            and d["rect"].width < 3]
    if divs:
        # 중앙에 가장 가까운 세로선
        divs.sort(key=lambda r: abs((r.x0 + r.x1) / 2 - PW / 2))
        return (divs[0].x0 + divs[0].x1) / 2
    return PW / 2


def _problem_anchors(pg, col_x):
    """문항 헤딩 '^N.' 텍스트 블록의 (번호, 컬럼, y). 컬럼 좌상단 근처만 인정."""
    heads = []
    for b in pg.get_text("blocks"):
        if b[6] != 0:
            continue
        x0, y0, txt = b[0], b[1], b[4]
        m = re.match(r"\s*(\d+)\.", txt)
        if not m:
            continue
        # 문항 번호는 컬럼 좌측 여백에서 시작한다(왼단 좌측 or 오른단 좌측)
        if x0 < col_x * 0.30 or (x0 > col_x and x0 < col_x + (pg.rect.width - col_x) * 0.30):
            col = 'L' if x0 < col_x else 'R'
            heads.append((int(m.group(1)), col, y0))
    heads.sort(key=lambda t: (t[1], t[2]))
    return heads


def _assign_problem(rect, heads, col_x):
    """그래픽 클러스터를 y중심이 드는 문항(같은 컬럼, 바로 위 헤딩)에 배정."""
    xc = (rect[0] + rect[2]) / 2
    yc = (rect[1] + rect[3]) / 2
    col = 'L' if xc < col_x else 'R'
    cand = [h for h in heads if h[1] == col]
    picked = None
    for h in cand:
        if h[2] <= yc + 6:      # 헤딩 아래(약간의 여유)
            picked = h
    if picked:
        return picked[0]
    return cand[0][0] if cand else None


def _overlap_ratio(a, b) -> float:
    """a 사각형이 b 사각형에 덮이는 비율(교집합/ a넓이)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    aa = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / aa


_CHOICE_LEAD = re.compile(r"^\s*[①②③④⑤]")


def _is_body_text(raw: str, body_norm: str) -> bool:
    """텍스트 블록이 native 로 그려질 '본문'인지: 지문·발문(problems.json 매칭) +
    '<보 기>' 상자 + 선택지(①…) 줄. 그림 라벨(A/㉠/(가))·데이터표 셀(비커/pH/7 8)은
    problems.json 에 없어 False → 그림/표 시드로 쓴다."""
    t = _norm(raw)
    if not t:
        return False
    if "보기" in t or "보 기" in raw:
        return True
    if _CHOICE_LEAD.match(raw):
        return True
    hits = total = 0
    for i in range(0, max(1, len(t) - 6), 6):
        total += 1
        if t[i:i + 6] in body_norm:
            hits += 1
    return total > 0 and hits / total >= 0.5


def detect_graphics_pdf(pdf_path: str, page_index: int, work_dir: str,
                        body_norm: str, *, dpi: int = 300, prefix: str = "g",
                        header_pad: float = 8.0):
    """디지털 PDF 페이지에서 그림·데이터표를 결정적으로 추출.

    body_norm: 해당 페이지 문항들의 stem+choices 를 공백제거·연결한 문자열(제외 판별용).
    반환: [{"problem": "3", "bbox": [x0,y0,x1,y1](0~1 정규화), "image": path,
            "source": "pdf_crop"}] / 스캔 페이지면 None(호출측 VLM 폴백).
    """
    if fitz is None:
        return None
    doc = fitz.open(pdf_path)
    pg = doc[page_index]
    PW, PH = pg.rect.width, pg.rect.height
    if not is_digital_pdf_page(pg):
        return None

    col_x = _column_x(pg)
    heads = _problem_anchors(pg, col_x)
    header_bot = (min(h[2] for h in heads) - header_pad) if heads else PH * 0.12
    rasters = _raster_rects(pg)

    def is_deco(g):
        return (g[3] <= header_bot            # 상단 헤더대
                or g[0] >= PW * 0.90          # 우측 세로 사이드바
                or g[1] >= PH * 0.90)         # 하단 페이지번호

    # 1) 시드 = 래스터(사진·모식도·트리) + non-body 텍스트 블록(그림 라벨·데이터표 셀).
    #    body 텍스트 블록(지문/보기/발문/선택지)은 시드가 아니므로 그 y구간이 빈틈이
    #    되어 병합이 그것을 넘지 못한다 → 지문 사이의 데이터표가 자동 분리된다.
    #    (벡터 상자는 개별 획으로 쪼개져 텍스트를 못 담아 신뢰 불가 → 시드에서 배제)
    seeds = [r for r in rasters if not is_deco(r)]
    for b in pg.get_text("blocks"):
        if b[6] != 0 or not b[4].strip():
            continue
        rect = (b[0], b[1], b[2], b[3])
        if is_deco(rect):
            continue
        if _is_body_text(b[4], body_norm):
            continue
        seeds.append(rect)

    # 2) 근접 병합(같은 그림의 서브요소·라벨 통합)
    clusters = _merge(seeds, gap=14.0)

    # 3) 문항 배정
    by_prob = {}
    for c in clusters:
        if (c[2] - c[0]) < 16 or (c[3] - c[1]) < 12:   # 잔여 조각 배제
            continue
        p = _assign_problem(c, heads, col_x)
        by_prob.setdefault(p, []).append(c)
    figs = []
    for p, cs in by_prob.items():
        for m in _merge(cs, gap=20.0):
            figs.append([p, m])

    # 4) 데이터표 완성: 각 그림 영역을 인접 벡터 격자로 확장한다. 표 셀 텍스트가
    #    보기와 우연히 겹쳐 시드에서 빠져도(예: '독립적으로 물질대사를 한다') 격자
    #    선을 흡수해 표 경계를 되살린다. 단 body 텍스트를 절반+ 덮는 벡터(보기/자료
    #    상자 외곽·지문 박스)는 흡수하지 않아 그림이 본문을 삼키지 않는다.
    vectors = [v for v in _vector_rects(pg) if not is_deco(v)]
    body_blocks = [(b[0], b[1], b[2], b[3]) for b in pg.get_text("blocks")
                   if b[6] == 0 and b[4].strip() and _is_body_text(b[4], body_norm)]

    def _hits_body(v):
        for bb in body_blocks:
            ix = max(0.0, min(v[2], bb[2]) - max(v[0], bb[0]))
            iy = max(0.0, min(v[3], bb[3]) - max(v[1], bb[1]))
            inter = ix * iy
            barea = max(1.0, (bb[2] - bb[0]) * (bb[3] - bb[1]))
            if inter / barea > 0.5:
                return True
        return False

    safe_vectors = [v for v in vectors if not _hits_body(v)]
    for fig in figs:
        c = list(fig[1])
        changed = True
        while changed:
            changed = False
            for v in safe_vectors:
                if (v[0] <= c[2] + 6 and c[0] <= v[2] + 6
                        and v[1] <= c[3] + 6 and c[1] <= v[3] + 6):
                    nx = [min(c[0], v[0]), min(c[1], v[1]),
                          max(c[2], v[2]), max(c[3], v[3])]
                    if nx != c:
                        c = nx
                        changed = True
        fig[1] = c

    # 4) 고해상 clip 렌더 + 정규화 bbox
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    out = []
    figs.sort(key=lambda pc: (pc[1][1], pc[1][0]))   # y, x 순
    for i, (p, c) in enumerate(figs):
        # 소폭 패딩(잘림 방지) + 페이지 clamp
        pad = 2.0
        x0 = max(0, c[0] - pad); y0 = max(0, c[1] - pad)
        x1 = min(PW, c[2] + pad); y1 = min(PH, c[3] + pad)
        img = str(Path(work_dir) / f"{prefix}_p{page_index}_{i}.png")
        pix = pg.get_pixmap(clip=fitz.Rect(x0, y0, x1, y1),
                            matrix=fitz.Matrix(dpi / 72, dpi / 72))
        pix.save(img)
        out.append({
            "problem": str(p) if p is not None else "",
            "bbox": [x0 / PW, y0 / PH, x1 / PW, y1 / PH],
            "image": img,
            "source": "pdf_crop",
        })
    return out
