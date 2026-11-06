"""Controlled OCR experiments on the same 12 split ID samples.

Baseline is artifacts/ocr_eval/raw_ocr.json (original images, original extractor).
Does not modify source images. Writes gitignored artifacts only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_split_ocr import SPLIT_DIR, OUT_DIR, discover_pairs  # noqa: E402
from egyid.extract import SERIAL_RE, extract_back, extract_front  # noqa: E402
from egyid.imaging import (  # noqa: E402
    GLARE_RATIO_THRESHOLD,
    barcode_present,
    highlight_ratio,
    load_image,
    specular_glare_ratio,
)
from egyid.nid import digits_only
from egyid.ocr import OcrLine, default_engine

GOLD_PATH = OUT_DIR / "gold.json"
BASELINE_PATH = OUT_DIR / "raw_ocr.json"
SERIAL_CROP = (0.0, 0.78, 0.48, 1.0)
OLD_GLARE_THRESHOLD = 0.08
LATIN_SERIAL_RE = re.compile(r"[A-Z]{2}\s?\d{6,10}", re.I)


def to_lines(items: list[dict]) -> list[OcrLine]:
    lines: list[OcrLine] = []
    for item in items:
        cx, cy = float(item["cx"]), float(item["cy"])
        lines.append(
            OcrLine(
                text=item["text"],
                score=float(item["score"]),
                x1=max(cx - 0.08, 0.0),
                y1=max(cy - 0.04, 0.0),
                x2=min(cx + 0.08, 1.0),
                y2=min(cy + 0.04, 1.0),
            )
        )
    return lines


def enhance_2x_contrast(image: np.ndarray) -> np.ndarray:
    up = cv2.resize(image, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    lab = cv2.cvtColor(up, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_ch = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_ch)
    return cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]), cv2.COLOR_LAB2BGR)


def crop_normalized(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    xa, ya = int(w * x1), int(h * y1)
    xb, yb = int(w * x2), int(h * y2)
    xa, ya = max(0, xa), max(0, ya)
    xb, yb = min(w, max(xa + 1, xb)), min(h, max(ya + 1, yb))
    return image[ya:yb, xa:xb]


def latin_engine():
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    return RapidOCR(
        params={
            "Rec.lang_type": LangRec.EN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
        }
    )


def ocr_to_lines(engine, image: np.ndarray) -> list[OcrLine]:
    if hasattr(engine, "run"):
        return engine.run(image)
    result = engine(image)
    if result is None:
        return []
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or txts is None:
        return []
    h, w = image.shape[:2]
    lines: list[OcrLine] = []
    for box, text, score in zip(boxes, txts, scores or [1.0] * len(txts)):
        pts = np.asarray(box, dtype=float).reshape(-1, 2)
        xs, ys = pts[:, 0], pts[:, 1]
        text = str(text).strip()
        if not text:
            continue
        lines.append(
            OcrLine(
                text=text,
                score=float(score),
                x1=float(xs.min() / w),
                y1=float(ys.min() / h),
                x2=float(xs.max() / w),
                y2=float(ys.max() / h),
            )
        )
    return lines


def pick_serial(lines: list[OcrLine]) -> str | None:
    hits: list[tuple[float, str]] = []
    for line in lines:
        match = LATIN_SERIAL_RE.search(line.text.upper().replace(" ", ""))
        if not match:
            match = LATIN_SERIAL_RE.search(line.text.upper())
        if match:
            hits.append((line.score, match.group(0).replace(" ", "").upper()))
    if not hits:
        return None
    preferred = [item for item in hits if re.fullmatch(r"[A-Z]{2}\d{7}", item[1])]
    pool = preferred or hits
    return max(pool, key=lambda item: item[0])[1]


def field_view(field) -> dict:
    if isinstance(field, dict):
        return {
            "status": field.get("status") or "absent",
            "value": field.get("value"),
        }
    return {"status": field.status.value, "value": field.value}


def score_row(views: dict[str, dict], gold: dict, dob_readable: bool) -> dict[str, dict]:
    mapping = {
        "card_serial": gold["serial"],
        "front_national_id": gold["nid"],
        "back_national_id": gold["nid"],
        "issue_date": gold["issue"],
        "expiry_date": gold["expiry"],
        "gender_printed": gold["gender"],
        "religion_printed": gold["religion"],
        "marital_status_ar": gold["marital"],
    }
    out: dict[str, dict] = {}
    for name, expected in mapping.items():
        view = views[name]
        status = view["status"]
        value = view["value"]
        out[name] = {
            "detected": status == "present",
            "correct": status == "present" and value == expected,
            "unreadable": status == "unreadable",
            "absent": status == "absent",
        }
    dob = views["date_of_birth_printed"]
    if dob_readable and gold.get("dob"):
        out["date_of_birth_printed"] = {
            "detected": dob["status"] == "present",
            "correct": dob["status"] == "present" and dob["value"] == gold["dob"],
            "unreadable": dob["status"] == "unreadable",
            "absent": dob["status"] == "absent",
        }
    else:
        out["date_of_birth_printed_unreadable_set"] = {
            "detected": dob["status"] == "present",
            "correct": dob["status"] in {"unreadable", "absent"},
            "unreadable": dob["status"] == "unreadable",
            "absent": dob["status"] == "absent",
            "false_present": dob["status"] == "present",
        }
    return out


def aggregate(rows: list[dict], key: str) -> dict:
    detected = correct = unreadable = absent = 0
    n = 0
    for row in rows:
        if key not in row:
            continue
        n += 1
        item = row[key]
        detected += int(item["detected"])
        correct += int(item["correct"])
        unreadable += int(item["unreadable"])
        absent += int(item.get("absent", False))
    return {
        "n": n,
        "detected": detected,
        "correct": correct,
        "unreadable": unreadable,
        "absent": absent,
    }


def extract_views(front_lines, back_lines, back_image) -> dict:
    barcode_ok, barcode_score = barcode_present(back_image)
    front = extract_front(front_lines)
    back = extract_back(back_lines, barcode_ok, barcode_score)
    return {
        "date_of_birth_printed": field_view(front.date_of_birth_printed),
        "front_national_id": field_view(front.national_id),
        "card_serial": field_view(front.card_serial),
        "back_national_id": field_view(back.national_id),
        "issue_date": field_view(back.issue_date),
        "expiry_date": field_view(back.expiry_date),
        "gender_printed": field_view(back.gender_printed),
        "religion_printed": field_view(back.religion_printed),
        "marital_status_ar": field_view(back.marital_status_ar),
    }


def serial_diagnosis(front_lines: list[OcrLine], predicted: str | None, gold_serial: str) -> dict:
    region = [ln for ln in front_lines if ln.cy > 0.75 and ln.cx < 0.50]
    region_shapes = []
    for ln in region:
        text = ln.text.upper()
        region_shapes.append(
            {
                "len": len(text.replace(" ", "")),
                "digits": len(digits_only(ln.text)),
                "has_latin": bool(re.search(r"[A-Za-z]", ln.text)),
                "serial_pattern": bool(SERIAL_RE.search(text.replace(" ", ""))),
            }
        )
    exact = predicted == gold_serial
    if not region:
        cause = "detection_miss"
    elif exact:
        extra = [item for item in region_shapes if not item["serial_pattern"]]
        cause = "ok_extra_non_serial_lines" if extra else "ok"
    elif any(item["serial_pattern"] for item in region_shapes):
        cause = "recognition_mismatch"
    else:
        cause = "detection_miss"
    return {
        "exact": exact,
        "predicted_len": len(predicted or ""),
        "gold_len": len(gold_serial),
        "region_line_count": len(region),
        "region_shapes": region_shapes,
        "cause": cause,
    }


def baseline_views(extracted: dict) -> dict:
    front = extracted["front"]
    back = extracted["back"]
    return {
        "date_of_birth_printed": field_view(front["date_of_birth_printed"]),
        "front_national_id": field_view(front["national_id"]),
        "card_serial": field_view(front["card_serial"]),
        "back_national_id": field_view(back["national_id"]),
        "issue_date": field_view(back["issue_date"]),
        "expiry_date": field_view(back["expiry_date"]),
        "gender_printed": field_view(back["gender_printed"]),
        "religion_printed": field_view(back["religion_printed"]),
        "marital_status_ar": field_view(back["marital_status_ar"]),
    }


def main() -> None:
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["samples"]
    raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    raw_by_id = {item["sample_id"]: item for item in raw}
    pairs, _unmatched = discover_pairs(SPLIT_DIR)

    arabic = default_engine()
    try:
        latin = latin_engine()
        latin_error = None
    except Exception as exc:  # noqa: BLE001
        latin = None
        latin_error = type(exc).__name__

    baseline_rows = []
    dates_rows = []
    enhance_rows = []
    latin_rows = []
    serial_rows = []
    glare_rows = []
    private = []

    for pair in pairs:
        sample_id = pair["sample_id"]
        print(f"experiment sample {sample_id}", flush=True)
        item = raw_by_id[sample_id]
        g = gold[sample_id]
        front_path, back_path = pair["front"], pair["back"]
        front_loaded = load_image(front_path.read_bytes())
        back_loaded = load_image(back_path.read_bytes())
        front_img, back_img = front_loaded.array, back_loaded.array
        orig_front_lines = to_lines(item["front_lines"])
        orig_back_lines = to_lines(item["back_lines"])

        base_views = baseline_views(item["extracted"])
        date_views = extract_views(orig_front_lines, orig_back_lines, back_img)

        enhanced_front = enhance_2x_contrast(front_img)
        enhanced_back = enhance_2x_contrast(back_img)
        enh_front_lines = ocr_to_lines(arabic, enhanced_front)
        enh_back_lines = ocr_to_lines(arabic, enhanced_back)
        enh_views = extract_views(enh_front_lines, enh_back_lines, back_img)

        latin_serial = None
        latin_line_count = 0
        if latin is not None:
            crop = crop_normalized(front_img, SERIAL_CROP)
            latin_lines = ocr_to_lines(latin, crop)
            latin_line_count = len(latin_lines)
            latin_serial = pick_serial(latin_lines)
        latin_view = dict(date_views)
        if latin_serial:
            latin_view["card_serial"] = {"status": "present", "value": latin_serial}

        base_score = score_row(base_views, g, g["dob_readable"])
        date_score = score_row(date_views, g, g["dob_readable"])
        enh_score = score_row(enh_views, g, g["dob_readable"])
        latin_score = score_row(latin_view, g, g["dob_readable"])
        baseline_rows.append(base_score)
        dates_rows.append(date_score)
        enhance_rows.append(enh_score)
        latin_rows.append(latin_score)

        pred_serial = date_views["card_serial"]["value"]
        diagnosis = serial_diagnosis(orig_front_lines, pred_serial, g["serial"])
        diagnosis["latin_exact"] = latin_serial == g["serial"]
        diagnosis["latin_detected"] = latin_serial is not None
        diagnosis["latin_line_count"] = latin_line_count
        serial_rows.append(diagnosis)

        front_gray = cv2.cvtColor(front_img, cv2.COLOR_BGR2GRAY)
        back_gray = cv2.cvtColor(back_img, cv2.COLOR_BGR2GRAY)
        glare_rows.append(
            {
                "sample_id": sample_id,
                "front_old_ratio": round(highlight_ratio(front_gray), 4),
                "back_old_ratio": round(highlight_ratio(back_gray), 4),
                "front_old_flag": highlight_ratio(front_gray) >= OLD_GLARE_THRESHOLD,
                "back_old_flag": highlight_ratio(back_gray) >= OLD_GLARE_THRESHOLD,
                "front_new_ratio": round(specular_glare_ratio(front_gray), 4),
                "back_new_ratio": round(specular_glare_ratio(back_gray), 4),
                "front_new_flag": specular_glare_ratio(front_gray) >= GLARE_RATIO_THRESHOLD,
                "back_new_flag": specular_glare_ratio(back_gray) >= GLARE_RATIO_THRESHOLD,
                "front_brightness": round(float(np.mean(front_gray)), 1),
                "back_brightness": round(float(np.mean(back_gray)), 1),
            }
        )

        private.append(
            {
                "sample_id": sample_id,
                "baseline": {k: v["value"] if isinstance(v, dict) else v for k, v in base_views.items()},
                "compact_dates": {k: v["value"] for k, v in date_views.items()},
                "enhanced": {k: v["value"] for k, v in enh_views.items()},
                "latin_serial": latin_serial,
                "serial_diagnosis": diagnosis,
            }
        )

    fields = [
        "card_serial",
        "front_national_id",
        "back_national_id",
        "issue_date",
        "expiry_date",
        "date_of_birth_printed",
        "date_of_birth_printed_unreadable_set",
        "gender_printed",
        "religion_printed",
        "marital_status_ar",
    ]

    def table(rows: list[dict]) -> dict:
        return {name: aggregate(rows, name) for name in fields if any(name in row for row in rows)}

    redacted = {
        "n_samples": 12,
        "latin_engine_error": latin_error,
        "baseline": table(baseline_rows),
        "compact_dates_on_original_ocr": table(dates_rows),
        "upscale2x_clahe": table(enhance_rows),
        "latin_serial_crop": {
            "card_serial": aggregate(latin_rows, "card_serial"),
            "latin_detected": sum(int(row["latin_detected"]) for row in serial_rows),
            "latin_exact": sum(int(row["latin_exact"]) for row in serial_rows),
        },
        "serial_digit_loss": {
            "baseline_exact": sum(int(row["exact"]) for row in serial_rows),
            "causes": {
                cause: sum(int(row["cause"] == cause) for row in serial_rows)
                for cause in sorted({row["cause"] for row in serial_rows})
            },
            "region_line_counts": [row["region_line_count"] for row in serial_rows],
            "note": "Gold serials are 2 letters + 7 digits. Extra region lines are non-serial digit blobs.",
        },
        "glare": {
            "old_front_flagged": sum(int(row["front_old_flag"]) for row in glare_rows),
            "old_back_flagged": sum(int(row["back_old_flag"]) for row in glare_rows),
            "new_front_flagged": sum(int(row["front_new_flag"]) for row in glare_rows),
            "new_back_flagged": sum(int(row["back_new_flag"]) for row in glare_rows),
            "per_sample": glare_rows,
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "experiment_summary.json").write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "experiment_raw.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(redacted, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
