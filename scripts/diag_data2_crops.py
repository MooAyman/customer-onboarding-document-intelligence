"""One-off framing vs crop diagnostic. Does not change the pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from egyid.eval import load_manifest  # noqa: E402
from egyid.extract import (  # noqa: E402
    GENDER_MAP,
    MONTH_DATE_RE,
    extract_issue_date,
    normalize_digits,
)
from egyid.imaging import GENDER_CROP, crop_normalized, load_image  # noqa: E402
from egyid.ocr import default_engine  # noqa: E402

OUT = ROOT / "artifacts" / "doc_eval" / "data2_crop_diag.json"
ISSUE_BOX = (0.0, 0.0, 0.50, 0.28)


def card_bbox_frac(image: np.ndarray) -> dict:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = gray.shape[:2]
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"area_frac": 1.0, "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}
    x, y, bw, bh = cv2.boundingRect(max(contours, key=cv2.contourArea))
    return {
        "area_frac": round((bw * bh) / max(w * h, 1), 4),
        "x1": round(x / w, 3),
        "y1": round(y / h, 3),
        "x2": round((x + bw) / w, 3),
        "y2": round((y + bh) / h, 3),
    }


def in_box(cx: float, cy: float, box: tuple[float, float, float, float]) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= cx <= x2 and y1 <= cy <= y2


def classify_line(text: str) -> str | None:
    norm = normalize_digits(text)
    if any(tok in text for tok in GENDER_MAP):
        return "gender"
    if MONTH_DATE_RE.search(norm):
        return "month_date"
    if "سارية" in text or "حتى" in text:
        return "expiry_phrase"
    return None


def inspect(path: Path, engine) -> dict:
    loaded = load_image(path.read_bytes())
    image = loaded.array
    h, w = image.shape[:2]
    card = card_bbox_frac(image)
    lines = engine.run(image)
    gender_crop_lines = engine.run(crop_normalized(image, GENDER_CROP))
    if lines:
        xs = [ln.x1 for ln in lines] + [ln.x2 for ln in lines]
        ys = [ln.y1 for ln in lines] + [ln.y2 for ln in lines]
        text_box = {
            "x1": round(min(xs), 3),
            "y1": round(min(ys), 3),
            "x2": round(max(xs), 3),
            "y2": round(max(ys), 3),
            "area_frac": round(max(max(xs) - min(xs), 0) * max(max(ys) - min(ys), 0), 4),
        }
    else:
        text_box = {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "area_frac": 1.0}
    issue_filtered = [ln for ln in lines if ln.cy < 0.28 and ln.cx < 0.50]
    issue_all = extract_issue_date(lines)
    issue_window = extract_issue_date(issue_filtered)
    hits = []
    for line in lines:
        kind = classify_line(line.text)
        if not kind:
            continue
        hits.append(
            {
                "kind": kind,
                "cx": round(line.cx, 3),
                "cy": round(line.cy, 3),
                "in_issue_window": in_box(line.cx, line.cy, ISSUE_BOX),
                "in_gender_crop": in_box(line.cx, line.cy, GENDER_CROP),
                "on_text_bbox": in_box(
                    line.cx, line.cy, (text_box["x1"], text_box["y1"], text_box["x2"], text_box["y2"])
                ),
            }
        )
    return {
        "size": [w, h],
        "aspect": round(w / max(h, 1), 3),
        "card_edge_bbox": card,
        "ocr_text_bbox": text_box,
        "n_ocr_lines": len(lines),
        "hits": hits,
        "issue_from_all_lines": issue_all.status.value,
        "issue_from_cy028_cx050": issue_window.status.value,
        "gender_crop_line_count": len(gender_crop_lines),
        "gender_crop_has_token": any(tok in ln.text for ln in gender_crop_lines for tok in GENDER_MAP),
    }


def main() -> None:
    manifest = load_manifest(ROOT / "artifacts" / "eval_manifest" / "manifest.json", root=ROOT)
    engine = default_engine()
    data1 = [doc for doc in manifest.back_docs() if "data1" in str(doc.path)][:4]
    data2 = [doc for doc in manifest.back_docs() if "data2" in str(doc.path)]
    rows = []
    for doc in data1 + data2:
        print(doc.doc_id, flush=True)
        cohort = "data2" if "data2" in str(doc.path) else "data1"
        rows.append({"doc_id": doc.doc_id, "cohort": cohort, **inspect(doc.path, engine)})
    summary = {
        "note": "Framing vs frozen crops. No PII. Does not modify pipeline.",
        "issue_window": {"cx_lt": 0.50, "cy_lt": 0.28, "has_crop": False},
        "gender_crop": GENDER_CROP,
        "by_cohort": {},
        "samples": rows,
    }
    for cohort in ("data1", "data2"):
        subset = [row for row in rows if row["cohort"] == cohort]
        month = [h for row in subset for h in row["hits"] if h["kind"] == "month_date"]
        gender = [h for row in subset for h in row["hits"] if h["kind"] == "gender"]
        summary["by_cohort"][cohort] = {
            "n": len(subset),
            "mean_edge_bbox_area": round(sum(row["card_edge_bbox"]["area_frac"] for row in subset) / max(len(subset), 1), 4),
            "mean_ocr_text_area": round(sum(row["ocr_text_bbox"]["area_frac"] for row in subset) / max(len(subset), 1), 4),
            "mean_aspect": round(sum(row["aspect"] for row in subset) / max(len(subset), 1), 3),
            "mean_text_cy_min": round(sum(row["ocr_text_bbox"]["y1"] for row in subset) / max(len(subset), 1), 3),
            "month_dates": len(month),
            "month_dates_in_issue_window": sum(int(h["in_issue_window"]) for h in month),
            "issue_present_all_lines": sum(int(row["issue_from_all_lines"] == "present") for row in subset),
            "issue_present_window": sum(int(row["issue_from_cy028_cx050"] == "present") for row in subset),
            "gender_hits": len(gender),
            "gender_hits_in_crop": sum(int(h["in_gender_crop"]) for h in gender),
            "gender_crop_token_images": sum(int(row["gender_crop_has_token"]) for row in subset),
        }
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["by_cohort"], indent=2))


if __name__ == "__main__":
    main()
