"""Expiry footer crop + current date parser, full-image fallback.

Does not modify the pipeline or original images.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_split_ocr import OUT_DIR, SPLIT_DIR, discover_pairs, redact_text
from egyid.extract import extract_back, extract_expiry_date, merge_present
from egyid.imaging import EXPIRY_CROP, barcode_present, crop_normalized, load_image
from egyid.nid import digits_only
from egyid.ocr import OcrLine, default_engine

GOLD_PATH = OUT_DIR / "gold.json"
RAW_PATH = OUT_DIR / "raw_ocr.json"


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


def score(status: str, value: str | None, gold: str) -> dict:
    detected = status == "present"
    return {
        "correct": detected and value == gold,
        "incorrect": detected and value != gold,
        "unreadable": status == "unreadable",
        "absent": status == "absent",
        "detected": detected,
    }


def tally(rows: list[dict], key: str) -> dict:
    out = {"correct": 0, "incorrect": 0, "unreadable": 0, "absent": 0, "n": len(rows)}
    for row in rows:
        item = row[key]
        for name in ("correct", "incorrect", "unreadable", "absent"):
            out[name] += int(item[name])
    return out


def main() -> None:
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["samples"]
    raw = {item["sample_id"]: item for item in json.loads(RAW_PATH.read_text(encoding="utf-8"))}
    pairs, _ = discover_pairs(SPLIT_DIR)
    engine = default_engine()

    rows = []
    private = []
    print("expiry crop box", EXPIRY_CROP, flush=True)
    for pair in pairs:
        sample_id = pair["sample_id"]
        print(f"expiry crop {sample_id}", flush=True)
        g = gold[sample_id]["expiry"]
        back_img = load_image(pair["back"].read_bytes()).array
        full_back = extract_back(to_lines(raw[sample_id]["back_lines"]), *barcode_present(back_img))
        crop_lines = engine.run(crop_normalized(back_img, EXPIRY_CROP))
        crop_field = extract_expiry_date(crop_lines)
        merged = merge_present(crop_field, full_back.expiry_date)
        source = "crop" if crop_field.status.value == "present" else "full_image"
        base = score(full_back.expiry_date.status.value, full_back.expiry_date.value, g)
        after = score(merged.status.value, merged.value, g)
        crop_only = score(crop_field.status.value, crop_field.value, g)
        change = "unchanged"
        if after["correct"] and not base["correct"]:
            change = "gained"
        elif base["correct"] and not after["correct"]:
            change = "regressed"
        elif after["incorrect"] and not base["incorrect"]:
            change = "false_date"
        rows.append({"baseline": base, "after": after, "crop_only": crop_only, "source": source, "change": change})
        private.append(
            {
                "sample_id": sample_id,
                "source": source,
                "change": change,
                "baseline_status": full_back.expiry_date.status.value,
                "crop_status": crop_field.status.value,
                "merged_status": merged.status.value,
                "baseline_value": full_back.expiry_date.value,
                "crop_value": crop_field.value,
                "merged_value": merged.value,
                "crop_shapes": [redact_text(ln.text) for ln in crop_lines],
                "crop_digit_lens": [len(digits_only(ln.text)) for ln in crop_lines],
            }
        )

    redacted = {
        "n_samples": 12,
        "crop_box": EXPIRY_CROP,
        "baseline_full_image": tally(rows, "baseline"),
        "crop_only": tally(rows, "crop_only"),
        "crop_with_full_fallback": tally(rows, "after"),
        "sources": {
            "crop": sum(row["source"] == "crop" for row in rows),
            "full_image": sum(row["source"] == "full_image" for row in rows),
        },
        "changes": {
            "gained": sum(row["change"] == "gained" for row in rows),
            "regressed": sum(row["change"] == "regressed" for row in rows),
            "false_date": sum(row["change"] == "false_date" for row in rows),
            "unchanged": sum(row["change"] == "unchanged" for row in rows),
        },
        "per_sample": [
            {
                "sample_id": item["sample_id"],
                "source": item["source"],
                "change": item["change"],
                "baseline_status": item["baseline_status"],
                "crop_status": item["crop_status"],
                "merged_status": item["merged_status"],
                "crop_shapes": item["crop_shapes"],
                "crop_digit_lens": item["crop_digit_lens"],
            }
            for item in private
        ],
        "keep_crop": (
            tally(rows, "after")["incorrect"] == 0
            and sum(row["change"] == "regressed" for row in rows) == 0
            and tally(rows, "after")["correct"] > tally(rows, "baseline")["correct"]
        ),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "expiry_crop_summary.json").write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "expiry_crop_raw.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(redacted, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
