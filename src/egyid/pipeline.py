from __future__ import annotations

import time
from datetime import date

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
    assess_quality,
    barcode_present,
    crop_normalized,
    load_image,
    portrait_present,
    to_image_validation,
)
from egyid.logging_utils import get_logger
from egyid.models import FieldSource, FieldValue, IdDocumentResult
from egyid.ocr import OcrEngine, default_engine
from egyid.validate import derive_from_nid, run_checks

logger = get_logger(__name__)


def process_id_pair(
    front_bytes: bytes,
    back_bytes: bytes,
    *,
    front_content_type: str | None = None,
    back_content_type: str | None = None,
    engine: OcrEngine | None = None,
    today: date | None = None,
) -> IdDocumentResult:
    """Run the staged ID pipeline on separate front and back images."""
    ocr = engine or default_engine()
    ocr_ms: dict[str, int] = {}

    front_loaded = load_image(front_bytes, front_content_type)
    back_loaded = load_image(back_bytes, back_content_type)
    front_quality = assess_quality(front_loaded.array) if front_loaded.ok else None
    back_quality = assess_quality(back_loaded.array) if back_loaded.ok else None
    front_image = to_image_validation(front_loaded, front_quality)
    back_image = to_image_validation(back_loaded, back_quality)

    front_fields = extract_front(_ocr_or_empty(ocr, front_loaded.array, ocr_ms, "front") if front_loaded.ok else [])
    back_lines = _ocr_or_empty(ocr, back_loaded.array, ocr_ms, "back") if back_loaded.ok else []
    barcode_ok, barcode_score = (
        barcode_present(back_loaded.array) if back_loaded.ok else (False, 0.0)
    )
    back_fields = extract_back(back_lines, barcode_ok, barcode_score)
    if back_loaded.ok:
        gender_lines = _ocr_or_empty(
            ocr, crop_normalized(back_loaded.array, GENDER_CROP), ocr_ms, "back_gender"
        )
        religion_lines = _ocr_or_empty(
            ocr, crop_normalized(back_loaded.array, RELIGION_CROP), ocr_ms, "back_religion"
        )
        back_fields.gender_printed = merge_present(
            extract_mapped_keyword(gender_lines, GENDER_MAP, unread_if_text=True),
            back_fields.gender_printed,
        )
        back_fields.religion_printed = merge_present(
            extract_mapped_keyword(religion_lines, RELIGION_VALUES, unread_if_text=True),
            back_fields.religion_printed,
        )
        expiry_lines = _ocr_or_empty(
            ocr, crop_normalized(back_loaded.array, EXPIRY_CROP), ocr_ms, "back_expiry"
        )
        crop_expiry = with_extraction_trace(
            extract_expiry_date(expiry_lines),
            "crop",
            " ".join(ln.text for ln in expiry_lines) or None,
        )
        full_expiry = with_extraction_trace(back_fields.expiry_date, "full_image")
        back_fields.expiry_date = merge_present(crop_expiry, full_expiry)

    if front_loaded.ok:
        portrait_ok, portrait_score = portrait_present(front_loaded.array)
        front_fields.portrait_present = (
            FieldValue.present("true", portrait_score, FieldSource.visual)
            if portrait_ok
            else FieldValue.unreadable(portrait_score, FieldSource.visual)
        )

    derived = derive_from_nid(front_fields, back_fields)
    checks, derived, decision, reasons = run_checks(
        front_fields, back_fields, derived, front_image, back_image, today=today
    )

    logger.info("pipeline decision=%s reasons=%s", decision.value, ",".join(reasons) or "none")
    return IdDocumentResult(
        decision=decision,
        reasons=reasons,
        front_image=front_image,
        back_image=back_image,
        front=front_fields,
        back=back_fields,
        derived=derived,
        checks=checks,
        ocr_ms=ocr_ms,
    )


def _ocr_or_empty(
    engine: OcrEngine, image: np.ndarray, timings: dict[str, int], label: str
) -> list:
    started = time.perf_counter()
    try:
        lines = engine.run(image)
    except Exception:
        logger.exception("ocr failed on %s image", label)
        lines = []
    timings[label] = int((time.perf_counter() - started) * 1000)
    return lines
