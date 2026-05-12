# HWPX 파일 구조 분석 노트

> **상태**: 공식 스펙 기반 초안. `unpacker.py`로 실제 파일 분석 후 보완 필요.

## 1. 컨테이너 구조

`.hwpx`는 ZIP 아카이브다. `unzip -l file.hwpx` 또는 `unpacker.py`로 내부를 확인한다.

```
document.hwpx  (ZIP)
├── mimetype                       # "application/haansofthwpx" (텍스트)
├── META-INF/
│   └── container.xml              # 루트 파일 선언 (OPC 규격)
└── Contents/
    ├── content.hml                # 본문 XML ← 핵심
    ├── header.xml                 # 스타일·폰트·문단모양 정의
    ├── section0.xml               # 버전에 따라 섹션 별도 파일
    └── BinData/                   # 이미지·OLE 객체 등 이진 데이터
```

## 2. META-INF/container.xml

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="Contents/content.hml"
              media-type="application/haansofthwpx"/>
  </rootfiles>
</container>
```

## 3. content.hml 뼈대

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<HWPML Version="2.0" SubVersion="8.0"
       xmlns="http://www.hancom.co.kr/hwpml/2012/HWPMLBody">
  <HEAD>
    <DOCSUMNINFO>
      <TITLE>문서 제목</TITLE>
    </DOCSUMNINFO>
    <DOCINFO>
      <!-- 페이지 크기, 여백 등 -->
    </DOCINFO>
    <MAPPINGLIST>
      <!-- 폰트·문단·글자 모양 인덱스 테이블 -->
    </MAPPINGLIST>
  </HEAD>
  <BODY>
    <SECTION>
      <P ParaShape="0" Style="0">
        <TEXT>
          <CHAR CharShape="0">텍스트 내용</CHAR>
        </TEXT>
      </P>
    </SECTION>
  </BODY>
</HWPML>
```

## 4. 수식 객체 (EQEDIT / Equation)

HWP 수식은 `<EQEDIT>` 또는 `<Equation>` 인라인 객체로 삽입된다.

```xml
<P>
  <TEXT>
    <CHAR>다음 적분을 계산하라.</CHAR>
  </TEXT>
  <!-- 수식 오브젝트 (TODO: 실제 파일에서 태그명·속성 확인 필요) -->
  <CTRL>
    <EQEDIT>
      <EQ><!-- HWP 수식 표기법 (LaTeX와 다름) --></EQ>
    </EQEDIT>
  </CTRL>
</P>
```

### LaTeX → HWP 수식 표기 변환 예시

| LaTeX | HWP 수식 |
|-------|----------|
| `\frac{a}{b}` | `{a} over {b}` |
| `\sqrt{x}` | `sqrt {x}` |
| `\int_0^1` | `int from {0} to {1}` |
| `x^2` | `x sup 2` |
| `x_n` | `x sub n` |
| `\sum_{i=1}^{n}` | `sum from {i=1} to {n}` |

> ⚠️ HWP 수식 표기는 MathML도 LaTeX도 아닌 독자 포맷이다.  
> 변환 로직은 `src/hwpx/formula_builder.py`에 구현 예정.

## 5. 표 (TABLE)

```xml
<TABLE RowCount="2" ColCount="3">
  <ROW>
    <CELL>...</CELL>
    <CELL>...</CELL>
    <CELL>...</CELL>
  </ROW>
  <ROW>...</ROW>
</TABLE>
```

## 6. 실제 파일 분석 방법

```bash
# samples/ 에 .hwpx 파일 복사 후 실행
python -m src.hwpx.unpacker samples/sample.hwpx

# _unpacked/sample/ 폴더에서 XML 확인
# 특히 확인할 것:
#   - EQEDIT / Equation 태그명 및 속성
#   - CHAR·TEXT·RUN 계층 구조
#   - 폰트·문단모양 인덱스 참조 방식
```

## 7. 미확인 항목 (실제 파일로 검증 필요)

- [ ] `<EQEDIT>` vs `<Equation>` — 정확한 태그명
- [ ] 수식 오브젝트의 너비·높이 속성
- [ ] `<MAPPINGLIST>` 내 폰트·모양 정의 최소 구조
- [ ] `header.xml` 분리 여부 (버전별 차이)
- [ ] 섹션이 `content.hml` 내부에 있는지 `section0.xml`로 분리되는지
