// 페이지 캔버스 그림 선택·드래그 크롭 및 전체 재작도 패널
import { useState, useEffect, useRef } from 'react';
import FigureCard from './FigureCard';
import { API } from '../lib/api';

export default function FigureTool({ job, data, probs, figures, setFigures }) {
  const pages = data.pages || [];
  const [pi, setPi] = useState(0);
  const [rect, setRect] = useState(null);
  const imgRef = useRef(null), cvRef = useRef(null), drag = useRef(null);
  const cands = (data.figure_candidates || []).filter(f => f.page === pi);
  const DPR = window.devicePixelRatio || 1;

  function draw() {
    const cv = cvRef.current, img = imgRef.current;
    if (!cv || !img || !img.complete || !img.naturalWidth) return;
    const Wcss = cv.clientWidth, scale = Wcss / img.naturalWidth, Hcss = img.naturalHeight * scale;
    cv.width = Math.round(Wcss * DPR); cv.height = Math.round(Hcss * DPR); cv.style.height = Hcss + 'px';
    const ctx = cv.getContext('2d'); ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.imageSmoothingQuality = 'high';
    ctx.clearRect(0, 0, Wcss, Hcss); ctx.drawImage(img, 0, 0, Wcss, Hcss);
    ctx.lineWidth = 1.5; ctx.strokeStyle = 'rgba(37,99,235,.55)';
    cands.forEach(f => {
      const [a, b, c, d] = f.bbox;
      ctx.strokeRect(a * Wcss, b * Hcss, (c - a) * Wcss, (d - b) * Hcss);
    });
    if (rect) { ctx.strokeStyle = '#16a34a'; ctx.lineWidth = 2; ctx.strokeRect(rect.x, rect.y, rect.w, rect.h); }
  }

  useEffect(() => {
    const img = imgRef.current;
    if (img) { img.onload = draw; if (img.complete) draw(); }
  }, [pi]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(draw, [rect, data, pi]); // eslint-disable-line react-hooks/exhaustive-deps

  function pos(e) {
    const r = cvRef.current.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  function down(e) { drag.current = pos(e); setRect(null); }
  function move(e) {
    if (!drag.current) return;
    const p = pos(e), s = drag.current;
    setRect({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) });
  }
  function up() { drag.current = null; }

  async function addCrop() {
    if (!rect || rect.w < 6 || rect.h < 6) return;
    const cv = cvRef.current;
    const Wcss = cv.clientWidth, Hcss = cv.clientHeight;
    const bbox = [rect.x / Wcss, rect.y / Hcss, (rect.x + rect.w) / Wcss, (rect.y + rect.h) / Hcss];
    try {
      const r = await fetch(`${API}/api/figure/crop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: job, page: pi, bbox }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.figure_id) {
        // 서버 오류를 조용히 삼키면 버튼이 안 먹는 것처럼 보인다(2026-07-06 실사고: cv2 500)
        alert('그림 추가 실패: ' + (j.detail || `서버 오류 (HTTP ${r.status})`));
        return;
      }
      // 기본 배정 없음(미배정) — 기본값을 문항 1(0)로 두면 배정을 깜빡한 그림이 조용히
      // 문항 1로 들어간다(2026-07-03 서광중 실사고: 문항 25 그래프가 문항 1에 삽입).
      setFigures(fs => [...fs, { figure_id: j.figure_id, page: pi, problemIdx: null, kind: 'figure' }]);
      setRect(null);
    } catch (e) {
      alert('그림 추가 실패: 네트워크 오류 — 잠시 후 다시 시도하세요.');
    }
  }

  function setAssign(fid, idx) { setFigures(fs => fs.map(f => f.figure_id === fid ? { ...f, problemIdx: idx } : f)); }
  function setKind(fid, kind) { setFigures(fs => fs.map(f => f.figure_id === fid ? { ...f, kind } : f)); }
  function remove(fid) { setFigures(fs => fs.filter(f => f.figure_id !== fid)); }

  const [batchVer, setBatchVer] = useState(0);
  const [batch, setBatch] = useState(null);

  async function redrawAll() {
    if (!figures.length || (batch && !batch.finished)) return;
    const total = figures.length; let done = 0, fail = 0;
    setBatch({ done: 0, total, fail: 0 });
    const queue = [...figures];
    const worker = async () => {
      while (queue.length) {
        const f = queue.shift();
        try {
          const r = await fetch(`${API}/api/figure/redraw`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: job, figure_id: f.figure_id, pro: false, kind: f.kind || 'figure' }),
          });
          if (!r.ok) fail++;
        } catch { fail++; }
        done++; setBatch({ done, total, fail });
      }
    };
    // 동시 2개까지만 — 3개 병렬은 Gemini 이미지 모델 rate limit(429 백오프로 2분+)을 유발
    await Promise.all([worker(), worker()]);
    setBatchVer(v => v + 1); setBatch({ done, total, fail, finished: true });
    if (fail) alert(`전체 재작도 완료 — 성공 ${total - fail}개, 실패 ${fail}개.\n실패한 그림은 개별 '🎨 이 그림 재작도'로 다시 시도하거나 '낙서 지우기'한 원본을 쓰세요.`);
    setTimeout(() => setBatch(null), 3000);
  }

  return (
    <div className="card scroll-col">
      <h4>그림 지정 / 낙서 제거 <span className="muted">— 여러 개 추가 가능</span></h4>
      <div className="thumbs">
        {pages.map((p, k) => (
          <button
            key={k}
            className={'ghost' + (k === pi ? ' sel' : '')}
            onClick={() => { setPi(k); setRect(null); }}
          >
            {k + 1}쪽
          </button>
        ))}
      </div>
      <p className="muted" style={{ margin: '8px 0' }}>
        그림이 있는 영역을 <b>마우스로 드래그</b>한 뒤 <b>＋ 그림 추가</b>. 선택한 것만 들어갑니다. 여러 개 반복 가능.
      </p>
      <img
        ref={imgRef}
        src={`${API}/api/jobs/${job}/pages/${pi}.png`}
        style={{ display: 'none' }}
        crossOrigin="anonymous"
        alt=""
      />
      <canvas
        ref={cvRef}
        onMouseDown={down}
        onMouseMove={move}
        onMouseUp={up}
        onMouseLeave={up}
        style={{ width: '100%' }}
      ></canvas>
      <div style={{ marginTop: 10 }}>
        <button disabled={!rect} onClick={addCrop}>＋ 그림 추가(크롭)</button>
      </div>
      {figures.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
            <h4 style={{ margin: 0 }}>추가된 그림 {figures.length}개</h4>
            <button onClick={redrawAll} disabled={!!(batch && !batch.finished)}>
              {batch && !batch.finished
                ? `재작도 중… ${batch.done}/${batch.total}`
                : `🎨 전체 AI 재작도 (${figures.length}개)`}
            </button>
            {batch && (
              <span className="muted" style={{ fontSize: 12 }}>
                완료 {batch.done}/{batch.total}{batch.fail ? ` · 실패 ${batch.fail}` : ''}{batch.finished ? ' ✓' : ''}
              </span>
            )}
          </div>
          <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
            각 그림의 <b>종류(그림/표)</b>를 정한 뒤 위 버튼 한 번으로 모두 변환됩니다.
            표는 격자·실선을, 그림은 도형을 재현해요(같은 엔진, 종류만 다름).
          </p>
          {figures.map(f => (
            <FigureCard
              key={f.figure_id}
              job={job}
              fig={f}
              probs={probs}
              onAssign={setAssign}
              onRemove={remove}
              onSetKind={setKind}
              batchVer={batchVer}
            />
          ))}
        </div>
      )}
    </div>
  );
}
