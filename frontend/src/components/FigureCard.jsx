// 개별 그림 카드 — 낙서 마스크 드래그, 지우기, 재작도, 원본↔재작도 비교
import { useState, useEffect, useRef } from 'react';
import { API, redirectToLogin } from '../lib/api';

export default function FigureCard({ job, fig, probs, onAssign, onRemove, batchVer, batchDone }) {
  const baseRef = useRef(null), maskRef = useRef(null), drawing = useRef(false), dirty = useRef(false);
  const [ver, setVer] = useState(0);
  const [size, setSize] = useState(16);
  const [busy, setBusy] = useState(false);
  const [hasPaint, setHasPaint] = useState(false);
  const [redrawn, setRedrawn] = useState(false);
  const [lastPro, setLastPro] = useState(false);
  const DPR = window.devicePixelRatio || 1;

  function fitMask() {
    const img = baseRef.current, m = maskRef.current;
    if (!img || !m || !img.naturalWidth) return;
    const Wcss = img.clientWidth, scale = Wcss / img.naturalWidth, Hcss = img.naturalHeight * scale;
    m.width = Math.round(Wcss * DPR); m.height = Math.round(Hcss * DPR);
    m.style.width = Wcss + 'px'; m.style.height = Hcss + 'px';
    const ctx = m.getContext('2d'); ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, Wcss, Hcss); dirty.current = false; setHasPaint(false);
  }

  useEffect(() => {
    const img = baseRef.current;
    if (img) { img.onload = fitMask; if (img.complete) fitMask(); }
  }, [ver, batchVer]); // eslint-disable-line react-hooks/exhaustive-deps

  // 배치 재작도 완료 신호. batchDone(이번 배치에서 실제 재작도된 figure_id→pro Map)에
  // 내 그림이 있을 때만 '재작도됨'으로 전환한다. 이게 없으면, 배치가 도는 도중 새로
  // 크롭한 카드가 batchVer 0→1 전이를 관찰해 '재작도된 적 없는데' 비교 패널을 열어
  // 원본 그대로를 '변환 완료'로 오표시했다(2026-07-08 실사고, 이전엔 원본 404까지).
  const batchSeen = useRef(batchVer);
  useEffect(() => {
    if (batchVer === batchSeen.current) return;   // 마운트/동일값 → 무시
    batchSeen.current = batchVer;
    if (!batchDone || !batchDone.has(fig.figure_id)) return;  // 이번 배치에서 재작도된 그림만
    setRedrawn(true); setLastPro(!!batchDone.get(fig.figure_id));
  }, [batchVer]); // eslint-disable-line react-hooks/exhaustive-deps

  function pos(e) {
    const r = maskRef.current.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  function dn(e) { drawing.current = true; mk(e); }
  function mv(e) { if (drawing.current) mk(e); }
  function up() { if (drawing.current && dirty.current) setHasPaint(true); drawing.current = false; }
  function mk(e) {
    const ctx = maskRef.current.getContext('2d'), p = pos(e);
    ctx.fillStyle = '#ff453a'; ctx.beginPath(); ctx.arc(p.x, p.y, size, 0, 7); ctx.fill();
    dirty.current = true;
  }
  function clearMask() {
    const m = maskRef.current, ctx = m.getContext('2d');
    ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, m.width, m.height); ctx.restore();
    dirty.current = false; setHasPaint(false);
  }

  async function applyErase() {
    if (!dirty.current) { alert("먼저 그림 위에서 '지울 부분'을 드래그로 빨갛게 칠한 뒤 누르세요."); return; }
    const c = maskRef.current;
    const blob = await new Promise(r => c.toBlob(r, 'image/png'));
    const fd = new FormData();
    fd.append('job_id', job); fd.append('figure_id', fig.figure_id); fd.append('mask', blob, 'm.png');
    setBusy(true);
    // 응답 미확인 금지 — 500 을 삼키면 '눌러도 아무 일 없음'으로 보인다(2026-07-11 실사고)
    try {
      const r = await fetch(`${API}/api/figure/erase`, { method: 'POST', body: fd });
      if (r.status === 401) { redirectToLogin(); return; }
      if (!r.ok) {
        const res = await r.json().catch(() => ({}));
        alert(`낙서 지우기 실패: ${res.detail || `HTTP ${r.status}`}`);
        return;
      }
      setVer(v => v + 1);
    } finally { setBusy(false); }
  }

  async function redraw(pro = false) {
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/figure/redraw`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: job, figure_id: fig.figure_id, pro, kind: 'figure' }),
      });
      const res = await r.json().catch(() => ({}));
      if (r.status === 401) { redirectToLogin(); return; }
      if (!r.ok) {
        // 이미지 제작 실패 — Flash 였으면 '이 그림만' Pro(고품질)로 재시도할지 물어본다.
        if (!pro && confirm(`이미지 제작 실패: ${res.detail || r.status}\n\n👉 이 그림만 Pro(고품질)로 다시 시도할까요? (비용↑)`)) {
          return await redraw(true);
        }
        alert(pro
          ? `Pro 재작도도 실패했어요: ${res.detail || r.status}\n'낙서 지우기'한 원본을 그대로 쓰세요.`
          : `이미지 제작 실패: ${res.detail || r.status}`);
      } else {
        setVer(v => v + 1); setRedrawn(true); setLastPro(!!res.pro);
        if (res.ok === false) {
          alert(`재작도했지만 원본과 차이가 있을 수 있어요 (유사도 ${res.score}점).\n차이: ${(res.issues || []).slice(0, 2).join(' / ') || '-'}\n\n👉 '🔁 Pro로 다시 생성'을 쓰거나, 그래도 별로면 '낙서 지우기'한 원본을 그대로 쓰세요.`);
        }
      }
    } finally { setBusy(false); }
  }

  const src = `${API}/api/figure/${job}/${fig.figure_id}.png?v=${ver}-${batchVer || 0}`;

  return (
    <div className="prob" style={{ marginTop: 8 }}>
      <p className="muted" style={{ margin: '0 0 6px', fontSize: 12 }}>
        ① 그림 위에서 <b>지울 낙서를 빨갛게 드래그</b> → ② <b>낙서 지우기</b>. 깔끔히 다시 그리려면 <b>🎨 AI 재작도</b>.
      </p>
      <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
        <img ref={baseRef} src={src} crossOrigin="anonymous" style={{ maxWidth: '100%', display: 'block', borderRadius: 6 }} alt="그림" />
        <canvas
          ref={maskRef}
          onMouseDown={dn}
          onMouseMove={mv}
          onMouseUp={up}
          onMouseLeave={up}
          style={{ position: 'absolute', left: 0, top: 0, opacity: 0.5, cursor: 'crosshair' }}
        ></canvas>
      </div>
      {redrawn && (
        <div className="compare-panel">
          <p className="muted" style={{ margin: '0 0 6px', fontSize: 12 }}>
            🔍 재작도가 숫자·기호를 바꾸지 않았는지 <b>원본과 비교</b>하세요 (특히 <b>F/F′</b> 같은 라벨, 숫자, 식).
          </p>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px' }}>
              <div className="muted" style={{ fontSize: 11, marginBottom: 3, fontWeight: 600 }}>원본</div>
              <img
                src={`${API}/api/figure/${job}/${fig.figure_id}/original.png?v=${ver}-${batchVer || 0}`}
                style={{ width: '100%', border: '1px solid var(--line2)', borderRadius: 6, display: 'block', background: '#fff' }}
                alt="원본"
              />
            </div>
            <div style={{ flex: '1 1 200px' }}>
              <div style={{ fontSize: 11, marginBottom: 3, fontWeight: 600, color: 'var(--ok)' }}>
                재작도 {lastPro ? '(Pro·고품질)' : '(Flash·저비용)'}
              </div>
              <img
                src={src}
                style={{ width: '100%', border: '1px solid var(--ok-line)', borderRadius: 6, display: 'block', background: '#fff' }}
                alt="재작도"
              />
            </div>
          </div>
          <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <button className="ghost sel" onClick={() => redraw(true)} disabled={busy}>
              {busy ? '생성 중…' : '🔁 Pro로 다시 생성'}
            </button>
            <span className="muted" style={{ fontSize: 11 }}>
              라벨·숫자가 틀렸거나 품질이 부족하면 Pro(고품질·비용↑)로 다시 만드세요.
            </span>
          </div>
        </div>
      )}
      <div className="row" style={{ marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: '0 0 130px', fontSize: '.78rem', color: 'var(--mut)', fontWeight: 600 }}>
          브러시<input type="range" min="6" max="40" value={size} onChange={e => setSize(+e.target.value)} />
        </div>
        <button
          className="ghost"
          onClick={applyErase}
          disabled={busy}
          style={hasPaint ? { borderColor: 'var(--danger-line)', color: 'var(--danger)', background: 'var(--danger-soft)' } : {}}
        >
          {busy ? '처리 중…' : '🧹 낙서 지우기'}
        </button>
        <button className="ghost" onClick={clearMask}>초기화</button>
        <button onClick={() => redraw(false)} disabled={busy}>
          {busy ? '재작도 중…' : '🎨 이 그림만 재작도'}
        </button>
      </div>
      <div className="row" style={{ marginTop: 8, alignItems: 'flex-end' }}>
        <div style={{ flex: 2 }}>
          <label style={{ margin: '0 0 4px' }}>배정 문제</label>
          <select
            value={fig.problemIdx ?? ''}
            onChange={e => onAssign(fig.figure_id, e.target.value === '' ? null : +e.target.value)}
            style={fig.problemIdx == null
              ? { borderColor: 'var(--danger-line)', color: 'var(--danger)', background: 'var(--danger-soft)' }
              : {}}
          >
            <option value="">⚠ 문항 선택(미배정)</option>
            {probs.map((p, i) => (
              <option key={i} value={i}>문항 {p.number || i + 1}</option>
            ))}
          </select>
        </div>
        <button className="ghost" style={{ flex: '0 0 72px' }} onClick={() => onRemove(fig.figure_id)}>삭제</button>
      </div>
    </div>
  );
}
