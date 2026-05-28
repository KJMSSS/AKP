# HWPX 생성 시 주의사항 (함정 모음)

차용 출처: `D:/f1/exam-studio/docs/hwpx-pitfalls.md` @ commit `4a96778`  
차용 일자: 2026-05-28  
AKP 코드 라인 매핑 추가됨.

---

## 치명적 (파일이 열리지 않음)

### 1. `<hp:script>` 내 XML 특수문자 미이스케이프

수식 스크립트는 XML 텍스트 노드이므로 `<`, `>`, `&`를 반드시 이스케이프해야 한다.

```
BAD:  <hp:script>x_1 <x_2</hp:script>      → XML 파싱 에러
GOOD: <hp:script>x_1 &lt;x_2</hp:script>    → 정상
```

Python 해결법:
```python
from xml.sax.saxutils import escape
script = escape(equation_text)
```

**AKP 처리 위치**:
- `src/text_only/text_builder.py` — `from xml.sax.saxutils import escape as _xe`  
  `<hp:script>{_xe(hwp)}</hp:script>` (line ~274)
- `src/template_based/builder.py` — `_xml_escape()` 래퍼 사용 (line ~75, ~101)
- **현재 상태**: ✅ 정상 처리됨

---

### 2. 테이블 `cellAddr` rowAddr 불일치

`<hp:cellAddr colAddr="C" rowAddr="R"/>` 에서 rowAddr는 해당 행의 0-based 인덱스여야 한다.

```
BAD:  모든 셀의 rowAddr="0"  → 한컴이 파일을 열지 못함
GOOD: row[0]→rowAddr="0", row[1]→rowAddr="1", row[2]→rowAddr="2"
```

**AKP 처리 위치**:
- `src/common/hwpx_table_inserter.py` — 조건/보기 박스 표 생성 시 rowAddr 설정
- 이상 발견 시: `src/common/hwpx_validator.py` `fix_hwpx()` 로 자동 수정 가능  
  (학원장 승인 후 `--fix` 활성화 예정)
- **현재 상태**: 1×1 단일 셀 표 위주 → rowAddr=0 고정 → ✅ 문제 없음

---

### 3. XML 태그 불균형

`<hp:subList>`, `<hp:endNote>`, `<hp:p>`, `<hp:tbl>`, `<hp:tr>`, `<hp:tc>` 등의
열림/닫힘이 일치하지 않으면 파싱 실패.

**AKP 처리 위치**:
- `src/text_only/text_builder.py` — 단락/표/수식 XML 생성 (raw string 조작)
- 검증: `src/common/hwpx_validator.py` `validate_hwpx()` 자동 점검
- **현재 상태**: 파이프라인 Step 9.5에서 자동 감지

---

## 경미 (렌더링 문제)

### 4. zOrder 중복

모든 shape 객체(equation, tbl, pic)의 zOrder 값은 고유해야 한다.
중복 시 객체 겹침/순서 오류.

**AKP 처리 위치**:
- `src/text_only/text_builder.py` — `_ez()` 메서드로 순차 증가 zOrder 할당
- 검증: `src/common/hwpx_validator.py` 자동 감지
- **현재 상태**: ✅ `_ez()` 카운터 방식으로 중복 없음

---

### 5. charPrIDRef / paraPrIDRef 불일치

section0.xml에서 사용하는 charPrIDRef 값이 header.xml의 `<hh:charPr>` 정의와
맞지 않으면 글꼴 크기·볼드·색상 등이 의도와 다르게 렌더링됨.

- 양식지(template)와 샘플의 header.xml은 charPr 정의가 다름
- 빌더가 샘플 구조를 참조하면서 양식지의 header를 사용하면 ID 매핑이 어긋남
- 해결: 양식지의 charPr 매핑 테이블을 미리 파악하고, 그에 맞는 IDRef 사용

**AKP 처리 위치**:
- `src/text_only/text_builder.py` — 템플릿 header.xml을 그대로 재사용
  (신규 charPr 생성 없음 → 기존 IDRef 재활용)
- **현재 상태**: 템플릿 기반이므로 매핑 불일치 위험 낮음

---

### 6. lxml 재직렬화 부작용

`etree.fromstring()` → `etree.tostring()`으로 XML을 파싱-재직렬화하면:
- 속성 따옴표가 `"` → `'`로 변경될 수 있음
- XML 선언 뒤에 개행이 추가됨
- 속성 순서가 변경될 수 있음

한컴오피스가 이를 거부하는 경우가 있으므로, **raw string 조작**을 권장.

**AKP 처리 위치**:
- `src/text_only/text_builder.py` — f-string raw XML 생성 (lxml 재직렬화 없음)
- `src/common/hwpx_table_inserter.py` — regex 기반 직접 수정
- **현재 상태**: ✅ raw string 조작 방식 사용 중

---

## 검증 도구 (AKP 통합)

```powershell
# 구조 검증만
python src/common/hwpx_validator.py samples/11b_production/동신여고_v5.hwpx

# 자동 수정 + 검증 (학원장 승인 후 활성화)
python src/common/hwpx_validator.py samples/11b_production/동신여고_v5.hwpx --fix

# 네임스페이스 정규화
python src/common/hwpx_namespace_fixer.py samples/11b_production/동신여고_v5.hwpx
```

파이프라인 자동 실행: `crop_ocr_builder.py` Step 9.5 (빌드 → 표 삽입 → 구조 검증 → 골드 검증)

---

## ZIP 구조 참고

HWPX는 ZIP 파일이며 다음 조건을 충족해야 한다:
- `mimetype`이 첫 번째 엔트리, STORED (비압축), extra field 없음
- `mimetype` 내용: `application/hwp+zip`
- 나머지 파일: DEFLATED 압축
- 필수 파일: `mimetype`, `version.xml`, `Contents/section0.xml`, `Contents/header.xml`,
  `Contents/content.hpf`, `Contents/masterpage0.xml`, `META-INF/container.xml`,
  `META-INF/container.rdf`, `META-INF/manifest.xml`, `settings.xml`
