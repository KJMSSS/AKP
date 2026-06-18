"""전 골드 PDF 회전 감사 (무과금) — 회전 수정+신뢰도 가드의 코퍼스 검증.

각 PDF를 페이지별로 OSD 분류해:
  - 적용될 회전(conf>=문턱, 진짜 회전으로 판단)
  - 기각된 저신뢰 감지(conf 1~문턱, 거짓양성 위험 → 가드가 막음)
를 모두 보고한다. PDF만 읽으므로 HWPX 정답 품질과 무관하게 유효.
"""
from __future__ import annotations
import re, sys, io, os
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, '.')
from src.ocr.claude_pdf_reader import read_pdf_as_markdown          # env 재현(load_dotenv)
from src.common.pdf_utils import _osd_tessdata_dir, _OSD_CONF_MIN, _BLANK_INK_RATIO
import fitz, pytesseract
from pytesseract import Output
from PIL import Image
import numpy as np

osd_dir = _osd_tessdata_dir()
if osd_dir:
    os.environ['TESSDATA_PREFIX'] = osd_dir

PAT = re.compile(r'(\d{4})_(\d)_(\d)_([ab])_([^_\]\[]+)_([^_\]\[]+)')


def osd_page(page, dpi=150):
    """반환 (base, rotate, conf, is_blank)."""
    base = page.rotation
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
    img = Image.open(io.BytesIO(pix.tobytes('png')))
    gray = np.asarray(img.convert('L'))
    if float((gray < 128).mean()) < _BLANK_INK_RATIO:
        return base, 0, 0.0, True
    try:
        o = pytesseract.image_to_osd(img, output_type=Output.DICT)
        r = int(o.get('rotate', 0)) % 360
        c = float(o.get('orientation_conf', 0) or 0)
    except Exception:
        r, c = -1, 0.0
    return base, r, c, False


def main():
    root = Path('samples')
    pairs = []
    for pdf in sorted(root.rglob('*.pdf')):
        if '_rotfix' in pdf.stem:
            continue
        if pdf.with_suffix('.hwpx').exists():
            m = PAT.search(pdf.stem)
            subj = m.group(5) if m else '?'
            pairs.append((pdf, subj))

    print(f'회전 감사 대상: {len(pairs)}쌍\n', flush=True)
    cat = defaultdict(list)   # 분류 → [라벨]
    fp_risk = []              # 저신뢰(거짓양성 위험) 페이지
    applied = []              # 실제 회전 적용될 PDF

    for idx, (pdf, subj) in enumerate(pairs):
        doc = fitz.open(str(pdf))
        hi, lo, meta = [], [], []
        for i, page in enumerate(doc):
            base, r, c, blank = osd_page(page)
            if base:
                meta.append((i+1, base))
            if blank or r in (0, -1):
                if c and 0 < c < _OSD_CONF_MIN and r not in (0, -1):
                    lo.append((i+1, r, round(c, 1)))
                continue
            # r in (90,180,270)
            if c >= _OSD_CONF_MIN:
                hi.append((i+1, r, round(c, 1)))
            elif c > 0:
                lo.append((i+1, r, round(c, 1)))
        doc.close()

        m = PAT.search(pdf.stem)
        label = f'{subj}/{m.group(6) if m else pdf.stem[:10]}'
        if hi:
            cat['rotate'].append(label); applied.append((label, meta, hi))
        elif meta and not hi:
            cat['meta_only'].append(label)
        else:
            cat['upright'].append(label)
        if lo:
            fp_risk.append((label, lo))
        if (idx+1) % 20 == 0:
            print(f'  ...{idx+1}/{len(pairs)} 진행', flush=True)

    print(f'\n=== 분류 요약 (문턱 conf>={_OSD_CONF_MIN}) ===')
    print(f'  🔴 실제회전적용 : {len(cat["rotate"])}쌍')
    print(f'  🟡 메타회전만   : {len(cat["meta_only"])}쌍 (이미 정상 처리)')
    print(f'  ⚪ 정방향      : {len(cat["upright"])}쌍')

    print(f'\n=== 🔴 회전 적용될 PDF ({len(applied)}) ===')
    for label, meta, hi in applied:
        print(f'  {label:<22} meta={meta}  고신뢰회전={hi}')

    print(f'\n=== ⚠️ 저신뢰 감지(가드가 기각 = 거짓양성 차단) {len(fp_risk)}건 ===')
    for label, lo in fp_risk[:40]:
        print(f'  {label:<22} {lo}')
    if len(fp_risk) > 40:
        print(f'  ... 외 {len(fp_risk)-40}건')


if __name__ == '__main__':
    main()
