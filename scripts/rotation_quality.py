"""회전 대상 골드쌍 품질 측정 (과금) — 회전 수정의 다과목 품질 검증.

회전 감사에서 🔴(고신뢰 회전 적용) 으로 분류된 골드쌍을 재OCR(회전 보정 적용)해
정답 HWPX 대비 유사도·내용겹침을 과목별로 측정한다. 회전 수정이 다른 과목에서도
정상 변환을 가능케 하는지 정량 검증.
"""
from __future__ import annotations
import sys, re
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, '.')
from scripts.gold_compare import (
    convert_pdf, content_overlap, doc_metrics, similarity, extract_text, _reg_key,
)
from src.ocr.claude_pdf_reader import read_pdf_as_markdown   # env 재현
from src.common.pdf_utils import _classify_page_rotation
import fitz

PAT = re.compile(r'\d{4}_\d_\d_[ab]_([^_\]\[]+)_([^_\]\[]+)')


def is_rotated(pdf: Path) -> list:
    """고신뢰 내용 회전이 있는 페이지 목록 (없으면 빈 리스트)."""
    doc = fitz.open(str(pdf))
    hits = []
    for i, page in enumerate(doc):
        k, a = _classify_page_rotation(page)
        if k == "osd" and a in (90, 180, 270):
            hits.append((i + 1, a))
    doc.close()
    return hits


def main():
    root = Path('samples')
    pairs = []
    for pdf in sorted(root.rglob('*.pdf')):
        if '_rotfix' in pdf.stem:
            continue
        if pdf.with_suffix('.hwpx').exists():
            pairs.append(pdf)

    print(f'전체 골드쌍 {len(pairs)} → 회전 대상 선별 중...', flush=True)
    targets = []
    for pdf in pairs:
        hits = is_rotated(pdf)
        if hits:
            targets.append((pdf, hits))
    print(f'회전 대상 {len(targets)}쌍 재OCR 시작\n', flush=True)

    rows = []
    for idx, (pdf, hits) in enumerate(targets):
        gold = pdf.with_suffix('.hwpx')
        key = _reg_key(gold)
        m = PAT.search(pdf.stem)
        subj = m.group(1) if m else '?'
        school = m.group(2) if m else key.split('_')[-1]
        print(f'[{idx+1}/{len(targets)}] {subj}/{school}  회전={hits}', flush=True)
        try:
            our = convert_pdf(pdf, key, reocr=True)
            if our is None:
                rows.append((subj, school, None, None, None, None, hits)); continue
            ov = content_overlap(our, gold)
            gm = doc_metrics(gold); om = doc_metrics(our)
            sim = similarity(om['_norm'], gm['_norm'])
            rows.append((subj, school, sim, ov, om['equations'], gm['equations'], hits))
            print(f'      유사도={sim:.1%}  겹침={ov:.2f}  수식={om["equations"]}/{gm["equations"]}', flush=True)
        except Exception as e:
            rows.append((subj, school, None, None, None, None, hits))
            print(f'      오류: {e}', flush=True)

    # ── 과목별 리포트 ──
    print('\n' + '=' * 70)
    print(f'{"과목":<8}{"학교":<12}{"유사도":>8}{"겹침":>7}{"수식(우/정)":>12}')
    print('-' * 70)
    bysubj = defaultdict(list)
    for subj, school, sim, ov, eqo, eqg, hits in rows:
        bysubj[subj].append((school, sim, ov, eqo, eqg))
    all_sims = []
    for subj in sorted(bysubj):
        sims = []
        for school, sim, ov, eqo, eqg in bysubj[subj]:
            s = f'{sim:.1%}' if sim is not None else '실패'
            o = f'{ov:.2f}' if ov is not None else '-'
            e = f'{eqo}/{eqg}' if eqo is not None else '-'
            print(f'{subj:<8}{school:<12}{s:>8}{o:>7}{e:>12}')
            if sim is not None:
                sims.append(sim); all_sims.append(sim)
        if sims:
            print(f'{"":8}{"└ 평균":<12}{sum(sims)/len(sims):>7.1%}')
        print()
    if all_sims:
        print('-' * 70)
        print(f'회전 대상 {len(all_sims)}쌍 전체 평균 유사도: {sum(all_sims)/len(all_sims):.1%}')
        lo = sorted((s, sc) for sub, sc, s, *_ in rows if s is not None)[:5]
        print('낮은 5쌍:', ', '.join(f'{sc}({s:.0%})' for s, sc in lo))


if __name__ == '__main__':
    main()
