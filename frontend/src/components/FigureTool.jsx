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
  function remove(fid) { setFigures(fs => fs.filter(f => f.figure_id !== fid)); }

  const [batchVer, setBatchVer] = useState(0);
  const [batch, setBatch] = useState(null);
  // 이번 배치에서 실제 재작도에 성공한 그림만 기록(figure_id → pro 여부). 배치 도중
  // 새로 추가한 그림이 '재작도됨'으로 오표시되던 버그 방지(2026-07-08).
  const [batchDone, setBatchDone] = useState(null);

  // 그림 묶음을 재작도 — 동시 2개까지(3개 병렬은 Gemini rate limit 429 유발).
  // 성공한 figure_id(→pro) 와 실패 항목을 돌려준다.
  async function runBatch(items, pro) {
    const total = items.length; let done = 0, fail = 0;
    const okIds = new Map(); const failedItems = [];
    const q = [...items];
    setBatch({ done: 0, total, fail: 0, pro });
    const worker = async () => {
      while (q.length) {
        const f = q.shift();
        try {
          const r = await fetch(`${API}/api/figure/redraw`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: job, figure_id: f.figure_id, pro, kind: 'figure' }),
          });
          if (r.ok) okIds.set(f.figure_id, pro); else { fail++; failedItems.push(f); }
        } catch { fail++; failedItems.push(f); }
        done++; setBatch({ done, total, fail, pro });
      }
    };
    await Promise.all([worker(), worker()]);
    setBatch({ done, total, fail, pro, finished: true });
    return { okIds, failedItems };
  }

  async function redrawAll() {
    if (!figures.length || (batch && !batch.finished)) return;
    const allOk = new Map();
    // 1차: 추가된 그림 전부를 Flash(저비용)로. 클릭 시점 figures 만 대상(스냅샷).
    const r1 = await runBatch(figures, false);
    r1.okIds.forEach((pro, id) => allOk.set(id, pro));
    setBatchDone(new Map(allOk)); setBatchVer(v => v + 1);
    // 실패분만 Pro(고품질)로 재시도 — '안 된 것만 다시 pro'.
    let failed = r1.failedItems;
    if (failed.length && confirm(`이미지 제작 실패 ${failed.length}개.\n\n👉 실패한 그림만 Pro(고품질)로 다시 시도할까요? (비용↑)`)) {
      const r2 = await runBatch(failed, true);
      r2.okIds.forEach((pro, id) => allOk.set(id, pro));
      setBatchDone(new Map(allOk)); setBatchVer(v => v + 1);
      failed = r2.failedItems;
    }
    if (failed.length) alert(`재작도 실패 ${failed.length}개 남음 — 개별 '🎨 이 그림만 재작도'로 다시 시도하거나 '낙서 지우기'한 원본을 쓰세요.`);
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
                ? `${batch.pro ? 'Pro 재시도' : '재작도'} 중… ${batch.done}/${batch.total}`
                : `🎨 전체 AI 재작도 (${figures.length}개)`}
            </button>
            {batch && (
              <span className="muted" style={{ fontSize: 12 }}>
                완료 {batch.done}/{batch.total}{batch.fail ? ` · 실패 ${batch.fail}` : ''}{batch.finished ? ' ✓' : ''}
              </span>
            )}
          </div>
          <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
            위 버튼 한 번으로 추가한 그림을 <b>모두 크롭·재현</b>합니다. 실패한 그림은
            <b> Pro(고품질)로 재시도</b>할지 물어봐요. 배치 도중 새로 추가한 그림은 다음 재작도 때 반영됩니다.
          </p>
          {figures.map(f => (
            <FigureCard
              key={f.figure_id}
              job={job}
              fig={f}
              probs={probs}
              onAssign={setAssign}
              onRemove={remove}
              batchVer={batchVer}
              batchDone={batchDone}
            />
          ))}
        </div>
      )}
    </div>
  );
}
