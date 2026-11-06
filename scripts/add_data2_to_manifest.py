"""Append attested data2 documents to the existing eval manifest.

Does not rebuild or rewrite the original split-12 entries. Pairing uses the
donor-attested front+back folder (frontN with backN). The 8 images in front/
are unpaired and never joined to a back.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from egyid.eval import (  # noqa: E402
    CaseRecord,
    DocumentRecord,
    EvalManifest,
    dump_manifest,
    load_manifest,
    unpaired_document,
)

MANIFEST_PATH = ROOT / "artifacts" / "eval_manifest" / "manifest.json"
DATA2 = ROOT / "National IDs" / "Front + Back" / "data2"
PAIRS_DIR = DATA2 / "front + back"
FRONTS_DIR = DATA2 / "front"
EVIDENCE = "donor_same_card"

PAIRS = [
    {
        "n": "01",
        "front": "front1 20190504_185334.jpg",
        "back": "back1 20190504_185343.jpg",
        "serial": "GK1591077",
        "nid": "29607220103693",
        "dob": "1996-07-22",
        "dob_readable": True,
        "gender": "male",
        "issue": "2016-02",
        "expiry": "2023-02-23",
        "religion": "مسيحي",
        "marital": "single",
    },
    {
        "n": "02",
        "front": "front2 20190504_201329.jpg",
        "back": "back2 20190504_201336.jpg",
        "serial": "GZ2162954",
        "nid": "29506010111013",
        "dob": "1995-06-01",
        "dob_readable": False,
        "gender": "male",
        "issue": "2017-04",
        "expiry": "2024-04-17",
        "religion": "مسلم",
        "marital": "single",
    },
    {
        "n": "03",
        "front": "front3 20190504_201359.jpg",
        "back": "back3 20190504_201405.jpg",
        "serial": "HW3117979",
        "nid": "29609151802731",
        "dob": "1996-09-15",
        "dob_readable": False,
        "gender": "male",
        "issue": "2019-01",
        "expiry": "2026-01-20",
        "religion": "مسلم",
        "marital": "single",
    },
    {
        "n": "04",
        "front": "front4 20190504_205720.jpg",
        "back": "back4 20190504_205726.jpg",
        "serial": "GC9412479",
        "nid": "29611091301456",
        "dob": "1996-11-09",
        "dob_readable": False,
        "gender": "male",
        "issue": "2015-08",
        "expiry": "2022-08-08",
        "religion": "مسلم",
        "marital": "single",
    },
    {
        "n": "05",
        "front": "front5 20190504_205730.jpg",
        "back": "back5 20190504_205735.jpg",
        "serial": "FI3677277",
        "nid": "29602060101417",
        "dob": "1996-02-06",
        "dob_readable": False,
        "gender": "male",
        "issue": "2013-06",
        "expiry": "2020-06-04",
        "religion": "مسلم",
        "marital": "single",
    },
    {
        "n": "06",
        "front": "front6 20190504_205750.jpg",
        "back": "back6 20190504_205755.jpg",
        "serial": "GD2905373",
        "nid": "29701050101841",
        "dob": "1997-01-05",
        "dob_readable": False,
        "gender": "female",
        "issue": "2015-07",
        "expiry": "2022-06-30",
        "religion": "مسلمة",
        "marital": "single",
    },
    {
        "n": "07",
        "front": "front7 imageId.jpg",
        "back": "back7 20190504_205804.jpg",
        "serial": "GG6848691",
        "nid": "29309011410774",
        "dob": "1993-09-01",
        "dob_readable": False,
        "gender": "male",
        "issue": "2015-11",
        "expiry": "2022-11-11",
        "religion": "مسلم",
        "marital": "single",
    },
]

UNPAIRED_FRONTS = [
    {"n": "u01", "file": "download.jpg", "serial": "BR6325226"},
    {
        "n": "u02",
        "file": "images-1.jpg",
        "serial": "GK1426362",
        "nid": "29811083504297",
        "dob": "1998-11-08",
        "dob_readable": False,
        "gender": "male",
    },
    {"n": "u03", "file": "images-11.jpg", "serial": "FC6335280"},
    {
        "n": "u04",
        "file": "images-13.jpg",
        "serial": "GW6974992",
        "nid": "28309103201111",
        "dob": "1983-09-10",
        "dob_readable": False,
        "gender": "male",
    },
    {
        "n": "u05",
        "file": "images-6.jpg",
        "serial": "GN1093604",
        "dob": "1999-09-12",
        "dob_readable": True,
    },
    {"n": "u06", "file": "images-7.jpg", "serial": "FM6551671"},
    {
        "n": "u07",
        "file": "images-8.jpg",
        "serial": "IS0664295",
        "nid": "25311301701705",
        "dob": "1953-11-30",
        "dob_readable": False,
        "gender": "female",
    },
    {
        "n": "u08",
        "file": "images-9.jpg",
        "serial": "HL0926243",
        "nid": "29903171700134",
        "dob": "1999-03-17",
        "dob_readable": True,
        "gender": "male",
    },
]


def _front_gold(item: dict) -> dict:
    gold = {"card_serial": item["serial"]}
    if item.get("nid"):
        gold["national_id"] = item["nid"]
    readable = bool(item.get("dob_readable"))
    gold["dob_readable"] = readable
    gold["date_of_birth_printed"] = item.get("dob") if readable else None
    if item.get("dob"):
        gold["derived_dob"] = item["dob"]
    if item.get("gender"):
        gold["derived_gender"] = item["gender"]
    return gold


def _back_gold(item: dict) -> dict:
    return {
        "national_id": item["nid"],
        "issue_date": item["issue"],
        "expiry_date": item["expiry"],
        "gender_printed": item["gender"],
        "religion_printed": item["religion"],
        "marital_status_ar": item["marital"],
        "derived_dob": item["dob"],
        "derived_gender": item["gender"],
    }


def _pair_docs(item: dict) -> tuple[DocumentRecord, DocumentRecord, CaseRecord]:
    case_id = f"case_d2_{item['n']}"
    front_path = PAIRS_DIR / item["front"]
    back_path = PAIRS_DIR / item["back"]
    if not front_path.is_file() or not back_path.is_file():
        raise FileNotFoundError(f"missing pair files for {item['n']}")
    front = DocumentRecord(
        doc_id=f"front_d2_{item['n']}",
        side="front",
        path=front_path,
        pairing="verified_pair",
        case_id=case_id,
        pair_evidence=EVIDENCE,
        gold=_front_gold(item),
    )
    back = DocumentRecord(
        doc_id=f"back_d2_{item['n']}",
        side="back",
        path=back_path,
        pairing="verified_pair",
        case_id=case_id,
        pair_evidence=EVIDENCE,
        gold=_back_gold(item),
    )
    case = CaseRecord(
        case_id=case_id,
        front_doc_id=front.doc_id,
        back_doc_id=back.doc_id,
        pair_evidence=EVIDENCE,
    )
    return front, back, case


def _unpaired_front(item: dict) -> DocumentRecord:
    path = FRONTS_DIR / item["file"]
    if not path.is_file():
        raise FileNotFoundError(path)
    rec = unpaired_document(
        doc_id=f"front_d2_{item['n']}",
        side="front",
        path=path,
        gold=_front_gold(item),
    )
    return DocumentRecord(
        doc_id=rec["doc_id"],
        side=rec["side"],
        path=path,
        pairing="unpaired",
        case_id=None,
        pair_evidence=None,
        gold=rec["gold"],
    )


def _repair_relocated_split12_paths() -> int:
    """Point existing split-12 entries at data1/Split if that folder moved."""
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    old = "National IDs/Front + Back/Split/"
    new = "National IDs/Front + Back/data1/Split/"
    changed = 0
    for item in payload.get("documents", []):
        path = str(item.get("path") or "").replace("\\", "/")
        if not path.startswith(old):
            continue
        candidate = new + path[len(old) :]
        if (ROOT / candidate).is_file():
            item["path"] = candidate
            changed += 1
    if changed:
        MANIFEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def main() -> None:
    repaired = _repair_relocated_split12_paths()
    existing = load_manifest(MANIFEST_PATH, root=ROOT)
    if any(doc.doc_id.startswith("front_d2_") or doc.doc_id.startswith("back_d2_") for doc in existing.documents):
        print(json.dumps({"status": "already_present", "n_documents": len(existing.documents), "split12_paths_repaired": repaired}, indent=2))
        return

    documents = list(existing.documents)
    cases = list(existing.cases)
    for item in PAIRS:
        front, back, case = _pair_docs(item)
        documents.extend([front, back])
        cases.append(case)
    for item in UNPAIRED_FRONTS:
        documents.append(_unpaired_front(item))

    dump_manifest(
        EvalManifest(documents=tuple(documents), cases=tuple(cases)),
        MANIFEST_PATH,
        root=ROOT,
        note=(
            "Private local eval manifest. Do not commit. pairing is never inferred. "
            "data2 pairs are donor_same_card; data2 front-only images are unpaired."
        ),
    )
    added = [doc for doc in documents if doc.doc_id.startswith(("front_d2_", "back_d2_"))]
    print(
        json.dumps(
            {
                "status": "appended",
                "split12_paths_repaired": repaired,
                "n_documents": len(documents),
                "n_cases": len(cases),
                "n_data2_documents": len(added),
                "n_data2_pairs": sum(1 for case in cases if case.case_id.startswith("case_d2_")),
                "n_data2_unpaired_fronts": sum(doc.pairing == "unpaired" and doc.doc_id.startswith("front_d2_") for doc in documents),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
