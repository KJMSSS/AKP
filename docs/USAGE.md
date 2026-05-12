# 운영 가이드 — AKP 학원 현장 사용법

> 학원장용 단계별 체크리스트. 처음부터 끝까지 따라하면 됩니다.

---

## 준비물 체크리스트

- [ ] Windows PC + Python 3.11 이상
- [ ] 한글(HWP) 설치
- [ ] Mathpix 계정 + API 키 (`MATHPIX_APP_ID`, `MATHPIX_APP_KEY`)
- [ ] `.env` 파일에 키 입력 완료
- [ ] 시험지 PDF 파일
- [ ] 해당 과목 워드초벌 `.hwpx` 파일

---

## 1단계: 시험지 파일 준비

```
samples/
├── [2025_2_1_a_확통_경신여고].pdf          ← 시험지 PDF
└── [2025_2_1_a_확통_경신여고][...][워드초벌].hwpx  ← 워드초벌
```

파일명 형식 권장: `[연도_학기_회차_과목약어_학교명]`

---

## 2단계: 자동 채우기 실행

터미널(PowerShell)을 `d:\f1\AKP` 폴더에서 실행합니다.

### 처음 실행 (Mathpix 과금 발생)

```powershell
py scripts/pdf_to_hwpx.py `
    "samples/[시험지].pdf" `
    "samples/[워드초벌].hwpx" `
    "samples/output_NEW.hwpx" `
    --highlight `
    --min-confidence 0.5 `
    --report "docs/review_NEW.md"
```

출력 중 이 줄을 기록해 두세요:
```
  pdf_id: 0202d1c2-9ca9-4d2f-8f51-ae117bc15d8c  ← 반드시 메모!
```

### 재실행 (과금 없음)

```powershell
py scripts/pdf_to_hwpx.py `
    "samples/[시험지].pdf" `
    "samples/[워드초벌].hwpx" `
    "samples/output_NEW_v2.hwpx" `
    --pdf-id "0202d1c2-9ca9-4d2f-8f51-ae117bc15d8c" `
    --highlight `
    --min-confidence 0.5
```

---

## 3단계: 결과 확인 (터미널)

```
  변경된 슬롯  : 22개
  ├ 형식 변경  : 21개 (안전)          ← 자동으로 OK
  └ 내용 변경  : 1개 (검수 필요)      ← 직접 확인
```

**내용 변경이 0개** → 그대로 사용 가능  
**내용 변경이 있음** → 아래 4단계 진행

---

## 4단계: 검수 리포트 확인

`docs/review_NEW.md` 파일을 메모장이나 VSCode로 엽니다.

검수 필요 슬롯 예시:
```markdown
| [118] | 13번 ④ 답지 | `7 over 27` | `{2} over {25}` |
```

- **왼쪽(원본)**: 워드초벌에 있던 값
- **오른쪽(적용값)**: OCR이 읽어온 값
- 시험지 원본 PDF와 대조해서 어느 쪽이 맞는지 확인

---

## 5단계: 한글에서 검수

1. `samples/output_NEW.hwpx` 를 한글에서 열기
2. **파란색 수식** = 형식만 다름, 수학적으로 동일 → 시각적으로만 확인
3. **빨간색 수식** = 내용이 바뀜 → 리포트와 대조해서 수정
4. 수정 완료 후 저장

---

## 6단계: 색상 제거 + 최종 저장

```powershell
# 현재 파일 덮어쓰기
py scripts/remove_highlights.py "samples/output_NEW.hwpx"

# 또는 새 파일로 저장
py scripts/remove_highlights.py "samples/output_NEW.hwpx" "samples/output_NEW_final.hwpx"
```

---

## 자주 하는 실수

### ❌ "output 파일이 열려 있어서 저장 실패"
한글에서 output 파일을 닫은 후 다시 실행하세요.

### ❌ "pdf_id를 잊어버림"
`samples/changes_*.json` 파일 안에 기록되어 있습니다. 없으면 재실행 (과금 발생).

### ❌ "서술형 문항이 전부 미매칭"
OCR이 `서술형1`, `서술형2` 레이블을 읽지 못한 경우입니다.  
해당 문항은 수동으로 채워야 합니다 (현재 알려진 한계).

### ❌ "11번·12번·15번이 미매칭"
2단 레이아웃으로 인한 OCR 오인식입니다. 수동으로 채우세요.

---

## 과목별 알려진 한계

| 과목 | 자동화율 | 한계 |
|------|---------|------|
| 확통 (경신여고 검증) | ~80% | 11·12·15·16·17번 수동 필요 |
| 공수1·기하·미적분 | 미검증 | 추후 확인 필요 |

---

## pdf_id 보관 방법

`samples/pdf_ids.txt` 같은 텍스트 파일에 정리:

```
# 확통 경신여고 2025-2학기 1회
0202d1c2-9ca9-4d2f-8f51-ae117bc15d8c

# 다음 시험지 이름
<pdf_id>
```

---

## 문제 발생 시

1. 터미널 오류 메시지 전체 복사
2. 어떤 파일로 실행했는지 기록
3. Claude Code에게 전달
