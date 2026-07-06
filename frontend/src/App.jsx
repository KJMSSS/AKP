// 시험지 변환기 최상위 컴포넌트 — 단계 전환 및 전역 상태 관리
import { useState, useEffect } from 'react';
import Upload from './components/Upload';
import PreviewRotate from './components/PreviewRotate';
import Review from './components/Review';
import Done from './components/Done';
import { fetchHealth, postAnalyze, getJob, postRun, postBuild } from './lib/api';

// 매트릭스(학교×과목)에서 넘어온 URL 파라미터 — 셀 컨텍스트 프리필
function readMatrixParams() {
  const q = new URLSearchParams(window.location.search);
  return {
    registryKey: q.get('key') || '',
    school: q.get('school') || '',
    subject: q.get('subject') || '',
    code: q.get('code') || '',
  };
}

const STEPS = ['업로드', '회전 확인', 'AI 분석', '검수', '완료'];

// 현재 화면 → 단계 인덱스 (processing 은 데이터 유무로 렌더중/분석중 구분)
function stepIndex(step, data) {
  if (step === 'upload') return 0;
  if (step === 'preview') return 1;
  if (step === 'processing') return data ? 2 : 1;
  if (step === 'review') return 3;
  return 4;
}

export default function App() {
  const [health, setHealth] = useState({});
  const [step, setStep] = useState('upload');
  const [file, setFile] = useState(null);
  const [meta, setMeta] = useState(() => {
    const m = readMatrixParams();
    return { school: m.school, code: m.code, region: '', subject: m.subject,
             examRange: '', registryKey: m.registryKey };
  });
  const [job, setJob] = useState(null);
  const [data, setData] = useState(null);
  const [buildRes, setBuildRes] = useState(null);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {});
  }, []);

  // 작업 중(분석~검수) 페이지 이탈 방지 — 같은 탭 이동 방식이라 실수 이탈 시 검수 상태가 날아간다
  useEffect(() => {
    if (step === 'upload' || step === 'done') return;
    const guard = e => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [step]);

  async function startAnalyze() {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('school', meta.school);
    fd.append('code', meta.code);
    fd.append('region', meta.region);
    fd.append('subject', meta.subject);
    fd.append('exam_range', meta.examRange);
    fd.append('registry_key', meta.registryKey || '');
    const j = await postAnalyze(fd);
    if (!j || !j.job_id) {
      // 세션 만료(로그인 페이지로 리다이렉트됨) 또는 비용 한도 초과
      alert(j && j.detail ? j.detail : '업로드 실패 — 로그인이 만료됐을 수 있습니다.');
      if (!j || !j.detail) window.location.href = '/login';
      return;
    }
    setJob(j.job_id);
    setStep('processing');
  }

  async function runAnalyze(rotations) {
    try {
      await postRun(job, rotations);
      setStep('processing');
    } catch (e) {
      alert(e.message);   // 비용 한도 초과 등 — 회전 화면에 그대로 머문다
    }
  }

  useEffect(() => {
    if (step !== 'processing' || !job) return;
    const t = setInterval(async () => {
      const j = await getJob(job);
      if (j.status === 'preview') { clearInterval(t); setData(j); setStep('preview'); }
      else if (j.status === 'done') { clearInterval(t); setData(j); setStep('review'); }
      else if (j.status === 'error') { clearInterval(t); alert('오류: ' + j.error); setStep('upload'); }
    }, 1500);
    return () => clearInterval(t);
  }, [step, job]);

  async function doBuild(figures) {
    const r = await postBuild(job, data.problems, figures);
    setBuildRes(r);
    setStep('done');
  }

  const cur = stepIndex(step, data);

  return (
    <>
      <div className="topbar">
        <div className="brand">AKP 변환기</div>
        <span className="crumb">시험지 변환</span>
        {meta.registryKey && (
          <span className="key-pill" title="빌드 완료 시 이 매트릭스 칸에 등록 + Drive 업로드">
            {meta.registryKey}
          </span>
        )}
        <div className="spacer" />
        <span className={'pill ' + (health.claude ? 'on' : '')}>Claude {health.claude ? '✓' : '✗'}</span>
        <span className={'pill ' + (health.gemini ? 'on' : '')}>Gemini {health.gemini ? '✓' : '✗'}</span>
        <a className="back-link" href="/">← 매트릭스</a>
      </div>

      <div className="steps">
        {STEPS.map((label, i) => (
          <span key={label} style={{ display: 'contents' }}>
            {i > 0 && <span className={'step-line' + (i <= cur ? ' done' : '')} />}
            <span className={'step' + (i === cur ? ' active' : i < cur ? ' done' : '')}>
              <span className="dot">{i < cur ? '✓' : i + 1}</span>
              <span className="lbl">{label}</span>
            </span>
          </span>
        ))}
      </div>

      <main>
        {step === 'upload' && (
          <Upload file={file} setFile={setFile} meta={meta} setMeta={setMeta} onStart={startAnalyze} />
        )}
        {step === 'processing' && (
          <div className="card center-card">
            <div className="icon">{data ? '🤖' : '📄'}</div>
            <h3>{data ? 'AI가 문제를 분석하고 있습니다' : '페이지를 준비하고 있습니다'}</h3>
            <p className="muted" style={{ marginBottom: 0 }}>
              {data
                ? 'OCR + 문제 구조화 + 수식 인식 — 쪽수에 따라 수십 초에서 수 분 걸립니다.'
                : 'PDF를 페이지 이미지로 변환 중입니다. 잠시만 기다려주세요.'}
            </p>
            <div className="bar"><i></i></div>
          </div>
        )}
        {step === 'preview' && (
          <PreviewRotate job={job} data={data} onRun={runAnalyze} />
        )}
        {step === 'review' && (
          <Review job={job} data={data} setData={setData} onBuild={doBuild} />
        )}
        {step === 'done' && (
          <Done
            job={job}
            res={buildRes}
            onReset={() => { setStep('upload'); setFile(null); setData(null); setBuildRes(null); }}
          />
        )}
      </main>
    </>
  );
}
