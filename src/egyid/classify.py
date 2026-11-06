from __future__ import annotations

import re

import numpy as np

from egyid.extract import is_card_title
from egyid.imaging import CardDetection, looks_like_id_card
from egyid.models import (
    BackFields,
    CheckResult,
    Decision,
    DocumentClass,
    DocumentClassification,
    FieldStatus,
    FrontFields,
    Severity,
)
from egyid.nid import parse_national_id
from egyid.ocr import OcrLine

MRZ_RE = re.compile(r"\bP<[A-Z]{3}|[A-Z0-9]<[A-Z<]{20,}")
PASSPORT_MARKERS = ("passport", "جواز السفر", "جواز سفر")
LICENSE_MARKERS = ("رخصة قيادة", "driving licence", "driving license", "driver license", "driver licence")
OTHER_DOC_MARKERS = (
    "birth certificate",
    "شهادة ميلاد",
    "invoice",
    "فاتورة",
    "utility bill",
    "visa",
    "إقامة",
    "residence permit",
)
EXPIRY_MARKERS = ("سارية", "حتى")
GENDER_MARKERS = ("ذكر", "أنثى", "انثى")
RELIGION_MARKERS = ("مسلم", "مسلمة", "مسيحي", "مسيحية", "يهودي", "يهودية")
SERIAL_RE = re.compile(r"\b[A-Z]{2}\s?\d{6,10}\b", re.I)
STRONG_NON_DOC = frozenset({"not_a_document", "no_card_texture"})


def _joined(lines: list[OcrLine]) -> str:
    return " ".join(line.text for line in lines)


def _has_any(blob: str, markers: tuple[str, ...]) -> bool:
    lower = blob.lower()
    for marker in markers:
        if not marker.isascii():
            if marker in blob:
                return True
            continue
        needle = marker.lower()
        if " " in needle or len(needle) > 8:
            if needle in lower:
                return True
        elif re.search(rf"\b{re.escape(needle)}\b", lower):
            return True
    return False


def _visual(image: np.ndarray | None, side: str) -> CardDetection:
    if image is None or image.size == 0:
        return CardDetection(False, 0.0, "missing")
    return looks_like_id_card(image, side)


def _arabic_name_band(lines: list[OcrLine]) -> bool:
    return any(
        line.cx >= 0.38
        and 0.22 <= line.cy < 0.62
        and any("\u0600" <= ch <= "\u06FF" for ch in line.text)
        for line in lines
    )


def classify_id_pair(
    *,
    front_image: np.ndarray | None,
    back_image: np.ndarray | None,
    front_lines: list[OcrLine],
    back_lines: list[OcrLine],
    front_fields: FrontFields,
    back_fields: BackFields,
    portrait_ok: bool = False,
    barcode_ok: bool = False,
) -> DocumentClassification:
    """Deterministic target / non-target / uncertain label. Not authenticity."""
    front_visual = _visual(front_image, "front")
    back_visual = _visual(back_image, "back")
    blob = f"{_joined(front_lines)} {_joined(back_lines)}"

    egy_ocr: list[str] = []
    if any(is_card_title(line.text) for line in front_lines):
        egy_ocr.append("card_title")
    if all(token in blob for token in EXPIRY_MARKERS):
        egy_ocr.append("expiry_phrase")
    if _has_any(blob, GENDER_MARKERS):
        egy_ocr.append("printed_gender")
    if _has_any(blob, RELIGION_MARKERS):
        egy_ocr.append("printed_religion")
    if SERIAL_RE.search(blob) or front_fields.card_serial.status == FieldStatus.present:
        egy_ocr.append("card_serial")
    if _arabic_name_band(front_lines):
        egy_ocr.append("arabic_name_band")

    strong_ocr = [item for item in egy_ocr if item in {"card_title", "expiry_phrase", "printed_gender", "printed_religion", "card_serial"}]

    egy_visual: list[str] = []
    if front_visual.ok:
        egy_visual.append("visual_front")
    if back_visual.ok:
        egy_visual.append("visual_back")
    if portrait_ok:
        egy_visual.append("front_portrait")
    if barcode_ok:
        egy_visual.append("back_barcode")

    nid_ok = False
    for field in (front_fields.national_id, back_fields.national_id):
        parsed = parse_national_id(field.value) if field.status == FieldStatus.present else None
        if parsed and parsed.ok:
            nid_ok = True
            break

    foreign: list[str] = []
    if MRZ_RE.search(blob.replace(" ", "")) or MRZ_RE.search(blob):
        foreign.append("mrz")
    if _has_any(blob, PASSPORT_MARKERS):
        foreign.append("passport")
    if _has_any(blob, LICENSE_MARKERS):
        foreign.append("driving_license")
    if _has_any(blob, OTHER_DOC_MARKERS):
        foreign.append("other_document")

    evidence = [*egy_visual, *egy_ocr]
    if nid_ok:
        evidence.append("nid_structure")
    evidence.extend(f"foreign:{item}" for item in foreign)
    evidence.extend(
        f"visual_fail:{side}:{det.detail}"
        for side, det in (("front", front_visual), ("back", back_visual))
        if not det.ok
    )

    strong_non_doc = (
        front_visual.detail in STRONG_NON_DOC and back_visual.detail in STRONG_NON_DOC
    ) or (
        front_visual.detail in STRONG_NON_DOC
        and back_visual.detail in STRONG_NON_DOC | {"not_national_id", "not_id_shape", "missing"}
    )

    # A 14-digit NID is never sufficient on its own.
    if foreign and not strong_ocr:
        return DocumentClassification(
            label=DocumentClass.non_target,
            evidence=evidence,
            detail="OCR markers match another document type, not an Egyptian national ID.",
        )
    if strong_non_doc and not strong_ocr and not egy_visual:
        return DocumentClassification(
            label=DocumentClass.non_target,
            evidence=evidence,
            detail="Visual evidence is a scene or unrelated page, not an ID card.",
        )
    if foreign and strong_ocr:
        return DocumentClassification(
            label=DocumentClass.uncertain,
            evidence=evidence,
            detail="Egyptian ID signals conflict with another document type.",
        )
    if (len(strong_ocr) >= 2 or (strong_ocr and egy_visual)) and not foreign:
        return DocumentClassification(
            label=DocumentClass.target_id,
            evidence=evidence,
            detail="Multiple Egyptian national ID signals. Not an authenticity check.",
        )
    return DocumentClassification(
        label=DocumentClass.uncertain,
        evidence=evidence,
        detail="Insufficient evidence to accept or reject the document type.",
    )


def apply_classification(
    classification: DocumentClassification,
    decision: Decision,
    reasons: list[str],
    checks: list[CheckResult],
) -> tuple[Decision, list[str], list[CheckResult]]:
    """Non-target fails. Uncertain goes to review even if fields were unreadable."""
    if classification.label is DocumentClass.non_target:
        checks.append(
            CheckResult(
                name="document_type_non_target",
                passed=False,
                severity=Severity.error,
                detail=classification.detail,
            )
        )
        return Decision.failed, ["document_type_non_target", *reasons], checks
    if classification.label is DocumentClass.uncertain:
        checks.append(
            CheckResult(
                name="document_type_uncertain",
                passed=False,
                severity=Severity.warning,
                detail=classification.detail,
            )
        )
        return Decision.needs_review, ["document_type_uncertain", *reasons], checks
    checks.append(
        CheckResult(
            name="document_type_target_id",
            passed=True,
            severity=Severity.info,
            detail=classification.detail,
        )
    )
    return decision, reasons, checks
