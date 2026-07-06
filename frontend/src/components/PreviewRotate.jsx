// 페이지 회전 미리보기 컴포넌트 — OCR 전 스캔 방향 교정
import { useState } from 'react';
import { API } from '../lib/api';

export default function PreviewRotate({ job, data, onRun }) {
  const pages = data.pages || [];
  const [rot, setRot] = useState({});
  const norm = a => ((a % 360) + 360) % 360;

  function rotate(idx, delta) {
    setRot(r => ({ ...r, [idx]: norm((r[idx] || 0) + delta) }));
  }
  function rotateAll(delta) {
    setRot(r => {
      const n = { ...r };
      pages.forEach(p => { n[p.index] = norm((n[p.index] || 0) + delta); });
      return n;
    });
  }

  return (
    <div className="card">
      <h3>회전 확인 <span className="muted" style={{ fontWeight: 400 }}>— 글자가 똑바로 보이게 맞춘 뒤 분석을 시작하세요</span></h3>
      <p className="muted">
        스캔이 누워 있으면 AI가 글자를 못 읽어 <b>발문이 빠집니다</b>. 각 페이지를{' '}
        <b>제목이 위로</b> 오도록 돌려주세요. 이미 똑바르면 그대로 분석을 시작하면 됩니다.
      </p>
      <div style={{ margin: '12px 0 16px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="ghost" onClick={() => rotateAll(90)}>↻ 전체 90°</button>
        <button className="ghost" onClick={() => rotateAll(-90)}>↺ 전체 −90°</button>
        <button className="big" onClick={() => onRun(rot)} style={{ marginLeft: 'auto' }}>
          이 방향으로 분석 시작 →
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(210px,1fr))', gap: 12 }}>
        {pages.map(p => (
          <div key={p.index} className="page-thumb">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span className="muted" style={{ fontSize: '.82rem', fontWeight: 600 }}>
                {p.index + 1}쪽{rot[p.index] ? ` · ${rot[p.index]}°` : ''}
              </span>
              <span style={{ display: 'flex', gap: 4 }}>
                <button className="ghost" style={{ padding: '3px 10px' }} onClick={() => rotate(p.index, -90)} title="반시계 90°">↺</button>
                <button className="ghost" style={{ padding: '3px 10px' }} onClick={() => rotate(p.index, 90)} title="시계 90°">↻</button>
              </span>
            </div>
            {/* 정사각 컨테이너 — CSS 회전은 박스 크기를 안 바꾸므로, 정사각 안에 두면
                0°/90°/180°/270° 어느 각도든 W↔H 가 바뀌어도 이미지가 잘리지 않는다. */}
            <div className="thumb-frame">
              <img
                src={`${API}/api/jobs/${job}/pages/${p.index}.png`}
                crossOrigin="anonymous"
                style={{ maxWidth: '92%', maxHeight: '92%', transform: `rotate(${rot[p.index] || 0}deg)`, transition: 'transform .2s' }}
                alt={`${p.index + 1}쪽 미리보기`}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
