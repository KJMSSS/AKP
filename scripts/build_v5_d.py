"""
D안 풀코스 빌드 스크립트.

Claude Vision 1차 + Mathpix 2차 + 그림 재생성 → HWPX

사용법:
  python scripts/build_v5_d.py                     # 전 학교
  python scripts/build_v5_d.py 경신여고             # 특정 학교
  python scripts/build_v5_d.py 경신여고 고려고       # 여러 학교

옵션:
  --no-figure-regen   그림 재생성 건너뜀 (원본 bbox 크롭 사용, 빠름)
  --no-llm-merge      수식 불일치 시 LLM 병합 안 함 (Vision 텍스트 유지)
  --force-vision      Vision 캐시 무시 재실행
  --force-mathpix     Mathpix 캐시 무시 재실행
  --force-merge       병합 캐시 무시 재실행
  --force-figure      그림 재생성 캐시 무시 재실행
  --force-all         모든 캐시 무시
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

from src.pipeline.d_plan_builder import build_one_d

ROOT      = Path(__file__).resolve().parent.parent
SRC_DIR   = ROOT / "samples" / "11b"
PROD_DIR  = ROOT / "samples" / "11b_production"
CROP_ROOT = ROOT / "log" / "cycle_16" / "crops"

PROD_DIR.mkdir(exist_ok=True)

ALL_SCHOOLS = sorted(
    p.stem.removeprefix("[").removesuffix("]").replace("2025_1_1_b_공수1_", "")
    for p in SRC_DIR.glob("[[]2025_1_1_b_공수1_*].pdf")
    if "광주고" not in p.stem
)


def main():
    args = sys.argv[1:]

    # 플래그 파싱
    no_figure_regen = "--no-figure-regen" in args
    no_llm_merge    = "--no-llm-merge"    in args
    force_all       = "--force-all"       in args
    force_vision    = force_all or "--force-vision"  in args
    force_mathpix   = force_all or "--force-mathpix" in args
    force_merge     = force_all or "--force-merge"   in args
    force_figure    = force_all or "--force-figure"  in args

    schools = [a for a in args if not a.startswith("--")]
    targets = schools if schools else ALL_SCHOOLS

    print(f"[D안 풀코스]")
    print(f"  대상: {targets}")
    print(f"  그림 재생성: {'OFF' if no_figure_regen else 'ON'}")
    print(f"  LLM 병합:    {'OFF' if no_llm_merge else 'ON'}")
    print()

    ok, fail = [], []

    for school in targets:
        source   = f"2025_1_1_b_공수1_{school}"
        crop_dir = CROP_ROOT / school

        try:
            _, verify = build_one_d(
                source=source,
                src_dir=SRC_DIR,
                prod_dir=PROD_DIR,
                crop_dir=crop_dir,
                force_vision=force_vision,
                force_mathpix=force_mathpix,
                force_merge=force_merge,
                force_figure=force_figure,
                no_figure_regen=no_figure_regen,
                llm_merge=not no_llm_merge,
            )
            if verify["pass"]:
                ok.append(school)
            else:
                fail.append(f"{school}(검증실패)")
        except Exception as e:
            import traceback
            print(f"\n[ERROR] {school}: {e}")
            traceback.print_exc()
            fail.append(school)

    print(f"\n\n{'='*55}")
    print(f"완료: {ok}")
    if fail:
        print(f"실패: {fail}")


if __name__ == "__main__":
    main()
