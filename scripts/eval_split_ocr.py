"""Temporary OCR evaluation on split National ID images.

Writes a redacted summary (safe to print) and a gitignored raw dump for inspection.
Does not modify source images.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from egyid.imaging import barcode_present, load_image
from egyid.nid import parse_national_id
from egyid.ocr import default_engine
from egyid.pipeline import process_id_pair

SPLIT_DIR = ROOT / "National IDs" / "Front + Back" / "Split"
OUT_DIR = ROOT / "artifacts" / "ocr_eval"

EASTERN = set("٠١٢٣٤٥٦٧٨٩")
WESTERN = set("0123456789")
SERIAL_RE = re.compile(r"^[A-Z]{2}\d{6,10}$", re.I)


def _stem_key(name: str) -> str | None:
    if name.startswith("1"):
        return "front", name[1:]
    if name.startswith("2"):
        return "back", name[1:]
    return None


def discover_pairs(folder: Path) -> list[dict]:
    """Legacy filename split for the attested combined-card 12 only.

    Do not use this to join new donations. Unpaired fronts and backs are
    separate documents until a human attests they are the same physical card.
    """
    files = [p for p in folder.iterdir() if p.is_file()]
    fronts: dict[str, Path] = {}
    backs: dict[str, Path] = {}
    unmatched: list[str] = []
    for path in files:
        parsed = _stem_key(path.name)
        if not parsed:
            unmatched.append(path.name)
            continue
        side, rest = parsed
        (fronts if side == "front" else backs)[rest] = path
    keys = sorted(set(fronts) | set(backs), key=str.lower)
    pairs = []
    for idx, key in enumerate(keys, start=1):
        pairs.append(
            {
                "sample_id": f"{idx:02d}",
                "key": key,
                "front": fronts.get(key),
                "back": backs.get(key),
            }
        )
    return pairs, unmatched


def char_profile(text: str) -> dict:
    eastern = sum(ch in EASTERN for ch in text)
    western = sum(ch in WESTERN for ch in text)
    arabic = sum("\u0600" <= ch <= "\u06FF" and ch not in EASTERN for ch in text)
    latin = sum(ch.isascii() and ch.isalpha() for ch in text)
    return {
        "len": len(text),
        "eastern_digits": eastern,
        "western_digits": western,
        "arabic_letters": arabic,
        "latin_letters": latin,
        "mean_score_placeholder": None,
    }


def redact_text(text: str) -> str:
    out = []
    for ch in text:
        if ch in EASTERN:
            out.append("E")
        elif ch.isdigit():
            out.append("W")
        elif "\u0600" <= ch <= "\u06FF":
            out.append("A")
        elif ch.isascii() and ch.isalpha():
            out.append("L")
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def field_summary(field) -> dict:
    return {
        "status": field.status.value,
        "confidence": field.confidence,
        "value_len": len(field.value or ""),
        "profile": char_profile(field.value or "") if field.value else None,
        "nid_ok": None,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs, unmatched = discover_pairs(SPLIT_DIR)
    engine = default_engine()
    raw = []
    redacted = {
        "split_dir": str(SPLIT_DIR),
        "pair_count": len(pairs),
        "complete_pairs": 0,
        "unmatched_files": unmatched,
        "samples": [],
    }

    for pair in pairs:
        front_path, back_path = pair["front"], pair["back"]
        complete = bool(front_path and back_path)
        sample = {
            "sample_id": pair["sample_id"],
            "complete_pair": complete,
            "front_file_present": front_path is not None,
            "back_file_present": back_path is not None,
        }
        if not complete:
            redacted["samples"].append(sample)
            continue
        redacted["complete_pairs"] += 1

        front_bytes = front_path.read_bytes()
        back_bytes = back_path.read_bytes()
        front_loaded = load_image(front_bytes)
        back_loaded = load_image(back_bytes)

        class _Capture:
            def __init__(self) -> None:
                self.front_lines = []
                self.back_lines = []
                self.calls = 0

            def run(self, image):
                self.calls += 1
                lines = engine.run(image)
                if self.calls == 1:
                    self.front_lines = lines
                else:
                    self.back_lines = lines
                return lines

        capture = _Capture()
        result = process_id_pair(front_bytes, back_bytes, engine=capture)
        front_lines = capture.front_lines
        back_lines = capture.back_lines

        front_nid = parse_national_id(result.front.national_id.value)
        back_nid = parse_national_id(result.back.national_id.value)

        sample.update(
            {
                "decision": result.decision.value,
                "reasons": result.reasons,
                "ocr_ms": result.ocr_ms,
                "front_quality": result.front_image.quality.model_dump() if result.front_image.quality else None,
                "back_quality": result.back_image.quality.model_dump() if result.back_image.quality else None,
                "front_ocr_lines": len(front_lines),
                "back_ocr_lines": len(back_lines),
                "front_ocr_mean_score": round(
                    sum(ln.score for ln in front_lines) / max(len(front_lines), 1), 3
                ),
                "back_ocr_mean_score": round(
                    sum(ln.score for ln in back_lines) / max(len(back_lines), 1), 3
                ),
                "front_digit_scripts": {
                    "eastern_lines": sum(any(ch in EASTERN for ch in ln.text) for ln in front_lines),
                    "western_lines": sum(any(ch in WESTERN for ch in ln.text) for ln in front_lines),
                },
                "back_digit_scripts": {
                    "eastern_lines": sum(any(ch in EASTERN for ch in ln.text) for ln in back_lines),
                    "western_lines": sum(any(ch in WESTERN for ch in ln.text) for ln in back_lines),
                },
                "fields": {
                    "full_name_ar": field_summary(result.front.full_name_ar),
                    "address_ar": field_summary(result.front.address_ar),
                    "date_of_birth_printed": field_summary(result.front.date_of_birth_printed),
                    "front_national_id": field_summary(result.front.national_id),
                    "card_serial": field_summary(result.front.card_serial),
                    "back_national_id": field_summary(result.back.national_id),
                    "issue_date": field_summary(result.back.issue_date),
                    "profession_ar": field_summary(result.back.profession_ar),
                    "gender_printed": field_summary(result.back.gender_printed),
                    "religion_printed": field_summary(result.back.religion_printed),
                    "marital_status_ar": field_summary(result.back.marital_status_ar),
                    "expiry_date": field_summary(result.back.expiry_date),
                    "barcode_present": field_summary(result.back.barcode_present),
                    "derived_dob": field_summary(result.derived.date_of_birth),
                    "derived_gender": field_summary(result.derived.gender),
                },
                "consistency": {
                    "front_nid_structure_ok": bool(front_nid and front_nid.ok),
                    "back_nid_structure_ok": bool(back_nid and back_nid.ok),
                    "front_back_nid_match": bool(
                        result.front.national_id.value
                        and result.front.national_id.value == result.back.national_id.value
                    ),
                    "printed_dob_vs_derived": (
                        result.front.date_of_birth_printed.value == result.derived.date_of_birth.value
                        if result.front.date_of_birth_printed.value and result.derived.date_of_birth.value
                        else None
                    ),
                    "printed_gender_vs_derived": (
                        result.back.gender_printed.value == result.derived.gender.value
                        if result.back.gender_printed.value and result.derived.gender.value
                        else None
                    ),
                    "serial_pattern_ok": bool(
                        result.front.card_serial.value
                        and SERIAL_RE.match(result.front.card_serial.value)
                    ),
                    "barcode_heuristic": barcode_present(back_loaded.array)[0],
                },
                "front_line_shapes": [
                    {"shape": redact_text(ln.text), "score": round(ln.score, 3), "cx": round(ln.cx, 2), "cy": round(ln.cy, 2)}
                    for ln in front_lines
                ],
                "back_line_shapes": [
                    {"shape": redact_text(ln.text), "score": round(ln.score, 3), "cx": round(ln.cx, 2), "cy": round(ln.cy, 2)}
                    for ln in back_lines
                ],
            }
        )
        sample["fields"]["front_national_id"]["nid_ok"] = bool(front_nid and front_nid.ok)
        sample["fields"]["back_national_id"]["nid_ok"] = bool(back_nid and back_nid.ok)
        redacted["samples"].append(sample)

        raw.append(
            {
                "sample_id": pair["sample_id"],
                "front_lines": [{"text": ln.text, "score": ln.score, "cx": ln.cx, "cy": ln.cy} for ln in front_lines],
                "back_lines": [{"text": ln.text, "score": ln.score, "cx": ln.cx, "cy": ln.cy} for ln in back_lines],
                "extracted": result.model_dump(mode="json"),
            }
        )

    (OUT_DIR / "redacted_summary.json").write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "raw_ocr.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {
            "complete_pairs": redacted["complete_pairs"],
            "pair_count": redacted["pair_count"],
            "unmatched_files": unmatched,
            "summary_path": str(OUT_DIR / "redacted_summary.json"),
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
