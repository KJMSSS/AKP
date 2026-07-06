// 변환 완료 화면 — 결과 요약 및 한글 파일 다운로드
import { API } from '../lib/api';

export default function Done({ job, res, onReset }) {
  return (
    <div className="card center-card" style={{ maxWidth: 640, textAlign: 'left' }}>
      <div style={{ textAlign: 'center' }}>
        <div className="icon">🎉</div>
        <h3 style={{ marginBottom: 2 }}>변환 완료</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          한글에서 열어 <b>단·페이지 배치와 수식</b>을 꼭 확인하세요.
        </p>
      </div>

      <div className="stat-row">
        <div className="stat">
          <div className="num">{res.problems}</div>
          <div className="lbl">문제</div>
        </div>
        <div className="stat">
          <div className="num ok">{res.equations_ok}</div>
          <div className="lbl">수식 변환</div>
        </div>
        <div className="stat">
          <div className={'num' + (res.equations_fallback > 0 ? ' warn' : '')}>{res.equations_fallback}</div>
          <div className="lbl">수식 폴백</div>
        </div>
        <div className="stat">
          <div className={'num' + (res.figures_inserted > 0 ? ' ok' : '')}>{res.figures_inserted}</div>
          <div className="lbl">그림 삽입</div>
        </div>
      </div>

      <span className={'notice ' + (res.drive_uploaded ? 'ok' : '')} style={{ display: 'block' }}>
        {res.drive_uploaded
          ? '☁ Google Drive 업로드 완료 — 매트릭스 칸에 등록되었습니다.'
          : '☁ Drive 업로드 안 됨 (매트릭스 밖 변환이거나 Drive 미연동) — 아래에서 직접 다운로드하세요.'}
      </span>

      {res.fallbacks && res.fallbacks.length > 0 && (
        <div className="notice warn" style={{ display: 'block' }}>
          🔴 <b>확인 필요 {res.fallbacks.length}곳</b> — 변환 못 한 수식이 문서에{' '}
          <code>【확인필요】</code> 표시와 함께 원본 그대로 들어갔습니다.
          한글에서 <b>Ctrl+F로 "확인필요"를 검색</b>해 수식을 손봐 주세요.
          매트릭스에도 🔴 배지가 떠 있으며, 다 고친 뒤 <b>확인 완료</b>를 누르면 사라집니다.
          <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: 'pointer', fontSize: '.82rem' }}>해당 수식 {res.fallbacks.length}개 보기</summary>
            <ul style={{ margin: '6px 0 0', paddingLeft: 20 }}>
              {res.fallbacks.map((f, i) => (
                <li key={i} style={{ fontSize: '.82rem', marginBottom: 3 }}>
                  <code>{f.latex}</code> <span className="muted">({f.reason})</span>
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}

      <div style={{ marginTop: 18, display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center' }}>
        <a className="dl" href={`${API}/api/jobs/${job}/download`}>⬇ 한글파일 다운로드</a>
        <a className="dl ghost" href="/">📋 매트릭스로 돌아가기</a>
        <button className="ghost" onClick={onReset}>새 변환</button>
      </div>
    </div>
  );
}
