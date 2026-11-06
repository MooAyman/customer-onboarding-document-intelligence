"""Evaluation helpers: document-level vs verified-pair case-level.

Identity is never inferred. A case exists only when the manifest marks a
verified pair. Filename, NID equality, and leftover unpaired files are not
joining keys.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from egyid.extract import (
    GENDER_MAP,
    RELIGION_VALUES,
    extract_back,
    extract_expiry_date,
    extract_front,
    extract_mapped_keyword,
    merge_present,
    with_extraction_trace,
)
from egyid.imaging import (
    EXPIRY_CROP,
    GENDER_CROP,
    RELIGION_CROP,
    barcode_present,
    crop_normalized,
    portrait_present,
)
from egyid.models import (
    BackFields,
    DerivedFields,
    FieldSource,
    FieldStatus,
    FieldValue,
    FrontFields,
    IdDocumentResult,
)
from egyid.ocr import OcrEngine
from egyid.validate import derive_from_nid

ALLOWED_PAIRING = frozenset({"unpaired", "verified_pair"})
ALLOWED_PAIR_EVIDENCE = frozenset({"split_from_combined_card", "donor_same_card"})
FORBIDDEN_PAIR_EVIDENCE = frozenset(
    {"filename", "nid_match", "folder", "batch", "leftover", "auto"}
)
FRONT_DOC_FIELDS = (
    "card_serial",
    "national_id",
    "date_of_birth_printed",
    "derived_dob",
    "derived_gender",
)
BACK_DOC_FIELDS = (
    "national_id",
    "issue_date",
    "expiry_date",
    "gender_printed",
    "religion_printed",
    "marital_status_ar",
    "derived_dob",
    "derived_gender",
)
CASE_FIELDS = (
    "card_serial",
    "front_national_id",
    "back_national_id",
    "issue_date",
    "expiry_date",
    "gender_printed",
    "religion_printed",
    "marital_status_ar",
    "date_of_birth_printed",
    "derived_dob",
    "derived_gender",
)


class UnverifiedPairError(ValueError):
    """Raised when case-level pairing is requested without attested evidence."""


@dataclass(frozen=True)
class DocumentRecord:
    doc_id: str
    side: str
    path: Path
    pairing: str
    case_id: str | None
    pair_evidence: str | None
    gold: dict[str, Any]


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    front_doc_id: str
    back_doc_id: str
    pair_evidence: str


@dataclass(frozen=True)
class EvalManifest:
    documents: tuple[DocumentRecord, ...]
    cases: tuple[CaseRecord, ...]

    def documents_by_id(self) -> dict[str, DocumentRecord]:
        return {doc.doc_id: doc for doc in self.documents}

    def front_docs(self) -> tuple[DocumentRecord, ...]:
        return tuple(doc for doc in self.documents if doc.side == "front")

    def back_docs(self) -> tuple[DocumentRecord, ...]:
        return tuple(doc for doc in self.documents if doc.side == "back")

    def verified_cases(self) -> tuple[CaseRecord, ...]:
        by_id = self.documents_by_id()
        verified = []
        for case in self.cases:
            require_verified_case(case, by_id)
            verified.append(case)
        return tuple(verified)


def unpaired_document(
    *,
    doc_id: str,
    side: str,
    path: str | Path,
    gold: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a manifest document that must not enter case-level eval."""
    if side not in {"front", "back"}:
        raise ValueError(f"side must be front or back, got {side}")
    return {
        "doc_id": doc_id,
        "side": side,
        "path": str(path).replace("\\", "/"),
        "pairing": "unpaired",
        "case_id": None,
        "pair_evidence": None,
        "gold": gold or {},
    }


def field_exact_match(predicted: FieldValue, gold: str | None) -> bool | None:
    """None means the gold field was not annotated."""
    if gold is None:
        return None
    if predicted.status != FieldStatus.present:
        return False
    return (predicted.value or "").strip() == gold.strip()


def score_field(
    predicted: FieldValue,
    gold: str | None,
    *,
    expect_value: bool = True,
) -> dict[str, Any]:
    """Exact / incorrect / unreadable / absent. Unannotated gold is not scored."""
    status = predicted.status.value
    value = predicted.value
    if not expect_value:
        return {
            "status": status,
            "correct": status in {"unreadable", "absent"},
            "incorrect": status == "present",
            "unreadable": status == "unreadable",
            "absent": status == "absent",
            "scored": True,
        }
    if gold is None:
        return {
            "status": status,
            "correct": None,
            "incorrect": None,
            "unreadable": status == "unreadable",
            "absent": status == "absent",
            "scored": False,
        }
    detected = status == "present"
    return {
        "status": status,
        "correct": detected and value == gold,
        "incorrect": detected and value != gold,
        "unreadable": status == "unreadable",
        "absent": status == "absent",
        "scored": True,
    }


def tally_fields(rows: list[dict], name: str) -> dict[str, Any]:
    scored = [row["fields"][name] for row in rows if row["fields"][name].get("scored")]
    n = len(scored)
    correct = sum(int(item["correct"]) for item in scored)
    return {
        "n": n,
        "correct": correct,
        "incorrect": sum(int(item["incorrect"]) for item in scored),
        "unreadable": sum(int(item["unreadable"]) for item in scored),
        "absent": sum(int(item["absent"]) for item in scored),
        "exact_match_accuracy": round(correct / n, 4) if n else None,
    }


def score_result(
    result: IdDocumentResult,
    gold: dict[str, str | None],
) -> dict[str, object]:
    """Compare extracted fields to a gold dict. Gold must not be logged by callers."""
    mapping = {
        "full_name_ar": result.front.full_name_ar,
        "address_ar": result.front.address_ar,
        "date_of_birth_printed": result.front.date_of_birth_printed,
        "front_national_id": result.front.national_id,
        "card_serial": result.front.card_serial,
        "back_national_id": result.back.national_id,
        "issue_date": result.back.issue_date,
        "profession_ar": result.back.profession_ar,
        "gender_printed": result.back.gender_printed,
        "religion_printed": result.back.religion_printed,
        "marital_status_ar": result.back.marital_status_ar,
        "expiry_date": result.back.expiry_date,
        "derived_dob": result.derived.date_of_birth,
        "derived_gender": result.derived.gender,
    }
    per_field: dict[str, bool] = {}
    for name, field in mapping.items():
        if name not in gold:
            continue
        matched = field_exact_match(field, gold[name])
        if matched is not None:
            per_field[name] = matched
    total = len(per_field)
    correct = sum(per_field.values())
    return {
        "fields_scored": total,
        "fields_correct": correct,
        "exact_match_rate": (correct / total) if total else None,
        "per_field": per_field,
    }


def derive_from_single_nid(nid_field: FieldValue) -> DerivedFields:
    """Derive from one image's NID only. Does not look at the other side."""
    return derive_from_nid(FrontFields(national_id=nid_field), BackFields())


def extract_front_document(image: np.ndarray, engine: OcrEngine) -> FrontFields:
    """Front OCR + extract_front. Does not run process_id_pair."""
    fields = extract_front(engine.run(image))
    portrait_ok, portrait_score = portrait_present(image)
    fields.portrait_present = (
        FieldValue.present("true", portrait_score, FieldSource.visual)
        if portrait_ok
        else FieldValue.unreadable(portrait_score, FieldSource.visual)
    )
    return fields


def extract_back_document(image: np.ndarray, engine: OcrEngine) -> BackFields:
    """Back OCR + crops + parsers. Does not run process_id_pair."""
    lines = engine.run(image)
    barcode_ok, barcode_score = barcode_present(image)
    fields = extract_back(lines, barcode_ok, barcode_score)
    gender_lines = engine.run(crop_normalized(image, GENDER_CROP))
    religion_lines = engine.run(crop_normalized(image, RELIGION_CROP))
    expiry_lines = engine.run(crop_normalized(image, EXPIRY_CROP))
    fields.gender_printed = merge_present(
        extract_mapped_keyword(gender_lines, GENDER_MAP, unread_if_text=True),
        fields.gender_printed,
    )
    fields.religion_printed = merge_present(
        extract_mapped_keyword(religion_lines, RELIGION_VALUES, unread_if_text=True),
        fields.religion_printed,
    )
    crop_expiry = with_extraction_trace(
        extract_expiry_date(expiry_lines),
        "crop",
        " ".join(ln.text for ln in expiry_lines) or None,
    )
    fields.expiry_date = merge_present(
        crop_expiry, with_extraction_trace(fields.expiry_date, "full_image")
    )
    return fields


def score_front_document(fields: FrontFields, gold: dict[str, Any]) -> dict[str, Any]:
    derived = derive_from_single_nid(fields.national_id)
    dob_readable = bool(gold.get("dob_readable"))
    return {
        "card_serial": score_field(fields.card_serial, gold.get("card_serial")),
        "national_id": score_field(fields.national_id, gold.get("national_id")),
        "date_of_birth_printed": score_field(
            fields.date_of_birth_printed,
            gold.get("date_of_birth_printed") if dob_readable else None,
            expect_value=dob_readable,
        ),
        "derived_dob": score_field(derived.date_of_birth, gold.get("derived_dob")),
        "derived_gender": score_field(derived.gender, gold.get("derived_gender")),
        "same_image_printed_derived_dob": _same_image_match(
            fields.date_of_birth_printed, derived.date_of_birth
        ),
        "same_image_printed_derived_gender": None,
    }


def score_back_document(fields: BackFields, gold: dict[str, Any]) -> dict[str, Any]:
    derived = derive_from_single_nid(fields.national_id)
    return {
        "national_id": score_field(fields.national_id, gold.get("national_id")),
        "issue_date": score_field(fields.issue_date, gold.get("issue_date")),
        "expiry_date": score_field(fields.expiry_date, gold.get("expiry_date")),
        "gender_printed": score_field(fields.gender_printed, gold.get("gender_printed")),
        "religion_printed": score_field(fields.religion_printed, gold.get("religion_printed")),
        "marital_status_ar": score_field(fields.marital_status_ar, gold.get("marital_status_ar")),
        "derived_dob": score_field(derived.date_of_birth, gold.get("derived_dob")),
        "derived_gender": score_field(derived.gender, gold.get("derived_gender")),
        "same_image_printed_derived_dob": None,
        "same_image_printed_derived_gender": _same_image_match(
            fields.gender_printed, derived.gender
        ),
    }


def case_gold(case: CaseRecord, docs: dict[str, DocumentRecord]) -> dict[str, Any]:
    front = docs[case.front_doc_id].gold
    back = docs[case.back_doc_id].gold
    return {
        "card_serial": front.get("card_serial"),
        "front_national_id": front.get("national_id"),
        "back_national_id": back.get("national_id"),
        "issue_date": back.get("issue_date"),
        "expiry_date": back.get("expiry_date"),
        "gender_printed": back.get("gender_printed"),
        "religion_printed": back.get("religion_printed"),
        "marital_status_ar": back.get("marital_status_ar"),
        "date_of_birth_printed": front.get("date_of_birth_printed"),
        "dob_readable": bool(front.get("dob_readable")),
        "derived_dob": front.get("derived_dob") or back.get("derived_dob"),
        "derived_gender": back.get("derived_gender") or front.get("derived_gender"),
    }


def score_case_result(result: IdDocumentResult, gold: dict[str, Any]) -> dict[str, Any]:
    dob_readable = bool(gold.get("dob_readable"))
    nid_match = bool(
        result.front.national_id.status == FieldStatus.present
        and result.back.national_id.status == FieldStatus.present
        and result.front.national_id.value == result.back.national_id.value
    )
    return {
        "card_serial": score_field(result.front.card_serial, gold.get("card_serial")),
        "front_national_id": score_field(result.front.national_id, gold.get("front_national_id")),
        "back_national_id": score_field(result.back.national_id, gold.get("back_national_id")),
        "issue_date": score_field(result.back.issue_date, gold.get("issue_date")),
        "expiry_date": score_field(result.back.expiry_date, gold.get("expiry_date")),
        "gender_printed": score_field(result.back.gender_printed, gold.get("gender_printed")),
        "religion_printed": score_field(result.back.religion_printed, gold.get("religion_printed")),
        "marital_status_ar": score_field(result.back.marital_status_ar, gold.get("marital_status_ar")),
        "date_of_birth_printed": score_field(
            result.front.date_of_birth_printed,
            gold.get("date_of_birth_printed") if dob_readable else None,
            expect_value=dob_readable,
        ),
        "derived_dob": score_field(result.derived.date_of_birth, gold.get("derived_dob")),
        "derived_gender": score_field(result.derived.gender, gold.get("derived_gender")),
        "front_back_nid_match": nid_match,
    }


def require_verified_case(case: CaseRecord, docs: dict[str, DocumentRecord]) -> tuple[DocumentRecord, DocumentRecord]:
    """Return attested front/back docs. Never invent a mate."""
    front = docs.get(case.front_doc_id)
    back = docs.get(case.back_doc_id)
    if front is None or back is None:
        raise UnverifiedPairError(f"{case.case_id}: missing front or back document")
    if front.side != "front" or back.side != "back":
        raise UnverifiedPairError(f"{case.case_id}: sides do not match front/back")
    for doc in (front, back):
        if doc.pairing != "verified_pair":
            raise UnverifiedPairError(f"{doc.doc_id} is not a verified pair member")
        if doc.case_id != case.case_id:
            raise UnverifiedPairError(f"{doc.doc_id} case_id does not match {case.case_id}")
        if doc.pair_evidence not in ALLOWED_PAIR_EVIDENCE:
            raise UnverifiedPairError(f"{doc.doc_id} has invalid pair evidence")
    if case.pair_evidence not in ALLOWED_PAIR_EVIDENCE:
        raise UnverifiedPairError(f"{case.case_id} has invalid pair evidence")
    if case.pair_evidence != front.pair_evidence or case.pair_evidence != back.pair_evidence:
        raise UnverifiedPairError(f"{case.case_id} evidence mismatch across documents")
    return front, back


def load_manifest(path: Path, *, root: Path, require_files: bool = True) -> EvalManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = tuple(
        _parse_document(item, root=root, require_files=require_files)
        for item in payload.get("documents", [])
    )
    doc_ids = [doc.doc_id for doc in documents]
    if len(doc_ids) != len(set(doc_ids)):
        raise ValueError("duplicate doc_id in manifest")
    cases = tuple(_parse_case(item) for item in payload.get("cases", []))
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate case_id in manifest")
    by_id = {doc.doc_id: doc for doc in documents}
    for case in cases:
        require_verified_case(case, by_id)
    return EvalManifest(documents=documents, cases=cases)


def dump_manifest(manifest: EvalManifest, path: Path, *, root: Path, note: str) -> None:
    documents = []
    for doc in manifest.documents:
        rel = doc.path
        try:
            rel = doc.path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = doc.path
        documents.append(
            {
                "doc_id": doc.doc_id,
                "side": doc.side,
                "path": str(rel).replace("\\", "/"),
                "pairing": doc.pairing,
                "case_id": doc.case_id,
                "pair_evidence": doc.pair_evidence,
                "gold": doc.gold,
            }
        )
    payload = {
        "note": note,
        "documents": documents,
        "cases": [
            {
                "case_id": case.case_id,
                "front_doc_id": case.front_doc_id,
                "back_doc_id": case.back_doc_id,
                "pair_evidence": case.pair_evidence,
            }
            for case in manifest.cases
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _same_image_match(printed: FieldValue, derived: FieldValue) -> bool | None:
    if printed.status != FieldStatus.present or derived.status != FieldStatus.present:
        return None
    return printed.value == derived.value


def _parse_document(item: dict[str, Any], *, root: Path, require_files: bool) -> DocumentRecord:
    doc_id = str(item["doc_id"])
    side = item.get("side")
    if side not in {"front", "back"}:
        raise ValueError(f"{doc_id}: side must be front or back")
    pairing = item.get("pairing")
    if pairing not in ALLOWED_PAIRING:
        raise ValueError(f"{doc_id}: pairing must be unpaired or verified_pair")
    evidence = item.get("pair_evidence")
    case_id = item.get("case_id")
    if evidence in FORBIDDEN_PAIR_EVIDENCE:
        raise ValueError(f"{doc_id}: pair_evidence {evidence} is not allowed")
    if pairing == "unpaired":
        if case_id is not None:
            raise ValueError(f"{doc_id}: unpaired documents cannot have a case_id")
        if evidence is not None:
            raise ValueError(f"{doc_id}: unpaired documents cannot have pair_evidence")
    else:
        if not case_id:
            raise ValueError(f"{doc_id}: verified_pair requires case_id")
        if evidence not in ALLOWED_PAIR_EVIDENCE:
            raise ValueError(f"{doc_id}: verified_pair requires allowed pair_evidence")
    path = Path(item["path"])
    if not path.is_absolute():
        path = root / path
    if require_files and not path.is_file():
        raise FileNotFoundError(f"{doc_id}: missing image {path}")
    return DocumentRecord(
        doc_id=doc_id,
        side=side,
        path=path,
        pairing=pairing,
        case_id=case_id,
        pair_evidence=evidence,
        gold=dict(item.get("gold") or {}),
    )


def _parse_case(item: dict[str, Any]) -> CaseRecord:
    evidence = item.get("pair_evidence")
    if evidence in FORBIDDEN_PAIR_EVIDENCE:
        raise ValueError(f"{item.get('case_id')}: pair_evidence {evidence} is not allowed")
    if evidence not in ALLOWED_PAIR_EVIDENCE:
        raise ValueError(f"{item.get('case_id')}: pair_evidence is not attested")
    return CaseRecord(
        case_id=str(item["case_id"]),
        front_doc_id=str(item["front_doc_id"]),
        back_doc_id=str(item["back_doc_id"]),
        pair_evidence=evidence,
    )
