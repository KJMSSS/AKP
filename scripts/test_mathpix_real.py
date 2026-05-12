"""
Mathpix API 실제 호출 테스트

사용법:
    py scripts/test_mathpix_real.py <이미지>           # 파싱 결과 출력
    py scripts/test_mathpix_real.py <이미지> --raw     # 원본 JSON 출력
    py scripts/test_mathpix_real.py <이미지> --save    # samples/last_response.json 저장
    py scripts/test_mathpix_real.py <이미지> --raw --save  # 둘 다
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.ocr.mathpix_client import MathpixClient, MathpixError, OcrResult

_SAVE_PATH = Path("samples/last_response.json")


def main() -> None:
    args = sys.argv[1:]
    raw_mode  = "--raw"  in args
    save_mode = "--save" in args
    paths     = [a for a in args if not a.startswith("--")]

    if not paths:
        print("사용법: py scripts/test_mathpix_real.py <이미지> [--raw] [--save]")
        sys.exit(1)

    image_path = Path(paths[0])
    if not image_path.exists():
        print(f"[오류] 파일 없음: {image_path}")
        sys.exit(1)

    # ── 키 확인 ──────────────────────────────────────────────────
    app_id  = os.getenv("MATHPIX_APP_ID", "")
    app_key = os.getenv("MATHPIX_APP_KEY", "")
    if not app_id or not app_key:
        print("[오류] .env에 MATHPIX_APP_ID / MATHPIX_APP_KEY가 없습니다.")
        sys.exit(1)

    masked_id  = app_id[:8]  + "*" * max(0, len(app_id)  - 8)
    masked_key = app_key[:6] + "*" * 10
    print(f"[정보] APP_ID  : {masked_id}")
    print(f"[정보] APP_KEY : {masked_key}  (일부만 표시)")
    print(f"[정보] 파일    : {image_path}  ({image_path.stat().st_size:,} bytes)")
    print(f"[정보] 모드    : {'raw' if raw_mode else '파싱'}"
          f"{' + save' if save_mode else ''}\n")

    # ── API 호출 ─────────────────────────────────────────────────
    try:
        client = MathpixClient()
        print("Mathpix API 호출 중...")
        raw = client.raw_ocr_image(image_path)
    except MathpixError as e:
        print(f"[실패] {e}")
        sys.exit(1)

    # ── 저장 ─────────────────────────────────────────────────────
    if save_mode:
        _SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SAVE_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[저장] {_SAVE_PATH}\n")

    # ── raw 모드: 원본 JSON 출력 후 종료 ─────────────────────────
    if raw_mode:
        print("=" * 60)
        print("[ RAW JSON 응답 ]")
        print("=" * 60)
        print(json.dumps(raw, ensure_ascii=False, indent=2))
        return

    # ── 파싱 모드: OcrResult 블록 출력 ───────────────────────────
    try:
        result = OcrResult.from_response(raw)
    except Exception as e:
        print(f"[파싱 오류] {type(e).__name__}: {e}")
        print("\n─ raw JSON (디버그용) ─")
        print(json.dumps(raw, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(f"총 블록 수: {len(result.blocks)}\n")
    sep = "─" * 60

    for i, block in enumerate(result.blocks, 1):
        label = {"text": "텍스트", "formula_display": "수식(display)",
                 "table": "표"}.get(block.kind, block.kind)
        print(f"{sep}\n[블록 {i}] {label}\n{block.content}")

    print(sep)

    if latex := raw.get("latex_styled"):
        print(f"\n[raw] latex_styled:\n{latex}")

    # 파싱 결과가 비어 있으면 raw data 구조 힌트 출력
    if not result.blocks:
        print("\n[힌트] 블록이 0개입니다. --raw --save 로 응답 구조를 확인하세요.")
        _show_keys(raw)


def _show_keys(data: dict, depth: int = 0, max_depth: int = 3) -> None:
    """JSON 응답의 키 구조만 들여쓰기로 출력한다."""
    if depth > max_depth:
        return
    indent = "  " * depth
    for k, v in data.items() if isinstance(data, dict) else []:
        if isinstance(v, dict):
            print(f"{indent}{k}:  {{dict}}")
            _show_keys(v, depth + 1, max_depth)
        elif isinstance(v, list):
            print(f"{indent}{k}:  [list, len={len(v)}]"
                  + (f"  item[0] keys={list(v[0].keys())}" if v and isinstance(v[0], dict) else ""))
        else:
            preview = str(v)[:60].replace("\n", "↵")
            print(f"{indent}{k}:  {preview!r}")


if __name__ == "__main__":
    main()
