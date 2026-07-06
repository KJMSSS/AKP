// Vite 설정 — dev 프록시(/api → FastAPI) 및 프로덕션 빌드 출력 경로
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8077',
    },
  },
  build: {
    outDir: 'dist',
  },
});
