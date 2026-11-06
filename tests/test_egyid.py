from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from egyid.api import create_app
from egyid.eval import (
    UnverifiedPairError,
    load_manifest,
    require_verified_case,
    score_back_document,
    score_front_document,
    score_result,
    unpaired_document,
)
from egyid.extract import (
    compact_date_from_digits,
    extract_back,
    extract_expiry_date,
    extract_front,
    extract_issue_date,
    extract_mapped_keyword,
    merge_present,
    slash_as_one_full,
    slash_as_one_month,
    with_extraction_trace,
)
from egyid.extract import GENDER_MAP, RELIGION_VALUES
from egyid.imaging import (
    EXPIRY_CROP,
    assess_quality,
    barcode_present,
    highlight_ratio,
    load_image,
    looks_like_id_card,
    specular_glare_ratio,
)
from egyid.models import BackFields, Decision, FieldSource, FieldStatus, FieldValue, FrontFields
from egyid.nid import parse_national_id
from egyid.ocr import OcrLine
from egyid.pipeline import process_id_pair
from egyid.validate import derive_from_nid, run_checks


def _png_bytes(array: np.ndarray) -> bytes:
    image = Image.fromarray(array)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _line(text: str, score: float, x1: float, y1: float, x2: float, y2: float) -> OcrLine:
    return OcrLine(text=text, score=score, x1=x1, y1=y1, x2=x2, y2=y2)


class StubEngine:
    def __init__(self, front_lines, back_lines) -> None:
        self.front_lines = front_lines
        self.back_lines = back_lines
        self.calls = 0

    def run(self, image: np.ndarray):
        self.calls += 1
        height, width = image.shape[:2]
        if height == 500 and width == 800:
            return self.front_lines
        return self.back_lines


class SequencedEngine:
    def __init__(self, sequence: list) -> None:
        self.sequence = sequence
        self.calls = 0

    def run(self, image: np.ndarray):
        idx = min(self.calls, len(self.sequence) - 1)
        self.calls += 1
        return self.sequence[idx]


FRONT_LINES = [
    _line("جمهورية مصر العربية", 0.99, 0.45, 0.04, 0.92, 0.12),
    _line("بطاقة تحقيق الشخصية", 0.98, 0.50, 0.12, 0.88, 0.18),
    _line("محمد", 0.96, 0.72, 0.20, 0.90, 0.28),
    _line("أحمد علي حسن", 0.95, 0.55, 0.28, 0.90, 0.36),
    _line("١٢ شارع النيل", 0.93, 0.52, 0.40, 0.90, 0.48),
    _line("المعادي - القاهرة", 0.92, 0.58, 0.48, 0.90, 0.56),
    _line("١٩٩٩/٠١/٠١", 0.90, 0.08, 0.64, 0.32, 0.74),
    _line("٢ ٩٩ ٠١ ٠١ ١٢ ٣٤ ٥٦ ٧", 0.94, 0.40, 0.64, 0.95, 0.76),
    _line("AB12345678", 0.97, 0.04, 0.84, 0.28, 0.94),
]

BACK_LINES = [
    _line("٢٠٢٠/٠١", 0.95, 0.18, 0.06, 0.34, 0.14),
    _line("29901011234567", 0.96, 0.40, 0.06, 0.78, 0.14),
    _line("مهندس برمجيات", 0.91, 0.40, 0.20, 0.78, 0.30),
    _line("ذكر مسلم أعزب", 0.93, 0.28, 0.34, 0.78, 0.44),
    _line("البطاقة سارية حتى ٢٠٢٧/٠١/١٥", 0.94, 0.22, 0.48, 0.80, 0.58),
    _line("1", 0.80, 0.88, 0.50, 0.96, 0.58),
]
FRONT_LINES_B = [
    _line("جمهورية مصر العربية", 0.99, 0.45, 0.04, 0.92, 0.12),
    _line("بطاقة تحقيق الشخصية", 0.98, 0.50, 0.12, 0.88, 0.18),
    _line("محمد", 0.96, 0.72, 0.20, 0.90, 0.28),
    _line("أحمد علي حسن", 0.95, 0.55, 0.28, 0.90, 0.36),
    _line("١٢ شارع النيل", 0.93, 0.52, 0.40, 0.90, 0.48),
    _line("المعادي - القاهرة", 0.92, 0.58, 0.48, 0.90, 0.56),
    _line("١٩٩٩/٠١/٠١", 0.90, 0.08, 0.64, 0.32, 0.74),
    _line("٢ ٩٨ ١٢ ١٠ ٢١ ٢٣ ٤٥ ٦", 0.94, 0.40, 0.64, 0.95, 0.76),
    _line("AB12345678", 0.97, 0.04, 0.84, 0.28, 0.94),
]
BACK_LINES_B = [
    _line("٢٠٢٠/٠١", 0.95, 0.18, 0.06, 0.34, 0.14),
    _line("29812102123456", 0.96, 0.40, 0.06, 0.78, 0.14),
    _line("مهندس برمجيات", 0.91, 0.40, 0.20, 0.78, 0.30),
    _line("ذكر مسلم أعزب", 0.93, 0.28, 0.34, 0.78, 0.44),
    _line("البطاقة سارية حتى ٢٠٢٧/٠١/١٥", 0.94, 0.22, 0.48, 0.80, 0.58),
    _line("1", 0.80, 0.88, 0.50, 0.96, 0.58),
]


def test_national_id_structure_and_derived_fields():
    parsed = parse_national_id("٢ ٩٩ ٠١ ٠١ ١٢ ٣٤ ٥٦ ٧")
    assert parsed is not None
    assert parsed.ok
    assert parsed.raw == "29901011234567"
    assert parsed.date_of_birth == "1999-01-01"
    assert parsed.gender == "أنثى"
    assert parsed.governorate_code == "12"
    assert parsed.governorate == "الدقهلية"

    invalid = parse_national_id("123")
    assert invalid is not None
    assert not invalid.ok

    bad_century = parse_national_id("19901011234567")
    assert bad_century is not None
    assert "invalid_century" in bad_century.issues


def test_front_and_back_extraction_from_layout_lines():
    front = extract_front(FRONT_LINES)
    assert front.national_id.value == "29901011234567"
    assert front.full_name_ar.value == "محمد أحمد علي حسن"
    assert "القاهرة" in (front.address_ar.value or "")
    assert front.date_of_birth_printed.value == "1999-01-01"
    assert front.card_serial.value == "AB12345678"
    assert front.header_present.status == FieldStatus.present

    back = extract_back(BACK_LINES, barcode_found=True, barcode_score=0.7)
    assert back.national_id.value == "29901011234567"
    assert back.issue_date.value == "2020-01"
    assert back.expiry_date.value == "2027-01-15"
    assert back.gender_printed.value == "ذكر"
    assert back.religion_printed.value == "مسلم"
    assert back.marital_status_ar.value == "أعزب"
    assert back.husband_name_ar.status == FieldStatus.not_applicable
    assert back.profession_ar.status == FieldStatus.present
    assert back.barcode_present.status == FieldStatus.present
    assert not hasattr(back, "footer_code")


def test_compact_dates_valid_lengths_only():
    assert compact_date_from_digits("20281031") == ("2028-10-31", None)
    assert compact_date_from_digits("202111") == (None, "2021-11")
    assert compact_date_from_digits("2021111") == (None, None)
    assert compact_date_from_digits("20210230") == (None, None)
    assert compact_date_from_digits("202113") == (None, None)
    assert compact_date_from_digits("29901011234567") == (None, None)

    back = extract_back(
        [
            _line("٢٠٢٣١٠", 0.9, 0.18, 0.06, 0.34, 0.14),
            _line("29901011234567", 0.96, 0.40, 0.06, 0.78, 0.14),
            _line("البطاقة سارية حتى ٢٠٢٨١٠٣١", 0.94, 0.22, 0.48, 0.80, 0.58),
        ],
        barcode_found=True,
        barcode_score=0.7,
    )
    assert back.issue_date.value == "2023-10"
    assert back.expiry_date.value == "2028-10-31"

    no_guess = extract_back(
        [
            _line("2021118", 0.9, 0.18, 0.06, 0.34, 0.14),
            _line("29901011234567", 0.96, 0.40, 0.06, 0.78, 0.14),
            _line("البطاقة سارية حتى ٢٠٢٨١٠٣١٩", 0.94, 0.22, 0.48, 0.80, 0.58),
        ],
        barcode_found=True,
        barcode_score=0.7,
    )
    assert no_guess.issue_date.value is None
    assert no_guess.issue_date.status == FieldStatus.unreadable
    assert no_guess.issue_date.confidence is None
    assert no_guess.expiry_date.value is None
    assert no_guess.expiry_date.status == FieldStatus.unreadable
    assert no_guess.expiry_date.confidence is None

    nid_fragment = extract_back(
        [
            _line("286011", 0.98, 0.50, 0.08, 0.62, 0.14),
            _line("21404434", 0.97, 0.70, 0.08, 0.86, 0.14),
        ],
        barcode_found=False,
        barcode_score=0.0,
    )
    assert nid_fragment.issue_date.status != FieldStatus.present

    crop_issue = extract_issue_date([_line("٢٠٢١/١١", 0.9, 0.4, 0.4, 0.8, 0.7)])
    assert crop_issue.value == "2021-11"
    crop_expiry = extract_expiry_date(
        [_line("البطاقة سارية حتى ٢٠٣٠/١٠/١٨", 0.9, 0.2, 0.3, 0.9, 0.7)]
    )
    assert crop_expiry.value == "2030-10-18"
    gender = extract_mapped_keyword([_line("ذكر", 0.9, 0.1, 0.1, 0.4, 0.4)], GENDER_MAP)
    assert gender.value == "ذكر"
    religion = extract_mapped_keyword([_line("مسلم", 0.9, 0.1, 0.1, 0.4, 0.4)], RELIGION_VALUES)
    assert religion.value == "مسلم"
    unread = extract_mapped_keyword(
        [_line("٥", 0.9, 0.1, 0.1, 0.4, 0.4)], GENDER_MAP, unread_if_text=True
    )
    assert unread.status == FieldStatus.unreadable
    assert unread.confidence is None
    kept = merge_present(FieldValue.absent(), extract_mapped_keyword([_line("ذكر", 0.9, 0.1, 0.1, 0.4, 0.4)], GENDER_MAP))
    assert kept.value == "ذكر"
    from egyid.extract import MARITAL_MAP
    longer = extract_mapped_keyword([_line("مطلقة", 0.9, 0.1, 0.1, 0.4, 0.4)], MARITAL_MAP)
    assert longer.value == "مطلقة"


def test_slash_as_one_dates_strict_pattern():
    assert slash_as_one_month("2023111") == "2023-11"
    assert slash_as_one_month("2025101") == "2025-01"
    assert slash_as_one_month("2021118") is None
    assert slash_as_one_month("2023211") is None
    assert slash_as_one_month("202311") is None
    assert slash_as_one_month("2860111") is None
    assert slash_as_one_full("2030111119") == "2030-11-19"
    assert slash_as_one_full("2028110131") == "2028-10-31"
    assert slash_as_one_full("2028110132") is None
    assert slash_as_one_full("20281031") is None
    assert slash_as_one_full("2030211119") is None

    issue = extract_issue_date([_line("٢٠٢١١١١", 0.9, 0.18, 0.06, 0.34, 0.14)])
    assert issue.value == "2021-11"
    assert issue.raw_ocr == "٢٠٢١١١١"

    expiry = extract_expiry_date(
        [_line("البطاقة سارية حتى ٢٠٢٨١١٠١٣١", 0.94, 0.22, 0.48, 0.80, 0.58)]
    )
    assert expiry.value == "2028-10-31"
    assert expiry.raw_ocr == "البطاقة سارية حتى ٢٠٢٨١١٠١٣١"

    compact_still_wins = extract_issue_date([_line("٢٠٢٣/١٠", 0.9, 0.18, 0.06, 0.34, 0.14)])
    assert compact_still_wins.value == "2023-10"
    assert compact_still_wins.raw_ocr == "٢٠٢٣/١٠"


def test_glare_ignores_white_card_background():
    white = np.full((600, 900), 230, dtype=np.uint8)
    assert highlight_ratio(white) > 0.0 or np.mean(white) > 200
    assert specular_glare_ratio(white) == 0.0
    assert assess_quality(cv2_bgr(white)).glare_detected is False

    hotspot = np.full((600, 900), 210, dtype=np.uint8)
    hotspot[60:200, 180:420] = 255
    quality = assess_quality(cv2_bgr(hotspot))
    assert quality.glare_detected is True


def cv2_bgr(gray: np.ndarray) -> np.ndarray:
    return np.stack([gray, gray, gray], axis=-1)


def test_unreadable_national_id_is_not_absent():
    lines = [_line("٢٩٩٠١٠١١٢٣٤", 0.4, 0.4, 0.66, 0.9, 0.76)]
    front = extract_front(lines)
    assert front.national_id.status == FieldStatus.unreadable
    assert front.national_id.confidence is None
    assert front.national_id.raw_ocr == "٢٩٩٠١٠١١٢٣٤"


def _ok_image():
    from egyid.models import ImageValidation, QualityReport

    quality = QualityReport(
        width=800,
        height=500,
        blur_score=200,
        brightness=120,
        glare_ratio=0.01,
        too_small=False,
        too_blurry=False,
        too_dark=False,
        too_bright=False,
        glare_detected=False,
        aspect_ratio=1.6,
    )
    return ImageValidation(ok=True, content_type="image/png", quality=quality)


BACK_LINES_MATCHED = [
    _line("٢٠٢٠/٠١", 0.95, 0.18, 0.06, 0.34, 0.14),
    _line("29901011234567", 0.96, 0.40, 0.06, 0.78, 0.14),
    _line("مهندس برمجيات", 0.91, 0.40, 0.20, 0.78, 0.30),
    _line("أنثى مسلمة عزباء", 0.93, 0.28, 0.34, 0.78, 0.44),
    _line("البطاقة سارية حتى ٢٠٢٧/٠١/١٥", 0.94, 0.22, 0.48, 0.80, 0.58),
    _line("1", 0.80, 0.88, 0.50, 0.96, 0.58),
]


def _ready_pair(back_lines=None):
    front = extract_front(FRONT_LINES)
    front.portrait_present = FieldValue.present("true", 0.9)
    back = extract_back(back_lines or BACK_LINES_MATCHED, True, 0.8)
    derived = derive_from_nid(front, back)
    return front, back, derived


def test_cross_field_validation_and_decision():
    front, back, derived = _ready_pair(BACK_LINES)
    assert derived.date_of_birth.value == "1999-01-01"
    assert derived.gender.value == "أنثى"
    image = _ok_image()
    checks, derived, decision, reasons = run_checks(front, back, derived, image, image)
    names = {item.name: item.passed for item in checks}
    assert names["front_back_nid_match"] is True
    assert names["printed_derived_dob_match"] is True
    assert names["printed_derived_gender_match"] is False
    assert names["usable_nid"] is True
    assert "front_dob_printed" not in reasons
    assert "back_religion" not in reasons
    assert decision == Decision.needs_review
    assert "printed_derived_gender_match" in reasons


def test_onboarding_decision_policy():
    image = _ok_image()
    from datetime import date as date_cls

    today = date_cls(2026, 11, 5)

    front, back, derived = _ready_pair()
    _, _, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert decision == Decision.completed
    assert reasons == []

    front, back, derived = _ready_pair()
    back.expiry_date = FieldValue.present("2020-01-15", 0.9)
    _, derived, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert decision == Decision.failed
    assert "expiry_not_past" in reasons
    assert derived.is_expired.value == "true"

    front, back, derived = _ready_pair()
    front.national_id = FieldValue.absent()
    back.national_id = FieldValue.absent()
    derived = derive_from_nid(front, back)
    _, _, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert decision == Decision.failed
    assert "usable_nid" in reasons

    front, back, derived = _ready_pair()
    back.national_id = FieldValue.present("30401011234567", 0.9)
    _, _, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert decision == Decision.failed
    assert "front_back_nid_match" in reasons

    front, back, derived = _ready_pair()
    front.national_id = FieldValue.present("32001011234567", 0.9)
    back.national_id = FieldValue.present("32001011234567", 0.9)
    derived = derive_from_nid(front, back)
    _, _, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert derived.date_of_birth.value == "2020-01-01"
    assert decision == Decision.failed
    assert "age_at_least_18" in reasons

    front, back, derived = _ready_pair()
    front.full_name_ar = FieldValue.unreadable()
    _, _, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert decision == Decision.needs_review
    assert "front_name" in reasons

    front, back, derived = _ready_pair()
    back.expiry_date = FieldValue.unreadable()
    _, derived, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert decision == Decision.needs_review
    assert "back_expiry" in reasons
    assert "expiry_not_past" not in reasons

    front, back, derived = _ready_pair()
    back.religion_printed = FieldValue.absent()
    back.marital_status_ar = FieldValue.absent()
    back.profession_ar = FieldValue.absent()
    front.date_of_birth_printed = FieldValue.unreadable()
    back.issue_date = FieldValue.absent()
    _, _, decision, reasons = run_checks(front, back, derived, image, image, today=today)
    assert decision == Decision.completed
    assert reasons == []


def test_image_quality_and_barcode_heuristic():
    blank = np.full((600, 900, 3), 20, dtype=np.uint8)
    quality = assess_quality(blank)
    assert quality.too_dark

    sharp = np.zeros((600, 900, 3), dtype=np.uint8)
    sharp[:, ::4] = 255
    found, score = barcode_present(sharp)
    assert found
    assert score > 0

    loaded = load_image(b"not-an-image")
    assert "unreadable_image" in loaded.issues


def _id_pair_images() -> tuple[np.ndarray, np.ndarray]:
    front_img = np.full((500, 800, 3), 180, dtype=np.uint8)
    back_img = np.zeros((700, 800, 3), dtype=np.uint8)
    back_img[480:, ::3] = 255
    return front_img, back_img


BACK_LINES_NO_EXPIRY = [
    _line("٢٠٢٠/٠١", 0.95, 0.18, 0.06, 0.34, 0.14),
    _line("29901011234567", 0.96, 0.40, 0.06, 0.78, 0.14),
    _line("مهندس برمجيات", 0.91, 0.40, 0.20, 0.78, 0.30),
    _line("ذكر مسلم أعزب", 0.93, 0.28, 0.34, 0.78, 0.44),
    _line("البطاقة سارية حتى ٢٠٢٨", 0.71, 0.22, 0.48, 0.80, 0.58),
]
CROP_VALID_EXPIRY = [_line("البطاقة سارية حتى ٢٠٣٠/١٠/١٨", 0.94, 0.10, 0.30, 0.90, 0.70)]
CROP_INVALID_NINE = [_line("البطاقة سارية حتى ٢٠٢٨١٠٣١٩", 0.90, 0.10, 0.30, 0.90, 0.70)]
CROP_INVALID_CALENDAR = [_line("البطاقة سارية حتى ٢٠٢١٠٢٣٠", 0.90, 0.10, 0.30, 0.90, 0.70)]


def test_expiry_crop_success_fallback_and_invalid_output():
    assert EXPIRY_CROP == (0.12, 0.40, 0.90, 0.58)

    crop = with_extraction_trace(
        extract_expiry_date(CROP_VALID_EXPIRY),
        "crop",
        CROP_VALID_EXPIRY[0].text,
    )
    full_missing = with_extraction_trace(
        extract_expiry_date(BACK_LINES_NO_EXPIRY),
        "full_image",
    )
    assert full_missing.status != FieldStatus.present
    used_crop = merge_present(crop, full_missing)
    assert used_crop.value == "2030-10-18"
    assert used_crop.extraction_source == "crop"
    assert used_crop.raw_ocr == CROP_VALID_EXPIRY[0].text

    full_ok = with_extraction_trace(
        extract_expiry_date(BACK_LINES),
        "full_image",
    )
    invalid_nine = with_extraction_trace(extract_expiry_date(CROP_INVALID_NINE), "crop")
    invalid_cal = with_extraction_trace(extract_expiry_date(CROP_INVALID_CALENDAR), "crop")
    assert invalid_nine.status != FieldStatus.present
    assert invalid_cal.status != FieldStatus.present
    fallback = merge_present(invalid_nine, full_ok)
    assert fallback.value == "2027-01-15"
    assert fallback.extraction_source == "full_image"
    assert merge_present(invalid_cal, full_ok).value == "2027-01-15"


def test_pipeline_expiry_crop_success_and_fallback():
    front_img, back_img = _id_pair_images()

    crop_engine = SequencedEngine(
        [FRONT_LINES, BACK_LINES_NO_EXPIRY, BACK_LINES_NO_EXPIRY, BACK_LINES_NO_EXPIRY, CROP_VALID_EXPIRY]
    )
    cropped = process_id_pair(_png_bytes(front_img), _png_bytes(back_img), engine=crop_engine)
    assert cropped.back.expiry_date.value == "2030-10-18"
    assert cropped.back.expiry_date.extraction_source == "crop"
    assert cropped.back.expiry_date.raw_ocr
    assert "back_expiry" in cropped.ocr_ms
    assert crop_engine.calls == 5

    fallback_engine = SequencedEngine(
        [FRONT_LINES, BACK_LINES, BACK_LINES, BACK_LINES, CROP_INVALID_NINE]
    )
    fallback = process_id_pair(_png_bytes(front_img), _png_bytes(back_img), engine=fallback_engine)
    assert fallback.back.expiry_date.value == "2027-01-15"
    assert fallback.back.expiry_date.extraction_source == "full_image"
    assert fallback_engine.calls == 5


def test_pipeline_with_stub_ocr_does_not_require_real_photos():
    front_img = np.full((500, 800, 3), 180, dtype=np.uint8)
    back_img = np.zeros((700, 800, 3), dtype=np.uint8)
    back_img[480:, ::3] = 255
    engine = StubEngine(FRONT_LINES, BACK_LINES)
    result = process_id_pair(_png_bytes(front_img), _png_bytes(back_img), engine=engine)
    assert result.front.national_id.value == "29901011234567"
    assert result.back.expiry_date.value == "2027-01-15"
    assert result.derived.date_of_birth.value == "1999-01-01"
    scored = score_result(
        result,
        {
            "front_national_id": "29901011234567",
            "derived_dob": "1999-01-01",
            "card_serial": "AB12345678",
        },
    )
    assert scored["fields_correct"] == 3


def test_ui_is_served():
    client = TestClient(create_app())
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert b"Customer portal" in page.content
    assert b"Submit application" in page.content
    desk = client.get("/review")
    assert desk.status_code == 200
    assert b"Reviewer dashboard" in desk.content
    css = client.get("/static/styles.css")
    customer_js = client.get("/static/customer.js")
    reviewer_js = client.get("/static/reviewer.js")
    assert css.status_code == 200
    assert customer_js.status_code == 200
    assert reviewer_js.status_code == 200
    assert b"/v1/applications" in customer_js.content
    assert b"egyid.customerId" in customer_js.content
    assert b"/v1/review/applications" in reviewer_js.content
    assert b"Front Side" in reviewer_js.content
    assert b"Accepted" not in reviewer_js.content


def test_api_health_and_process_with_stub(monkeypatch):
    front_img = np.full((500, 800, 3), 180, dtype=np.uint8)
    back_img = np.full((700, 800, 3), 180, dtype=np.uint8)
    back_img[500:, ::3] = 255
    engine = StubEngine(FRONT_LINES, BACK_LINES)

    from egyid import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: engine)
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post(
        "/v1/id-documents",
        files={
            "front": ("front.png", _png_bytes(front_img), "image/png"),
            "back": ("back.png", _png_bytes(back_img), "image/png"),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["front"]["national_id"]["value"] == "29901011234567"
    assert "checks" in body


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_manifest_keeps_unpaired_documents_out_of_cases(tmp_path):
    payload = {
        "documents": [
            unpaired_document(doc_id="front_only", side="front", path="front.jpg"),
            unpaired_document(doc_id="back_only", side="back", path="back.jpg"),
        ],
        "cases": [],
    }
    manifest = load_manifest(_write_manifest(tmp_path, payload), root=tmp_path, require_files=False)
    assert len(manifest.documents) == 2
    assert manifest.verified_cases() == ()
    assert all(doc.pairing == "unpaired" and doc.case_id is None for doc in manifest.documents)


def test_manifest_rejects_filename_or_nid_pair_evidence(tmp_path):
    for evidence in ("filename", "nid_match", "leftover"):
        payload = {
            "documents": [
                {
                    "doc_id": "front_01",
                    "side": "front",
                    "path": "front.jpg",
                    "pairing": "verified_pair",
                    "case_id": "case_01",
                    "pair_evidence": evidence,
                    "gold": {},
                }
            ],
            "cases": [],
        }
        with pytest.raises(ValueError):
            load_manifest(_write_manifest(tmp_path, payload), root=tmp_path, require_files=False)


def test_verified_case_requires_attested_same_card(tmp_path):
    payload = {
        "documents": [
            {
                "doc_id": "front_01",
                "side": "front",
                "path": "front.jpg",
                "pairing": "verified_pair",
                "case_id": "case_01",
                "pair_evidence": "donor_same_card",
                "gold": {"national_id": "29901011234567"},
            },
            {
                "doc_id": "back_01",
                "side": "back",
                "path": "back.jpg",
                "pairing": "verified_pair",
                "case_id": "case_01",
                "pair_evidence": "donor_same_card",
                "gold": {"national_id": "29901011234567"},
            },
        ],
        "cases": [
            {
                "case_id": "case_01",
                "front_doc_id": "front_01",
                "back_doc_id": "back_01",
                "pair_evidence": "donor_same_card",
            }
        ],
    }
    manifest = load_manifest(_write_manifest(tmp_path, payload), root=tmp_path, require_files=False)
    front, back = require_verified_case(manifest.cases[0], manifest.documents_by_id())
    assert front.doc_id == "front_01"
    assert back.doc_id == "back_01"


def test_unpaired_docs_cannot_be_forced_into_process_id_pair(tmp_path):
    payload = {
        "documents": [
            unpaired_document(doc_id="front_only", side="front", path="front.jpg"),
            unpaired_document(doc_id="back_only", side="back", path="back.jpg"),
        ],
        "cases": [
            {
                "case_id": "forged",
                "front_doc_id": "front_only",
                "back_doc_id": "back_only",
                "pair_evidence": "donor_same_card",
            }
        ],
    }
    with pytest.raises(UnverifiedPairError):
        load_manifest(_write_manifest(tmp_path, payload), root=tmp_path, require_files=False)


def test_document_score_uses_same_image_nid_only():
    front = FrontFields(
        national_id=FieldValue.present("29901011234567", 0.9),
        date_of_birth_printed=FieldValue.present("1999-01-01", 0.9),
        card_serial=FieldValue.present("AB1234567", 0.9),
    )
    scored = score_front_document(
        front,
        {
            "card_serial": "AB1234567",
            "national_id": "29901011234567",
            "date_of_birth_printed": "1999-01-01",
            "dob_readable": True,
            "derived_dob": "1999-01-01",
            "derived_gender": "أنثى",
        },
    )
    assert scored["derived_dob"]["correct"] is True
    assert scored["derived_gender"]["correct"] is True
    assert scored["same_image_printed_derived_dob"] is True

    back = BackFields(
        national_id=FieldValue.present("29901011234567", 0.9),
        gender_printed=FieldValue.present("أنثى", 0.9),
        expiry_date=FieldValue.present("2028-10-31", 0.9),
    )
    back_scored = score_back_document(
        back,
        {
            "national_id": "29901011234567",
            "gender_printed": "أنثى",
            "expiry_date": "2028-10-31",
            "derived_gender": "أنثى",
        },
    )
    assert back_scored["same_image_printed_derived_gender"] is True
    assert back_scored["expiry_date"]["correct"] is True


def test_synthetic_parser_strings_are_not_an_ocr_eval():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    from eval_synthetic_back_fields import evaluate_parsers

    summary = evaluate_parsers()
    assert summary["n_samples"] == 14
    assert "Not an OCR" in summary["note"] or "Parser-only" in summary["note"]
    assert summary["expiry_valid"]["incorrect"] == 0
    assert summary["expiry_invalid_correctly_rejected_or_false"]["false_dates"] == 0
    assert summary["gender"]["incorrect"] == 0
    assert summary["marital"]["incorrect"] == 0


def _sample_extraction():
    front_img = np.full((500, 800, 3), 180, dtype=np.uint8)
    back_img = np.full((700, 800, 3), 180, dtype=np.uint8)
    back_img[500:, ::3] = 255
    result = process_id_pair(
        _png_bytes(front_img),
        _png_bytes(back_img),
        front_content_type="image/png",
        back_content_type="image/png",
        engine=StubEngine(FRONT_LINES, BACK_LINES),
    )
    return result.model_dump_public()


def test_review_validates_and_preserves_original_arabic():
    from egyid.review import FIELD_SPECS, MSG_DATE, MSG_NAME_DIGITS, MSG_NID, MSG_SERIAL, build_review

    extraction = _sample_extraction()
    assert "footer_code" not in extraction["back"]
    original_name = extraction["front"]["full_name_ar"]["value"]
    assert "محمد" in original_name

    record, complete = build_review(extraction, {})
    assert complete
    ids = [item["id"] for item in record["fields"]]
    assert "back.footer_code" not in ids
    assert "GENDER_MAP" not in json.dumps(record)
    name_row = next(item for item in record["fields"] if item["id"] == "front.full_name_ar")
    assert name_row["original"]["value"] == original_name
    assert name_row["corrected_value"] is None
    assert name_row["final_value"] == original_name

    edited, ok = build_review(
        extraction,
        {"front.full_name_ar": "محمد أحمد علي حسن", "derived.gender": "أنثى"},
    )
    name_row = next(item for item in edited["fields"] if item["id"] == "front.full_name_ar")
    gender_row = next(item for item in edited["fields"] if item["id"] == "derived.gender")
    assert ok
    assert name_row["original"]["value"] == original_name
    assert name_row["corrected_value"] == "محمد أحمد علي حسن"
    assert name_row["final_value"] == "محمد أحمد علي حسن"
    assert gender_row["original"]["value"] == extraction["derived"]["gender"]["value"]
    assert gender_row["corrected_value"] == "أنثى"
    assert gender_row["final_value"] == "أنثى"
    assert extraction["front"]["full_name_ar"]["value"] == original_name

    failed, ok = build_review(
        extraction,
        {
            "front.full_name_ar": "محمد 12",
            "front.national_id": "123",
            "back.expiry_date": "2020-01-01",
            "front.card_serial": "12",
            "back.religion_printed": "unknown",
        },
    )
    assert not ok
    by_id = {item["id"]: item for item in failed["fields"]}
    assert MSG_NAME_DIGITS in by_id["front.full_name_ar"]["review_validation"]["messages"]
    assert MSG_NID in by_id["front.national_id"]["review_validation"]["messages"]
    assert MSG_DATE in by_id["back.expiry_date"]["review_validation"]["messages"]
    assert MSG_SERIAL in by_id["front.card_serial"]["review_validation"]["messages"]
    assert "Unsupported categorical value." in by_id["back.religion_printed"]["review_validation"]["messages"]
    empty, empty_ok = build_review(extraction, {"front.full_name_ar": "   "})
    assert not empty_ok
    assert "Required field is empty." in next(
        item for item in empty["fields"] if item["id"] == "front.full_name_ar"
    )["review_validation"]["messages"]
    assert {spec.path for spec in FIELD_SPECS} == {item["id"] for item in record["fields"]}
    assert failed["automated_decision"] == extraction["decision"]
    assert failed["review_decision"] != failed["automated_decision"] or not ok


def test_review_api_persists_json(monkeypatch, tmp_path):
    from egyid import review as review_mod

    monkeypatch.setattr(review_mod, "REVIEW_DIR", tmp_path)
    extraction = _sample_extraction()
    client = TestClient(create_app())
    blocked = client.post(
        "/v1/reviews",
        json={"extraction": extraction, "corrections": {"front.full_name_ar": "Name12"}},
    )
    assert blocked.status_code == 422
    assert list(tmp_path.glob("*.json")) == []
    saved = client.post("/v1/reviews", json={"extraction": extraction, "corrections": {}})
    assert saved.status_code == 200
    body = saved.json()
    assert body["review"]["review_complete"] is True
    assert "download_url" in body
    downloaded = client.get(body["download_url"])
    assert downloaded.status_code == 200
    payload = downloaded.json()
    assert payload["fields"][0]["original"]["value"] == extraction["front"]["full_name_ar"]["value"]
    assert all(item["id"] != "back.footer_code" for item in payload["fields"])
    assert (tmp_path / f"{body['review_id']}.json").is_file()


CUSTOMER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CUSTOMER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _png_pair(tag: int = 0):
    front_img = np.full((500, 800, 3), 180, dtype=np.uint8)
    back_img = np.full((700, 800, 3), 180, dtype=np.uint8)
    back_img[500:, ::3] = 255
    front_img[0, 0] = tag
    back_img[0, 0] = tag
    return {
        "front": ("front.png", _png_bytes(front_img), "image/png"),
        "back": ("back.png", _png_bytes(back_img), "image/png"),
    }


def _submit(client, path="/v1/applications", customer_id=CUSTOMER_A, files=None):
    return client.post(path, files=files or _png_pair(), data={"customer_id": customer_id})


def _workflow_client(tmp_path, monkeypatch, back_lines=None, *, auto_approve=False):
    from egyid import pipeline as pipeline_mod
    from egyid import workflow as workflow_mod
    from egyid.imaging import CardDetection
    from egyid.models import QualityReport
    from egyid.store import Store

    lines = BACK_LINES_MATCHED if auto_approve else (back_lines or BACK_LINES)
    engine = StubEngine(FRONT_LINES, lines)
    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: engine)
    monkeypatch.setattr(
        workflow_mod,
        "looks_like_id_card",
        lambda image, side="front": CardDetection(True, 1.0, "stub"),
    )
    if auto_approve:
        quality = QualityReport(
            width=800,
            height=500,
            blur_score=200,
            brightness=120,
            glare_ratio=0.0,
            too_small=False,
            too_blurry=False,
            too_dark=False,
            too_bright=False,
            glare_detected=False,
            aspect_ratio=1.6,
            issues=[],
        )
        monkeypatch.setattr(pipeline_mod, "assess_quality", lambda img: quality)
        monkeypatch.setattr(pipeline_mod, "portrait_present", lambda img: (True, 0.9))
    store = Store(tmp_path / "egyid.sqlite", tmp_path / "uploads")
    return TestClient(create_app(store=store)), store


def test_customer_auto_approve_and_next_stage(monkeypatch, tmp_path):
    client, _store = _workflow_client(tmp_path, monkeypatch, auto_approve=True)
    created = _submit(client)
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "approved"
    assert body["next_stage"]["available"] is True
    assert body["stage"] == "application_form"
    again = client.get(f"/v1/applications/{body['id']}")
    assert again.json()["status"] == "approved"
    assert "footer_code" not in client.get("/v1/review/applications/" + body["id"]).json()["extraction"]["back"]


def test_reviewer_decision_updates_customer_status(monkeypatch, tmp_path):
    client, store = _workflow_client(tmp_path, monkeypatch)
    created = _submit(client).json()
    assert created["status"] == "pending_review"
    app_id = created["id"]
    listed = client.get("/v1/review/applications").json()["applications"]
    assert listed[0]["id"] == app_id
    blocked = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "accept", "corrections": {"front.full_name_ar": "Name12"}},
    )
    assert blocked.status_code == 422
    assert client.get(f"/v1/applications/{app_id}").json()["status"] == "pending_review"

    rejected = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "reject", "reason": "Photo does not match a valid ID card."},
    )
    assert rejected.status_code == 200
    customer = client.get(f"/v1/applications/{app_id}").json()
    assert customer["status"] == "rejected"
    assert "Photo does not match" in customer["customer_message"]
    assert customer["reviewer_action"] == "rejected"

    from egyid.store import Store

    restarted = TestClient(create_app(store=Store(tmp_path / "egyid.sqlite", tmp_path / "uploads")))
    persisted = restarted.get(f"/v1/applications/{app_id}").json()
    assert persisted["status"] == "rejected"
    assert persisted["customer_message"] == customer["customer_message"]


def test_reviewer_accept_and_resubmission_flow(monkeypatch, tmp_path):
    client, _store = _workflow_client(tmp_path, monkeypatch)
    pending = _submit(client).json()
    app_id = pending["id"]
    ask = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "resubmit", "reason": "Please recapture the back of the card."},
    )
    assert ask.status_code == 200
    waiting = client.get(f"/v1/applications/{app_id}").json()
    assert waiting["status"] == "resubmission_required"
    assert waiting["can_resubmit"] is True

    again = _submit(client, f"/v1/applications/{app_id}/resubmit")
    assert again.status_code == 200
    assert again.json()["status"] == "pending_review"

    accepted = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "accept", "corrections": {}},
    )
    assert accepted.status_code == 200, accepted.text
    done = client.get(f"/v1/applications/{app_id}").json()
    assert done["status"] == "approved"
    assert done["next_stage"]["available"] is True
    detail = client.get(f"/v1/review/applications/{app_id}").json()
    assert detail["review"]["automated_decision"] == pending["automated_decision"]
    name = next(item for item in detail["review"]["fields"] if item["id"] == "front.full_name_ar")
    assert name["original"]["value"] == name["final_value"]
    assert "محمد" in (name["final_value"] or "")
    assert client.get(f"/v1/applications/{app_id}/front").status_code == 200
    assert client.get(f"/v1/applications/{app_id}/back").status_code == 200


def test_extraction_keeps_arabic_categorical_values():
    from egyid.review import build_review

    extraction = _sample_extraction()
    assert extraction["back"]["gender_printed"]["value"] == "ذكر"
    assert extraction["derived"]["gender"]["value"] == "أنثى"
    assert extraction["back"]["marital_status_ar"]["value"] == "أعزب"
    record, _ok = build_review(extraction, {})
    groups = {item["group"] for item in record["fields"]}
    assert groups == {"front", "back"}


def test_customer_identities_are_isolated(monkeypatch, tmp_path):
    from egyid import pipeline as pipeline_mod

    client, _store = _workflow_client(tmp_path, monkeypatch)
    first = _submit(client, customer_id=CUSTOMER_A).json()
    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: StubEngine(FRONT_LINES_B, BACK_LINES_B))
    second = _submit(client, customer_id=CUSTOMER_B, files=_png_pair(tag=1)).json()
    listed_a = client.get(f"/v1/customers/{CUSTOMER_A}/applications").json()["applications"]
    listed_b = client.get(f"/v1/customers/{CUSTOMER_B}/applications").json()["applications"]
    assert [item["id"] for item in listed_a] == [first["id"]]
    assert [item["id"] for item in listed_b] == [second["id"]]
    assert first["customer_id"] == CUSTOMER_A
    blocked = client.get("/v1/customers/not-a-uuid/applications")
    assert blocked.status_code == 400


def test_invalid_uploads_are_rejected_before_reviewer_queue(monkeypatch, tmp_path):
    client, store = _workflow_client(tmp_path, monkeypatch)
    garbage = client.post(
        "/v1/applications",
        files={
            "front": ("front.png", b"not-an-image", "image/png"),
            "back": ("back.png", b"also-bad", "image/png"),
        },
        data={"customer_id": CUSTOMER_A},
    )
    assert garbage.status_code == 422
    body = garbage.json()
    assert "unreadable" in body["customer_message"].lower() or "corrupted" in body["customer_message"].lower()
    assert body["reasons"]
    assert any(item.startswith("Front image:") for item in body["reasons"])
    assert any(item.startswith("Back image:") for item in body["reasons"])
    assert "\n" in body["customer_message"]
    assert store.list_all() == []
    assert client.get("/v1/review/applications").json()["applications"] == []

    empty = client.post(
        "/v1/applications",
        files={
            "front": ("front.png", b"", "image/png"),
            "back": _png_pair()["back"],
        },
        data={"customer_id": CUSTOMER_A},
    )
    assert empty.status_code == 422
    assert "empty" in empty.json()["customer_message"].lower()
    assert store.list_all() == []

    tiny = _png_bytes(np.full((50, 50, 3), 180, dtype=np.uint8))
    small = client.post(
        "/v1/applications",
        files={
            "front": ("front.png", tiny, "image/png"),
            "back": ("back.png", tiny, "image/png"),
        },
        data={"customer_id": CUSTOMER_A},
    )
    assert small.status_code == 422
    assert "too small" in small.json()["customer_message"].lower()
    assert store.list_all() == []

    dark = np.full((500, 800, 3), 12, dtype=np.uint8)
    dark_back = np.full((700, 800, 3), 12, dtype=np.uint8)
    dark_back[500:, ::3] = 30
    created = client.post(
        "/v1/applications",
        files={
            "front": ("front.png", _png_bytes(dark), "image/png"),
            "back": ("back.png", _png_bytes(dark_back), "image/png"),
        },
        data={"customer_id": CUSTOMER_A},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "pending_review"
    assert len(store.list_all()) == 1

    gif_buf = io.BytesIO()
    Image.fromarray(np.full((500, 800, 3), 180, dtype=np.uint8)).save(gif_buf, format="GIF")
    unsupported = client.post(
        "/v1/applications",
        files={
            "front": ("front.gif", gif_buf.getvalue(), "application/octet-stream"),
            "back": _png_pair()["back"],
        },
        data={"customer_id": CUSTOMER_A},
    )
    assert unsupported.status_code == 422
    assert "supported" in unsupported.json()["customer_message"].lower()
    assert len(store.list_all()) == 1


def test_reviewer_cannot_accept_mismatched_national_ids(monkeypatch, tmp_path):
    from datetime import date as date_cls

    from egyid.review import MSG_NID_MISMATCH, build_review

    extraction = _sample_extraction()
    mismatched, complete = build_review(
        extraction,
        {
            "front.national_id": "29901011234567",
            "back.national_id": "30401011234567",
        },
        today=date_cls(2026, 11, 6),
    )
    assert complete is False
    by_id = {item["id"]: item for item in mismatched["fields"]}
    assert MSG_NID_MISMATCH in by_id["front.national_id"]["review_validation"]["messages"]
    assert MSG_NID_MISMATCH in by_id["back.national_id"]["review_validation"]["messages"]
    assert any(check["name"] == "front_back_nid_match" and not check["passed"] for check in mismatched["cross_checks"])

    spaced, ok = build_review(
        extraction,
        {
            "front.national_id": "2 9901011234567",
            "back.national_id": "٢٩٩٠١٠١١٢٣٤٥٦٧",
        },
        today=date_cls(2026, 11, 6),
    )
    assert ok is True

    client, _store = _workflow_client(tmp_path, monkeypatch)
    pending = _submit(client).json()
    blocked = client.post(
        f"/v1/review/applications/{pending['id']}/decision",
        json={
            "action": "accept",
            "corrections": {
                "front.national_id": "29901011234567",
                "back.national_id": "30401011234567",
            },
        },
    )
    assert blocked.status_code == 422
    assert MSG_NID_MISMATCH in blocked.json()["detail"]
    assert client.get(f"/v1/applications/{pending['id']}").json()["status"] == "pending_review"


def test_review_expiry_requires_unambiguous_full_date():
    from datetime import date as date_cls

    from egyid.review import MSG_DATE, MSG_EXPIRY, parse_full_date, build_review

    assert parse_full_date("2030/05/17") == date_cls(2030, 5, 17)
    assert parse_full_date("2030-05-17") == date_cls(2030, 5, 17)
    assert parse_full_date("٢٠٣٠/٠٥/١٧") == date_cls(2030, 5, 17)
    assert parse_full_date("2028-01") is None
    assert parse_full_date("2021-02-30") is None

    extraction = _sample_extraction()
    today = date_cls(2026, 11, 6)
    slash, ok = build_review(extraction, {"back.expiry_date": "2030/05/17"}, today=today)
    assert ok
    expiry_row = next(item for item in slash["fields"] if item["id"] == "back.expiry_date")
    assert expiry_row["final_value"] == "2030/05/17"
    assert expiry_row["hint"] == "YYYY/MM/DD e.g. 2030/05/17"
    assert "card valid until" in expiry_row["label"]

    month_only, month_ok = build_review(extraction, {"back.expiry_date": "2028-01"}, today=today)
    assert not month_ok
    assert MSG_EXPIRY in next(
        item for item in month_only["fields"] if item["id"] == "back.expiry_date"
    )["review_validation"]["messages"]

    invalid_day, day_ok = build_review(extraction, {"back.expiry_date": "2021-02-30"}, today=today)
    assert not day_ok
    assert MSG_EXPIRY in next(
        item for item in invalid_day["fields"] if item["id"] == "back.expiry_date"
    )["review_validation"]["messages"]

    past, past_ok = build_review(extraction, {"back.expiry_date": "2020/01/01"}, today=today)
    assert not past_ok
    assert MSG_DATE in next(
        item for item in past["fields"] if item["id"] == "back.expiry_date"
    )["review_validation"]["messages"]


def test_review_date_fields_use_matching_precision():
    from datetime import date as date_cls

    from egyid.review import HINT_FULL_DOB, HINT_MONTH, MSG_DOB, MSG_ISSUE, parse_month_date, build_review

    assert parse_month_date("2021/11") == date_cls(2021, 11, 1)
    assert parse_month_date("2021-11") == date_cls(2021, 11, 1)
    assert parse_month_date("٢٠٢١/١١") == date_cls(2021, 11, 1)
    assert parse_month_date("2021-11-05") is None
    assert parse_month_date("2021") is None

    extraction = _sample_extraction()
    today = date_cls(2026, 11, 6)
    record, ok = build_review(
        extraction,
        {
            "front.date_of_birth_printed": "1999/01/15",
            "derived.date_of_birth": "1999/01/01",
            "back.issue_date": "2021/11",
            "back.expiry_date": "2030/05/17",
        },
        today=today,
    )
    assert ok
    by_id = {item["id"]: item for item in record["fields"]}
    assert by_id["front.date_of_birth_printed"]["hint"] == HINT_FULL_DOB
    assert by_id["derived.date_of_birth"]["hint"] == HINT_FULL_DOB
    assert by_id["back.issue_date"]["hint"] == HINT_MONTH
    assert "1999/01/15" in HINT_FULL_DOB
    assert "2021/11" in HINT_MONTH

    month_as_dob, dob_ok = build_review(extraction, {"derived.date_of_birth": "1999-01"}, today=today)
    assert not dob_ok
    assert MSG_DOB in next(
        item for item in month_as_dob["fields"] if item["id"] == "derived.date_of_birth"
    )["review_validation"]["messages"]

    full_as_issue, issue_ok = build_review(extraction, {"back.issue_date": "2021/11/05"}, today=today)
    assert not issue_ok
    assert MSG_ISSUE in next(
        item for item in full_as_issue["fields"] if item["id"] == "back.issue_date"
    )["review_validation"]["messages"]


def _scene_photo():
    img = np.zeros((640, 960, 3), dtype=np.uint8)
    img[:, :, 1] = 170
    img[:, :, 0] = 40
    img[:220, :] = (230, 170, 50)
    return img


def _plain_document():
    img = np.full((1100, 800, 3), 245, dtype=np.uint8)
    for y in range(80, 1000, 28):
        img[y : y + 2, 60:740] = 30
    return img


def _selfie_photo():
    img = np.full((800, 600, 3), 42, dtype=np.uint8)
    yy, xx = np.ogrid[:800, :600]
    face = (xx - 300) ** 2 / 140 ** 2 + (yy - 340) ** 2 / 180 ** 2 <= 1
    img[face] = (90, 110, 190)
    return img


def _split_id_pairs() -> list[tuple[Path, Path]]:
    split = Path("National IDs") / "Front + Back" / "data1" / "Split"
    if not split.is_dir():
        return []
    fronts: dict[str, Path] = {}
    backs: dict[str, Path] = {}
    for path in split.iterdir():
        if path.suffix.lower() not in {".jpg", ".jpeg"}:
            continue
        key = path.name[1:]
        if path.name.startswith("1"):
            fronts[key] = path
        elif path.name.startswith("2"):
            backs[key] = path
    return [(fronts[key], backs[key]) for key in sorted(fronts) if key in backs]


def _usable_id_images():
    root = Path("National IDs")
    if not root.is_dir():
        return []
    found = []
    seen = set()
    for path in list(root.rglob("*.jpeg")) + list(root.rglob("*.jpg")) + list(root.rglob("*.JPG")):
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        loaded = load_image(path.read_bytes())
        if loaded.ok and min(loaded.array.shape[:2]) >= 400:
            found.append(loaded.array)
    return found


def test_id_card_detector_accepts_real_ids_and_rejects_unrelated_photos():
    assert looks_like_id_card(_scene_photo(), "front").ok is False
    assert looks_like_id_card(_plain_document(), "front").ok is False
    assert looks_like_id_card(_selfie_photo(), "front").ok is False
    assert looks_like_id_card(np.full((500, 800, 3), 180, dtype=np.uint8), "front").ok is False

    ids = _usable_id_images()
    if not ids:
        pytest.skip("local National ID images are not available")
    missed = sum(1 for image in ids if not looks_like_id_card(image, "front").ok)
    assert missed == 0


def test_non_id_uploads_never_enter_the_reviewer_queue(tmp_path):
    from egyid.store import Store

    store = Store(tmp_path / "egyid.sqlite", tmp_path / "uploads")
    client = TestClient(create_app(store=store))
    scene = _png_bytes(_scene_photo())
    page = _png_bytes(_plain_document())
    rejected = client.post(
        "/v1/applications",
        files={
            "front": ("front.png", scene, "image/png"),
            "back": ("back.png", page, "image/png"),
        },
        data={"customer_id": CUSTOMER_A},
    )
    assert rejected.status_code == 422
    message = rejected.json()["customer_message"].lower()
    assert "national id" in message
    assert "front" in message
    assert store.list_all() == []
    assert client.get("/v1/review/applications").json()["applications"] == []


def test_real_id_pair_is_accepted_into_review(tmp_path):
    from egyid.store import Store

    split = Path("National IDs") / "Front + Back" / "data1" / "Split"
    fronts = sorted(p for p in split.glob("1*") if p.suffix.lower() in {".jpg", ".jpeg"})
    backs = sorted(p for p in split.glob("2*") if p.suffix.lower() in {".jpg", ".jpeg"})
    if not fronts or not backs:
        pytest.skip("local National ID images are not available")
    front_bytes = fronts[0].read_bytes()
    back_bytes = backs[0].read_bytes()
    front_img = load_image(front_bytes).array
    back_img = load_image(back_bytes).array
    assert looks_like_id_card(front_img, "front").ok
    assert looks_like_id_card(back_img, "back").ok

    store = Store(tmp_path / "egyid.sqlite", tmp_path / "uploads")
    client = TestClient(create_app(store=store))
    created = client.post(
        "/v1/applications",
        files={
            "front": ("front.jpg", front_bytes, "image/jpeg"),
            "back": ("back.jpg", back_bytes, "image/jpeg"),
        },
        data={"customer_id": CUSTOMER_A},
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] in {"pending_review", "approved", "rejected"}
    assert len(store.list_all()) == 1
    assert client.get("/v1/review/applications").json()["applications"][0]["id"] == created.json()["id"]
    duplicate = client.post(
        "/v1/applications",
        files={
            "front": ("front.jpg", front_bytes, "image/jpeg"),
            "back": ("back.jpg", back_bytes, "image/jpeg"),
        },
        data={"customer_id": CUSTOMER_B},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert "front image" in duplicate.json()["customer_message"].lower()
    assert "back image" in duplicate.json()["customer_message"].lower()
    assert set(duplicate.json()["duplicate"]) >= {"front", "back"}
    assert len(store.list_all()) == 1


def test_uncertain_presence_is_unreadable_not_absent():
    front = extract_front([_line("محمد", 0.9, 0.72, 0.20, 0.90, 0.28)])
    assert front.header_present.status == FieldStatus.unreadable
    assert front.header_present.value is None
    assert front.header_present.confidence is None
    back = extract_back(BACK_LINES, barcode_found=False, barcode_score=0.82)
    assert back.barcode_present.status == FieldStatus.unreadable
    assert back.barcode_present.value is None
    assert back.barcode_present.source == FieldSource.visual
    assert back.barcode_present.confidence == pytest.approx(0.82)


def test_reviewer_presence_governorate_and_expired_rules():
    from datetime import date as date_cls

    from egyid.review import MSG_EXPIRED, MSG_GOV_NID, MSG_PRESENCE, build_review

    extraction = _sample_extraction()
    original_name = extraction["front"]["full_name_ar"]["value"]
    today = date_cls(2026, 11, 6)
    record, ok = build_review(
        extraction,
        {
            "front.portrait_present": "true",
            "front.header_present": "true",
            "back.barcode_present": "false",
            "derived.birth_governorate_code": "12",
        },
        today=today,
    )
    assert ok
    by_id = {item["id"]: item for item in record["fields"]}
    assert by_id["back.barcode_present"]["label"] == "Barcode (existence)"
    assert by_id["front.portrait_present"]["editable"] is True
    assert by_id["derived.birth_governorate"]["editable"] is True
    assert by_id["derived.is_expired"]["editable"] is True
    assert by_id["back.barcode_present"]["corrected_value"] == "false"
    assert by_id["back.barcode_present"]["final_value"] == "false"
    assert extraction["back"]["barcode_present"]["value"] != "false" or extraction["back"]["barcode_present"]["status"] != "present"
    assert by_id["derived.birth_governorate"]["final_value"] == "الدقهلية"
    assert by_id["derived.birth_governorate_code"]["final_value"] == "12"
    assert by_id["derived.is_expired"]["final_value"] == "false"
    assert by_id["front.full_name_ar"]["original"]["value"] == original_name
    conf = by_id["front.full_name_ar"]["original"]["confidence"]
    assert conf is None or 0 <= conf <= 1

    contradiction, bad = build_review(
        extraction,
        {"derived.is_expired": "true", "back.expiry_date": "2030/05/17"},
        today=today,
    )
    assert bad is False
    assert MSG_EXPIRED in next(
        item for item in contradiction["fields"] if item["id"] == "derived.is_expired"
    )["review_validation"]["messages"]

    mismatched_gov, gov_ok = build_review(
        extraction,
        {"derived.birth_governorate_code": "01"},
        today=today,
    )
    assert gov_ok is False
    assert MSG_GOV_NID in next(
        item for item in mismatched_gov["fields"] if item["id"] == "derived.birth_governorate_code"
    )["review_validation"]["messages"]

    invalid_bool, bool_ok = build_review(extraction, {"front.portrait_present": "yes"}, today=today)
    assert bool_ok is False
    assert MSG_PRESENCE in next(
        item for item in invalid_bool["fields"] if item["id"] == "front.portrait_present"
    )["review_validation"]["messages"]


def test_accepted_corrections_are_persisted_without_mutating_ocr(monkeypatch, tmp_path):
    client, store = _workflow_client(tmp_path, monkeypatch)
    pending = _submit(client).json()
    app_id = pending["id"]
    original = client.get(f"/v1/review/applications/{app_id}").json()["extraction"]["front"]["full_name_ar"]["value"]
    corrected = "على أحمد حسن محمد"
    accepted = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={
            "action": "accept",
            "corrections": {
                "front.full_name_ar": corrected,
                "front.portrait_present": "true",
                "front.header_present": "true",
            },
        },
    )
    assert accepted.status_code == 200, accepted.text
    from egyid.store import Store

    restarted = TestClient(create_app(store=Store(tmp_path / "egyid.sqlite", tmp_path / "uploads")))
    detail = restarted.get(f"/v1/review/applications/{app_id}").json()
    assert detail["extraction"]["front"]["full_name_ar"]["value"] == original
    assert detail["corrections"]["front.full_name_ar"] == corrected
    name = next(item for item in detail["review"]["fields"] if item["id"] == "front.full_name_ar")
    assert name["original"]["value"] == original
    assert name["corrected_value"] == corrected
    assert name["final_value"] == corrected
    portrait = next(item for item in detail["review"]["fields"] if item["id"] == "front.portrait_present")
    assert portrait["final_value"] == "true"


def test_unreadable_fields_do_not_advertise_extraction_confidence():
    from egyid.review import build_review

    back = extract_back(BACK_LINES, barcode_found=False, barcode_score=0.82)
    assert back.barcode_present.status == FieldStatus.unreadable
    extraction = _sample_extraction()
    extraction["back"]["barcode_present"] = back.barcode_present.model_dump(mode="json")
    extraction["back"]["expiry_date"] = {
        "value": None,
        "status": "unreadable",
        "confidence": 0.82,
        "source": "printed",
        "raw_ocr": None,
        "extraction_source": None,
    }
    record, _ok = build_review(extraction, {})
    by_id = {item["id"]: item for item in record["fields"]}
    assert by_id["back.expiry_date"]["original"]["status"] == "unreadable"
    assert by_id["back.expiry_date"]["original"]["confidence"] is None
    assert by_id["back.barcode_present"]["original"]["status"] == "unreadable"
    assert by_id["back.barcode_present"]["original"]["confidence"] is None
    assert by_id["back.barcode_present"]["original"]["detection_confidence"] == pytest.approx(0.82)
    name_conf = by_id["front.full_name_ar"]["original"]["confidence"]
    assert name_conf is None or 0 <= name_conf <= 1


def test_english_governorate_alias_normalizes_to_arabic():
    from egyid.review import build_review

    extraction = _sample_extraction()
    record, ok = build_review(extraction, {"derived.birth_governorate": "Dakahlia"})
    assert ok
    gov = next(item for item in record["fields"] if item["id"] == "derived.birth_governorate")
    assert gov["final_value"] == "الدقهلية"
    assert gov["original"]["value"] == "الدقهلية"
    assert extraction["derived"]["birth_governorate_code"]["value"] == "12"


def test_customer_reload_shows_persisted_approved_status(monkeypatch, tmp_path):
    from egyid.store import Store
    from egyid.workflow import CUSTOMER_MESSAGES

    client, _store = _workflow_client(tmp_path, monkeypatch, auto_approve=True)
    created = _submit(client).json()
    assert created["status"] == "approved"
    assert created["customer_message"] == CUSTOMER_MESSAGES["approved"]
    assert created["next_stage"]["available"] is True

    reloaded = TestClient(create_app(store=Store(tmp_path / "egyid.sqlite", tmp_path / "uploads")))
    listed = reloaded.get(f"/v1/customers/{CUSTOMER_A}/applications").json()["applications"]
    assert listed[0]["id"] == created["id"]
    assert listed[0]["status"] == "approved"
    assert listed[0]["customer_message"] == CUSTOMER_MESSAGES["approved"]
    assert listed[0]["next_stage"]["available"] is True
    detail = reloaded.get(f"/v1/applications/{created['id']}").json()
    assert detail["status"] == "approved"
    assert detail["customer_message"] == CUSTOMER_MESSAGES["approved"]
    assert "Keep this page to watch for reviewer updates" not in detail["customer_message"]


def test_customer_and_reviewer_ui_status_confidence_and_governorates():
    root = Path(__file__).resolve().parents[1] / "static"
    customer = (root / "customer.js").read_text(encoding="utf-8")
    reviewer = (root / "reviewer.js").read_text(encoding="utf-8")
    assert "Application stored. Keep this page to watch for reviewer updates." not in customer
    assert "statusLine.textContent = message" in customer
    assert "if (app.status === \"pending_review\") startPoll(app.id)" in customer
    assert "Approved — you can continue" in customer
    assert "submitBtn.disabled = !ready || resubmitting" in customer
    assert "next-stage\").classList.add(\"hidden\")" in customer
    assert "function confidenceMarkup" in reviewer
    assert "detection ${detection}" in reviewer
    assert "الدقهلية" in reviewer
    assert "function codeForGovName" in reviewer
    assert "Applicant must be at least 18 years old." in reviewer
    assert "function liveNidAgeOk" in reviewer
    assert "nidsMatch() && liveNidAgeOk()" in reviewer
    assert "summary.status !== current.status" in reviewer
    assert "statusLine.classList.add(\"error\")" in customer
    css = (root / "styles.css").read_text(encoding="utf-8")
    assert "overflow-x: hidden" in css
    assert "button.danger:not(:disabled):hover" in css


def test_extracted_values_preserve_script_and_normalize_digits():
    from egyid.extract import preserve_extracted_text
    from egyid.nid import nids_from_extraction
    from egyid.validate import derive_from_nid

    assert preserve_extracted_text("  محمد   ١٢ ") == "محمد 12"
    front = extract_front(FRONT_LINES)
    assert front.full_name_ar.value == "محمد أحمد علي حسن"
    assert "١٢" in (front.address_ar.raw_ocr or "")
    assert "12" in (front.address_ar.value or "")
    assert "١٢" not in (front.address_ar.value or "")
    assert front.card_serial.value == "AB12345678"
    assert front.national_id.value == "29901011234567"
    assert "٢" in (front.national_id.raw_ocr or "")

    english_back = extract_back(
        [
            _line("2020/01", 0.95, 0.18, 0.06, 0.34, 0.14),
            _line("29901011234567", 0.96, 0.40, 0.06, 0.78, 0.14),
            _line("Software Engineer", 0.91, 0.40, 0.20, 0.78, 0.30),
            _line("Male Muslim single", 0.93, 0.28, 0.34, 0.78, 0.44),
            _line("Valid until 2027/01/15", 0.94, 0.22, 0.48, 0.80, 0.58),
        ],
        barcode_found=True,
        barcode_score=0.7,
    )
    assert english_back.profession_ar.value == "Software Engineer"
    assert english_back.gender_printed.value == "Male"
    assert english_back.marital_status_ar.value == "single"
    assert english_back.religion_printed.value != "Muslim"
    derived = derive_from_nid(front, english_back)
    assert derived.birth_governorate.value == "الدقهلية"
    assert derived.birth_governorate_code.value == "12"
    assert nids_from_extraction(
        {"front": {"national_id": {"value": "٢ ٩٩ ٠١ ٠١ ١٢ ٣٤ ٥٦ ٧"}}, "back": {}}
    ) == ["29901011234567"]


def test_duplicate_nid_blocked_for_new_application_not_resubmit(monkeypatch, tmp_path):
    from egyid.workflow import DUPLICATE_NID_MESSAGE

    client, store = _workflow_client(tmp_path, monkeypatch)
    first = _submit(client)
    assert first.status_code == 200
    pending_dup = _submit(client, customer_id=CUSTOMER_B, files=_png_pair(tag=1))
    assert pending_dup.status_code == 409
    assert pending_dup.json()["duplicate"] == ["nid"]
    assert pending_dup.json()["customer_message"] == DUPLICATE_NID_MESSAGE
    assert len(store.list_all()) == 1

    app_id = first.json()["id"]
    rejected = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "reject", "reason": "Document mismatch."},
    )
    assert rejected.status_code == 200
    rejected_dup = _submit(client, customer_id=CUSTOMER_B, files=_png_pair(tag=2))
    assert rejected_dup.status_code == 409
    assert rejected_dup.json()["duplicate"] == ["nid"]
    assert len(store.list_all()) == 1


def test_duplicate_nid_blocked_when_original_is_approved(monkeypatch, tmp_path):
    from egyid.workflow import DUPLICATE_NID_MESSAGE

    client, store = _workflow_client(tmp_path, monkeypatch, auto_approve=True)
    created = _submit(client)
    assert created.status_code == 200
    assert created.json()["status"] == "approved"
    duplicate = _submit(client, customer_id=CUSTOMER_B, files=_png_pair(tag=1))
    assert duplicate.status_code == 409
    assert duplicate.json()["duplicate"] == ["nid"]
    assert DUPLICATE_NID_MESSAGE in duplicate.json()["customer_message"]
    assert len(store.list_all()) == 1


def test_resubmit_keeps_same_nid_on_existing_application(monkeypatch, tmp_path):
    client, store = _workflow_client(tmp_path, monkeypatch)
    pending = _submit(client).json()
    app_id = pending["id"]
    asked = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "resubmit", "reason": "Please recapture both sides."},
    )
    assert asked.status_code == 200
    again = _submit(client, f"/v1/applications/{app_id}/resubmit")
    assert again.status_code == 200
    assert again.json()["id"] == app_id
    assert len(store.list_all()) == 1


def test_concurrent_duplicate_nid_creates_one_application(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from egyid.store import DuplicateSubmissionError
    from egyid.workflow import submit_application

    _client, store = _workflow_client(tmp_path, monkeypatch)
    pair = _png_pair()

    def once():
        try:
            return submit_application(
                store,
                pair["front"][1],
                pair["back"][1],
                front_filename="front.png",
                back_filename="back.png",
                front_content_type="image/png",
                back_content_type="image/png",
                customer_id=CUSTOMER_A,
            )
        except DuplicateSubmissionError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: once(), range(8)))
    created = [item for item in results if item is not None]
    assert len(created) == 1
    assert len(store.list_all()) == 1


def test_duplicate_images_are_blocked_even_without_nid(monkeypatch, tmp_path):
    from egyid import workflow as workflow_mod

    client, store = _workflow_client(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_mod, "nids_from_extraction", lambda extraction: [])
    first = _submit(client)
    assert first.status_code == 200
    duplicate = _submit(client, customer_id=CUSTOMER_B)
    assert duplicate.status_code == 409, duplicate.text
    assert set(duplicate.json()["duplicate"]) == {"front", "back"}
    message = duplicate.json()["customer_message"].lower()
    assert "front image" in message
    assert "back image" in message
    assert "national id" not in message
    assert len(store.list_all()) == 1


def test_reused_front_or_back_image_is_blocked(monkeypatch, tmp_path):
    from egyid import pipeline as pipeline_mod
    from egyid import workflow as workflow_mod

    client, store = _workflow_client(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_mod, "nids_from_extraction", lambda extraction: [])
    first = _submit(client, files=_png_pair(tag=0))
    assert first.status_code == 200
    mixed_front = {"front": _png_pair(tag=0)["front"], "back": _png_pair(tag=1)["back"]}
    front_dup = _submit(client, customer_id=CUSTOMER_B, files=mixed_front)
    assert front_dup.status_code == 409
    assert front_dup.json()["duplicate"] == ["front"]
    assert "front image" in front_dup.json()["customer_message"].lower()

    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: StubEngine(FRONT_LINES_B, BACK_LINES_B))
    mixed_back = {"front": _png_pair(tag=2)["front"], "back": _png_pair(tag=0)["back"]}
    back_dup = _submit(client, customer_id=CUSTOMER_B, files=mixed_back)
    assert back_dup.status_code == 409
    assert back_dup.json()["duplicate"] == ["back"]
    assert "back image" in back_dup.json()["customer_message"].lower()
    assert len(store.list_all()) == 1


def test_concurrent_duplicate_images_create_one_application(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from egyid import workflow as workflow_mod
    from egyid.store import DuplicateSubmissionError
    from egyid.workflow import submit_application

    _client, store = _workflow_client(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_mod, "nids_from_extraction", lambda extraction: [])
    pair = _png_pair(tag=9)

    def once():
        try:
            return submit_application(
                store,
                pair["front"][1],
                pair["back"][1],
                front_filename="front.png",
                back_filename="back.png",
                front_content_type="image/png",
                back_content_type="image/png",
                customer_id=CUSTOMER_A,
            )
        except DuplicateSubmissionError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: once(), range(8)))
    created = [item for item in results if item is not None]
    assert len(created) == 1
    assert len(store.list_all()) == 1


def test_failed_automated_decision_is_rejected(monkeypatch, tmp_path):
    from egyid import workflow as workflow_mod
    from egyid.models import Decision
    from egyid.workflow import status_from_automated

    assert status_from_automated(Decision.failed.value) == "rejected"
    assert status_from_automated(Decision.completed.value) == "approved"
    assert status_from_automated(Decision.needs_review.value) == "pending_review"

    client, _store = _workflow_client(tmp_path, monkeypatch, auto_approve=True)
    real_process = workflow_mod.process_id_pair

    def failed_process(*args, **kwargs):
        result = real_process(*args, **kwargs)
        result.decision = Decision.failed
        result.reasons = ["usable_nid"]
        return result

    monkeypatch.setattr(workflow_mod, "process_id_pair", failed_process)
    created = _submit(client)
    assert created.status_code == 200
    assert created.json()["status"] == "rejected"
    assert created.json()["can_resubmit"] is False


def test_gender_check_canonicalizes_without_rewriting_ocr():
    front, back, derived = _ready_pair()
    back.gender_printed = FieldValue.present("Female", 0.9)
    checks, _derived, decision, reasons = run_checks(front, back, derived, _ok_image(), _ok_image())
    names = {item.name: item.passed for item in checks}
    assert names["printed_derived_gender_match"] is True
    assert back.gender_printed.value == "Female"
    assert derived.gender.value == "أنثى"
    assert "printed_derived_gender_match" not in reasons
    assert decision == Decision.completed


def test_derive_from_nid_uses_first_valid_side():
    front = FrontFields(national_id=FieldValue.present("12345678901234", 0.9))
    back = BackFields(national_id=FieldValue.present("29901011234567", 0.9))
    derived = derive_from_nid(front, back)
    assert derived.date_of_birth.value == "1999-01-01"
    assert derived.gender.value == "أنثى"


def test_review_attaches_age_message_to_dob():
    from egyid.review import MSG_AGE, build_review

    extraction = _sample_extraction()
    record, ok = build_review(
        extraction,
        {"front.national_id": "32001011234567", "back.national_id": "32001011234567"},
    )
    assert ok is False
    dob = next(item for item in record["fields"] if item["id"] == "derived.date_of_birth")
    assert MSG_AGE in dob["review_validation"]["messages"]


def test_accept_claims_review_final_nids(monkeypatch, tmp_path):
    from egyid import pipeline as pipeline_mod

    client, store = _workflow_client(tmp_path, monkeypatch)
    pending = _submit(client).json()
    app_id = pending["id"]
    accepted = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={
            "action": "accept",
            "corrections": {
                "front.national_id": "29812102123456",
                "back.national_id": "29812102123456",
                "back.gender_printed": "أنثى",
            },
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert store.claimed_nids(app_id) == ["29812102123456"]
    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: StubEngine(FRONT_LINES_B, BACK_LINES_B))
    blocked = _submit(client, customer_id=CUSTOMER_B, files=_png_pair(tag=3))
    assert blocked.status_code == 409
    assert "nid" in blocked.json()["duplicate"]


def test_resubmit_keeps_nid_claim_when_ocr_misses(monkeypatch, tmp_path):
    from egyid import workflow as workflow_mod
    from egyid.nid import nids_from_extraction as real_nids

    client, store = _workflow_client(tmp_path, monkeypatch)
    pending = _submit(client, files=_png_pair(tag=0)).json()
    app_id = pending["id"]
    assert "29901011234567" in store.claimed_nids(app_id)
    asked = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "resubmit", "reason": "Please recapture both sides."},
    )
    assert asked.status_code == 200
    monkeypatch.setattr(workflow_mod, "nids_from_extraction", lambda extraction: [])
    again = _submit(client, f"/v1/applications/{app_id}/resubmit", files=_png_pair(tag=1))
    assert again.status_code == 200
    assert "29901011234567" in store.claimed_nids(app_id)
    monkeypatch.setattr(workflow_mod, "nids_from_extraction", real_nids)
    blocked = _submit(client, customer_id=CUSTOMER_B, files=_png_pair(tag=2))
    assert blocked.status_code == 409
    assert "nid" in blocked.json()["duplicate"]


def test_resubmit_does_not_replace_images_when_duplicate_blocked(monkeypatch, tmp_path):
    from pathlib import Path as PathType

    from egyid import pipeline as pipeline_mod

    client, store = _workflow_client(tmp_path, monkeypatch)
    first = _submit(client, files=_png_pair(tag=0)).json()
    app_id = first["id"]
    original = PathType(store.get(app_id)["front_path"]).read_bytes()
    asked = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "resubmit", "reason": "Please recapture both sides."},
    )
    assert asked.status_code == 200
    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: StubEngine(FRONT_LINES_B, BACK_LINES_B))
    second = _submit(client, customer_id=CUSTOMER_B, files=_png_pair(tag=1))
    assert second.status_code == 200
    blocked = _submit(client, f"/v1/applications/{app_id}/resubmit", files=_png_pair(tag=1))
    assert blocked.status_code == 409
    assert PathType(store.get(app_id)["front_path"]).read_bytes() == original
    leftover = list((tmp_path / "uploads" / app_id).glob("*.tmp"))
    assert leftover == []


def test_resubmit_replaces_image_bytes_after_commit(monkeypatch, tmp_path):
    from pathlib import Path as PathType

    client, store = _workflow_client(tmp_path, monkeypatch)
    first = _submit(client, files=_png_pair(tag=0)).json()
    app_id = first["id"]
    original = PathType(store.get(app_id)["front_path"]).read_bytes()
    asked = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "resubmit", "reason": "Please recapture both sides."},
    )
    assert asked.status_code == 200
    replacement = _png_pair(tag=4)
    again = _submit(client, f"/v1/applications/{app_id}/resubmit", files=replacement)
    assert again.status_code == 200
    stored = PathType(store.get(app_id)["front_path"]).read_bytes()
    assert stored == replacement["front"][1]
    assert stored != original
    assert client.get(f"/v1/applications/{app_id}/front").content == stored


def _prepare_resubmit(client, files=None):
    first = _submit(client, files=files or _png_pair(tag=0)).json()
    app_id = first["id"]
    asked = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "resubmit", "reason": "Please recapture both sides."},
    )
    assert asked.status_code == 200
    return app_id


def _live_and_hash(store, app_id, side: str) -> tuple[bytes, str]:
    rec = store.get(app_id)
    live = Path(rec[f"{side}_path"]).read_bytes()
    row = store._conn.execute(
        "SELECT sha256 FROM application_images WHERE application_id = ? AND side = ?",
        (app_id, side),
    ).fetchone()
    return live, row["sha256"]


def _assert_originals_consistent(store, app_id, originals: dict[str, tuple[bytes, str]], tmp_path) -> None:
    from egyid.store import sha256_hex

    rec = store.get(app_id)
    for side, (original, original_hash) in originals.items():
        live, db_hash = _live_and_hash(store, app_id, side)
        assert live == original
        assert db_hash == original_hash
        assert sha256_hex(live) == original_hash
    folder = tmp_path / "uploads" / app_id
    assert list(folder.glob("*.tmp")) == []
    assert list(folder.glob("*.bak")) == []
    assert rec["status"] == "resubmission_required"


def test_resubmit_keeps_original_hashes_when_replace_fails(monkeypatch, tmp_path):
    client, store = _workflow_client(tmp_path, monkeypatch)
    app_id = _prepare_resubmit(client)
    originals = {side: _live_and_hash(store, app_id, side) for side in ("front", "back")}
    store._backup_and_replace = lambda pairs: (_ for _ in ()).throw(OSError("injected replace failure"))
    with pytest.raises(OSError, match="injected replace failure"):
        _submit(client, f"/v1/applications/{app_id}/resubmit", files=_png_pair(tag=9))
    _assert_originals_consistent(store, app_id, originals, tmp_path)


def test_resubmit_restores_originals_when_second_replace_fails(monkeypatch, tmp_path):
    client, store = _workflow_client(tmp_path, monkeypatch)
    app_id = _prepare_resubmit(client, files=_png_pair(tag=1))
    originals = {side: _live_and_hash(store, app_id, side) for side in ("front", "back")}
    rec = store.get(app_id)
    back_dest = Path(rec["back_path"])
    real_replace = os.replace

    def flaky(src, dst):
        src_path, dst_path = Path(src), Path(dst)
        if dst_path == back_dest and src_path.name.endswith(".tmp"):
            raise OSError("injected second-file replace failure")
        real_replace(src, dst)

    monkeypatch.setattr("egyid.store.os.replace", flaky)
    with pytest.raises(OSError, match="injected second-file replace failure"):
        _submit(client, f"/v1/applications/{app_id}/resubmit", files=_png_pair(tag=8))
    _assert_originals_consistent(store, app_id, originals, tmp_path)


def test_resubmit_restores_originals_when_commit_fails(monkeypatch, tmp_path):
    client, store = _workflow_client(tmp_path, monkeypatch)
    app_id = _prepare_resubmit(client, files=_png_pair(tag=2))
    originals = {side: _live_and_hash(store, app_id, side) for side in ("front", "back")}

    class ConnProxy:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)
            object.__setattr__(self, "fail_commit", False)

        def commit(self):
            if self.fail_commit:
                raise sqlite3.DatabaseError("injected commit failure")
            return self._inner.commit()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    store._conn = ConnProxy(store._conn)
    store._conn.fail_commit = True
    with pytest.raises(sqlite3.DatabaseError, match="injected commit failure"):
        _submit(client, f"/v1/applications/{app_id}/resubmit", files=_png_pair(tag=7))
    _assert_originals_consistent(store, app_id, originals, tmp_path)


def test_ocr_exception_goes_to_needs_review(monkeypatch, tmp_path):
    from egyid import pipeline as pipeline_mod
    from egyid.workflow import CUSTOMER_MESSAGES

    client, store = _workflow_client(tmp_path, monkeypatch)

    class BoomEngine:
        def run(self, image):
            raise RuntimeError("ocr engine crashed")

    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: BoomEngine())
    created = _submit(client)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "pending_review"
    assert body["automated_decision"] == "needs_review"
    assert body["customer_message"] == CUSTOMER_MESSAGES["pending_review"]
    assert "ocr engine crashed" not in str(body).lower()
    rec = store.get(body["id"])
    assert Path(rec["front_path"]).is_file()
    assert Path(rec["back_path"]).is_file()
    queue = client.get("/v1/review/applications").json()["applications"]
    assert queue[0]["id"] == body["id"]
    assert queue[0]["status"] == "pending_review"


def test_ocr_exception_on_resubmit_goes_to_needs_review(monkeypatch, tmp_path):
    from egyid import pipeline as pipeline_mod

    client, store = _workflow_client(tmp_path, monkeypatch)
    app_id = _prepare_resubmit(client, files=_png_pair(tag=0))
    replacement = _png_pair(tag=5)

    class BoomEngine:
        def run(self, image):
            raise RuntimeError("ocr engine crashed")

    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: BoomEngine())
    again = _submit(client, f"/v1/applications/{app_id}/resubmit", files=replacement)
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["id"] == app_id
    assert body["status"] == "pending_review"
    assert body["automated_decision"] == "needs_review"
    assert "ocr engine crashed" not in str(body).lower()
    assert Path(store.get(app_id)["front_path"]).read_bytes() == replacement["front"][1]


def test_empty_ocr_without_exception_still_rejects(monkeypatch, tmp_path):
    from egyid import pipeline as pipeline_mod

    client, store = _workflow_client(tmp_path, monkeypatch)
    monkeypatch.setattr(pipeline_mod, "default_engine", lambda: StubEngine([], []))
    created = _submit(client)
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "rejected"
    assert body["automated_decision"] == "failed"
    assert store.get(body["id"])["status"] == "rejected"


def test_stale_accept_returns_conflict(monkeypatch, tmp_path):
    client, _store = _workflow_client(tmp_path, monkeypatch)
    pending = _submit(client).json()
    app_id = pending["id"]
    first = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "accept", "corrections": {"back.gender_printed": "أنثى"}},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/v1/review/applications/{app_id}/decision",
        json={"action": "reject", "reason": "Too late."},
    )
    assert second.status_code == 409
    assert client.get(f"/v1/applications/{app_id}").json()["status"] == "approved"


def _wait_health(base: str, timeout: float = 20) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.25)
    raise AssertionError("server did not become healthy")


def test_playwright_real_id_browser_flow(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    pairs = _split_id_pairs()
    if len(pairs) < 2:
        pytest.skip("local National ID image pairs are not available")
    front_a, back_a = pairs[0]
    front_b, back_b = pairs[1]
    bytes_a = front_a.read_bytes()
    bytes_b = front_b.read_bytes()
    db_path = tmp_path / "egyid.sqlite"
    upload_dir = tmp_path / "uploads"
    port = 8011
    base = f"http://127.0.0.1:{port}"
    launcher = tmp_path / "serve.py"
    launcher.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "import uvicorn\n"
        "from egyid.api import create_app\n"
        "from egyid.store import Store\n"
        "uvicorn.run(\n"
        "    create_app(store=Store(Path(os.environ['EGYID_DB']), Path(os.environ['EGYID_UPLOADS']))),\n"
        "    host='127.0.0.1',\n"
        f"    port={port},\n"
        "    log_level='warning',\n"
        ")\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(Path("src").resolve()),
        "EGYID_DB": str(db_path),
        "EGYID_UPLOADS": str(upload_dir),
    }
    proc = subprocess.Popen(
        [sys.executable, str(launcher)],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    evidence: dict[str, str] = {}
    try:
        _wait_health(base)

        def restart():
            nonlocal proc
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            proc = subprocess.Popen(
                [sys.executable, str(launcher)],
                cwd=str(Path(__file__).resolve().parents[1]),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _wait_health(base)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(base + "/")
            page.set_input_files("#front-file", str(front_a.resolve()))
            page.set_input_files("#back-file", str(back_a.resolve()))
            page.click("#submit")
            page.wait_for_function(
                "() => document.getElementById('status-panel') && !document.getElementById('status-panel').classList.contains('hidden')",
                timeout=120000,
            )
            status_a = page.locator("#status-banner strong").inner_text()
            evidence["first_submit"] = status_a
            app_a = page.locator("#app-id-line").inner_text().replace("Application ", "").strip()
            assert app_a

            page.click("#submit")
            page.wait_for_function(
                "() => document.getElementById('status-line').classList.contains('error')",
                timeout=30000,
            )
            dup_text = page.locator("#status-line").inner_text().lower()
            evidence["duplicate"] = "front image" in dup_text or "national id" in dup_text
            assert evidence["duplicate"]

            page.set_input_files("#front-file", str(front_b.resolve()))
            page.set_input_files("#back-file", str(back_b.resolve()))
            page.click("#submit")
            page.wait_for_function(
                f"() => document.getElementById('app-id-line') && document.getElementById('app-id-line').textContent && !document.getElementById('app-id-line').textContent.includes('{app_a}')",
                timeout=120000,
            )
            status_b = page.locator("#status-banner strong").inner_text()
            evidence["second_submit"] = status_b
            app_b = page.locator("#app-id-line").inner_text().replace("Application ", "").strip()
            assert app_b and app_b != app_a

            page.goto(base + "/review")
            page.wait_for_selector(f"[data-id='{app_a}']", timeout=15000)
            page.click(f"[data-id='{app_a}']")
            page.wait_for_selector("#decision-reason")
            page.fill("#decision-reason", "Need a clearer expiry date.")
            page.click("#resubmit")
            page.wait_for_function(
                "() => document.getElementById('status-line').textContent.includes('Decision saved')",
                timeout=15000,
            )

            page.goto(base + "/")
            page.click(f"[data-id='{app_a}']")
            page.wait_for_function(
                "() => document.getElementById('resubmit-box') && !document.getElementById('resubmit-box').classList.contains('hidden')",
                timeout=15000,
            )
            assert page.locator("#submit").is_disabled()
            page.set_input_files("#front-file", str(front_b.resolve()))
            page.set_input_files("#back-file", str(back_b.resolve()))
            page.click("#resubmit")
            page.wait_for_function(
                "() => document.getElementById('status-line').classList.contains('error')",
                timeout=30000,
            )
            import urllib.request

            with urllib.request.urlopen(base + f"/v1/applications/{app_a}/front") as response:
                still_a = response.read()
            evidence["resubmit_blocked_keeps_bytes"] = still_a == bytes_a
            assert still_a == bytes_a

            page.set_input_files("#front-file", str(front_a.resolve()))
            page.set_input_files("#back-file", str(back_a.resolve()))
            page.click("#resubmit")
            page.wait_for_function(
                "() => document.getElementById('resubmit-box').classList.contains('hidden')",
                timeout=120000,
            )

            page.goto(base + "/review")
            page.click(f"[data-id='{app_a}']")
            page.wait_for_selector("[data-edit='back.expiry_date']")
            if page.locator("#accept").is_disabled():
                page.click("[data-edit='back.expiry_date']")
                page.fill("#edit-input", "2030/05/17")
                page.click("#edit-apply")
            page.wait_for_function(
                "() => !document.getElementById('accept').disabled",
                timeout=10000,
            )
            page.click("#accept")
            page.wait_for_function(
                "() => document.getElementById('status-line').textContent.includes('Decision saved') || document.getElementById('status-line').textContent.includes('review')",
                timeout=15000,
            )
            accept_line = page.locator("#status-line").inner_text()
            evidence["accept_ui"] = accept_line
            import json as json_lib
            import urllib.request

            with urllib.request.urlopen(base + f"/v1/applications/{app_a}") as response:
                server_a = json_lib.loads(response.read().decode("utf-8"))
            evidence["accept_server_status"] = server_a["status"]
            if "Decision saved" in accept_line:
                assert server_a["status"] == "approved"

            shots = Path("artifacts") / "browser_evidence"
            shots.mkdir(parents=True, exist_ok=True)
            page.goto(base + "/")
            page.click(f"[data-id='{app_a}']")
            page.wait_for_selector("#status-banner")
            page.evaluate(
                "() => document.querySelectorAll('#front-preview, #back-preview, img').forEach((el) => el.remove())"
            )
            page.locator("#status-panel").screenshot(path=str(shots / "customer-status.png"))
            evidence["screenshot"] = str(shots / "customer-status.png")
            browser.close()

        import urllib.request

        with urllib.request.urlopen(base + f"/v1/applications/{app_a}/front") as response:
            before = response.read()
        with urllib.request.urlopen(base + f"/v1/applications/{app_b}/front") as response:
            before_b = response.read()
        restart()
        with urllib.request.urlopen(base + f"/v1/applications/{app_a}/front") as response:
            after = response.read()
        with urllib.request.urlopen(base + f"/v1/applications/{app_b}/front") as response:
            after_b = response.read()
        evidence["restart_a"] = after == before == bytes_a
        evidence["restart_b"] = after_b == before_b == bytes_b
        assert after == before == bytes_a
        assert after_b == before_b == bytes_b
        assert evidence["resubmit_blocked_keeps_bytes"]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
