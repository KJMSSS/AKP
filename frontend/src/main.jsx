// 시험지 변환기 앱 진입점 — React 루트 마운트
import { createRoot } from 'react-dom/client';
import './styles.css';
import App from './App';

createRoot(document.getElementById('root')).render(<App />);
