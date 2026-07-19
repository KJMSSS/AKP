"""
그림 처리: 크롭 + 낙서 제거(인페인팅) + (AI 재작도 폴백 훅).

3단계 폴백 전략(사용자 결정):
  ① 크롭 + 인페인팅으로 낙서 제거 → 원본 그림 충실 (이 파일이 담당, opencv)
  ② 살리기 어려우면 AI 재작도(비전→구조스펙→matplotlib/SVG) → redraw_with_ai (훅)
  ③ 삽화성 그림은 생성형 — 별도(여기 없음)

좌표는 0~1 정규화 bbox 를 받는다(드래그 UI / detect_* 결과 공통).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CropResult:
    path: str
    width: int
    height: int


def _to_px(bbox: list, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox[:4]
    px = (int(round(x0 * w)), int(round(y0 * h)), int(round(x1 * w)), int(round(y1 * h)))
    x0, y0, x1, y1 = px
    x0, x1 = sorted((max(0, x0), min(w, x1)))
    y0, y1 = sorted((max(0, y0), min(h, y1)))
    return x0, y0, x1, y1


def _whiten_background(im):
    """스캔 배경(누런/회색·조명 불균일)을 흰색으로 정규화. per-channel divide(조명 보정)라
    색·선·음영은 유지하고 배경만 밝힌다 — 재작도 안 한 크롭이 회색으로 들어가는 것을 방지."""
    import numpy as np
    from PIL import Image, ImageFilter
    im = im.convert("RGB")
    w, h = im.size
    rad = max(9, min(w, h) // 12)
    arr = np.asarray(im, dtype=np.float32)
    out = np.empty_like(arr)
    for c in range(3):
        ch = Image.fromarray(arr[:, :, c].astype(np.uint8))
        bg = np.asarray(ch.filter(ImageFilter.GaussianBlur(rad)), dtype=np.float32)
        out[:, :, c] = np.clip(arr[:, :, c] / (bg + 1.0) * 245.0, 0, 255)
    return Image.fromarray(out.astype(np.uint8))


def whiten_flat_background(image_path: str, *, min_bg: int = 180, max_bg: int = 248) -> bool:
    """평탄한 회색 배경을 흰색으로 정규화(파일 제자리 수정). 바꿨으면 True.

    Gemini 재작도가 가끔 '흰 배경' 지시를 무시하고 스캔의 회색 톤(중앙값 ~232)을
    그대로 따라 그린다(2026-07-02 화정중 실사고). 재작도본은 큰 균일 회색 '채움'이
    있을 수 있어 국소 divide(_whiten_background — 큰 균일면을 흰색으로 날림)는 위험
    → 전역 화이트포인트 스케일(채널별 배경 중앙값 → 흰색)로 채움의 상대 명암을 보존한다.
    배경 중앙값이 [min_bg, max_bg) 밖이면(이미 흰색이거나 사진처럼 어두움) 건드리지 않는다.
    """
    import numpy as np
    from PIL import Image
    im = Image.open(image_path).convert("RGB")
    arr = np.asarray(im, dtype=np.float32)
    bg = np.median(arr.reshape(-1, 3), axis=0)          # 배경이 다수 픽셀 → 중앙값=배경색
    if not (min_bg <= float(bg.min()) and float(bg.max()) < max_bg):
        return False
    out = np.clip(arr * (252.0 / np.maximum(bg, 1.0)), 0, 255)
    out[(out >= 246.0).all(axis=2)] = 255.0             # 배경 근처 JPEG 얼룩 스냅
    Image.fromarray(out.astype(np.uint8)).save(image_path)
    return True


def _tilt_from_lines(im, *, max_tilt: float = 6.0) -> float:
    """수평 근방(±max_tilt)의 '긴' 직선(상자 테두리·좌표축·표선)들의 중앙값 각도.
    재작도본처럼 깨끗한 선이 있는 이미지에 강함. 근거가 약하면 0.

    긴 선(폭의 25% 이상)만 보므로 글자 획·짧은 사선은 배제되고, 수평 근방만 모으므로
    도형의 빗변·세로선은 애초에 후보가 아니다.
    """
    import math
    import numpy as np
    try:
        import cv2
    except ImportError:
        return 0.0
    g = np.asarray(im.convert("L"))
    h, w = g.shape
    if w < 80 or h < 40:
        return 0.0
    edges = cv2.Canny(g, 60, 180)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720.0, threshold=60,
                            minLineLength=max(40, int(w * 0.25)), maxLineGap=6)
    if lines is None:
        return 0.0
    angs = []
    for x1, y1, x2, y2 in lines[:, 0]:
        ang = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
        if ang > 90.0:
            ang -= 180.0
        elif ang < -90.0:
            ang += 180.0
        if abs(ang) <= max_tilt:
            angs.append(ang)
    if len(angs) < 2:
        return 0.0
    med = float(np.median(angs))
    return med if 0.3 <= abs(med) <= max_tilt else 0.0


def _tilt_from_projection(im, *, max_tilt: float = 6.0) -> float:
    """투영 프로파일 방식: 후보 각도(0.25° 간격)로 돌려 보며 '행 합 프로파일이 가장
    날카로워지는'(텍스트 행이 수평 정렬되는) 각도를 찾는다. 노이즈 낀 스캔 크롭의
    텍스트 기울기에 강함(Hough 는 스캔의 끊긴 테두리를 못 잡는다 — 2026-07-03 실측).
    확신 게이트: 최적 점수가 0° 점수보다 20% 이상 좋아야 보정(도형·사진 오탐 방지)."""
    import numpy as np
    try:
        import cv2
    except ImportError:
        return 0.0
    g = np.asarray(im.convert("L"))
    h, w = g.shape
    if w < 80 or h < 40:
        return 0.0
    if w > 600:                                   # 속도: 폭 600 으로 축소해 탐색
        g = cv2.resize(g, (600, max(1, int(h * 600.0 / w))), interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    b = (b > 0).astype(np.float32)
    ink = float(b.mean())
    if ink < 0.005 or ink > 0.5:                  # 빈 이미지/사진성 이미지는 스킵
        return 0.0
    hh, ww = b.shape

    def sharpness(ang: float) -> float:
        M = cv2.getRotationMatrix2D((ww / 2.0, hh / 2.0), ang, 1.0)
        r = cv2.warpAffine(b, M, (ww, hh), flags=cv2.INTER_LINEAR, borderValue=0.0)
        rows = r.sum(axis=1)
        d = rows[1:] - rows[:-1]
        return float((d * d).sum())               # 행 정렬될수록 프로파일 경계가 날카로움

    cand = np.arange(-max_tilt, max_tilt + 1e-9, 0.25)
    scores = [sharpness(float(a)) for a in cand]
    i = int(np.argmax(scores))
    best = float(cand[i])
    if abs(best) < 0.3 or scores[i] < sharpness(0.0) * 1.2:
        return 0.0
    return best


def _estimate_tilt_deg(im, *, max_tilt: float = 6.0) -> float:
    """크롭 내용의 미세 기울기 보정각(°). PIL Image.rotate() 인자로 바로 쓸 부호.

    두 추정기 교차검증(⚠ 이중 회전 방지 — 2026-07-03 실측):
    - 한 번 보정된 크롭(회전 여백 포함)에서 Hough 가 '스캔배경↔흰채움 경계선'을 기운
      선으로 잡는다(내용은 곧은데 -4° 보고) → Hough 만 감지되면 아티팩트로 보고 무보정.
    - Gemini 재작도본은 상자 테두리를 일부러 삐뚜름하게(스케치풍) 그려 Hough 가 과대추정
      한다(테두리 +3.2° vs 텍스트 행 +2.5° — 과대보정하면 잔여 -0.5°가 또 보정 대상이 됨)
      → 둘 다 감지·같은 부호면 '내용(행 정렬)' 기준인 투영값 채택, Hough 는 확증용.
    - 투영만 감지 → 투영 채택(노이즈 스캔 텍스트 — Hough 는 끊긴 테두리를 못 잡음).
    - 부호 상충 → 무보정(확신 없음).
    """
    lines = _tilt_from_lines(im, max_tilt=max_tilt)
    proj = _tilt_from_projection(im, max_tilt=max_tilt)
    if lines and proj:
        return proj if lines * proj > 0 else 0.0
    if lines and not proj:
        return 0.0
    return proj


def deskew_small_tilt(im):
    """스캔 기울기 미세 보정. (보정된 이미지, 보정각°) 반환 — 보정 없으면 (원본, 0.0).

    ⚠ 페이지 자동 deskew(2026-07-01 사고: 누운 스캔 90° 오판 + 고정 프레임 회전으로 내용
    유실)와는 다른 물건이다: 크롭 단위 · ±6° 하드바운드(방향 오판 구조적으로 불가) ·
    expand=True + 흰 채움(잘림 불가). 확신 없으면 무보정이 기본값.
    """
    from PIL import Image
    ang = _estimate_tilt_deg(im)
    if not ang:
        return im, 0.0
    out = im.convert("RGB").rotate(ang, expand=True, resample=Image.BICUBIC,
                                   fillcolor=(255, 255, 255))
    return out, ang


def deskewed_copy_for_embed(image_path: str) -> str:
    """hwpx 삽입 직전 안전망: 기울어 있으면 곧게 편 사본(deskewed_*.png)을 만들어 그 경로를
    반환, 곧으면 원본 경로 그대로. ⚠ 원본 파일은 제자리 수정하지 않는다 — whiten 과 달리
    기하가 변해서, 열려 있는 검수 UI 의 지우개 마스크 좌표가 어긋난다. 'deskewed_' 접두라
    _rehydrate 의 crop_* 파일명 글롭에도 안 걸린다."""
    from PIL import Image
    if Path(image_path).name.startswith("deskewed_"):
        return image_path          # 이미 보정 사본 — 재보정(이중 회전) 금지
    try:
        with Image.open(image_path) as im:
            out, ang = deskew_small_tilt(im)
        if not ang:
            return image_path
        p = Path(image_path)
        dst = p.with_name(f"deskewed_{p.name}")
        out.save(dst)
        return str(dst)
    except Exception:  # noqa: BLE001 — 보정 실패해도 원본으로 삽입은 진행
        return image_path


def snap_rect_bbox(image_path: str, bbox: list) -> list | None:
    """VLM 이 준 대략적 bbox 를 근방의 '인쇄된 사각 테두리'에 정밀 스냅.

    스캔본 표·상자 감지의 고질: 눈대중 bbox 가 상자를 위/아래로 통째로 빗나가거나
    마지막 줄을 자른다(2026-07-19 용봉중 실사고 — 문항5 상자 전체 빗나감, 문항6
    '∴' 줄 잘림). 표/조건 상자는 4변 실선 테두리가 인쇄돼 있으므로, bbox 주변을
    넉넉히 탐색해 4변이 모두 '선'으로 이어진 직사각형을 찾아 그 좌표(정규화)를
    돌려준다. 못 찾으면 None — 호출부는 원래 bbox 로 폴백.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None or not bbox or len(bbox) < 4:
        return None
    H, W = img.shape[:2]
    x0, y0, x1, y1 = bbox
    bw, bh = (x1 - x0) * W, (y1 - y0) * H
    if bw <= 8 or bh <= 8:
        return None
    # 탐색 창 — 세로 오프셋 실측이 상자 높이의 1.7배까지 나오므로 넉넉히 잡는다
    # (후보 오검출은 아래 '4변 선 커버리지' 필터가 걸러준다)
    my = int(max(1.8 * bh, 0.07 * H))
    mx = int(max(0.4 * bw, 0.02 * W))
    rx0, ry0 = max(0, int(x0 * W) - mx), max(0, int(y0 * H) - my)
    rx1, ry1 = min(W, int(x1 * W) + mx), min(H, int(y1 * H) + my)
    roi = img[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return None
    thr = cv2.adaptiveThreshold(roi, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY_INV, 31, 12)
    rh, rw = thr.shape

    # '긴 직선'만 남긴다 — 글자·수식·연필 낙서(테두리에 붙어 컨투어를 늘리는 주범)를
    # 원천 제거. 방향별 팽창은 스캔 미세 기울기(0.3~0.4° → 800px 변에서 선이 5px
    # 흘러내림)로 끊기는 런을 이어 붙인 뒤 오프닝한다.
    hk = max(24, int(bw * 0.35))
    vk = max(24, int(bh * 0.35))
    hor = cv2.dilate(thr, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 5)))
    hor = cv2.morphologyEx(hor, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (hk, 1)))
    ver = cv2.dilate(thr, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 1)))
    ver = cv2.morphologyEx(ver, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (1, vk)))
    frame = cv2.bitwise_or(hor, ver)

    def _edge_cov(x, y, w, h) -> float:
        """4변 각각의 띠에서 '선 픽셀' 비율의 최소값 — 4변 모두 선이어야 상자/표."""
        bh_ = min(16, 6 + w // 150)               # 가로 변 띠 폭(기울기 여유)
        bv_ = min(16, 6 + h // 150)
        bands = ((frame[y:y + bh_, x:x + w], 0),
                 (frame[max(0, y + h - bh_):y + h, x:x + w], 0),
                 (frame[y:y + h, x:x + bv_], 1),
                 (frame[y:y + h, max(0, x + w - bv_):x + w], 1))
        covs = []
        for band, axis in bands:
            if band.size == 0:
                return 0.0
            covs.append(float(np.mean(band.max(axis=axis) > 0)))
        return min(covs)

    # RETR_LIST: 단 구획 큰 박스 안에 중첩된 상자도 후보로 나오게(EXTERNAL 은 누락 위험)
    cnts, _ = cv2.findContours(frame, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best, best_score = None, 0.0
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 0.5 * bw or h < 0.4 * bh:          # 조각 선(단 경계 등)은 제외
            continue
        if w >= rw - 4 or h >= rh - 4:            # ROI 에 잘려 들어온 거대 구획 박스
            continue
        cov = _edge_cov(x, y, w, h)
        if cov < 0.7:                              # 프레임은 순수 선 — 강하게 요구
            continue
        score = cov * w * h
        if score > best_score:
            best, best_score = (x, y, w, h), score
    if not best:
        return None
    x, y, w, h = best
    p = 2                                          # 테두리 선 포함 여유(px)
    return [max(0.0, (rx0 + x - p) / W), max(0.0, (ry0 + y - p) / H),
            min(1.0, (rx0 + x + w + p) / W), min(1.0, (ry0 + y + h + p) / H)]


def crop_region(image_path: str, bbox: list, out_path: str, *, pad: float = 0.0) -> CropResult:
    """정규화 bbox 영역을 잘라 저장. pad 는 양옆 여유(0~1 비율).

    스캔 자체가 기울어 있으면 크롭에 그대로 들어오므로(2026-07-03 서광중 — 삽입된 보기
    상자만 삐딱하게 렌더) 미세 기울기를 보정한 뒤 배경을 흰색으로 정규화한다.
    """
    from PIL import Image
    im = Image.open(image_path).convert("RGB")
    w, h = im.size
    if pad:
        dx, dy = (bbox[2] - bbox[0]) * pad, (bbox[3] - bbox[1]) * pad
        bbox = [bbox[0] - dx, bbox[1] - dy, bbox[2] + dx, bbox[3] + dy]
    x0, y0, x1, y1 = _to_px(bbox, w, h)
    crop, _tilt = deskew_small_tilt(im.crop((x0, y0, x1, y1)))   # 스캔 기울기 미세 보정
    crop = _whiten_background(crop)                              # 배경을 흰색으로 정규화
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path)
    return CropResult(out_path, crop.width, crop.height)


# --- 인페인팅 백엔드: OpenCV(저비용) + LaMa(고품질) 라우팅 ------------------
_LAMA = None


def _get_lama():
    """SimpleLama 싱글톤(첫 호출 시 모델 로드). torch 미설치면 예외 → 호출측 폴백."""
    global _LAMA
    if _LAMA is None:
        from simple_lama_inpainting import SimpleLama
        _LAMA = SimpleLama()
    return _LAMA


def _lama_inpaint_bgr(img_bgr, mask_u8, out_path: str) -> str:
    """LaMa 인페인팅. img_bgr: cv2 BGR, mask_u8: uint8(255=지움 영역)."""
    import cv2
    from PIL import Image
    lama = _get_lama()
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    res = lama(Image.fromarray(rgb), Image.fromarray(mask_u8).convert("L"))
    res.convert("RGB").save(out_path)
    return out_path


def _inpaint_dispatch(img, mask, out_path: str, method: str) -> str:
    """마스크/방식에 따라 인페인팅 백엔드 선택.

    method="auto": 마스크 면적 비율로 라우팅(작은 낙서→OpenCV TELEA, 큰 낙서·격자→LaMa).
    method="lama"/"telea"/"ns": 명시 지정. LaMa 실패(미설치 등)는 OpenCV 로 폴백.
    """
    import cv2
    import numpy as np
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if method == "auto":
        ratio = float(np.count_nonzero(mask)) / max(1, mask.size)
        method = "lama" if ratio > 0.03 else "telea"   # 큰 마스크는 LaMa 가 우월
    if method == "lama":
        try:
            return _lama_inpaint_bgr(img, mask, out_path)
        except Exception:
            method = "telea"                           # LaMa 불가 → OpenCV 폴백
    flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
    cv2.imwrite(out_path, cv2.inpaint(img, mask, 3, flag))
    return out_path


def remove_scribbles(image_path: str, mark_boxes: list[list], out_path: str, *,
                     method: str = "auto") -> str:
    """mark_boxes(정규화 bbox 목록) 영역을 인페인팅으로 지운다.

    각 박스를 마스크로 칠하고 복원한다. method="auto"면 마스크 크기로 OpenCV/LaMa 자동
    선택(큰 낙서·격자 배경은 LaMa 가 충실). 인쇄 내용 위 겹친 낙서엔 한계가 있다.
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for b in mark_boxes:
        x0, y0, x1, y1 = _to_px(b, w, h)
        cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)
    return _inpaint_dispatch(img, mask, out_path, method)


def erase_with_mask(image_path: str, mask_path: str, out_path: str, *,
                    method: str = "auto") -> str:
    """UI 지우개 브러시가 만든 마스크(흰=지움)로 인페인팅. method 로 OpenCV/LaMa 선택."""
    import cv2
    img = cv2.imread(image_path)
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if img is None or mask is None:
        raise FileNotFoundError(f"{image_path} / {mask_path}")
    if mask.shape[:2] != img.shape[:2]:
        mask = cv2.resize(mask, (img.shape[1], img.shape[0]))
    _, mask = cv2.threshold(mask, 10, 255, cv2.THRESH_BINARY)
    return _inpaint_dispatch(img, mask, out_path, method)


def redraw_with_ai(image_path: str, bbox: list | None = None, out_path: str = "",
                   *, model: str | None = None) -> str:
    """② AI 재작도(검증 루프 포함): figure_ai.redraw_figure_verified 로 위임.

    비전이 그림 구조를 matplotlib 코드로 재현 → 샌드박스 렌더 → 원본과 비교 채점 →
    미달 시 차이점 피드백으로 재생성. 최종 실패는 RedrawError(상위가 인페인팅 크롭 폴백).
    image_path 는 이미 크롭된 그림을 기대한다(bbox 는 하위호환용).
    """
    from .figure_ai import redraw_figure_verified
    path, _verdict = redraw_figure_verified(image_path, out_path, model=model)
    return path


def process_figure(page_image_path: str, bbox: list, work_dir: str, *,
                   model: str | None = None, redraw: bool = True, prefix: str = "fig",
                   crop_only: bool = False) -> dict:
    """그림 1개 디지털화 공정(딥리서치 기반):

      ① 도형 영역 크롭 → ② 잡티/낙서 감지 후 인페인팅(OpenCV/LaMa 라우팅)
      → ③ 수학적 재작도(matplotlib) + VLM 검증 루프 → ④ 실패 시 인페인팅 크롭 폴백.

    반환 dict: {final, source(crop|inpaint|redraw), crop, cleaned?, redraw?, verdict?, *_error?}.
    final 은 항상 사용 가능한 '최선의' 이미지 경로다.

    crop_only=True: ②③ 를 건너뛰고 순수 크롭만 반환한다. 깨끗한 디지털 인쇄 시험지
    (손글씨·스캔 잡티 없음)에서는 낙서 감지가 인쇄된 라벨(ⓐⓑ)·점선 화살표를 오검출해
    인페인팅으로 지우거나(도형 훼손), 재작도가 환각을 넣을 수 있으므로 원본 크롭이 가장
    충실하다. 후속 Gemini 재작도(낙서 제거 지시 포함)의 입력으로도 원본 크롭이 적합하다.
    """
    from pathlib import Path as _P
    from .vision_claude import detect_scribbles
    from .figure_ai import redraw_figure_verified, RedrawError
    _P(work_dir).mkdir(parents=True, exist_ok=True)

    # ① 도형 영역 크롭
    crop_path = str(_P(work_dir) / f"{prefix}_crop.png")
    crop_region(page_image_path, bbox, crop_path, pad=0.01)
    result = {"bbox": bbox, "crop": crop_path, "final": crop_path, "source": "crop"}

    if crop_only:
        return result

    # ② 잡티 제거: 크롭 내부의 낙서/손글씨/직인을 감지해 인페인팅(자동 OpenCV/LaMa)
    try:
        marks = detect_scribbles(crop_path, model=model)
        if marks:
            cleaned = str(_P(work_dir) / f"{prefix}_clean.png")
            remove_scribbles(crop_path, [m.bbox for m in marks], cleaned, method="auto")
            result.update(cleaned=cleaned, final=cleaned, source="inpaint", marks=len(marks))
    except Exception as e:  # noqa: BLE001 (감지/인페인팅 실패는 크롭으로 진행)
        result["inpaint_error"] = str(e)

    # ③ 수학적 재작도 + VLM 검증 루프(잡티 제거본 기준)
    if redraw:
        try:
            redrawn = str(_P(work_dir) / f"{prefix}_redraw.png")
            path, verdict = redraw_figure_verified(result["final"], redrawn, model=model)
            result.update(redraw=path, final=path, source="redraw", verdict=verdict)
        except RedrawError as e:        # ④ 렌더 자체 실패 → 인페인팅 크롭 유지(폴백)
            result["redraw_error"] = str(e)
        except Exception as e:  # noqa: BLE001 (API 타임아웃 등도 폴백 — 전체 파이프라인 보호)
            result["redraw_error"] = f"재작도 예외: {e}"
    return result
