"""Parser-only tests for synthetic back-field text.

The 42 canvases are not an OCR or Document Intelligence eval set. This script
injects the layout strings into the frozen parsers. Images may still be
generated, but they are not scored with RapidOCR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from egyid.extract import extract_back  # noqa: E402
from egyid.ocr import OcrLine  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "synthetic_eval"
IMAGE_DIR = OUT_DIR / "images"
EASTERN_MAP = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
SEED = 42
WIDTH, HEIGHT = 960, 600
FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path(r"C:\Windows\Fonts\tahoma.ttf"),
]
DATE_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\tahoma.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
]


def _font_from(candidates: list[Path], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _font(size: int) -> ImageFont.FreeTypeFont:
    return _font_from(FONT_CANDIDATES, size)


def _date_font(size: int) -> ImageFont.FreeTypeFont:
    return _font_from(DATE_FONT_CANDIDATES, size)


# Presentation forms + bidi. Logical unshaped text does not join in Pillow.
SHAPED = {
    "ذكر": "ﺫﻛﺮ",
    "أنثى": "ﺃﻧﺜﻰ",
    "انثى": "ﺍﻧﺜﻰ",
    "مسلم": "ﻣﺴﻠﻢ",
    "مسلمة": "ﻣﺴﻠﻤﺔ",
    "مسيحي": "ﻣﺴﻴﺤﻲ",
    "مسيحية": "ﻣﺴﻴﺤﻴﺔ",
    "يهودية": "ﻳﻬﻮﺩﻳﺔ",
    "متزوج": "ﻣﺘﺰﻭﺝ",
    "متزوجة": "ﻣﺘﺰﻭﺟﺔ",
    "أعزب": "ﺃﻋﺰﺏ",
    "عزباء": "ﻋﺰﺑﺎﺀ",
    "مطلق": "ﻣﻄﻠﻖ",
    "مطلقة": "ﻣﻄﻠﻘﺔ",
    "أرمل": "ﺃﺭﻣﻞ",
    "أرملة": "ﺃﺭﻣﻠﺔ",
    "البطاقة سارية حتى": "ﺍﻟﺒﻄﺎﻗﺔ ﺳﺎﺭﻳﺔ ﺣﺘﻰ",
}


def _rtl(text: str) -> str:
    visual = SHAPED.get(text, text)
    try:
        from bidi.algorithm import get_display

        return get_display(visual)
    except Exception:
        return visual


def to_script(text: str, script: str) -> str:
    return text.translate(EASTERN_MAP) if script == "eastern" else text


def format_date(iso: str, script: str, pattern: str) -> str:
    year, month, day = iso.split("-")
    year, month, day = to_script(year, script), to_script(month, script), to_script(day, script)
    one = "١" if script == "eastern" else "1"
    if pattern == "slashed":
        return f"{year}/{month}/{day}"
    if pattern == "compact8":
        return f"{year}{month}{day}"
    if pattern == "slash1":
        return f"{year}{one}{month}{one}{day}"
    if pattern == "digits7":
        return to_script("2028113", script)
    if pattern == "digits9":
        return to_script("202810319", script)
    return f"{year}/{month}/{day}"


def bases() -> list[dict]:
    return [
        {"date_iso": "2028-10-31", "valid": True, "pattern": "slashed", "script": "eastern", "phrase": True, "gender": ("ذكر", "male"), "religion": "مسلم", "marital": ("متزوج", "married")},
        {"date_iso": "2030-11-19", "valid": True, "pattern": "slashed", "script": "western", "phrase": True, "gender": ("أنثى", "female"), "religion": "مسلمة", "marital": ("عزباء", "single")},
        {"date_iso": "2029-10-02", "valid": True, "pattern": "compact8", "script": "eastern", "phrase": True, "gender": ("ذكر", "male"), "religion": "مسلم", "marital": ("أعزب", "single")},
        {"date_iso": "2032-01-27", "valid": True, "pattern": "compact8", "script": "western", "phrase": True, "gender": ("ذكر", "male"), "religion": "مسيحي", "marital": ("متزوج", "married")},
        {"date_iso": "2027-12-20", "valid": True, "pattern": "slash1", "script": "eastern", "phrase": True, "gender": ("أنثى", "female"), "religion": "مسلمة", "marital": ("متزوجة", "married")},
        {"date_iso": "2031-08-24", "valid": True, "pattern": "slash1", "script": "western", "phrase": True, "gender": ("ذكر", "male"), "religion": "مسلم", "marital": ("مطلق", "divorced")},
        {"date_iso": "2026-05-28", "valid": True, "pattern": "slashed", "script": "eastern", "phrase": True, "spaces": True, "gender": ("ذكر", "male"), "religion": "مسلم", "marital": ("أرمل", "widowed")},
        {"date_iso": "2028-06-15", "valid": True, "pattern": "slashed", "script": "western", "phrase": False, "gender": ("أنثى", "female"), "religion": "مسيحية", "marital": ("مطلقة", "divorced")},
        {"date_iso": "2021-02-30", "valid": False, "pattern": "slashed", "script": "eastern", "phrase": True, "gender": ("ذكر", "male"), "religion": "مسلم", "marital": ("متزوج", "married")},
        {"date_iso": "2032-03-25", "valid": False, "pattern": "digits7", "script": "eastern", "phrase": True, "gender": ("أنثى", "female"), "religion": "مسلمة", "marital": ("عزباء", "single")},
        {"date_iso": "2030-12-16", "valid": False, "pattern": "digits9", "script": "western", "phrase": True, "gender": ("ذكر", "male"), "religion": "مسيحي", "marital": ("أعزب", "single")},
        {"date_iso": None, "valid": False, "pattern": "none", "script": "eastern", "phrase": True, "gender": ("ذكر", "male"), "religion": "مسلم", "marital": ("متزوج", "married")},
        {"date_iso": "2029-10-02", "valid": True, "pattern": "slashed", "script": "eastern", "phrase": True, "gender": ("انثى", "female"), "religion": "يهودية", "marital": ("أرملة", "widowed")},
        {"date_iso": "2027-12-20", "valid": True, "pattern": "compact8", "script": "eastern", "phrase": False, "gender": ("ذكر", "male"), "religion": "مسلم", "marital": ("متزوج", "married")},
    ]


def perturbations() -> list[dict]:
    return [
        {"name": "clean", "jpeg": None, "brightness": 1.0, "scale": 1.0, "shift": (0.0, 0.0)},
        {"name": "jpeg_dark", "jpeg": 35, "brightness": 0.72, "scale": 1.0, "shift": (0.0, 0.0)},
        {"name": "shift_resize", "jpeg": None, "brightness": 1.0, "scale": 0.68, "shift": (0.05, -0.04)},
        {"name": "jpeg_bright_shift", "jpeg": 28, "brightness": 1.28, "scale": 1.15, "shift": (-0.04, 0.03)},
    ]


def render_canvas(spec: dict, shift: tuple[float, float]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (236, 232, 220))
    draw = ImageDraw.Draw(image)
    draw.rectangle([18, 18, WIDTH - 18, HEIGHT - 18], outline=(170, 170, 170), width=2)
    draw.line([(36, int(HEIGHT * 0.43)), (WIDTH - 36, int(HEIGHT * 0.43))], fill=(190, 190, 190), width=1)
    draw.text((40, 28), "synthetic form  (not an ID card)", font=_font(16), fill=(160, 160, 160))

    dx, dy = shift
    attr_font = _font(40)
    date_font = _date_font(44)
    phrase_font = _font(34)

    def xy(nx: float, ny: float) -> tuple[int, int]:
        return int((nx + dx) * WIDTH), int((ny + dy) * HEIGHT)

    draw.text(xy(0.36, 0.34), _rtl(spec["marital"][0]), font=attr_font, fill=(20, 20, 20), anchor="mm")
    draw.text(xy(0.60, 0.34), _rtl(spec["religion"]), font=attr_font, fill=(20, 20, 20), anchor="mm")
    draw.text(xy(0.80, 0.34), _rtl(spec["gender"][0]), font=attr_font, fill=(20, 20, 20), anchor="mm")

    if spec["pattern"] != "none" and spec["date_iso"]:
        date_text = format_date(spec["date_iso"], spec["script"], spec["pattern"])
        if spec.get("spaces"):
            date_text = f"{date_text[:4]} / {date_text[5:7]} / {date_text[8:]}" if "/" in date_text else date_text
        draw.text(xy(0.30, 0.51), date_text, font=date_font, fill=(15, 15, 15), anchor="mm")
    if spec.get("phrase"):
        draw.text(xy(0.66, 0.51), _rtl("البطاقة سارية حتى"), font=phrase_font, fill=(15, 15, 15), anchor="mm")
    return image


def apply_image_ops(image: Image.Image, spec: dict) -> np.ndarray:
    array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    if spec["brightness"] != 1.0:
        array = np.clip(array.astype(np.float32) * spec["brightness"], 0, 255).astype(np.uint8)
    if spec["scale"] != 1.0:
        h, w = array.shape[:2]
        array = cv2.resize(array, (max(80, int(w * spec["scale"])), max(80, int(h * spec["scale"]))), interpolation=cv2.INTER_AREA)
    if spec["jpeg"] is not None:
        ok, encoded = cv2.imencode(".jpg", array, [int(cv2.IMWRITE_JPEG_QUALITY), spec["jpeg"]])
        if ok:
            array = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    return array


def score_field(status: str, value: str | None, gold: str | None, *, expect_value: bool) -> dict:
    detected = status == "present"
    if expect_value:
        return {
            "correct": detected and value == gold,
            "incorrect": detected and value != gold,
            "unreadable": status == "unreadable",
            "absent": status == "absent",
        }
    return {
        "correct": status in {"unreadable", "absent"},
        "incorrect": detected,
        "unreadable": status == "unreadable",
        "absent": status == "absent",
    }


def tally(rows: list[dict], name: str) -> dict:
    keys = ("correct", "incorrect", "unreadable", "absent")
    out = {key: 0 for key in keys}
    out["n"] = len(rows)
    out["exact_match_accuracy"] = 0.0
    for row in rows:
        for key in keys:
            out[key] += int(row[name][key])
    if rows:
        out["exact_match_accuracy"] = round(out["correct"] / len(rows), 4)
    return out


def generate() -> list[dict]:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    samples = []
    idx = 0
    all_perturbs = perturbations()
    for base_i, base in enumerate(bases(), start=1):
        # 14 layouts x 3 perturbations = 42 images. Rotate the dropped perturbation.
        for pi, perturb in enumerate(all_perturbs):
            if pi == (base_i - 1) % len(all_perturbs):
                continue
            idx += 1
            sample_id = f"syn_{idx:03d}"
            image = render_canvas(base, perturb["shift"])
            array = apply_image_ops(image, perturb)
            path = IMAGE_DIR / f"{sample_id}.jpg"
            cv2.imwrite(str(path), array)
            samples.append(
                {
                    "sample_id": sample_id,
                    "image": str(path.relative_to(OUT_DIR)).replace("\\", "/"),
                    "base_id": f"base_{base_i:02d}",
                    "perturbation": perturb["name"],
                    "script": base["script"],
                    "date_pattern": base["pattern"],
                    "expiry_valid": base["valid"] and base["pattern"] not in {"digits7", "digits9", "none"},
                    "expiry_gold": base["date_iso"] if base["valid"] and base["pattern"] not in {"digits7", "digits9", "none"} else None,
                    "gender_gold": base["gender"][0],
                    "religion_gold": base["religion"],
                    "marital_gold": base["marital"][0],
                    "has_phrase": bool(base.get("phrase")),
                }
            )
    return samples


def parser_lines_from_spec(spec: dict) -> list[OcrLine]:
    """Inject synthetic strings. No OCR and no images."""
    lines = [
        OcrLine(spec["marital"][0], 0.99, 0.28, 0.30, 0.44, 0.38),
        OcrLine(spec["religion"], 0.99, 0.52, 0.30, 0.68, 0.38),
        OcrLine(spec["gender"][0], 0.99, 0.72, 0.30, 0.88, 0.38),
    ]
    date_text = None
    if spec["pattern"] != "none" and spec["date_iso"]:
        date_text = format_date(spec["date_iso"], spec["script"], spec["pattern"])
        if spec.get("spaces") and "/" in date_text:
            date_text = f"{date_text[:4]} / {date_text[5:7]} / {date_text[8:]}"
    if spec.get("phrase") and date_text:
        lines.append(OcrLine(f"{date_text} البطاقة سارية حتى", 0.99, 0.20, 0.48, 0.82, 0.56))
    elif date_text:
        lines.append(OcrLine(date_text, 0.99, 0.20, 0.48, 0.42, 0.56))
    elif spec.get("phrase"):
        lines.append(OcrLine("البطاقة سارية حتى", 0.99, 0.40, 0.48, 0.82, 0.56))
    return lines


def evaluate_parsers() -> dict:
    rows = []
    for idx, spec in enumerate(bases(), start=1):
        valid = spec["valid"] and spec["pattern"] not in {"digits7", "digits9", "none"}
        expiry_gold = spec["date_iso"] if valid else None
        parsed = extract_back(parser_lines_from_spec(spec), barcode_found=False, barcode_score=0.0)
        scored = {
            "sample_id": f"parser_{idx:02d}",
            "script": spec["script"],
            "date_pattern": spec["pattern"],
            "expiry_valid": valid,
            "pred": {
                "expiry": {"status": parsed.expiry_date.status.value, "value": parsed.expiry_date.value},
                "gender": {"status": parsed.gender_printed.status.value, "value": parsed.gender_printed.value},
                "religion": {"status": parsed.religion_printed.status.value, "value": parsed.religion_printed.value},
                "marital": {"status": parsed.marital_status_ar.status.value, "value": parsed.marital_status_ar.value},
            },
            "gold": {
                "expiry": expiry_gold,
                "gender": spec["gender"][0],
                "religion": spec["religion"],
                "marital": spec["marital"][0],
            },
            "expiry": score_field(parsed.expiry_date.status.value, parsed.expiry_date.value, expiry_gold, expect_value=valid),
            "gender": score_field(parsed.gender_printed.status.value, parsed.gender_printed.value, spec["gender"][0], expect_value=True),
            "religion": score_field(parsed.religion_printed.status.value, parsed.religion_printed.value, spec["religion"], expect_value=True),
            "marital": score_field(parsed.marital_status_ar.status.value, parsed.marital_status_ar.value, spec["marital"][0], expect_value=True),
        }
        rows.append(scored)

    valid_expiry = [row for row in rows if row["expiry_valid"]]
    invalid_expiry = [row for row in rows if not row["expiry_valid"]]
    return {
        "n_samples": len(rows),
        "note": "Parser-only synthetic strings. Not an OCR or Document Intelligence eval. Separate from artifacts/doc_eval and artifacts/case_eval.",
        "expiry_valid": tally(valid_expiry, "expiry"),
        "expiry_invalid_correctly_rejected_or_false": {
            **tally(invalid_expiry, "expiry"),
            "false_dates": tally(invalid_expiry, "expiry")["incorrect"],
            "correct_reject": tally(invalid_expiry, "expiry")["correct"],
        },
        "gender": tally(rows, "gender"),
        "religion": tally(rows, "religion"),
        "marital": tally(rows, "marital"),
        "per_sample": rows,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = evaluate_parsers()
    (OUT_DIR / "parser_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    public = {k: v for k, v in summary.items() if k != "per_sample"}
    print(json.dumps(public, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
