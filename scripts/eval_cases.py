"""Case-level eval for verified front/back pairs only.

Refuses unpaired documents and never invents a mate. Cross-document checks
run through process_id_pair. Results stay under artifacts/case_eval/.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from egyid.eval import (  # noqa: E402
    CASE_FIELDS,
    UnverifiedPairError,
    case_gold,
    load_manifest,
    require_verified_case,
    score_case_result,
    tally_fields,
)
from egyid.pipeline import process_id_pair  # noqa: E402

MANIFEST_PATH = ROOT / "artifacts" / "eval_manifest" / "manifest.json"
OUT_DIR = ROOT / "artifacts" / "case_eval"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path-substr", default=None)
    parser.add_argument("--summary-name", default="summary.json")
    parser.add_argument("--private-name", default="private.json")
    args = parser.parse_args(argv)
    if args.summary_name.startswith("split12_") or args.private_name.startswith("split12_"):
        raise SystemExit("refusing to overwrite split-12 baseline files")

    manifest = load_manifest(MANIFEST_PATH, root=ROOT)
    docs = manifest.documents_by_id()
    cases = manifest.verified_cases()
    if args.path_substr:
        cases = tuple(
            case
            for case in cases
            if args.path_substr in str(docs[case.front_doc_id].path)
            and args.path_substr in str(docs[case.back_doc_id].path)
        )
    if not cases:
        raise SystemExit("no verified pairs in manifest")
    today = date.today().isoformat()
    rows: list[dict] = []
    private: list[dict] = []
    decisions = Counter()
    reason_counts = Counter()

    for case in cases:
        front_doc, back_doc = require_verified_case(case, docs)
        print(f"case {case.case_id} evidence={case.pair_evidence}", flush=True)
        result = process_id_pair(front_doc.path.read_bytes(), back_doc.path.read_bytes())
        gold = case_gold(case, docs)
        fields = score_case_result(result, gold)
        failed_checks = [
            {"name": item.name, "severity": item.severity.value, "detail": item.detail}
            for item in result.checks
            if not item.passed and item.severity.value != "info"
        ]
        blocking_errors = [item["name"] for item in failed_checks if item["severity"] == "error"]
        review_warnings = [item["name"] for item in failed_checks if item["severity"] == "warning"]
        for item in failed_checks:
            reason_counts[item["name"]] += 1
        decisions[result.decision.value] += 1
        rows.append(
            {
                "case_id": case.case_id,
                "front_doc_id": case.front_doc_id,
                "back_doc_id": case.back_doc_id,
                "pair_evidence": case.pair_evidence,
                "decision": result.decision.value,
                "reasons": result.reasons,
                "blocking_errors": blocking_errors,
                "review_warnings": review_warnings,
                "failed_checks": failed_checks,
                "fields": {name: fields[name] for name in CASE_FIELDS},
                "front_back_nid_match": fields["front_back_nid_match"],
            }
        )
        private.append(
            {
                "case_id": case.case_id,
                "decision": result.decision.value,
                "values": {
                    "card_serial": result.front.card_serial.value,
                    "front_national_id": result.front.national_id.value,
                    "back_national_id": result.back.national_id.value,
                    "issue_date": result.back.issue_date.value,
                    "expiry_date": result.back.expiry_date.value,
                    "gender_printed": result.back.gender_printed.value,
                    "religion_printed": result.back.religion_printed.value,
                    "marital_status_ar": result.back.marital_status_ar.value,
                    "date_of_birth_printed": result.front.date_of_birth_printed.value,
                    "derived_dob": result.derived.date_of_birth.value,
                    "derived_gender": result.derived.gender.value,
                },
            }
        )

    public = {
        "note": "Verified pairs only. Do not average with artifacts/doc_eval. Synthetics excluded.",
        "decision_policy": "telecom_onboarding_v1",
        "path_substr": args.path_substr,
        "n_verified_pairs": len(rows),
        "eval_today": today,
        "decisions": dict(decisions),
        "field_totals": {name: tally_fields(rows, name) for name in CASE_FIELDS},
        "front_back_nid_match_correct": sum(int(row["front_back_nid_match"]) for row in rows),
        "reason_counts": dict(reason_counts),
        "cases": rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / args.summary_name).write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / args.private_name).write_text(json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8")
    printable = {k: v for k, v in public.items() if k != "cases"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except UnverifiedPairError as exc:
        raise SystemExit(f"refusing process_id_pair: {exc}") from exc
