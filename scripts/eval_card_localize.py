"""Experiment: OpenCV card localization + ID-1 warp vs current document eval.

Does not modify the production pipeline. Writes gitignored artifacts only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from egyid.eval import (  # noqa: E402
    BACK_DOC_FIELDS,
    FRONT_DOC_FIELDS,
    extract_back_document,
    extract_front_document,
    load_manifest,
    score_back_document,
    score_front_document,
    tally_fields,
)
from egyid.imaging import load_image  # noqa: E402
from egyid.ocr import default_engine  # noqa: E402

MANIFEST_PATH = ROOT / "artifacts" / "eval_manifest" / "manifest.json"
OUT_DIR = ROOT / "artifacts" / "doc_eval"
DEBUG_DIR = OUT_DIR / "localize_debug_native"
COMPARE_PATH = OUT_DIR / "localize_compare_native.json"
ID1_ASPECT = 856 / 540
DETECT_MAX_SIDE = 900
MIN_AREA = 0.08
MAX_AREA = 0.78
SKIP_FILL = 0.82
ASPECT_RANGE = (1.05, 3.0)
MAX_WARP_SIDE = 4000


def _order_quad(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(4)
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(diff)], pts[np.argmax(s)], pts[np.argmax(diff)]],
        dtype=np.float32,
    )


def _work_image(image: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = image.shape[:2]
    scale = DETECT_MAX_SIDE / max(h, w)
    if scale >= 0.999:
        return image, 1.0
    small = cv2.resize(image, (max(int(w * scale), 1), max(int(h * scale), 1)), interpolation=cv2.INTER_AREA)
    return small, scale


def _morph_close_open(mask: np.ndarray, close: int = 15, open_: int = 7) -> np.ndarray:
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (close, close)), iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (open_, open_)))
    return mask


def _bright_mask(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (11, 11), 0)
    thr = max(int(np.percentile(blur, 35)), 90)
    return _morph_close_open((blur >= thr).astype(np.uint8) * 255)


def _pale_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    v_thr = max(int(np.percentile(val, 40)), 130)
    s_thr = min(int(np.percentile(sat, 40)), 70)
    return _morph_close_open(((val >= v_thr) & (sat <= s_thr)).astype(np.uint8) * 255)


def _quad_from_contour(contour: np.ndarray, image_area: int) -> np.ndarray | None:
    hull = cv2.convexHull(contour)
    peri = cv2.arcLength(hull, True)
    for eps in (0.02, 0.03, 0.04, 0.06):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2)
    area = cv2.contourArea(hull) / max(image_area, 1)
    if MIN_AREA <= area <= MAX_AREA:
        return cv2.boxPoints(cv2.minAreaRect(hull))
    return None


def _quads_from_mask(mask: np.ndarray, image_area: int, top_n: int = 3) -> list[tuple[np.ndarray, float]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:top_n]
    found: list[tuple[np.ndarray, float]] = []
    for contour in contours:
        area_frac = float(cv2.contourArea(contour) / max(image_area, 1))
        if area_frac < MIN_AREA or area_frac > MAX_AREA:
            continue
        quad = _quad_from_contour(contour, image_area)
        if quad is not None:
            found.append((quad, area_frac))
    return found


def _canny_quads(image: np.ndarray) -> list[tuple[np.ndarray, float]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 130)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    h, w = image.shape[:2]
    image_area = max(w * h, 1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    found: list[tuple[np.ndarray, float]] = []
    for contour in contours:
        area_frac = float(cv2.contourArea(contour) / image_area)
        if area_frac < MIN_AREA or area_frac > MAX_AREA:
            continue
        peri = cv2.arcLength(contour, True)
        for eps in (0.02, 0.03, 0.04):
            approx = cv2.approxPolyDP(contour, eps * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                found.append((approx.reshape(4, 2).astype(np.float32), area_frac))
                break
    return found


def _expand_quad(quad: np.ndarray, shape: tuple[int, ...], margin: float = 0.03) -> np.ndarray:
    ordered = _order_quad(quad)
    center = ordered.mean(axis=0)
    expanded = center + (ordered - center) * (1.0 + margin)
    h, w = shape[:2]
    expanded[:, 0] = np.clip(expanded[:, 0], 0, w - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, h - 1)
    return expanded.astype(np.float32)


def _largest_fill(mask: np.ndarray) -> float:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    h, w = mask.shape[:2]
    return float(cv2.contourArea(max(contours, key=cv2.contourArea)) / max(w * h, 1))


def _score_quad(image: np.ndarray, quad: np.ndarray, area_frac: float) -> dict | None:
    ordered = _order_quad(quad)
    h, w = image.shape[:2]
    width = float(np.linalg.norm(ordered[1] - ordered[0]))
    height = float(np.linalg.norm(ordered[3] - ordered[0]))
    aspect = width / max(height, 1.0)
    if aspect < 1.0:
        aspect = 1.0 / max(aspect, 1e-6)
    if not (ASPECT_RANGE[0] <= aspect <= ASPECT_RANGE[1]):
        return None
    poly = np.zeros((h, w), dtype=np.uint8)
    cv2.fillConvexPoly(poly, ordered.astype(np.int32), 255)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    inside = poly > 0
    if not np.any(inside):
        return None
    sat_in = float(hsv[:, :, 1][inside].mean())
    val_in = float(hsv[:, :, 2][inside].mean())
    aspect_score = 1.0 - min(abs(aspect - ID1_ASPECT) / 0.8, 1.0)
    pale_score = max(0.0, (55.0 - sat_in) / 55.0) * 0.6 + max(0.0, (val_in - 110.0) / 145.0) * 0.4
    area_score = 1.0 if 0.12 <= area_frac <= 0.65 else 0.35
    margin = 6
    on_border = sum(
        1
        for x, y in ordered
        if x <= margin or y <= margin or x >= w - 1 - margin or y >= h - 1 - margin
    )
    score = 0.40 * aspect_score + 0.40 * pale_score + 0.20 * area_score
    if on_border >= 3:
        score -= 0.45
    return {
        "score": round(float(score), 4),
        "quad": ordered,
        "aspect": round(aspect, 3),
        "area_frac": round(float(area_frac), 4),
        "sat_in": round(sat_in, 1),
        "val_in": round(val_in, 1),
        "on_border": on_border,
    }


def locate_card(image: np.ndarray) -> dict:
    small, scale = _work_image(image)
    h, w = small.shape[:2]
    image_area = w * h
    bright = _bright_mask(small)
    pale = _pale_mask(small)
    bright_fill = _largest_fill(bright)
    # Tight crops fill the frame. Do not treat a pale table as a filled card.
    if bright_fill >= SKIP_FILL:
        return {"ok": False, "reason": "already_filled", "area_frac": round(bright_fill, 4), "quad": None}
    candidates: list[dict] = []
    raw_quads = _quads_from_mask(bright, image_area) + _quads_from_mask(pale, image_area) + _canny_quads(small)
    for quad, area_frac in raw_quads:
        scored = _score_quad(small, quad, area_frac)
        if scored is not None:
            candidates.append(scored)
    if not candidates:
        return {"ok": False, "reason": "no_quad", "area_frac": round(bright_fill, 4), "quad": None}
    best = max(candidates, key=lambda item: (item["score"], item["area_frac"]))
    if best["score"] < 0.35:
        return {
            "ok": False,
            "reason": "low_score",
            "area_frac": best["area_frac"],
            "aspect": best["aspect"],
            "score": best["score"],
            "quad": None,
        }
    quad = _expand_quad(best["quad"], small.shape) / scale
    return {
        "ok": True,
        "reason": "warped",
        "area_frac": best["area_frac"],
        "aspect": best["aspect"],
        "score": best["score"],
        "sat_in": best["sat_in"],
        "quad": quad.astype(np.float32),
    }


def warp_size(quad: np.ndarray) -> tuple[int, int]:
    ordered = _order_quad(quad)
    width = max(float(np.linalg.norm(ordered[1] - ordered[0])), float(np.linalg.norm(ordered[2] - ordered[3])))
    height = max(float(np.linalg.norm(ordered[3] - ordered[0])), float(np.linalg.norm(ordered[2] - ordered[1])))
    width = max(width, 1.0)
    height = max(height, 1.0)
    if width / height >= ID1_ASPECT:
        out_w = width
        out_h = width / ID1_ASPECT
    else:
        out_h = height
        out_w = height * ID1_ASPECT
    scale = min(1.0, MAX_WARP_SIDE / max(out_w, out_h))
    return max(int(round(out_w * scale)), 1), max(int(round(out_h * scale)), 1)


def warp_card(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    out_w, out_h = warp_size(quad)
    dest = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(_order_quad(quad), dest)
    return cv2.warpPerspective(image, matrix, (out_w, out_h), flags=cv2.INTER_LINEAR)


def load_baseline_scores() -> dict[str, dict]:
    by_id = {}
    for name in ("summary.json", "data2_summary.json"):
        path = OUT_DIR / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("documents", []):
            by_id[row["doc_id"]] = row
    return by_id


def scored_fields(row: dict) -> dict:
    return {
        name: value
        for name, value in (row.get("fields") or {}).items()
        if isinstance(value, dict) and "scored" in value
    }


def compare_field(base: dict | None, loc: dict | None) -> str:
    if not base or not loc or not base.get("scored") or not loc.get("scored"):
        return "unscored"
    if loc.get("correct") and not base.get("correct"):
        return "gain"
    if base.get("correct") and not loc.get("correct"):
        return "regression"
    if loc.get("correct") and base.get("correct"):
        return "same_correct"
    return "same_miss"


def main() -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(MANIFEST_PATH, root=ROOT)
    baseline = load_baseline_scores()
    engine = default_engine()
    rows = []
    for doc in manifest.documents:
        print(f"localize {doc.doc_id}", flush=True)
        loaded = load_image(doc.path.read_bytes())
        if not loaded.ok:
            continue
        location = locate_card(loaded.array)
        used_warp = bool(location["ok"])
        warped = warp_card(loaded.array, location["quad"]) if used_warp else None
        image = warped if warped is not None else loaded.array
        field_names = FRONT_DOC_FIELDS if doc.side == "front" else BACK_DOC_FIELDS
        base_row = baseline.get(doc.doc_id) or {"fields": {}}
        warp_wh = list(image.shape[1::-1]) if used_warp else None
        if used_warp:
            overlay = loaded.array.copy()
            cv2.polylines(overlay, [location["quad"].astype(int)], True, (0, 255, 0), 6)
            cv2.imwrite(str(DEBUG_DIR / f"{doc.doc_id}_quad.jpg"), overlay)
            cv2.imwrite(str(DEBUG_DIR / f"{doc.doc_id}_warp.jpg"), image)
            if doc.side == "front":
                scored = score_front_document(extract_front_document(image, engine), doc.gold)
            else:
                scored = score_back_document(extract_back_document(image, engine), doc.gold)
        else:
            scored = dict(base_row.get("fields") or {})
        loc_row = {"fields": scored}
        deltas = {
            name: compare_field(base_row.get("fields", {}).get(name), scored.get(name))
            for name in field_names
        }
        cohort = "data2" if "data2" in str(doc.path).replace("\\", "/") else "data1"
        rows.append(
            {
                "doc_id": doc.doc_id,
                "side": doc.side,
                "cohort": cohort,
                "used_warp": used_warp,
                "locate_reason": location.get("reason"),
                "area_frac": location.get("area_frac"),
                "aspect": location.get("aspect"),
                "warp_size": warp_wh,
                "src_size": [loaded.array.shape[1], loaded.array.shape[0]],
                "baseline": scored_fields(base_row),
                "localized": scored_fields(loc_row),
                "delta": deltas,
            }
        )

    def subset(pred) -> list[dict]:
        return [row for row in rows if pred(row)]

    def field_tally(items: list[dict], key: str, names: tuple[str, ...]) -> dict:
        out = {}
        for name in names:
            fake = [{"fields": row[key]} for row in items if name in row[key]]
            if fake:
                out[name] = tally_fields(fake, name)
        return out

    def delta_counts(items: list[dict], names: tuple[str, ...]) -> dict:
        out = {}
        for name in names:
            counts = {"gain": 0, "regression": 0, "same_correct": 0, "same_miss": 0, "unscored": 0}
            for row in items:
                counts[row["delta"].get(name, "unscored")] += 1
            out[name] = counts
        return out

    fronts = subset(lambda row: row["side"] == "front")
    data1_fronts = subset(lambda row: row["side"] == "front" and row["cohort"] == "data1")
    data2_fronts = subset(lambda row: row["side"] == "front" and row["cohort"] == "data2")
    data1_backs = subset(lambda row: row["side"] == "back" and row["cohort"] == "data1")
    data2_backs = subset(lambda row: row["side"] == "back" and row["cohort"] == "data2")

    def movement(items: list[dict], names: tuple[str, ...]) -> dict:
        out = {name: {"gain": [], "regression": []} for name in names}
        for row in items:
            for name in names:
                kind = row["delta"].get(name)
                if kind in {"gain", "regression"}:
                    out[name][kind].append(row["doc_id"])
        return out

    public = {
        "note": "Experiment only. Native-resolution ID-1 warp vs current pipeline. Production unchanged.",
        "warp_mode": "native_id1",
        "n_documents": len(rows),
        "warped": sum(int(row["used_warp"]) for row in rows),
        "skipped": sum(int(not row["used_warp"]) for row in rows),
        "locate_reasons": {},
        "data1_backs_localized": field_tally(data1_backs, "localized", BACK_DOC_FIELDS),
        "data1_backs_baseline": field_tally(data1_backs, "baseline", BACK_DOC_FIELDS),
        "data1_backs_delta": delta_counts(data1_backs, BACK_DOC_FIELDS),
        "data2_backs_localized": field_tally(data2_backs, "localized", BACK_DOC_FIELDS),
        "data2_backs_baseline": field_tally(data2_backs, "baseline", BACK_DOC_FIELDS),
        "data2_backs_delta": delta_counts(data2_backs, BACK_DOC_FIELDS),
        "data1_fronts_delta": delta_counts(data1_fronts, FRONT_DOC_FIELDS),
        "data2_fronts_delta": delta_counts(data2_fronts, FRONT_DOC_FIELDS),
        "fronts_delta": delta_counts(fronts, FRONT_DOC_FIELDS),
        "focus": {
            "issue_date": {
                "data1": delta_counts(data1_backs, ("issue_date",)),
                "data2": delta_counts(data2_backs, ("issue_date",)),
            },
            "gender_printed": {
                "data1": delta_counts(data1_backs, ("gender_printed",)),
                "data2": delta_counts(data2_backs, ("gender_printed",)),
            },
            "national_id": {
                "data1": delta_counts(data1_backs, ("national_id",)),
                "data2": delta_counts(data2_backs, ("national_id",)),
            },
            "expiry_date": {
                "data1": delta_counts(data1_backs, ("expiry_date",)),
                "data2": delta_counts(data2_backs, ("expiry_date",)),
            },
        },
        "movements": {
            "data1_backs": movement(data1_backs, BACK_DOC_FIELDS),
            "data2_backs": movement(data2_backs, BACK_DOC_FIELDS),
            "data1_fronts": movement(data1_fronts, FRONT_DOC_FIELDS),
            "data2_fronts": movement(data2_fronts, FRONT_DOC_FIELDS),
        },
        "documents": rows,
    }
    for row in rows:
        public["locate_reasons"][row["locate_reason"]] = public["locate_reasons"].get(row["locate_reason"], 0) + 1
    COMPARE_PATH.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    printable = {k: v for k, v in public.items() if k != "documents"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
