# HWPX 파일 구조 분석 — 확정판

> 분석 기준: 광주고 워드초벌 (광덕고로 교차 검증 완료)  
> 상태: 실제 파일 분석으로 전면 업데이트 (2026-05-12)

---

## 1. 컨테이너 구조 (확정)

```
document.hwpx  (ZIP)
├── mimetype                       # "application/haansofthwpx"
├── settings.xml
├── version.xml
├── META-INF/
│   ├── container.xml              # rootfile = Contents/content.hpf
│   ├── container.rdf
│   └── manifest.xml
├── Contents/
│   ├── content.hpf                # 섹션 목록 (section0.xml 참조)
│   ├── header.xml                 # charPr / paraPr / borderFill 스타일 정의
│   ├── masterpage0.xml            # 마스터 페이지 (헤더/푸터 레이아웃)
│   └── section0.xml              ← 본문 전체 (모든 문항 포함)
├── BinData/
│   ├── image1.jpg … image7.png    # 문항 삽화 이미지
└── Preview/
    ├── PrvImage.png
    └── PrvText.txt
```

---

## 2. 네임스페이스

```
xmlns:hp  = "http://www.hancom.co.kr/hwpml/2011/paragraph"
xmlns:hs  = "http://www.hancom.co.kr/hwpml/2011/section"
xmlns:hh  = "http://www.hancom.co.kr/hwpml/2011/head"
xmlns:hm  = "http://www.hancom.co.kr/hwpml/2011/master-page"
```

루트 요소: `<hs:sec xmlns:hp="..." ...>`

---

## 3. 본문 단락 기본 구조

```xml
<hp:p id="UINT32" paraPrIDRef="N" styleIDRef="M" pageBreak="0" columnBreak="0" merged="0">
  <hp:run charPrIDRef="K">

    <!-- 텍스트 -->
    <hp:t>텍스트 내용</hp:t>

    <!-- 인라인 수식 -->
    <hp:equation ...>
      <hp:script>HWP 수식 표기</hp:script>
    </hp:equation>

    <!-- 탭 -->
    <hp:t>
      <hp:tab width="N" leader="0" type="1"/>
      다음 텍스트
    </hp:t>

  </hp:run>
  <hp:linesegarray>
    <hp:lineseg textpos="0" vertpos="N" vertsize="N" textheight="N" .../>
  </hp:linesegarray>
</hp:p>
```

---

## 4. `hp:equation` — 수식 객체 (확정)

### 완전한 구조

```xml
<hp:equation
  id="2130792694"                      <!-- 32비트 고유 ID (한글이 자동 부여) -->
  zOrder="31"                           <!-- 렌더링 순서 -->
  numberingType="EQUATION"
  textWrap="TOP_AND_BOTTOM"
  textFlow="BOTH_SIDES"
  lock="0"
  dropcapstyle="None"
  version="Equation Version 60"         <!-- 한글 수식 편집기 버전 -->
  baseLine="85"                         <!-- 기준선 높이(%) — 대부분 85 -->
  textColor="#000000"
  baseUnit="1100"                       <!-- 기본 단위 (HWPUNIT) -->
  lineMode="CHAR"                       <!-- 인라인 모드 -->
  font="HYhwpEQ">                       <!-- 수식 전용 폰트 -->

  <hp:sz width="737" widthRelTo="ABSOLUTE"
         height="1125" heightRelTo="ABSOLUTE" protect="0"/>
         <!-- width/height: HWPUNIT 단위, 수식 크기 -->

  <hp:pos
    treatAsChar="1"                     <!-- ★ 1 = 인라인(문자로 취급) -->
    affectLSpacing="0" flowWithText="1"
    allowOverlap="0" holdAnchorAndSO="0"
    vertRelTo="PARA" horzRelTo="COLUMN"
    vertAlign="TOP" horzAlign="LEFT"
    vertOffset="0" horzOffset="0"/>

  <hp:outMargin left="0" right="0" top="0" bottom="0"/>
  <hp:shapeComment>김종민</hp:shapeComment>  <!-- 작성자 메모 (무시 가능) -->

  <hp:script>LaTeX와 유사한 HWP 수식 표기</hp:script>
</hp:equation>
```

### `hp:script` — HWP 수식 표기법

실제 파일에서 추출한 예시:

| `hp:script` 내용 | 수식 의미 |
|-----------------|-----------|
| `x` | $x$ |
| `a+b+c=2` | $a+b+c=2$ |
| `x^{2}` | $x^2$ |
| `a^{2}+b^{2}+c^{2}` | $a^2+b^2+c^2$ |
| `A=x^{2}+2x+3y,~B=2x^{2}+x-2y` | 두 식, `~` = 공백 |
| `ab+bc+ca=-1` | $ab+bc+ca=-1$ |
| `x^{2}+(a-1)x+5=x^{2}+2x+(b+3)` | 방정식 |

> **핵심 규칙:**
> - `^{n}` 또는 `^n` : 위첨자 (LaTeX `^` 동일)
> - `_{n}` : 아래첨자 (LaTeX `_` 동일)
> - `~` : 강제 공백
> - `{a} over {b}` : 분수 (LaTeX `\frac{a}{b}`)
> - `sqrt {x}` : 루트 (LaTeX `\sqrt{x}`)
> - `int from {0} to {1}` : 적분 (LaTeX `\int_0^1`)
> - 괄호, 등호, 사칙연산은 LaTeX와 동일

### LaTeX → HWP Script 변환 규칙

| LaTeX | HWP Script |
|-------|-----------|
| `\frac{a}{b}` | `{a} over {b}` |
| `\sqrt{x}` | `sqrt {x}` |
| `\sqrt[n]{x}` | `nroot {n} {x}` |
| `\int_a^b` | `int from {a} to {b}` |
| `\sum_{i=1}^{n}` | `sum from {i=1} to {n}` |
| `\lim_{x \to 0}` | `lim_{x->0}` |
| `\infty` | `inf` |
| `\cdot` | `cdot` |
| `\times` | `times` |
| `x^{2}` | `x^{2}` (동일) |
| `x_{n}` | `x_{n}` (동일) |

---

## 5. 문항 구조 패턴

### 5-1. 문제 본문 단락

```xml
<!-- 문항 본문: paraPrIDRef="6" styleIDRef="1" charPrIDRef="8" -->
<hp:p paraPrIDRef="6" styleIDRef="1">
  <hp:run charPrIDRef="8">
    <hp:t>2. </hp:t>
    <!-- 인라인 수식 -->
    <hp:equation ...><hp:script>a+b+c=2</hp:script></hp:equation>
    <hp:t>일 때, </hp:t>
    <hp:equation ...><hp:script>a^{2}+b^{2}+c^{2}</hp:script></hp:equation>
    <hp:t>의 값은?</hp:t>
    <!-- endNote에 [정답] 저장 -->
    <hp:ctrl>
      <hp:endNote number="2" ...>
        <hp:subList>
          <hp:p ...><hp:run ...><hp:t> [정답] ①</hp:t></hp:run></hp:p>
          <hp:p ...><hp:run ...><hp:t>&lt;해설&gt; ...</hp:t></hp:run></hp:p>
        </hp:subList>
      </hp:endNote>
    </hp:ctrl>
  </hp:run>
</hp:p>
```

### 5-2. 선택지 단락 (5지선다)

```xml
<!-- 선택지: 같은 paraPrIDRef="6" charPrIDRef="8" -->
<hp:p paraPrIDRef="6" styleIDRef="1">
  <hp:run charPrIDRef="8">
    <hp:t>① </hp:t>
    <hp:equation ...><hp:script>FORMULA1</hp:script></hp:equation>
    <hp:t>
      <hp:tab width="1175" leader="0" type="1"/>
      <hp:tab width="3500" leader="0" type="1"/>
      ② 
    </hp:t>
    <hp:equation ...><hp:script>FORMULA2</hp:script></hp:equation>
    <hp:t>
      <hp:tab .../>
      <hp:tab .../>
      ③ 
    </hp:t>
    <hp:equation ...><hp:script>FORMULA3</hp:script></hp:equation>
  </hp:run>
</hp:p>
<!-- ④ ⑤ 는 별도 단락 또는 이어지는 run -->
```

> 선택지 기호 ①②③④⑤는 유니코드 문자 그대로 `<hp:t>` 안에 삽입

### 5-3. 점수 표기

```xml
<!-- 점수: paraPrIDRef="7" styleIDRef="3" charPrIDRef="16" -->
<hp:p paraPrIDRef="7" styleIDRef="3">
  <hp:run charPrIDRef="16">
    <hp:t>4.0점</hp:t>
  </hp:run>
</hp:p>
```

### 5-4. 정답/해설 (endNote)

- 정답과 해설은 본문 단락 안의 `<hp:endNote>` 컨트롤로 저장
- `[정답] ②` 형태로 `charPrIDRef="7"`(소형 글자)로 표기
- 페이지 끝 미주 영역에 렌더링

---

## 6. header.xml charPr / paraPr 매핑 (광주고 기준)

| id | height(pt 환산) | 용도 추정 |
|----|----------------|----------|
| 0 | 10pt | 기본 본문 |
| 1 | 10pt | 기본 본문 (변형) |
| 5 | 18pt | 헤더 제목 |
| 6 | 28pt | 대제목 |
| 7 | 12pt | 미주/정답 |
| 8 | 12pt | 문항 본문 ★ |
| 9 | 5pt | 극소자 |
| 16 | 12pt | 점수 표기 |

> HWPUNIT = 100 × (1/7200인치). height 1200 ≈ 10pt, 1800 ≈ 15pt

---

## 7. 광주고 vs 광덕고 비교

| 항목 | 광주고 | 광덕고 |
|------|--------|--------|
| 파일 구조 | 동일 | 동일 |
| 네임스페이스 | 동일 | 동일 |
| 수식 태그 | `hp:equation` | `hp:equation` |
| script 문법 | 동일 | 동일 |
| charPrIDRef 범위 | 0–18 | 0–21 |
| paraPrIDRef 범위 | 0–14 | 0–19 |
| equation 개수 | ~170 | 382 |

**결론:** 두 파일의 XML 구조·수식 태그·script 문법은 완전히 동일. charPr/paraPr ID는 파일별로 다르나 역할(본문=8, 점수=16 등)은 유사하게 매핑됨.

---

## 8. builder.py 구현 전략

### 전략: 템플릿 치환 (Template Substitution)

새 HWPX를 scratch에서 생성하는 것은 리스크가 크다.  
**기존 .hwpx를 템플릿으로 써서 `hp:script`와 `hp:t` 내용만 교체**하는 방식이 안전하다.

```
1. 기존 .hwpx를 ZIP으로 열어 section0.xml 추출
2. XML 파싱 (lxml or ElementTree)
3. 각 hp:equation의 hp:script 텍스트 → LaTeX→HWP 변환 후 교체
4. hp:t 텍스트 → 새 문항 내용으로 교체
5. 수정된 XML을 ZIP에 다시 저장
```

### 신규 수식 삽입 시 필요한 최소 속성

```python
EQ_ATTRS = {
    "numberingType": "EQUATION",
    "textWrap": "TOP_AND_BOTTOM",
    "textFlow": "BOTH_SIDES",
    "lock": "0",
    "dropcapstyle": "None",
    "version": "Equation Version 60",
    "baseLine": "85",
    "textColor": "#000000",
    "baseUnit": "1100",
    "lineMode": "CHAR",
    "font": "HYhwpEQ",
}
EQ_POS_ATTRS = {
    "treatAsChar": "1",
    "affectLSpacing": "0",
    "flowWithText": "1",
    "allowOverlap": "0",
    "holdAnchorAndSO": "0",
    "vertRelTo": "PARA",
    "horzRelTo": "COLUMN",
    "vertAlign": "TOP",
    "horzAlign": "LEFT",
    "vertOffset": "0",
    "horzOffset": "0",
}
```

---

## 9. 미구현 / 추가 확인 필요

- [ ] `linesegarray` 자동 계산 여부 (한글이 자동 재계산하는지 확인)
- [ ] 신규 `id` 생성 규칙 (32비트 난수 or 순차 증가)
- [ ] 이미지(BinData) 교체 방법 (삽화 있는 문항용)
- [ ] 2단 컬럼 레이아웃에서 `colPr` 처리
