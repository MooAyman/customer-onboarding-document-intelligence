"""Document-level eval for individual front or back images.

Uses frozen OCR, crops, and parsers. Does not call process_id_pair and does
not run cross-person checks. Unpaired images are scored here only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from egyid.eval import (  # noqa: E402
    BACK_DOC_FIELDS,
    FRONT_DOC_FIELDS,
    extract_back_document,
    extract_front_document,
    load_manifest,
    tally_fields,
    score_back_document,
    score_front_document,
)
from egyid.imaging import load_image  # noqa: E402
from egyid.ocr import default_engine  # noqa: E402

MANIFEST_PATH = ROOT / "artifacts" / "eval_manifest" / "manifest.json"
OUT_DIR = ROOT / "artifacts" / "doc_eval"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path-substr", default=None)
    parser.add_argument("--summary-name", default="summary.json")
    parser.add_argument("--private-name", default="private.json")
    args = parser.parse_args(argv)
    if args.summary_name.startswith("split12_") or args.private_name.startswith("split12_"):
        raise SystemExit("refusing to overwrite split-12 baseline files")

    manifest = load_manifest(MANIFEST_PATH, root=ROOT)
    engine = default_engine()
    rows: list[dict] = []
    private: list[dict] = []
    documents = manifest.documents
    if args.path_substr:
        documents = tuple(doc for doc in documents if args.path_substr in str(doc.path))

    for doc in documents:
        print(f"document {doc.doc_id} {doc.side} {doc.pairing}", flush=True)
        loaded = load_image(doc.path.read_bytes())
        if not loaded.ok:
            row = {
                "doc_id": doc.doc_id,
                "side": doc.side,
                "pairing": doc.pairing,
                "case_id": doc.case_id,
                "load_ok": False,
                "fields": {},
            }
            rows.append(row)
            continue
        if doc.side == "front":
            fields = extract_front_document(loaded.array, engine)
            scored = score_front_document(fields, doc.gold)
            values = {
                "card_serial": fields.card_serial.value,
                "national_id": fields.national_id.value,
                "date_of_birth_printed": fields.date_of_birth_printed.value,
            }
        else:
            fields = extract_back_document(loaded.array, engine)
            scored = score_back_document(fields, doc.gold)
            values = {
                "national_id": fields.national_id.value,
                "issue_date": fields.issue_date.value,
                "expiry_date": fields.expiry_date.value,
                "gender_printed": fields.gender_printed.value,
                "religion_printed": fields.religion_printed.value,
                "marital_status_ar": fields.marital_status_ar.value,
            }
        rows.append(
            {
                "doc_id": doc.doc_id,
                "side": doc.side,
                "pairing": doc.pairing,
                "case_id": doc.case_id,
                "load_ok": True,
                "fields": scored,
            }
        )
        private.append({"doc_id": doc.doc_id, "side": doc.side, "values": values})

    fronts = [row for row in rows if row["side"] == "front" and row.get("fields")]
    backs = [row for row in rows if row["side"] == "back" and row.get("fields")]
    public = {
        "note": "Document-level only. Not a case scorecard. Separate from artifacts/case_eval and artifacts/synthetic_eval.",
        "path_substr": args.path_substr,
        "n_documents": len(rows),
        "n_front_docs": len(fronts),
        "n_back_docs": len(backs),
        "n_unpaired": sum(row["pairing"] == "unpaired" for row in rows),
        "n_verified_pair_members": sum(row["pairing"] == "verified_pair" for row in rows),
        "front_field_totals": {name: tally_fields(fronts, name) for name in FRONT_DOC_FIELDS},
        "back_field_totals": {name: tally_fields(backs, name) for name in BACK_DOC_FIELDS},
        "documents": [
            {
                "doc_id": row["doc_id"],
                "side": row["side"],
                "pairing": row["pairing"],
                "case_id": row["case_id"],
                "load_ok": row["load_ok"],
                "fields": {
                    name: row["fields"][name]
                    for name in row["fields"]
                    if isinstance(row["fields"].get(name), dict)
                    and "scored" in row["fields"][name]
                },
            }
            for row in rows
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / args.summary_name).write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / args.private_name).write_text(json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8")
    printable = {k: v for k, v in public.items() if k != "documents"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
