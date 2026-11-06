"""Field-cropped RapidOCR experiment on the original 12 split ID backs.

Baseline is current extract.py on full-image OCR (raw_ocr.json).
Crops are in-memory only. Original images are not modified.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_split_ocr import OUT_DIR, SPLIT_DIR, discover_pairs, redact_text
from egyid.extract import (
    GENDER_MAP,
    RELIGION_VALUES,
    extract_back,
    extract_expiry_date,
    extract_issue_date,
    extract_mapped_keyword,
)
from egyid.imaging import (
    GENDER_CROP,
    RELIGION_CROP,
    barcode_present,
    crop_normalized,
    load_image,
)

ISSUE_CROP = (0.10, 0.00, 0.48, 0.24)
EXPIRY_CROP = (0.12, 0.40, 0.90, 0.58)
from egyid.nid import digits_only
from egyid.ocr import OcrLine, default_engine

GOLD_PATH = OUT_DIR / "gold.json"
BASELINE_PATH = OUT_DIR / "raw_ocr.json"
FIELDS = ("issue_date", "expiry_date", "gender_printed", "religion_printed")


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


def view(field) -> dict:
    return {"status": field.status.value, "value": field.value}


def score_one(status: str, value: str | None, gold: str) -> dict:
    detected = status == "present"
    correct = detected and value == gold
    incorrect = detected and value != gold
    return {
        "detected": detected,
        "correct": correct,
        "incorrect": incorrect,
        "unreadable": status == "unreadable",
        "absent": status == "absent",
    }


def aggregate(rows: list[dict], name: str) -> dict:
    keys = ("detected", "correct", "incorrect", "unreadable", "absent")
    out = {key: 0 for key in keys}
    out["n"] = 0
    for row in rows:
        item = row[name]
        out["n"] += 1
        for key in keys:
            out[key] += int(item[key])
    return out


def line_stats(lines: list[OcrLine]) -> dict:
    texts = [ln.text for ln in lines]
    joined = " ".join(texts)
    return {
        "n_lines": len(lines),
        "digit_len": len(digits_only(joined)),
        "has_sep": bool(re.search(r"[/\-.]", joined)),
        "shapes": [redact_text(ln.text) for ln in lines],
        "mean_score": round(sum(ln.score for ln in lines) / max(len(lines), 1), 3),
    }


def date_cause(stats: dict, scored: dict, gold_digits: int) -> str:
    if scored["correct"]:
        return "ok"
    if stats["n_lines"] == 0 or stats["digit_len"] == 0:
        return "crop_or_detection"
    if scored["incorrect"]:
        return "recognition"
    if stats["digit_len"] != gold_digits:
        return "recognition"
    return "extraction"


def keyword_cause(stats: dict, scored: dict, token_hit: bool) -> str:
    if scored["correct"]:
        return "ok"
    if stats["n_lines"] == 0:
        return "crop_or_detection"
    if token_hit and scored["incorrect"]:
        return "extraction"
    if not token_hit:
        return "recognition"
    return "extraction"


def merge_field(crop: dict, full: dict) -> dict:
    if crop["status"] == "present":
        return crop
    if full["status"] == "present":
        return full
    if crop["status"] == "unreadable":
        return crop
    return full


def main() -> None:
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["samples"]
    raw = {item["sample_id"]: item for item in json.loads(BASELINE_PATH.read_text(encoding="utf-8"))}
    pairs, _ = discover_pairs(SPLIT_DIR)
    engine = default_engine()

    baseline_rows = []
    crop_rows = []
    merge_rows = []
    causes = {name: [] for name in FIELDS}
    private = []
    crop_boxes = {
        "issue_date": ISSUE_CROP,
        "expiry_date": EXPIRY_CROP,
        "gender_printed": GENDER_CROP,
        "religion_printed": RELIGION_CROP,
    }

    for pair in pairs:
        sample_id = pair["sample_id"]
        print(f"crop experiment {sample_id}", flush=True)
        g = gold[sample_id]
        item = raw[sample_id]
        back_img = load_image(pair["back"].read_bytes()).array
        full_lines = to_lines(item["back_lines"])
        barcode_ok, barcode_score = barcode_present(back_img)
        full_back = extract_back(full_lines, barcode_ok, barcode_score)

        issue_lines = engine.run(crop_normalized(back_img, ISSUE_CROP))
        expiry_lines = engine.run(crop_normalized(back_img, EXPIRY_CROP))
        gender_lines = engine.run(crop_normalized(back_img, GENDER_CROP))
        religion_lines = engine.run(crop_normalized(back_img, RELIGION_CROP))

        crop_fields = {
            "issue_date": extract_issue_date(issue_lines),
            "expiry_date": extract_expiry_date(expiry_lines),
            "gender_printed": extract_mapped_keyword(
                gender_lines, GENDER_MAP, unread_if_text=True
            ),
            "religion_printed": extract_mapped_keyword(
                religion_lines, RELIGION_VALUES, unread_if_text=True
            ),
        }
        full_fields = {
            "issue_date": full_back.issue_date,
            "expiry_date": full_back.expiry_date,
            "gender_printed": full_back.gender_printed,
            "religion_printed": full_back.religion_printed,
        }
        gold_map = {
            "issue_date": g["issue"],
            "expiry_date": g["expiry"],
            "gender_printed": g["gender"],
            "religion_printed": g["religion"],
        }
        gold_digits = {"issue_date": 6, "expiry_date": 8}

        base_score = {}
        crop_score = {}
        merge_score = {}
        sample_private = {"sample_id": sample_id, "crops": {}}
        for name in FIELDS:
            base_view = view(full_fields[name])
            crop_view = view(crop_fields[name])
            merged = merge_field(crop_view, base_view)
            base_score[name] = score_one(base_view["status"], base_view["value"], gold_map[name])
            crop_score[name] = score_one(crop_view["status"], crop_view["value"], gold_map[name])
            merge_score[name] = score_one(merged["status"], merged["value"], gold_map[name])

        issue_stats = line_stats(issue_lines)
        expiry_stats = line_stats(expiry_lines)
        gender_stats = line_stats(gender_lines)
        religion_stats = line_stats(religion_lines)
        causes["issue_date"].append(date_cause(issue_stats, crop_score["issue_date"], 6))
        causes["expiry_date"].append(date_cause(expiry_stats, crop_score["expiry_date"], 8))
        causes["gender_printed"].append(
            keyword_cause(
                gender_stats,
                crop_score["gender_printed"],
                any(tok in "".join(ln.text for ln in gender_lines) for tok in GENDER_MAP),
            )
        )
        causes["religion_printed"].append(
            keyword_cause(
                religion_stats,
                crop_score["religion_printed"],
                any(tok in "".join(ln.text for ln in religion_lines) for tok in RELIGION_VALUES),
            )
        )

        baseline_rows.append(base_score)
        crop_rows.append(crop_score)
        merge_rows.append(merge_score)
        sample_private["crops"] = {
            "issue_date": {"stats": issue_stats, **view(crop_fields["issue_date"])},
            "expiry_date": {"stats": expiry_stats, **view(crop_fields["expiry_date"])},
            "gender_printed": {"stats": gender_stats, **view(crop_fields["gender_printed"])},
            "religion_printed": {"stats": religion_stats, **view(crop_fields["religion_printed"])},
        }
        private.append(sample_private)

        print(
            sample_id,
            "issue",
            crop_score["issue_date"]["correct"],
            "expiry",
            crop_score["expiry_date"]["correct"],
            "gender",
            crop_score["gender_printed"]["correct"],
            "religion",
            crop_score["religion_printed"]["correct"],
            flush=True,
        )

    def table(rows: list[dict]) -> dict:
        return {name: aggregate(rows, name) for name in FIELDS}

    def cause_counts(name: str) -> dict:
        values = causes[name]
        return {key: values.count(key) for key in sorted(set(values))}

    redacted = {
        "n_samples": 12,
        "crop_boxes": crop_boxes,
        "baseline_full_image": table(baseline_rows),
        "field_crops_only": table(crop_rows),
        "crop_then_full_fallback": table(merge_rows),
        "causes_crop_only": {name: cause_counts(name) for name in FIELDS},
        "crop_shapes": [
            {
                "sample_id": item["sample_id"],
                **{
                    name: {
                        "n_lines": item["crops"][name]["stats"]["n_lines"],
                        "digit_len": item["crops"][name]["stats"]["digit_len"],
                        "has_sep": item["crops"][name]["stats"]["has_sep"],
                        "shapes": item["crops"][name]["stats"]["shapes"],
                        "status": item["crops"][name]["status"],
                    }
                    for name in FIELDS
                },
            }
            for item in private
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "crop_experiment_summary.json").write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "crop_experiment_raw.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    public = {k: v for k, v in redacted.items() if k != "crop_shapes"}
    public["crop_line_counts"] = [
        {
            "sample_id": row["sample_id"],
            **{name: {"n_lines": row[name]["n_lines"], "digit_len": row[name]["digit_len"], "has_sep": row[name]["has_sep"], "status": row[name]["status"]} for name in FIELDS},
        }
        for row in redacted["crop_shapes"]
    ]
    print(json.dumps(public, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
