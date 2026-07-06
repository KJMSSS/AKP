"""
HWPX 조립 엔진 (검증 완료).

전략: 빈 문서를 처음부터 만들지 않는다. 한글 시험지 양식이 들어있는 '기준 템플릿'
(base.hwpx = 루트 샘플)을 열어서 스타일/마스터페이지/단(2단)/페이지(A3) 정의는 그대로
두고, section0.xml 의 '본문 단락'만 갈아끼운다. 이렇게 하면 한글에서 열었을 때
원본과 동일한 시각적 양식이 보장된다.

핵심 사실(루트 샘플에서 확인):
  - section0.xml 의 첫 <hp:p> 가 <hp:secPr>(A3 세로 + colCount=2 NEWSPAPER)를 들고 있다.
    -> 이 단락은 반드시 보존한다.
  - 수식은 LaTeX 가 아니라 한글 수식 스크립트로 저장된다:
    <hp:equation ...><hp:script>cases {x &gt; 1 # x le 2}</hp:script></hp:equation>
  - 문제 메타(학교/번호/코드/난이도/배점)는 표(<hp:tbl>)로 들어간다.
  - 그림은 <hp:pic> + BinData/ 이미지로 인라인 삽입된다.

이 파일은 순수 표준 라이브러리(zipfile + ElementTree)만 사용한다. Hancom Office,
python-hwpx 등 외부 의존성 없음. (roundtrip + 본문 주입 + 유효성 검증 통과)
"""
from __future__ import annotations

import copy
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field

NS = {
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)


def _q(prefix: str, tag: str) -> str:
    return "{%s}%s" % (NS[prefix], tag)


# 문제 한 덩어리(메타+지문+보기)를 단/페이지 경계에서 쪼개지지 않게 보호하는 본문 단락
# paraPr. base.hwpx 본문(id=5)을 복제해 keepWithNext/keepLines=1 로 만든 것을 save() 가
# header.xml 에 id=17 로 주입한다. blocks.BlockFactory 가 문제 단락에 이 ID 를 단다.
# (원본 시험지는 pageBreak 0 = 한글 자동 페이지흐름. 우리는 명시 break 를 쓰지 않고
#  이 keep 속성만으로 '문제 통째 유지 + 단/페이지 자동 채움'을 한글에 맡긴다.)
KEEP_PARA_PR = "17"
# 그림(pic) 단락용 keep paraPr. base 의 그림 스타일(id=12, 가운데정렬)을 복제해
# keepWithNext/keepLines=1 로 만든 것을 save() 가 id=18 로 주입한다. add_figure 가 사용.
# (그림을 본문 keep 체인에 묶어 '지문↔그림'이 단/페이지 경계에서 분리되지 않게 한다.
#  id=5 가 아니라 id=12 기반이라 그림의 가운데정렬이 그대로 보존된다.)
KEEP_PIC_PARA_PR = "18"


@dataclass
class HwpxBuilder:
    """기준 템플릿을 열어 본문만 재구성하는 빌더."""

    template_path: str
    _zin: zipfile.ZipFile = field(init=False)
    _root: ET.Element = field(init=False)          # <hs:sec>
    _pic_tpl: ET.Element = field(init=False, default=None)   # pic 단락 골격
    _images: list = field(init=False, default_factory=list)  # (id, fname, media, bytes)

    def __post_init__(self) -> None:
        self._zin = zipfile.ZipFile(self.template_path)
        sec_bytes = self._zin.read("Contents/section0.xml")
        self._root = ET.fromstring(sec_bytes)
        ps = self._root.findall("hp:p", NS)
        if not ps or ps[0].find(".//hp:secPr", NS) is None:
            raise ValueError("템플릿 첫 단락에 secPr 가 없습니다. 다른 템플릿 필요.")
        # secPr 단락만 남기고 본문 비우기
        for p in ps[1:]:
            self._root.remove(p)
        # pic 단락 골격 수확(있으면)
        for p in ps[1:]:
            if next(p.iter(_q("hp", "pic")), None) is not None:
                self._pic_tpl = copy.deepcopy(p)
                break
        # 헤더 앵커(ps[0])에 붙은 '잔여 문제 메타박스(1×6)'를 제거(secPr·시험헤더는 보존)
        self._strip_anchor_meta(ps[0])
        # BinData 의 base 이미지 개수 — 새 그림 ID 가 base image* 와 충돌하면
        # 한글이 새 그림 대신 base 이미지(로고)를 표시하므로, 그 다음 번호부터 부여한다.
        self._base_img_count = sum(1 for n in self._zin.namelist()
                                   if n.startswith("BinData/image"))

    @staticmethod
    def _strip_anchor_meta(anchor: ET.Element) -> None:
        """앵커 단락에서 잔여 객체(colCnt6 메타표 + 잔여 그림 pic)를 정밀 제거.

        시험 헤더(4칸표)·secPr 은 보존. 잔여 객체를 감싼 가장 가까운 <hp:container>
        를(있으면) 제거하고, 없으면 객체를 직접 부모에서 제거한다.
        """
        parent = {c: p for p in anchor.iter() for c in p}

        def collect(tag_pred):
            out = []
            for el in anchor.iter():
                if tag_pred(el):
                    node, target = el, el
                    while node is not None and node.tag != _q("hp", "run"):
                        if node.tag == _q("hp", "container"):
                            target = node
                            break
                        node = parent.get(node)
                    par = parent.get(target)
                    if par is not None:
                        out.append((par, target))
            return out

        victims = collect(lambda e: e.tag == _q("hp", "tbl") and e.get("colCnt") == "6")
        victims += collect(lambda e: e.tag == _q("hp", "pic"))
        for par, node in victims:
            try:
                par.remove(node)
            except ValueError:
                pass

    def set_exam_header(self, *, school: str = "", subject: str = "",
                        exam_range: str = "", term: str = "", region: str = "") -> None:
        """상단 시험 헤더(2×4 표)의 과목/범위/지역/학교/학기 텍스트를 교체.

        빈 값은 템플릿 기본 텍스트를 유지한다(부분 교체 허용).
        """
        anchor = self._root.findall("hp:p", NS)[0]
        for tbl in anchor.iter(_q("hp", "tbl")):
            if tbl.get("colCnt") == "4":
                ts = [t for t in tbl.iter(_q("hp", "t")) if (t.text or "").strip()]
                # 템플릿 순서: 과목, 범위, 지역, 학교, 학기
                mapping = [subject, exam_range, region, school, term]
                for el, val in zip(ts, mapping):
                    if val:
                        el.text = val
                break

    # --- 본문 구성 API -----------------------------------------------------
    def add_text(self, text: str, *, para_pr: str = "0", char_pr: str = "0",
                 column_break: bool = False, page_break: bool = False) -> ET.Element:
        p = ET.SubElement(self._root, _q("hp", "p"))
        p.set("paraPrIDRef", para_pr)
        p.set("styleIDRef", "0")
        p.set("pageBreak", "1" if page_break else "0")
        p.set("columnBreak", "1" if column_break else "0")
        p.set("merged", "0")
        if text:
            r = ET.SubElement(p, _q("hp", "run"))
            r.set("charPrIDRef", char_pr)
            t = ET.SubElement(r, _q("hp", "t"))
            t.text = text
        return p

    def add_equation_object(self, equation_el: ET.Element, *, char_pr: str = "0") -> ET.Element:
        """인라인 수식 객체(<hp:equation>)를 새 단락의 run 에 넣는다."""
        p = ET.SubElement(self._root, _q("hp", "p"))
        p.set("paraPrIDRef", "0")
        p.set("styleIDRef", "0")
        for a in ("pageBreak", "columnBreak", "merged"):
            p.set(a, "0")
        r = ET.SubElement(p, _q("hp", "run"))
        r.set("charPrIDRef", char_pr)
        r.append(copy.deepcopy(equation_el))
        return p

    def add_block(self, element: ET.Element) -> ET.Element:
        """미리 만든 단락(<hp:p>) 요소를 본문 끝에 그대로 붙인다."""
        self._root.append(element)
        return element

    def add_blocks(self, elements) -> None:
        for el in elements:
            self._root.append(el)

    # --- 그림(이미지) 임베딩 ----------------------------------------------
    def add_figure(self, image_path: str, *, width_mm: float = 70.0,
                   max_height_mm: float = 108.0, char_pr: str = "0") -> ET.Element:
        """크롭/정리된 그림을 인라인 이미지로 본문에 추가한다.

        content.hpf 에 항목을 등록하고 BinData/ 에 바이트를 넣는다(save 시).
        크기는 width_mm 기준으로 종횡비 유지.
        """
        if self._pic_tpl is None:
            raise ValueError("템플릿에 pic 골격이 없어 그림을 넣을 수 없습니다.")
        # 최종 안전망: 어떤 경로(크롭/지우기/재작도/과거 job)로 왔든 평탄 회색 배경이면
        # 흰색으로 정규화하고 넣는다(2026-07-02 화정중 — 재작도본 회색 배경이 그대로 삽입됨).
        try:
            from .figure import whiten_flat_background, deskewed_copy_for_embed
        except ImportError:
            from pipeline.figure import whiten_flat_background, deskewed_copy_for_embed
        try:
            whiten_flat_background(image_path)
        except Exception:  # noqa: BLE001 — 보정 실패해도 삽입은 진행
            pass
        # 기울기 안전망: 스캔 기울기가 크롭에 남았거나 Gemini 가 기울기까지 충실 복제한
        # 경우(2026-07-03 서광중 — 재작도 보기상자 +3.2° 삐딱하게 삽입) 곧게 편 '사본'을
        # 넣는다. 원본 파일은 기하가 변하므로 제자리 수정하지 않는다(검수 UI 마스크 좌표).
        image_path = deskewed_copy_for_embed(image_path)
        from PIL import Image
        with Image.open(image_path) as im:
            pw, ph = im.size
            fmt = (im.format or "PNG").lower()
        with open(image_path, "rb") as f:
            data = f.read()

        media = "image/jpeg" if fmt in ("jpg", "jpeg") else f"image/{fmt}"
        ext = "jpg" if fmt in ("jpg", "jpeg") else fmt
        img_id = f"image{self._base_img_count + len(self._images) + 1}"
        fname = f"BinData/{img_id}.{ext}"
        self._images.append((img_id, fname, media, data))

        HW = 7200.0 / 25.4   # 1mm -> HWPUNIT
        w_hu = int(round(width_mm * HW))           # 화면 표시 크기(sz)
        h_hu = int(round(w_hu * ph / pw)) if pw else w_hu
        # 페이지/단 overflow 방지: 높이 상한 초과 시 비율 유지하며 축소
        max_h = int(round(max_height_mm * HW))
        if h_hu > max_h and h_hu > 0:
            w_hu = int(round(w_hu * max_h / h_hu))
            h_hu = max_h
        dimw = int(round(pw * 7200 / 96))          # 원본 이미지 크기(96dpi 기준)
        dimh = int(round(ph * 7200 / 96))

        p = copy.deepcopy(self._pic_tpl)
        # 그림 단락을 문제 keep 체인에 묶어 '지문↔그림'이 단/페이지 경계에서 분리되지
        # 않게 한다(가운데정렬 보존: id=12 기반 KEEP_PIC_PARA_PR). 명시 break 는 0 —
        # 단/페이지 배치는 '문제 시작(메타) 단락'의 columnBreak 가 결정한다.
        p.set("paraPrIDRef", KEEP_PIC_PARA_PR)
        for a in ("pageBreak", "columnBreak", "merged"):
            p.set(a, "0")
        pic = next(p.iter(_q("hp", "pic")))
        for tag in ("sz", "orgSz"):
            el = pic.find(_q("hp", tag))
            if el is not None:
                el.set("width", str(w_hu)); el.set("height", str(h_hu))
        cur = pic.find(_q("hp", "curSz"))
        if cur is not None:
            cur.set("width", "0"); cur.set("height", "0")   # 0 = sz 사용(샘플과 동일)
        clip = pic.find(_q("hp", "imgClip"))
        if clip is not None:
            clip.set("left", "0"); clip.set("top", "0")
            clip.set("right", str(dimw)); clip.set("bottom", str(dimh))
        dim = pic.find(_q("hp", "imgDim"))
        if dim is not None:
            dim.set("dimwidth", str(dimw)); dim.set("dimheight", str(dimh))
        pos = pic.find(_q("hp", "pos"))
        if pos is not None:
            pos.set("treatAsChar", "1")   # 글자처럼 취급 → 본문에 인라인 배치
        img = next(pic.iter(_q("hc", "img")), None)
        if img is not None:
            img.set("binaryItemIDRef", img_id)
        # imgRect(그림이 그려질 사각형)를 새 그림 크기로 — base 템플릿 그림 비율 잔존 시
        # 새 그림이 엉뚱한 영역/비율로 매핑돼 깨지므로 반드시 갱신.
        rect = pic.find(_q("hp", "imgRect"))
        if rect is not None:
            for tag, (x, y) in zip(("pt0", "pt1", "pt2", "pt3"),
                                   ((0, 0), (dimw, 0), (dimw, dimh), (0, dimh))):
                pt = rect.find(_q("hc", tag))
                if pt is not None:
                    pt.set("x", str(x)); pt.set("y", str(y))
        # base 그림의 잔존 메타(원본 파일명/크기 주석) 제거 + pic 고유 id 부여(중복 방지)
        sc = pic.find(_q("hp", "shapeComment"))
        if sc is not None:
            pic.remove(sc)
        uid = str(2000000000 + len(self._images))
        pic.set("instid", uid); pic.set("id", uid)
        self._root.append(p)
        return p

    def _patch_header_keep(self, hdr_bytes: bytes) -> bytes:
        """header.xml 에 keepWithNext/keepLines=1 paraPr 두 개를 주입.

          - KEEP_PARA_PR(=17): base 본문(id=5, LEFT)을 복제 → 메타+지문+보기용.
          - KEEP_PIC_PARA_PR(=18): base 그림(id=12, CENTER)을 복제 → 그림용(정렬 보존).

        breakSetting 의 keep 두 속성만 1 로 바꾼다. 이 keep 은 '문제 한 덩어리 유지'의
        보조 수단이며, 실제 단/페이지 배치는 build_exam 이 메타단락에 다는 columnBreak 가
        결정한다(원본 base.hwpx 와 동일: pageBreak 0, columnBreak 만).
        """
        s = hdr_bytes.decode("utf-8")
        added = 0
        for new_id, base_id in ((KEEP_PARA_PR, "5"), (KEEP_PIC_PARA_PR, "12")):
            if '<hh:paraPr id="%s"' % new_id in s:
                continue  # 이미 주입됨(멱등). charPr/borderFill 의 같은 id 와 혼동 금지
                          # (header 엔 charPr id="17"·borderFill id="17" 이 별개로 존재한다)
            m = re.search(r'<hh:paraPr id="%s".*?</hh:paraPr>' % base_id, s, re.S)
            if not m:
                continue  # 원본 paraPr 못 찾으면 그 항목은 생략(안전)
            keep = m.group(0)
            keep = keep.replace('id="%s"' % base_id, 'id="%s"' % new_id, 1)
            keep = keep.replace('keepWithNext="0"', 'keepWithNext="1"', 1)
            keep = keep.replace('keepLines="0"', 'keepLines="1"', 1)
            s = s.replace("</hh:paraProperties>", keep + "</hh:paraProperties>", 1)
            added += 1
        if added:
            mc = re.search(r'<hh:paraProperties itemCnt="(\d+)">', s)
            if mc:
                n = int(mc.group(1))
                s = s.replace(mc.group(0),
                              '<hh:paraProperties itemCnt="%d">' % (n + added), 1)
        return s.encode("utf-8")

    def _patch_hpf(self, hpf_bytes: bytes) -> bytes:
        """content.hpf 의 <opf:manifest> 에 새 이미지 항목을 추가."""
        if not self._images:
            return hpf_bytes
        s = hpf_bytes.decode("utf-8")
        items = "".join(
            f'<opf:item id="{i}" href="{href}" media-type="{m}" isEmbeded="1"/>'
            for (i, href, m, _b) in self._images)
        # 마지막 </opf:manifest> 직전에 삽입(없으면 manifest 자동 생성은 생략)
        if "</opf:manifest>" in s:
            s = s.replace("</opf:manifest>", items + "</opf:manifest>", 1)
        return s.encode("utf-8")

    # --- 저장 -------------------------------------------------------------
    def save(self, out_path: str) -> str:
        new_sec = ET.tostring(self._root, encoding="utf-8", xml_declaration=True)
        out = Path(out_path)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            # mimetype 은 반드시 첫 멤버 + 무압축
            zout.writestr(
                zipfile.ZipInfo("mimetype"),
                self._zin.read("mimetype"),
                compress_type=zipfile.ZIP_STORED,
            )
            for n in self._zin.namelist():
                if n == "mimetype":
                    continue
                if n == "Contents/section0.xml":
                    data = new_sec
                elif n == "Contents/content.hpf":
                    data = self._patch_hpf(self._zin.read(n))
                elif n == "Contents/header.xml":
                    data = self._patch_header_keep(self._zin.read(n))
                else:
                    data = self._zin.read(n)
                zout.writestr(n, data)
            # 새 이미지 바이트 기록
            for (_id, fname, _media, blob) in self._images:
                zout.writestr(fname, blob)
        return str(out)


def harvest_equation(template_path: str) -> ET.Element:
    """템플릿에서 실제 수식 객체 1개를 추출(스크립트 교체용 골격으로 재사용)."""
    sec = zipfile.ZipFile(template_path).read("Contents/section0.xml")
    m = re.search(rb"<hp:equation\b.*?</hp:equation>", sec, re.S)
    if not m:
        raise ValueError("템플릿에 수식 객체가 없습니다.")
    wrap = b'<w xmlns:hp="%s" xmlns:hc="%s">%s</w>' % (
        NS["hp"].encode(), NS["hc"].encode(), m.group(0))
    return ET.fromstring(wrap).find("hp:equation", NS)


# --- 수식 객체 크기 근사 ------------------------------------------------------
# 한글은 hwpx 에 저장된 수식 <hp:sz>/baseLine 을 열 때 축소 재계산하지 않는다
# (실측 2026-07-02: 템플릿 고정 3573×2415 재사용 시 'x' 한 글자 수식 뒤 ~10mm 공백,
#  수식 든 모든 줄의 줄간격 팽창. 내용이 박스보다 크면 늘려 그리므로 과소치는 안전).
# 아래 상수는 전부 base.hwpx 의 한글 저작 수식 270개에서 역산한 실측값(baseUnit=1100).
# 예: 숫자 '3'=525, 'x'=600, '='=1287, '<'=1362, 'le'=1512, '+'=1133, '-'=979,
#     괄호 375, 백쿼트 137, 위첨자 배율 ≈0.8/h1313/bl87, 분수 h2580/bl65, cases h2415/bl66.
_EQ_CHAR_W = {
    "=": 1287, "<": 1362, ">": 1362, "+": 1133, "-": 979, "*": 1133, "/": 550,
    "(": 375, ")": 375, "[": 375, "]": 375, "|": 300, ",": 375, ".": 300,
    "`": 137, "'": 250, "!": 300, ":": 375, ";": 375, "%": 900, "?": 550,
    "i": 375, "j": 375, "l": 375, "t": 450, "r": 450, "s": 450, "b": 450,
    "c": 450, "m": 900, "w": 900,
}
_EQ_KW_ZERO = {"over", "of", "left", "right", "LEFT", "RIGHT", "matrix", "pile",
               "rpile", "lpile", "rm", "it", "bold", "vec", "bar", "hat", "tilde",
               "dot", "ddot", "acute", "grave", "overline", "underline"}
_EQ_KW_W = {
    "le": 1512, "ge": 1512, "in": 1362, "notin": 1362, "subset": 1362,
    "supset": 1362, "approx": 1362, "equiv": 1362, "sim": 1133, "prop": 1133,
    "times": 1133, "div": 1133, "cdot": 800, "cup": 1050, "cap": 1050,
    "star": 800, "circ": 800, "sum": 1650, "prod": 1650, "int": 1100,
    "lim": 1650, "inf": 1100, "cdots": 1650, "dots": 1650, "vdots": 600,
    "ddots": 1050, "angle": 1050, "perp": 1050, "parallel": 1050,
    "mapsto": 1650, "lbrace": 525, "rbrace": 525, "cases": 525,
    "sqrt": 1000, "root": 1000,   # radical 기호+가로선 오버헤드(내용은 별도 합산)
}
_EQ_GREEK = {"alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
             "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
             "xi", "pi", "rho", "sigma", "tau", "phi", "varphi", "chi", "psi",
             "omega", "GAMMA", "DELTA", "THETA", "LAMBDA", "XI", "PI", "SIGMA",
             "PHI", "PSI", "OMEGA"}
_EQ_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp",
             "max", "min", "gcd", "deg"}


def _eq_word_width(word: str) -> float:
    if word in _EQ_KW_ZERO:
        return 0.0
    if word in _EQ_KW_W:
        return float(_EQ_KW_W[word])
    if word in _EQ_GREEK:
        return 650.0
    if word in _EQ_FUNCS:
        return 470.0 * len(word)
    # 일반 변수/식별자 run — 글자별 합산
    w = 0.0
    for ch in word:
        if ch.isupper():
            w += 800
        else:
            w += _EQ_CHAR_W.get(ch, 550)
    return w


def _eq_estimate_geometry(script: str) -> tuple[int, int, int]:
    """한글 수식 스크립트의 (width, height, baseLine) 근사(HWPUNIT/%, baseUnit=1100).

    한글이 내용보다 작은 박스는 늘려 그리므로 약간의 과소 추정은 안전하고,
    과대 추정만 공백으로 남는다 → 실측 상수 + 소폭 하향 편향.
    """
    w, i, n = 0.0, 0, len(script)
    scale, stack, depth = 1.0, [], 0
    while i < n:
        c = script[i]
        if c == "{":
            depth += 1; i += 1; continue
        if c == "}":
            depth -= 1
            if stack and stack[-1][0] == depth:
                scale = stack.pop()[1]
            i += 1; continue
        if c in "^_":
            i += 1
            while i < n and script[i].isspace():
                i += 1
            if i < n and script[i] == "{":   # 위/아래첨자 그룹은 축소 배율
                stack.append((depth, scale))
                scale *= 0.8
                depth += 1; i += 1
            continue
        two = script[i:i + 2]
        if two in ("+-", "-+", "<>", "->", "=>", "<-", "<<", ">>"):
            w += 1300 * scale; i += 2; continue
        if c.isalpha():
            j = i
            while j < n and script[j].isalpha():
                j += 1
            w += _eq_word_width(script[i:j]) * scale
            i = j; continue
        if c.isdigit():
            w += 525 * scale
        elif c.isspace() or c in "#&":
            pass
        elif ord(c) > 0x2000:                # 한글/유니코드 기호(□△∴∵ 등)
            w += 1100 * scale
        else:
            w += _EQ_CHAR_W.get(c, 550) * scale
        i += 1

    rows = 1
    if re.search(r"\b(cases|pile|rpile|lpile|matrix)\b", script):
        rows = 1 + script.count("#")
    if " over " in script:
        rows = max(rows, 2)
    if rows >= 2:
        w = w * 0.62 + 550                   # 세로 적층(분수/cases)은 최장 행 폭 근사
        h = 1125 + 1455 * (rows - 1)
        bl = 65 if rows == 2 else 55
    elif re.search(r"[\^_]", script) or re.search(r"\b(sqrt|root|bar|vec|hat|overline)\b", script):
        h, bl = 1313, 87
    else:
        h, bl = 1125, 85
    return max(int(w), 380), h, bl


def set_equation_script(equation_el: ET.Element, hwp_script: str) -> ET.Element:
    """수식 객체의 한글 수식 스크립트를 교체하고 크기를 내용에 맞게 근사한다.

    중요: hwp_script 는 **원시(raw) 한글 수식 스크립트**여야 한다. 연산자 `>`, `<`, `&`
    는 그대로 넘긴다 — ElementTree 가 저장 시 `&gt;`,`&lt;`,`&amp;` 로 정확히 1회
    이스케이프한다. 절대 미리 `&gt;` 로 넣지 말 것(이중 이스케이프됨).
    """
    el = copy.deepcopy(equation_el)
    script = el.find("hp:script", NS)
    if script is None:
        script = ET.SubElement(el, _q("hp", "script"))
        script.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    script.text = hwp_script
    w, h, bl = _eq_estimate_geometry(hwp_script)
    el.set("baseLine", str(bl))
    sz = el.find("hp:sz", NS)
    if sz is not None:
        sz.set("width", str(w))
        sz.set("height", str(h))
    return el


if __name__ == "__main__":
    import sys
    tpl = sys.argv[1] if len(sys.argv) > 1 else "examconv/backend/templates/base.hwpx"
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/examconv_demo.hwpx"
    b = HwpxBuilder(tpl)
    eq = harvest_equation(tpl)
    b.add_text("1. 다음 연립부등식의 해는?")
    b.add_equation_object(set_equation_script(eq, "cases {``x &gt; 1 #`` x le 2}`"))
    b.add_text("① x>1   ② x≤2   ③ 해없음   ④ 모든 실수   ⑤ x=1")
    print("saved:", b.save(out))
