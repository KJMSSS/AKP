"""
변경 로그 — JSON 저장 + Markdown 검수 리포트 생성

ChangeRecord: 슬롯 단위 변경 기록
  confidence 1.0 → 형식만 다름 (수학적으로 동일, 자동 적용 안전)
  confidence 0.3 → 내용이 실제로 다름 (검수 필요)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class ChangeRecord:
    slot_idx: int        # 전체 슬롯에서의 1-based 인덱스
    problem_num: int     # 문항 번호 (1-22)
    slot_kind: str       # 'answer' | 'content'
    answer_num: int      # 답지 번호 ①-⑤ (content 슬롯이면 0)
    original: str        # 템플릿 원본 내용
    applied: str         # 실제 적용된 내용
    confidence: float    # 1.0=형식 동일, 0.3=내용 변경
    is_suspicious: bool  # norm(original) != norm(applied)

    @property
    def slot_label(self) -> str:
        if self.slot_kind == 'answer':
            marks = ['①', '②', '③', '④', '⑤']
            mark = marks[self.answer_num - 1] if 1 <= self.answer_num <= 5 else f'({self.answer_num})'
            return f'{self.problem_num}번 {mark} 답지'
        return f'{self.problem_num}번 본문'


# ── JSON 출력 ────────────────────────────────────────────────────

def write_change_log(changes: list[ChangeRecord], out_path: Path) -> None:
    data = {
        'generated': str(date.today()),
        'total': len(changes),
        'suspicious': sum(1 for c in changes if c.is_suspicious),
        'format_only': sum(1 for c in changes if not c.is_suspicious),
        'changes': [asdict(c) for c in changes],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


# ── Markdown 검수 리포트 ──────────────────────────────────────────

def write_review_report(
    changes: list[ChangeRecord],
    output_hwpx: Path,
    out_path: Path,
    total_slots: int = 0,
) -> None:
    suspicious = [c for c in changes if c.is_suspicious]
    safe       = [c for c in changes if not c.is_suspicious]

    lines = [
        f'# 변경 검수 리포트',
        f'',
        f'- **생성일**: {date.today()}',
        f'- **출력 파일**: `{output_hwpx.name}`',
        f'- **전체 슬롯**: {total_slots}개',
        f'- **변경된 슬롯**: {len(changes)}개',
        f'  - 형식만 변경 (안전): {len(safe)}개',
        f'  - 실질 변경 (검수 필요): {len(suspicious)}개',
        f'',
    ]

    if suspicious:
        lines += [
            '## ⚠️ 검수 필요 — 내용이 실제로 바뀐 슬롯',
            '',
            '> OCR과 워드초벌이 다릅니다. 한글에서 직접 확인 후 올바른 값으로 수정하세요.',
            '',
            '| 슬롯 | 위치 | 워드초벌 (원본) | OCR 추출 (적용값) |',
            '|------|------|----------------|-----------------|',
        ]
        for c in suspicious:
            orig = _md_escape(c.original)
            applied = _md_escape(c.applied)
            lines.append(f'| [{c.slot_idx:03d}] | {c.slot_label} | `{orig}` | `{applied}` |')
        lines.append('')

    if safe:
        lines += [
            '## ✅ 형식만 변경 — 수학적으로 동일한 슬롯',
            '',
            '> 백틱 간격(`` ` ``) 제거, 중괄호 표기 변환 등 표기 차이만 있습니다. 내용 검수 불필요.',
            '',
            '| 슬롯 | 위치 | 원본 | 변경 후 |',
            '|------|------|------|---------|',
        ]
        for c in safe:
            orig = _md_escape(c.original)
            applied = _md_escape(c.applied)
            lines.append(f'| [{c.slot_idx:03d}] | {c.slot_label} | `{orig}` | `{applied}` |')
        lines.append('')

    lines += [
        '## 한글 검수 포인트',
        '',
        '1. 빨간색으로 표시된 수식(⚠️ 검수 필요 항목)을 찾아 원본 시험지와 대조',
        '2. 파란색 수식(형식 변경)은 수학적으로 동일하므로 시각적으로만 확인',
        '3. 검수 완료 후 `scripts/remove_highlights.py` 실행하여 색상 제거',
        '',
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(lines), encoding='utf-8')


def _md_escape(s: str) -> str:
    return s.replace('|', '\\|').replace('\n', ' ')
