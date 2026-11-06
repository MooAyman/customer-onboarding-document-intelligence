"""Seed the gitignored eval manifest from the attested split-12 set.

The historical 1*/2* filename split is used only because those files were cut
from combined cards. New donations must be added as unpaired documents unless
the donor attests they are the same physical card. This script never joins
leftover fronts to leftover backs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_split_ocr import SPLIT_DIR, discover_pairs  # noqa: E402
from egyid.eval import (  # noqa: E402
    ALLOWED_PAIR_EVIDENCE,
    CaseRecord,
    DocumentRecord,
    EvalManifest,
    dump_manifest,
    unpaired_document,
)

GOLD_PATH = ROOT / "artifacts" / "ocr_eval" / "gold.json"
MANIFEST_PATH = ROOT / "artifacts" / "eval_manifest" / "manifest.json"
EVIDENCE = "split_from_combined_card"


def main() -> None:
    if EVIDENCE not in ALLOWED_PAIR_EVIDENCE:
        raise SystemExit("bootstrap evidence is not allowed")
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))["samples"]
    pairs, unmatched = discover_pairs(SPLIT_DIR)
    documents: list[DocumentRecord] = []
    cases: list[CaseRecord] = []

    for pair in pairs:
        sample_id = pair["sample_id"]
        front_path, back_path = pair["front"], pair["back"]
        labeled = gold.get(sample_id)
        if labeled and front_path and back_path:
            case_id = f"case_{sample_id}"
            front_gold = {
                "card_serial": labeled["serial"],
                "national_id": labeled["nid"],
                "date_of_birth_printed": labeled["dob"] if labeled.get("dob_readable") else None,
                "dob_readable": bool(labeled.get("dob_readable")),
                "derived_dob": labeled.get("dob"),
                "derived_gender": labeled["gender"],
            }
            back_gold = {
                "national_id": labeled["nid"],
                "issue_date": labeled["issue"],
                "expiry_date": labeled["expiry"],
                "gender_printed": labeled["gender"],
                "religion_printed": labeled["religion"],
                "marital_status_ar": labeled["marital"],
                "derived_dob": labeled.get("dob"),
                "derived_gender": labeled["gender"],
            }
            documents.append(
                DocumentRecord(
                    doc_id=f"front_{sample_id}",
                    side="front",
                    path=front_path,
                    pairing="verified_pair",
                    case_id=case_id,
                    pair_evidence=EVIDENCE,
                    gold=front_gold,
                )
            )
            documents.append(
                DocumentRecord(
                    doc_id=f"back_{sample_id}",
                    side="back",
                    path=back_path,
                    pairing="verified_pair",
                    case_id=case_id,
                    pair_evidence=EVIDENCE,
                    gold=back_gold,
                )
            )
            cases.append(
                CaseRecord(
                    case_id=case_id,
                    front_doc_id=f"front_{sample_id}",
                    back_doc_id=f"back_{sample_id}",
                    pair_evidence=EVIDENCE,
                )
            )
            continue

        # Incomplete historical files stay unpaired. Never invent a mate.
        if front_path:
            documents.append(_unpaired_record(f"front_unpaired_{sample_id}", "front", front_path))
        if back_path:
            documents.append(_unpaired_record(f"back_unpaired_{sample_id}", "back", back_path))

    for idx, name in enumerate(unmatched, start=1):
        path = SPLIT_DIR / name
        side = "front" if name.startswith("1") else "back" if name.startswith("2") else None
        if side is None:
            continue
        documents.append(_unpaired_record(f"{side}_unmatched_{idx:02d}", side, path))

    manifest = EvalManifest(documents=tuple(documents), cases=tuple(cases))
    dump_manifest(
        manifest,
        MANIFEST_PATH,
        root=ROOT,
        note=(
            "Private local eval manifest. Do not commit. pairing is never inferred. "
            "Add new images as unpaired unless the donor attests the same physical card."
        ),
    )
    print(
        json.dumps(
            {
                "manifest": str(MANIFEST_PATH),
                "n_documents": len(documents),
                "n_front": sum(doc.side == "front" for doc in documents),
                "n_back": sum(doc.side == "back" for doc in documents),
                "n_verified_pairs": len(cases),
                "n_unpaired": sum(doc.pairing == "unpaired" for doc in documents),
            },
            indent=2,
        )
    )


def _unpaired_record(doc_id: str, side: str, path: Path) -> DocumentRecord:
    item = unpaired_document(doc_id=doc_id, side=side, path=path, gold={})
    return DocumentRecord(
        doc_id=item["doc_id"],
        side=item["side"],
        path=path,
        pairing=item["pairing"],
        case_id=None,
        pair_evidence=None,
        gold={},
    )


if __name__ == "__main__":
    main()
