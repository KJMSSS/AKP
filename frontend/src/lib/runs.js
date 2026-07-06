// 수식 runs ↔ 편집 텍스트 변환 유틸리티 ($latex$ 형식)

export function runsToText(runs) {
  return (runs || []).map(r => r.type === 'eqn' ? `$${r.latex || r.hwp_script || ''}$` : (r.text || '')).join('');
}

export function textToRuns(t) {
  const out = [];
  const re = /\$([^$]+)\$/g;
  let last = 0, m;
  while ((m = re.exec(t))) {
    if (m.index > last) {
      const s = t.slice(last, m.index);
      if (s) out.push({ type: 'text', text: s });
    }
    out.push({ type: 'eqn', latex: m[1] });
    last = re.lastIndex;
  }
  if (last < t.length) {
    const s = t.slice(last);
    if (s) out.push({ type: 'text', text: s });
  }
  return out;
}
