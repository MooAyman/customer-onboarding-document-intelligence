from __future__ import annotations

import json
from pathlib import Path

import pytest

from egyid.form_map import map_application_fields
from egyid.models import FieldStatus

ROOT = Path(__file__).resolve().parents[1]
BLANK_JSON = ROOT / "Azure Doc" / "response_1793999563250.json"
FILLED_JSON = ROOT / "Azure Doc" / "response2_1794001821936.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fourteen_digit_payload() -> dict:
    return {
        "pages": [
            {
                "page_number": 1,
                "lines": [
                    {
                        "content": "الإسم: أحمد محمد علي",
                        "polygon": [{"x": 4.0, "y": 2.0}, {"x": 7.0, "y": 2.0}, {"x": 7.0, "y": 2.3}, {"x": 4.0, "y": 2.3}],
                    },
                    {
                        "content": "رقم إثبات الشخصية: ٢٩٩٠١٠١١٢٣٤٥٦٧",
                        "polygon": [{"x": 1.0, "y": 2.4}, {"x": 5.0, "y": 2.4}, {"x": 5.0, "y": 2.7}, {"x": 1.0, "y": 2.7}],
                    },
                    {
                        "content": "النوع: ذكر انثى",
                        "polygon": [{"x": 1.2, "y": 2.8}, {"x": 2.6, "y": 2.8}, {"x": 2.6, "y": 3.0}, {"x": 1.2, "y": 3.0}],
                    },
                    {
                        "content": "رقم الهاتف المحمول: 01012345678",
                        "polygon": [{"x": 5.0, "y": 3.1}, {"x": 7.3, "y": 3.1}, {"x": 7.3, "y": 3.4}, {"x": 5.0, "y": 3.4}],
                    },
                    {
                        "content": "السرعة :",
                        "polygon": [{"x": 6.5, "y": 4.5}, {"x": 7.0, "y": 4.5}, {"x": 7.0, "y": 4.7}, {"x": 6.5, "y": 4.7}],
                    },
                    {
                        "content": "70 MB",
                        "polygon": [{"x": 5.5, "y": 4.5}, {"x": 6.4, "y": 4.5}, {"x": 6.4, "y": 4.8}, {"x": 5.5, "y": 4.8}],
                    },
                    {
                        "content": "30Mbps - 70 Mbps - 100 Mbps",
                        "polygon": [{"x": 2.4, "y": 4.5}, {"x": 4.7, "y": 4.5}, {"x": 4.7, "y": 4.8}, {"x": 2.4, "y": 4.8}],
                    },
                ],
                "selection_marks": [
                    {
                        "state": "unselected",
                        "confidence": 0.96,
                        "polygon": [{"x": 1.01, "y": 2.81}, {"x": 1.16, "y": 2.81}, {"x": 1.16, "y": 2.95}, {"x": 1.01, "y": 2.95}],
                    },
                    {
                        "state": "selected",
                        "confidence": 0.94,
                        "polygon": [{"x": 1.59, "y": 2.81}, {"x": 1.76, "y": 2.81}, {"x": 1.76, "y": 2.96}, {"x": 1.59, "y": 2.96}],
                    },
                ],
            }
        ],
        "tables": [],
    }


@pytest.mark.skipif(not BLANK_JSON.is_file(), reason="blank Azure layout JSON is not available")
def test_blank_form_fields_are_absent():
    result = map_application_fields(_load(BLANK_JSON))
    assert result.full_name.status == FieldStatus.absent
    assert result.national_id.status == FieldStatus.absent
    assert result.gender.status == FieldStatus.absent
    assert result.mobile.status == FieldStatus.absent
    assert result.speed.status == FieldStatus.absent
    assert result.needs_review is True
    assert "name_missing" in result.review_flags
    assert "national_id_missing" in result.review_flags
    assert "gender_missing" in result.review_flags


@pytest.mark.skipif(not FILLED_JSON.is_file(), reason="filled Azure layout JSON is not available")
def test_filled_form_maps_name_gender_speed_and_unreadble_nid():
    result = map_application_fields(_load(FILLED_JSON))
    assert result.full_name.status == FieldStatus.present
    assert result.full_name.value
    assert "الإسم" not in result.full_name.value
    assert result.national_id.status == FieldStatus.unreadable
    raw_digits = "".join(ch for ch in (result.national_id.raw_ocr or "") if ch.isdigit())
    assert len(raw_digits) != 14
    assert result.national_id.value is None
    assert result.gender.status == FieldStatus.present
    assert result.gender.value == "ذكر"
    assert result.mobile.status == FieldStatus.absent
    assert result.speed.status == FieldStatus.present
    assert result.speed.value == "30 Mbps"
    assert "gender_low_confidence" in result.review_flags
    assert "national_id_unreadable" in result.review_flags
    assert result.needs_review is True


@pytest.mark.skipif(not FILLED_JSON.is_file(), reason="filled Azure layout JSON is not available")
def test_filled_form_flags_stage1_mismatches():
    result = map_application_fields(
        _load(FILLED_JSON),
        id_name="محمد أحمد",
        id_nid="29901011234567",
        id_gender="أنثى",
    )
    assert "name_mismatch" in result.review_flags
    assert "gender_mismatch" in result.review_flags
    assert "national_id_unreadable" in result.review_flags
    assert "national_id_mismatch" not in result.review_flags
    assert result.needs_review is True


def test_fourteen_digit_nid_is_normalized_and_not_padded():
    result = map_application_fields(_fourteen_digit_payload())
    assert result.national_id.status == FieldStatus.present
    assert result.national_id.value == "29901011234567"
    assert result.full_name.value == "أحمد محمد علي"
    assert result.gender.value == "ذكر"
    assert result.gender.confidence == 0.94
    assert result.mobile.value == "01012345678"
    assert result.speed.value == "70 Mbps"
    assert result.needs_review is False


def test_printed_speed_options_alone_are_not_a_selection():
    payload = {
        "pages": [
            {
                "page_number": 1,
                "lines": [
                    {
                        "content": "السرعة :",
                        "polygon": [{"x": 6.5, "y": 4.5}, {"x": 7.0, "y": 4.7}],
                    },
                    {
                        "content": "30Mbps - 70 Mbps - 100 Mbps",
                        "polygon": [{"x": 2.4, "y": 4.5}, {"x": 4.7, "y": 4.8}],
                    },
                ],
                "selection_marks": [],
            }
        ],
        "tables": [],
    }
    result = map_application_fields(payload)
    assert result.speed.status == FieldStatus.absent


def test_short_digit_run_is_unreadable_not_guessed():
    payload = {
        "pages": [
            {
                "page_number": 1,
                "lines": [
                    {
                        "content": "رقم إثبات الشخصية: ١٢٣٤٥٦٧٨٩٠١٢٣",
                        "polygon": [{"x": 1.0, "y": 2.5}, {"x": 5.0, "y": 2.8}],
                    }
                ],
                "selection_marks": [],
            }
        ],
        "tables": [],
    }
    result = map_application_fields(payload)
    assert result.national_id.status == FieldStatus.unreadable
    assert result.national_id.value is None
    assert "national_id_unreadable" in result.review_flags


def test_missing_and_unreadable_fields_never_match_stage1():
    blank = {
        "pages": [{"page_number": 1, "lines": [], "selection_marks": []}],
        "tables": [],
    }
    missing = map_application_fields(
        blank,
        id_name="محمد أحمد علي حسن",
        id_nid="29901011234567",
        id_gender="أنثى",
    )
    assert "name_missing" in missing.review_flags
    assert "national_id_missing" in missing.review_flags
    assert "gender_missing" in missing.review_flags
    assert "name_mismatch" not in missing.review_flags
    assert "national_id_mismatch" not in missing.review_flags
    assert "gender_mismatch" not in missing.review_flags
    assert missing.needs_review is True

    unread = {
        "pages": [
            {
                "page_number": 1,
                "lines": [
                    {
                        "content": "رقم إثبات الشخصية: ١٢٣٤٥٦٧٨٩٠١٢٣",
                        "polygon": [{"x": 1.0, "y": 2.5}, {"x": 5.0, "y": 2.8}],
                    }
                ],
                "selection_marks": [],
            }
        ],
        "tables": [],
    }
    result = map_application_fields(
        unread,
        id_name="محمد أحمد علي حسن",
        id_nid="29901011234567",
        id_gender="أنثى",
    )
    assert "national_id_unreadable" in result.review_flags
    assert "national_id_mismatch" not in result.review_flags
    assert result.needs_review is True
