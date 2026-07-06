// API 요청 래퍼 — 동일 출처(dev: Vite proxy /api → 8077, prod: FastAPI 직접 서빙)
export const API = '';

export const fetchHealth = () =>
  fetch(API + '/api/health').then(r => r.json());

export const postAnalyze = (formData) =>
  fetch(API + '/api/analyze', { method: 'POST', body: formData })
    .then(r => r.json())
    .catch(() => null);   // 세션 만료로 로그인 HTML이 오면 JSON 파싱 실패 → null

export const getJob = (jobId) =>
  fetch(`${API}/api/jobs/${jobId}`).then(r => r.json());

export const postRun = async (jobId, rotations) => {
  const r = await fetch(`${API}/api/jobs/${jobId}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rotations: rotations || {} }),
  });
  if (!r.ok) {
    // 429(비용 한도)·409(렌더 미완) 등 — 조용히 삼키면 회전 화면으로 무한 복귀한다
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail || `분석 시작 실패 (HTTP ${r.status})`);
  }
  return r;
};

export const postBuild = (jobId, problems, figures) =>
  fetch(`${API}/api/jobs/${jobId}/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      problems,
      figures: (figures || []).map(f => ({ figure_id: f.figure_id, problem_index: f.problemIdx })),
    }),
  }).then(r => r.json());
