"""
OcrResult → .hwpx 변환기 (스켈레톤)

실제 XML 태그·속성은 unpacker.py 로 실제 .hwpx를 분석한 뒤 채운다.
현재는 ZIP 패키징과 파일 레이아웃만 확정된 상태.
"""
import zipfile
from pathlib import Path
from textwrap import dedent

from src.ocr.mathpix_client import OcrBlock, OcrResult


# ── XML 조각 생성 헬퍼 (실제 HML 분석 후 교체 예정) ──────────────

def _para_xml(block: OcrBlock) -> str:
    """OcrBlock 하나를 HML <P> 요소 문자열로 변환한다."""
    if block.kind == "text":
        # TODO: 실제 HML 태그 구조로 교체
        return f'<P><TEXT><CHAR>{_escape(block.content)}</CHAR></TEXT></P>'
    if block.kind == "formula_display":
        # TODO: HWP 수식 객체(EQEDIT) 형식으로 교체
        return f'<P><TEXT><CHAR>[수식] {_escape(block.content)}</CHAR></TEXT></P>'
    if block.kind == "table":
        # TODO: HML <TABLE> 요소로 교체
        return f'<P><TEXT><CHAR>[표]</CHAR></TEXT></P>'
    return ""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


# ── 파일별 XML 생성 ──────────────────────────────────────────────

def _container_xml() -> bytes:
    # OPC 컨테이너 — META-INF/container.xml
    return dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
          <rootfiles>
            <rootfile full-path="Contents/content.hml"
                      media-type="application/haansofthwpx"/>
          </rootfiles>
        </container>
    """).encode("utf-8")


def _content_hml(paragraphs: list[str]) -> bytes:
    # TODO: 실제 .hwpx 분석 후 HEAD·DOCINFO·스타일 정보 추가
    body = "\n    ".join(paragraphs) if paragraphs else '<P><TEXT><CHAR/></TEXT></P>'
    return dedent(f"""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <HWPML Version="2.0" SubVersion="8.0"
               xmlns="http://www.hancom.co.kr/hwpml/2012/HWPMLBody">
          <HEAD>
            <DOCSUMNINFO>
              <TITLE>AKP 변환 문서</TITLE>
            </DOCSUMNINFO>
          </HEAD>
          <BODY>
            <SECTION>
              {body}
            </SECTION>
          </BODY>
        </HWPML>
    """).encode("utf-8")


# ── 공개 API ─────────────────────────────────────────────────────

def build(result: OcrResult, out_path: Path) -> Path:
    """OcrResult를 받아 .hwpx 파일을 생성한다."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    paragraphs = [_para_xml(b) for b in result.blocks if _para_xml(b)]

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/haansofthwpx")
        zf.writestr("META-INF/container.xml", _container_xml())
        zf.writestr("Contents/content.hml", _content_hml(paragraphs))

    return out_path
