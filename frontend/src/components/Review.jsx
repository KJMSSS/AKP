// 문제 목록 검수 컴포넌트 — 번호·배점·발문·보기 편집 및 그림 배정
import { useState } from 'react';
import FigureTool from './FigureTool';
import { runsToText, textToRuns } from '../lib/runs';

// 발문 속 <보기> 상자 감지 — 백엔드 build_exam._has_boki_box_in_stem 과 같은 기준.
// (헤더 + 항목 라벨 2개 이상. 라벨은 'ㄱ.' 꼴 또는 원문자 ㉠~㉳)
const BOKI_HDR = /[<〈]\s*보\s*기\s*[>〉]/;
const BOKI_ITEM = /(?:^|\s)[ㄱㄴㄷㄹㅁㅂ][.)．]|[㉠-㉳]/g;
function hasBokiBox(stemText) {
  return BOKI_HDR.test(stemText) && (stemText.match(BOKI_ITEM) || []).length >= 2;
}

export default function Review({ job, data, setData, onBuild }) {
  const probs = data.problems || [];
  const [figures, setFigures] = useState(() =>
    (data.auto_figures || []).map(f => ({
      figure_id: f.figure_id,
      page: f.page,
      problemIdx: f.problem_index,
      kind: 'figure',
      auto: true,
    }))
  );

  function update(i, field, val) {
    const p = [...probs];
    p[i] = { ...p[i], [field]: val };
    setData({ ...data, problems: p });
  }
  function updateStemText(i, txt) { update(i, 'stem', textToRuns(txt)); }
  function updateChoiceText(i, ci, txt) {
    const p = [...probs];
    const ch = [...(p[i].choices || [])];
    ch[ci] = textToRuns(txt);
    p[i] = { ...p[i], choices: ch };
    setData({ ...data, problems: p });
  }
  function removeProb(i) {
    const p = probs.filter((_, k) => k !== i);
    setData({ ...data, problems: p });
    setFigures(fs =>
      fs.filter(f => f.problemIdx !== i).map(f => f.problemIdx > i ? { ...f, problemIdx: f.problemIdx - 1 } : f)
    );
  }
  const figWarn = data.figure_warnings || [];
  const unassigned = figures.filter(f => f.problemIdx == null).length;

  return (
    <div>
      <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', padding: '16px 24px' }}>
        <div style={{ flex: '1 1 300px' }}>
          <h3 style={{ marginBottom: 2 }}>
            검수 <span className="muted" style={{ fontWeight: 400 }}>
              — 인식된 문제 <b style={{ color: 'var(--fg)' }}>{probs.length}</b>개 · 그림 <b style={{ color: 'var(--fg)' }}>{figures.length}</b>개
            </span>
          </h3>
          <p className="muted" style={{ margin: 0, fontSize: '.82rem' }}>
            텍스트/수식은 <code>$ ... $</code>(LaTeX)로 편집 · 그림은 오른쪽 페이지에서 드래그로 선택
          </p>
        </div>
        <button
          className="big"
          onClick={() => {
            if (unassigned) {
              alert(`문항 배정이 안 된 그림이 ${unassigned}개 있습니다.\n각 그림 카드의 '배정 문제'에서 문항을 선택하거나, 안 쓸 그림은 삭제한 뒤 다시 눌러주세요.`);
              return;
            }
            onBuild(figures);
          }}
        >
          한글파일(.hwpx) 생성 →
        </button>
      </div>

      {(figWarn.length > 0 || unassigned > 0) && (
        <div style={{ marginBottom: 16 }}>
          {figWarn.length > 0 && (
            <span className="notice warn">
              ⚠ 자동 추출 실패/미배정 그림 {figWarn.length}건 — 오른쪽 페이지에서 직접 드래그로 추가하세요.
            </span>
          )}
          {unassigned > 0 && (
            <span className="notice warn">
              ⚠ 문항 배정이 안 된 그림 {unassigned}개 — 각 그림 카드의 '배정 문제'에서 문항을 선택하세요. 배정 전에는 생성할 수 없습니다.
            </span>
          )}
        </div>
      )}

      <div className="grid">
        <div className="card scroll-col">
          <h4>문제 목록</h4>
          {probs.map((p, i) => (
            <div className="prob" key={i}>
              <h4>
                문항 {p.number || i + 1} <span className="muted">· {p.points || '배점?'}</span>
                <button className="ghost" style={{ float: 'right', padding: '2px 10px', fontSize: '.76rem' }} onClick={() => removeProb(i)}>삭제</button>
              </h4>
              <div className="row">
                <div><label>번호</label><input value={p.number || ''} onChange={e => update(i, 'number', e.target.value)} /></div>
                <div><label>배점</label><input value={p.points || ''} onChange={e => update(i, 'points', e.target.value)} /></div>
                <div><label>난이도</label><input value={p.difficulty || ''} onChange={e => update(i, 'difficulty', e.target.value)} /></div>
              </div>
              <label>발문</label>
              <textarea rows="2" value={runsToText(p.stem)} onChange={e => updateStemText(i, e.target.value)} />
              <label>보기 (①~⑤)</label>
              {(p.choices || []).map((c, ci) => (
                <input key={ci} style={{ marginBottom: 4 }} value={runsToText(c)} onChange={e => updateChoiceText(i, ci, e.target.value)} />
              ))}
              {figures.filter(f => f.problemIdx === i).length > 0 && (
                <span className="notice ok" style={{ marginBottom: 0 }}>
                  📎 그림 {figures.filter(f => f.problemIdx === i).length}개 배정됨
                </span>
              )}
              {hasBokiBox(runsToText(p.stem)) && (
                <span className={'notice ' + (figures.some(f => f.problemIdx === i) ? 'info' : 'warn')} style={{ marginBottom: 0 }}>
                  {figures.some(f => f.problemIdx === i)
                    ? <>🧾 발문 속 {'<보기>'} 상자 텍스트는 빌드 때 자동 제거되고, 배정된 그림이 대신 들어갑니다.</>
                    : <>⚠ 발문에 {'<보기>'} 상자 텍스트가 있습니다 — 원본처럼 상자로 넣으려면 보기 영역을 크롭해 이 문항에 배정하세요(배정하면 발문 텍스트에서 자동 제거).</>}
                </span>
              )}
            </div>
          ))}
        </div>
        <FigureTool job={job} data={data} probs={probs} figures={figures} setFigures={setFigures} />
      </div>
    </div>
  );
}
