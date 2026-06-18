"""회전 대상 골드쌍 품질 측정 (과금) — 회전 수정의 다과목 품질 검증.

회전 감사에서 🔴(고신뢰 회전 적용) 으로 분류된 골드쌍을 재OCR(회전 보정 적용)해
정답 HWPX 대비 포함도(문제만 공정척도)·유사도·내용겹침을 과목별로 측정한다.

소수 샘플 감시:
  --limit N      처리할 최대 회전 대상 수 (감시 비용 통제)
  --cache-only   캐시 있는 쌍만 (무과금 보장)
  --no-reocr     캐시 우선(있으면 OCR 안 함)
파일별 소요시간을 찍어, OCR 타임아웃 가드(스트림 240s 데드라인)가
무한 행 없이 빠르게 끝나는지 감시한다.
"""
from __future__ import annotations
import sys, re, time, argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, '.')
from scripts.gold_compare import (
    convert_pdf, content_overlap, doc_metrics, similarity, extract_text, _reg_key,
    containment, problem_count,
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
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='최대 회전 대상 수 (0=전체)')
    ap.add_argument('--cache-only', action='store_true', help='캐시만(무과금 보장)')
    ap.add_argument('--no-reocr', action='store_true', help='캐시 있으면 OCR 안 함')
    args = ap.parse_args()
    reocr = not (args.no_reocr or args.cache_only)

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
    if args.limit > 0:
        targets = targets[:args.limit]
    mode = '캐시만(무과금)' if args.cache_only else ('캐시우선' if args.no_reocr else '재OCR(과금)')
    print(f'회전 대상 {len(targets)}쌍 — {mode} 시작\n', flush=True)

    rows = []
    for idx, (pdf, hits) in enumerate(targets):
        gold = pdf.with_suffix('.hwpx')
        key = _reg_key(gold)
        m = PAT.search(pdf.stem)
        subj = m.group(1) if m else '?'
        school = m.group(2) if m else key.split('_')[-1]
        print(f'[{idx+1}/{len(targets)}] {subj}/{school}  회전={hits}', flush=True)
        t0 = time.time()
        try:
            our = convert_pdf(pdf, key, reocr=reocr, cache_only=args.cache_only)
            dt = time.time() - t0
            if our is None:
                print(f'      건너뜀/실패 ({dt:.0f}s)', flush=True)
                rows.append((subj, school, None, None, None, None, None, None, None, hits)); continue
            ov = content_overlap(our, gold)
            gm = doc_metrics(gold); om = doc_metrics(our)
            sim = similarity(om['_norm'], gm['_norm'])
            cover = containment(om['_norm'], gm['_norm'])
            pc_o, pc_g = problem_count(extract_text(our)), problem_count(extract_text(gold))
            rows.append((subj, school, sim, cover, ov, om['equations'], gm['equations'],
                         pc_o, pc_g, hits))
            print(f'      포함도={cover:.0%} 유사도={sim:.0%} 겹침={ov:.2f} '
                  f'수식={om["equations"]}/{gm["equations"]} 문항={pc_o}/{pc_g} ({dt:.0f}s)',
                  flush=True)
        except Exception as e:
            dt = time.time() - t0
            rows.append((subj, school, None, None, None, None, None, None, None, hits))
            print(f'      오류({dt:.0f}s): {e}', flush=True)

    # ── 과목별 리포트 ──
    print('\n' + '=' * 74)
    print(f'{"과목":<8}{"학교":<12}{"포함도":>7}{"유사도":>7}{"겹침":>6}{"수식(우/정)":>11}{"문항":>8}')
    print('-' * 74)
    bysubj = defaultdict(list)
    for subj, school, sim, cover, ov, eqo, eqg, pco, pcg, hits in rows:
        bysubj[subj].append((school, sim, cover, ov, eqo, eqg, pco, pcg))
    all_cov = []
    for subj in sorted(bysubj):
        covs = []
        for school, sim, cover, ov, eqo, eqg, pco, pcg in bysubj[subj]:
            c = f'{cover:.0%}' if cover is not None else '실패'
            s = f'{sim:.0%}' if sim is not None else '-'
            o = f'{ov:.2f}' if ov is not None else '-'
            e = f'{eqo}/{eqg}' if eqo is not None else '-'
            p = f'{pco}/{pcg}' if pco is not None else '-'
            print(f'{subj:<8}{school:<12}{c:>7}{s:>7}{o:>6}{e:>11}{p:>8}')
            if cover is not None:
                covs.append(cover); all_cov.append(cover)
        if covs:
            print(f'{"":8}{"└ 평균 포함도":<12}{sum(covs)/len(covs):>7.0%}')
        print()
    if all_cov:
        print('-' * 74)
        print(f'회전 대상 {len(all_cov)}쌍 전체 평균 포함도: {sum(all_cov)/len(all_cov):.0%}')
        low = sorted(((cover, school) for _, school, _, cover, *_ in rows if cover is not None))[:5]
        print('낮은 5쌍:', ', '.join(f'{sc}({c:.0%})' for c, sc in low))


if __name__ == '__main__':
    main()
