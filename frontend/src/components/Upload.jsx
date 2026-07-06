// 시험지 파일 업로드 컴포넌트 — 드롭존 + 메타 입력
import { useRef, useState } from 'react';

export default function Upload({ file, setFile, meta, setMeta, onStart }) {
  const inputRef = useRef(null);
  const [over, setOver] = useState(false);

  function onDrop(e) {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) setFile(f);
  }

  const fromMatrix = !!meta.registryKey;

  return (
    <div className="card" style={{ maxWidth: 760, margin: '0 auto' }}>
      <h3>시험지 업로드</h3>
      <p className="muted">
        스캔 PDF 또는 이미지(JPG/PNG). 텍스트가 없어도 AI가 읽습니다.
        {fromMatrix && <> 빌드가 끝나면 <b>매트릭스 칸에 자동 등록</b>되고 Google Drive에 올라갑니다.</>}
      </p>

      <div
        className={'dropzone' + (over ? ' over' : '') + (file ? ' has-file' : '')}
        onClick={() => inputRef.current && inputRef.current.click()}
        onDragOver={e => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <div className="dz-icon">{file ? '✅' : '📄'}</div>
        <div className="dz-main">
          {file ? file.name : '여기에 PDF를 끌어다 놓거나 클릭해서 선택'}
        </div>
        <div className="dz-sub">
          {file
            ? `${(file.size / 1024 / 1024).toFixed(1)} MB · 다른 파일을 선택하려면 다시 클릭`
            : 'PDF · JPG · PNG'}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.png,.jpg,.jpeg"
          style={{ display: 'none' }}
          onChange={e => setFile(e.target.files[0])}
        />
      </div>

      <div className="row" style={{ marginTop: 6 }}>
        <div>
          <label>학교{fromMatrix ? '' : ' (선택)'}</label>
          <input
            value={meta.school}
            onChange={e => setMeta({ ...meta, school: e.target.value })}
            placeholder="예: 광덕고"
          />
        </div>
        <div>
          <label>코드{fromMatrix ? '' : ' (선택)'}</label>
          <input
            value={meta.code}
            onChange={e => setMeta({ ...meta, code: e.target.value })}
            placeholder="예: 2025_2_1_b_기하"
          />
        </div>
      </div>
      <div className="row">
        <div>
          <label>지역 (선택)</label>
          <input
            value={meta.region}
            onChange={e => setMeta({ ...meta, region: e.target.value })}
            placeholder="예: 북구"
          />
        </div>
        <div>
          <label>과목 (선택)</label>
          <input
            value={meta.subject}
            onChange={e => setMeta({ ...meta, subject: e.target.value })}
            placeholder="예: 공통수학1"
          />
        </div>
        <div>
          <label>주제·범위 (선택)</label>
          <input
            value={meta.examRange}
            onChange={e => setMeta({ ...meta, examRange: e.target.value })}
            placeholder="예: 여러 가지 방정식 ~ 행렬"
          />
        </div>
      </div>
      <p className="muted" style={{ fontSize: '.78rem', margin: '8px 0 0' }}>
        입력한 값은 시험지 1페이지 상단 헤더 표(과목/범위/지역/학교/학기)에 그대로 들어갑니다.
      </p>

      <div style={{ marginTop: 18, textAlign: 'right' }}>
        <button className="big" disabled={!file} onClick={onStart}>변환 시작 →</button>
      </div>
    </div>
  );
}
