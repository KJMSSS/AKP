// API 요청 래퍼 — 동일 출처(dev: Vite proxy /api → 8077, prod: FastAPI 직접 서빙)
// 세션 만료 시 백엔드가 /api/* 에 401 JSON 을 준다(2026-07-11 auth.py 수정).
// 401 은 'AUTH' 에러로 통일해 호출부가 로그인 페이지로 보낼 수 있게 한다.
export const API = '';

const AUTH_ERROR = 'AUTH';

async function jsonOrThrow(r, fallbackMsg) {
  if (r.status === 401) throw new Error(AUTH_ERROR);
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error((j && j.detail) || `${fallbackMsg} (HTTP ${r.status})`);
  return j;
}

export const fetchHealth = () =>
  fetch(API + '/api/health').then(r => r.json());

export const postAnalyze = async (formData) => {
  const r = await fetch(API + '/api/analyze', { method: 'POST', body: formData });
  const j = await jsonOrThrow(r, '업로드 실패');
  if (!j || !j.job_id) throw new Error((j && j.detail) || '업로드 실패');
  return j;
};

export const getJob = async (jobId) => {
  const r = await fetch(`${API}/api/jobs/${jobId}`);
  return jsonOrThrow(r, '상태 조회 실패');
};

export const postRun = async (jobId, rotations) => {
  const r = await fetch(`${API}/api/jobs/${jobId}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rotations: rotations || {} }),
  });
  // 429(비용 한도)·409(렌더 미완) 등 — 조용히 삼키면 회전 화면으로 무한 복귀한다
  await jsonOrThrow(r, '분석 시작 실패');
  return r;
};

export const postBuild = async (jobId, problems, figures) => {
  const r = await fetch(`${API}/api/jobs/${jobId}/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      problems,
      // kind(그림/표): 표는 빌드에서 단 폭 삽입 + 발문 중복 제거가 달라진다
      figures: (figures || []).map(f => ({
        figure_id: f.figure_id, problem_index: f.problemIdx, kind: f.kind || 'figure',
      })),
    }),
  });
  return jsonOrThrow(r, '빌드 실패');
};

export const isAuthError = (e) => e && e.message === AUTH_ERROR;

export const redirectToLogin = () => {
  alert('로그인이 만료되었습니다. 다시 로그인해주세요.');
  window.location.href = '/auth/login';
};
