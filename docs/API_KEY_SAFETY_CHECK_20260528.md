# API 키 안전 점검 보고서
**점검일**: 2026-05-28  
**점검자**: Claude Code  
**점검 목적**: 차용 작업(exam-studio 코드 참조) 시작 전 사전 안전 확인

---

## 1. .gitignore — .env 포함 여부

**결과: ✅ 포함됨**

```
.env
.env.*
.env.local
.env.*.local
*.env.bak
```

`.gitignore` 상단에 명시적으로 등록되어 있음.  
추가 조치 불필요.

**참고**: `.gitignore` 마지막 줄에 `. e n v  ` (공백 삽입된 중복 항목) 확인됨 — 보안 영향 없음, 필요 시 정리 가능.

---

## 2. git history — .env commit 이력

**결과: ✅ 이력 없음**

```
git log --all --full-history -- .env
(출력 없음)
```

.env 파일이 한 번도 커밋된 적 없음. 재발급 불필요.

---

## 3. exam-studio 로컬 클론 — API 키 흔적 grep

**결과: ⚠️ exam-studio 폴더 미발견**

`D:\f1` 하위 폴더:
```
AKP / math-academy / math-tracker / SDl / .claude
```

`exam-studio` 로컬 클론이 존재하지 않음.  
→ 차용 작업 전 클론 필요. 클론 후 아래 명령으로 재점검:

```powershell
# exam-studio 클론 후 실행
Select-String -Path "D:\f1\exam-studio\resources\hwpx_scripts\*","D:\f1\exam-studio\equation.py","D:\f1\exam-studio\docs\hwpx-pitfalls.md","D:\f1\exam-studio\docs\hwp_equation_format.md" `
  -Pattern "sk-ant-|AIza|ANTHROPIC_API|GEMINI_API|GOOGLE_API|Bearer " -Recurse
```

---

## 4. 차용 작업 중 .env 접근 정책 확인

**금지 행동 (재확인)**:
- `print(os.environ)` / `repr(os.environ)` 등 환경 변수 dump
- 로그·문서에 API 키 raw 출력
- `git add .env` 또는 `git add -A` (.env 포함 위험)
- 스크린샷에 터미널 노출 시 키 포함 여부 확인

**허용**:
- `.env` → `load_dotenv()` 경유 코드 내 사용
- `os.environ.get("ANTHROPIC_API_KEY")` (값 출력 없이)

---

## 5. AKP .env 파일 권한

**결과: ⚠️ 주의 (일반 Windows 기본값)**

```
BUILTIN\Administrators : (I)(F)   — 관리자 전체 권한
NT AUTHORITY\SYSTEM    : (I)(F)   — 시스템 전체 권한
Authenticated Users    : (I)(M)   — 인증된 사용자 수정 가능
BUILTIN\Users          : (I)(RX)  — 일반 사용자 읽기 가능
```

**해석**: Windows 기본값 상속 권한. 단일 사용자 PC라면 실용적 위험 낮음.  
다른 사용자 계정이 있거나 네트워크 공유 환경이라면 `icacls .env /inheritance:r /grant:r "%USERNAME%:F"` 로 상속 제거 권장.

---

## 종합 판정

| 항목 | 상태 |
|------|------|
| .gitignore .env 등록 | ✅ 정상 |
| git history 이력 | ✅ 없음 |
| exam-studio grep | ⚠️ 클론 후 재점검 필요 |
| .env 접근 정책 | ✅ 인지됨 |
| .env 파일 권한 | ⚠️ 단일 사용자면 OK, 공유 환경이면 상속 제거 권장 |

**차용 작업 진행 가능**: exam-studio 클론 후 3번 항목 재점검 필요.

---

*키 값 자체는 이 문서에 기록하지 않음.*
