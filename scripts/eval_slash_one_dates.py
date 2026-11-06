"""Evaluate slash-as-1 date normalization on stored full-image OCR.

Uses the same 12 samples and gold as prior evals. Does not re-OCR or
modify original images. Writes gitignored artifacts only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_split_ocr import OUT_DIR
from egyid.extract import extract_back
from egyid.imaging import barcode_present, load_image
from egyid.ocr import OcrLine
from eval_split_ocr import SPLIT_DIR, discover_pairs

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
        "detected": detected,
        "correct": detected and value == gold,
        "incorrect": detected and value != gold,
        "unreadable": status == "unreadable",
        "absent": status == "absent",
        "slash_as_one": False,
    }


def tally(rows: list[dict], name: str) -> dict:
    keys = ("detected", "correct", "incorrect", "unreadable", "absent")
    out = {key: 0 for key in keys}
    out["n"] = len(rows)
    out["slash_as_one"] = 0
    for row in rows:
        item = row[name]
        for key in keys:
            out[key] += int(item[key])
        out["slash_as_one"] += int(item.get("slash_as_one", False))
    return out


def main() -> None:
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["samples"]
    raw = {item["sample_id"]: item for item in json.loads(RAW_PATH.read_text(encoding="utf-8"))}
    pairs, _ = discover_pairs(SPLIT_DIR)
    previous = json.loads((OUT_DIR / "crop_experiment_summary.json").read_text(encoding="utf-8"))[
        "baseline_full_image"
    ]

    rows = []
    private = []
    for pair in pairs:
        sample_id = pair["sample_id"]
        item = raw[sample_id]
        g = gold[sample_id]
        back_img = load_image(pair["back"].read_bytes()).array
        back = extract_back(to_lines(item["back_lines"]), *barcode_present(back_img))
        issue = score(back.issue_date.status.value, back.issue_date.value, g["issue"])
        expiry = score(back.expiry_date.status.value, back.expiry_date.value, g["expiry"])
        issue["slash_as_one"] = bool(back.issue_date.raw_ocr)
        expiry["slash_as_one"] = bool(back.expiry_date.raw_ocr)
        rows.append({"issue_date": issue, "expiry_date": expiry})
        private.append(
            {
                "sample_id": sample_id,
                "issue": {
                    "status": back.issue_date.status.value,
                    "correct": issue["correct"],
                    "incorrect": issue["incorrect"],
                    "slash_as_one": issue["slash_as_one"],
                    "value": back.issue_date.value,
                },
                "expiry": {
                    "status": back.expiry_date.status.value,
                    "correct": expiry["correct"],
                    "incorrect": expiry["incorrect"],
                    "slash_as_one": expiry["slash_as_one"],
                    "value": back.expiry_date.value,
                },
            }
        )

    issue_now = tally(rows, "issue_date")
    expiry_now = tally(rows, "expiry_date")
    regressions = {
        "issue_date": [],
        "expiry_date": [],
    }
    false_new = {"issue_date": 0, "expiry_date": 0}
    for item in private:
        sid = item["sample_id"]
        if item["issue"]["incorrect"]:
            false_new["issue_date"] += 1
            regressions["issue_date"].append(sid)
        if item["expiry"]["incorrect"]:
            false_new["expiry_date"] += 1
            regressions["expiry_date"].append(sid)

    redacted = {
        "n_samples": 12,
        "previous_baseline": {
            "issue_date": {k: previous["issue_date"][k] for k in ("detected", "correct", "incorrect", "unreadable")},
            "expiry_date": {k: previous["expiry_date"][k] for k in ("detected", "correct", "incorrect", "unreadable")},
        },
        "slash_as_one": {
            "issue_date": {k: issue_now[k] for k in ("detected", "correct", "incorrect", "unreadable", "slash_as_one")},
            "expiry_date": {
                k: expiry_now[k] for k in ("detected", "correct", "incorrect", "unreadable", "slash_as_one")
            },
        },
        "previously_correct_regressed": {
            "issue_date": issue_now["correct"] < previous["issue_date"]["correct"],
            "expiry_date": expiry_now["correct"] < previous["expiry_date"]["correct"],
            "issue_correct_delta": issue_now["correct"] - previous["issue_date"]["correct"],
            "expiry_correct_delta": expiry_now["correct"] - previous["expiry_date"]["correct"],
        },
        "false_dates_introduced": false_new,
        "keep_rule": (
            false_new["issue_date"] == 0
            and false_new["expiry_date"] == 0
            and (
                issue_now["correct"] > previous["issue_date"]["correct"]
                or expiry_now["correct"] > previous["expiry_date"]["correct"]
            )
        ),
        "per_sample": [
            {
                "sample_id": item["sample_id"],
                "issue_status": item["issue"]["status"],
                "issue_correct": item["issue"]["correct"],
                "issue_slash_as_one": item["issue"]["slash_as_one"],
                "expiry_status": item["expiry"]["status"],
                "expiry_correct": item["expiry"]["correct"],
                "expiry_slash_as_one": item["expiry"]["slash_as_one"],
            }
            for item in private
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "slash_one_summary.json").write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "slash_one_raw.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(redacted, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
