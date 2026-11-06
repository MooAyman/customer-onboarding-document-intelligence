from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from egyid.extract import MARITAL_MAP, RELIGION_VALUES, SERIAL_RE
from egyid.models import Decision, FieldSource, FieldValue, IdDocumentResult
from egyid.nid import GOVERNORATE_CODES, code_for_governorate_name, digits_only, normalize_digits, parse_national_id
from egyid.validate import MIN_AGE, _age_years

REVIEW_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "reviews"
_REVIEW_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
GENDER_ARABIC = ("ذكر", "أنثى", "انثى")
MSG_REQUIRED = "Required field is empty."
MSG_NAME_DIGITS = "Name must not contain digits."
MSG_NID = "NID must contain exactly 14 digits and pass structural/date validation."
MSG_DATE = "Invalid date or expired date."
MSG_DOB = "Enter a valid date of birth as YYYY/MM/DD, e.g. 1999/01/15."
MSG_EXPIRY = "Enter a valid expiry date as YYYY/MM/DD, e.g. 2030/05/17."
MSG_ISSUE = "Enter a valid issue date as YYYY/MM, e.g. 2021/11."
MSG_NID_MISMATCH = "Front and back National ID numbers do not match."
MSG_CATEGORICAL = "Unsupported categorical value."
MSG_SERIAL = "Invalid serial number format."
MSG_PRESENCE = "Enter true or false."
MSG_EXPIRED = "Expired must be true if the expiry date is in the past, and false if it is still valid."
MSG_GOV_CODE = "Governorate code must be a valid two-digit Egyptian NID code."
MSG_GOV_NAME = "Governorate name must match the governorate code."
MSG_GOV_NID = "Governorate must match the National ID."
MSG_AGE = "Applicant must be at least 18 years old."
PRESENCE_VALUES = ("true", "false")
HINT_FULL_DOB = "YYYY/MM/DD e.g. 1999/01/15"
HINT_FULL_EXPIRY = "YYYY/MM/DD e.g. 2030/05/17"
HINT_MONTH = "YYYY/MM e.g. 2021/11"
_FULL_DATE_RE = re.compile(r"^(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})$")
_MONTH_DATE_RE = re.compile(r"^(\d{4})[/.\-](\d{1,2})$")


@dataclass(frozen=True)
class FieldSpec:
    path: str
    group: str
    label: str
    kind: str
    required: bool
    editable: bool
    reasons: tuple[str, ...] = ()
    hint: str = ""


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("front.full_name_ar", "front", "Full name", "person_name", True, True, ("front_name",)),
    FieldSpec("front.national_id", "front", "National ID", "nid", False, True, ("usable_nid", "nid_single_side", "front_back_nid_match")),
    FieldSpec("front.address_ar", "front", "Address", "address", True, True, ("front_address",)),
    FieldSpec("front.card_serial", "front", "Card serial", "serial", True, True, ("front_serial",)),
    FieldSpec("front.date_of_birth_printed", "front", "Printed date of birth", "dob", False, True, ("printed_derived_dob_match",), HINT_FULL_DOB),
    FieldSpec("derived.date_of_birth", "front", "Date of birth (from NID)", "dob", True, True, ("age_at_least_18", "printed_derived_dob_match"), HINT_FULL_DOB),
    FieldSpec("derived.gender", "front", "Gender (from NID)", "gender", True, True, ("printed_derived_gender_match",)),
    FieldSpec("derived.birth_governorate", "front", "Birth governorate", "governorate", False, True),
    FieldSpec("derived.birth_governorate_code", "front", "Governorate code", "gov_code", False, True),
    FieldSpec("front.portrait_present", "front", "Portrait", "presence", False, True, ("front_portrait",), "true or false"),
    FieldSpec("front.header_present", "front", "Header", "presence", False, True, (), "true or false"),
    FieldSpec("back.national_id", "back", "National ID", "nid", False, True, ("usable_nid", "nid_single_side", "front_back_nid_match")),
    FieldSpec("back.expiry_date", "back", "Expiry date (card valid until)", "expiry", True, True, ("back_expiry", "expiry_not_past"), HINT_FULL_EXPIRY),
    FieldSpec("derived.is_expired", "back", "Expired", "expired", False, True, ("expiry_not_past",), "true or false"),
    FieldSpec("back.issue_date", "back", "Issue date", "month_date", False, True, (), HINT_MONTH),
    FieldSpec("back.gender_printed", "back", "Printed gender", "gender", False, True, ("printed_derived_gender_match",)),
    FieldSpec("back.religion_printed", "back", "Religion", "religion", False, True),
    FieldSpec("back.marital_status_ar", "back", "Marital status", "marital", False, True),
    FieldSpec("back.profession_ar", "back", "Profession", "text", False, True),
    FieldSpec("back.husband_name_ar", "back", "Husband name", "person_name", False, True),
    FieldSpec("back.barcode_present", "back", "Barcode (existence)", "presence", False, True, (), "true or false"),
)


def _lookup(result: IdDocumentResult, path: str) -> FieldValue:
    section, key = path.split(".", 1)
    return getattr(getattr(result, section), key)


def _snapshot(field: FieldValue) -> dict[str, Any]:
    visual = field.source == FieldSource.visual
    has_value = field.status.value == "present" and bool(field.value)
    extraction_confidence = field.confidence if has_value and not visual else None
    detection_confidence = field.confidence if visual and field.confidence is not None else None
    return {
        "value": field.value,
        "status": field.status.value,
        "confidence": extraction_confidence,
        "detection_confidence": detection_confidence,
        "raw_ocr": field.raw_ocr,
        "source": field.source.value,
        "extraction_source": field.extraction_source,
    }


def parse_full_date(value: str | None) -> date | None:
    """Parse a calendar day. Cards use YYYY/MM/DD; extraction stores ISO YYYY-MM-DD.

    Month-only values such as YYYY-MM are rejected as ambiguous.
    """
    if not value:
        return None
    text = re.sub(r"\s+", "", normalize_digits(value).strip())
    match = _FULL_DATE_RE.fullmatch(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    digits = digits_only(text)
    if len(digits) == 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def parse_month_date(value: str | None) -> date | None:
    """Parse a year-month. Cards print YYYY/MM; extraction stores ISO YYYY-MM.

    Full calendar days are rejected so issue date stays month-only.
    """
    if not value:
        return None
    text = re.sub(r"\s+", "", normalize_digits(value).strip())
    if _FULL_DATE_RE.fullmatch(text):
        return None
    match = _MONTH_DATE_RE.fullmatch(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), 1)
        except ValueError:
            return None
    digits = digits_only(text)
    if len(digits) == 6:
        try:
            return date(int(digits[:4]), int(digits[4:6]), 1)
        except ValueError:
            return None
    return None


def _normalized_nid_digits(value: str | None) -> str:
    digits = digits_only(value or "")
    return digits if len(digits) == 14 else ""


def _validate_value(spec: FieldSpec, value: str | None, *, edited: bool, today: date) -> list[str]:
    text = (value or "").strip()
    if not text:
        return [MSG_REQUIRED] if spec.required else []
    if spec.kind == "person_name":
        if digits_only(text):
            return [MSG_NAME_DIGITS]
        return []
    if spec.kind == "nid":
        parsed = parse_national_id(text)
        if parsed is None or not parsed.ok:
            return [MSG_NID]
        return []
    if spec.kind == "dob":
        parsed = parse_full_date(text)
        if parsed is None:
            return [MSG_DOB]
        if parsed > today:
            return [MSG_DATE]
        return []
    if spec.kind == "expiry":
        parsed = parse_full_date(text)
        if parsed is None:
            return [MSG_EXPIRY]
        if parsed < today:
            return [MSG_DATE]
        return []
    if spec.kind == "month_date":
        if parse_month_date(text) is None:
            return [MSG_ISSUE]
        return []
    if spec.kind == "serial":
        compact = text.replace(" ", "").upper()
        if not SERIAL_RE.fullmatch(compact) and not SERIAL_RE.search(text.upper()):
            return [MSG_SERIAL]
        return []
    if spec.kind == "gender":
        if text in GENDER_ARABIC or text in {"male", "female", "Male", "Female"}:
            return []
        return [MSG_CATEGORICAL]
    if spec.kind == "religion":
        if text in RELIGION_VALUES:
            return []
        return [MSG_CATEGORICAL]
    if spec.kind == "marital":
        if text in MARITAL_MAP:
            return []
        return [MSG_CATEGORICAL]
    if spec.kind == "address":
        return []
    if spec.kind in {"presence", "expired"}:
        if text.lower() not in PRESENCE_VALUES:
            return [MSG_PRESENCE]
        return []
    if spec.kind == "gov_code":
        code = digits_only(text)
        if len(code) != 2 or code not in GOVERNORATE_CODES:
            return [MSG_GOV_CODE]
        return []
    if spec.kind == "governorate":
        if not code_for_governorate_name(text):
            return [MSG_GOV_NAME]
        return []
    return []


def _accepted_nid(front: str | None, back: str | None) -> str | None:
    for candidate in (front, back):
        parsed = parse_national_id(candidate)
        if parsed and parsed.ok:
            return parsed.raw
    return None


def build_review(
    extraction: dict[str, Any],
    corrections: dict[str, str] | None = None,
    *,
    today: date | None = None,
    review_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Build a review record. Original extraction values are copied, never mutated."""
    today = today or date.today()
    result = IdDocumentResult.model_validate(extraction)
    corrections = {key: value for key, value in (corrections or {}).items() if key}
    fields_out: list[dict[str, Any]] = []
    finals: dict[str, str | None] = {}
    all_ok = True

    for spec in FIELD_SPECS:
        original = _lookup(result, spec.path)
        edited = spec.path in corrections
        if edited:
            final = corrections[spec.path].strip() or None
        else:
            final = original.value
        if spec.kind in {"presence", "expired"} and final:
            final = final.strip().lower()
        finals[spec.path] = final
        messages = _validate_value(spec, final, edited=edited, today=today)
        if spec.kind == "nid" and not final and not spec.required:
            messages = [msg for msg in messages if msg != MSG_REQUIRED]
        ok = not messages
        all_ok = all_ok and ok
        fields_out.append(
            {
                "id": spec.path,
                "group": spec.group,
                "label": spec.label,
                "hint": spec.hint,
                "required": spec.required,
                "editable": spec.editable,
                "original": _snapshot(original),
                "corrected_value": corrections[spec.path].strip() if edited else None,
                "final_value": final,
                "final_source": "reviewer" if edited else "ocr",
                "extraction_status": original.status.value,
                "review_validation": {"ok": ok, "messages": messages},
            }
        )

    front_nid = finals.get("front.national_id")
    back_nid = finals.get("back.national_id")
    parsed_front = parse_national_id(front_nid)
    parsed_back = parse_national_id(back_nid)
    front_ok = bool(parsed_front and parsed_front.ok)
    back_ok = bool(parsed_back and parsed_back.ok)
    usable = front_ok or back_ok
    front_digits = _normalized_nid_digits(front_nid)
    back_digits = _normalized_nid_digits(back_nid)
    nid_match = True
    if front_digits and back_digits:
        nid_match = front_digits == back_digits

    accepted = _accepted_nid(front_nid, back_nid)
    parsed_accepted = parse_national_id(accepted) if accepted else None
    by_id = {item["id"]: item for item in fields_out}

    if parsed_accepted:
        if "derived.date_of_birth" not in corrections:
            by_id["derived.date_of_birth"]["final_value"] = parsed_accepted.date_of_birth or by_id["derived.date_of_birth"]["final_value"]
            by_id["derived.date_of_birth"]["final_source"] = (
                "derived_from_nid"
                if by_id["derived.date_of_birth"]["corrected_value"] is None
                else by_id["derived.date_of_birth"]["final_source"]
            )
            finals["derived.date_of_birth"] = by_id["derived.date_of_birth"]["final_value"]
            dob_messages = _validate_value(
                next(spec for spec in FIELD_SPECS if spec.path == "derived.date_of_birth"),
                finals["derived.date_of_birth"],
                edited=False,
                today=today,
            )
            by_id["derived.date_of_birth"]["review_validation"] = {
                "ok": not dob_messages,
                "messages": dob_messages,
            }
            all_ok = all_ok and not dob_messages
        if "derived.gender" not in corrections:
            original_gender = by_id["derived.gender"]["original"]["value"]
            by_id["derived.gender"]["final_value"] = original_gender or parsed_accepted.gender
            by_id["derived.gender"]["final_source"] = "derived_from_nid"
            finals["derived.gender"] = by_id["derived.gender"]["final_value"]
            gender_messages = _validate_value(
                next(spec for spec in FIELD_SPECS if spec.path == "derived.gender"),
                finals["derived.gender"],
                edited=False,
                today=today,
            )
            by_id["derived.gender"]["review_validation"] = {
                "ok": not gender_messages,
                "messages": gender_messages,
            }
            all_ok = all_ok and not gender_messages
        if "derived.birth_governorate_code" not in corrections and parsed_accepted.governorate_code:
            by_id["derived.birth_governorate_code"]["final_value"] = parsed_accepted.governorate_code
            by_id["derived.birth_governorate_code"]["final_source"] = "derived_from_nid"
            finals["derived.birth_governorate_code"] = parsed_accepted.governorate_code
            by_id["derived.birth_governorate_code"]["review_validation"] = {"ok": True, "messages": []}
        if "derived.birth_governorate" not in corrections and parsed_accepted.governorate:
            by_id["derived.birth_governorate"]["final_value"] = parsed_accepted.governorate
            by_id["derived.birth_governorate"]["final_source"] = "derived_from_nid"
            finals["derived.birth_governorate"] = parsed_accepted.governorate
            by_id["derived.birth_governorate"]["review_validation"] = {"ok": True, "messages": []}

    gov_code = digits_only(finals.get("derived.birth_governorate_code") or "")
    gov_name = (finals.get("derived.birth_governorate") or "").strip()
    if gov_code in GOVERNORATE_CODES and "derived.birth_governorate" not in corrections:
        gov_name = GOVERNORATE_CODES[gov_code]
        by_id["derived.birth_governorate"]["final_value"] = gov_name
        finals["derived.birth_governorate"] = gov_name
    elif code_for_governorate_name(gov_name) and "derived.birth_governorate_code" not in corrections:
        gov_code = code_for_governorate_name(gov_name)
        by_id["derived.birth_governorate_code"]["final_value"] = gov_code
        finals["derived.birth_governorate_code"] = gov_code
    name_code = code_for_governorate_name(gov_name)
    if name_code and gov_name != GOVERNORATE_CODES[name_code]:
        gov_name = GOVERNORATE_CODES[name_code]
        by_id["derived.birth_governorate"]["final_value"] = gov_name
        finals["derived.birth_governorate"] = gov_name
    if gov_code and gov_name:
        if gov_code not in GOVERNORATE_CODES or name_code != gov_code:
            for path in ("derived.birth_governorate", "derived.birth_governorate_code"):
                by_id[path]["review_validation"] = {"ok": False, "messages": [MSG_GOV_NAME]}
            all_ok = False
        elif parsed_accepted and parsed_accepted.governorate_code and gov_code != parsed_accepted.governorate_code:
            for path in ("derived.birth_governorate", "derived.birth_governorate_code"):
                by_id[path]["review_validation"] = {"ok": False, "messages": [MSG_GOV_NID]}
            all_ok = False

    expiry = parse_full_date(finals.get("back.expiry_date"))
    expired = bool(expiry and expiry < today)
    expected_expired = "true" if expired else "false" if expiry else None
    if "derived.is_expired" in by_id:
        if "derived.is_expired" in corrections:
            shown = (finals.get("derived.is_expired") or "").lower()
            if expected_expired and shown and shown != expected_expired:
                by_id["derived.is_expired"]["review_validation"] = {
                    "ok": False,
                    "messages": [MSG_EXPIRED],
                }
                all_ok = False
        elif expected_expired:
            by_id["derived.is_expired"]["final_value"] = expected_expired
            by_id["derived.is_expired"]["final_source"] = "derived_from_expiry"
            finals["derived.is_expired"] = expected_expired
            by_id["derived.is_expired"]["review_validation"] = {"ok": True, "messages": []}

    born = parse_full_date(finals.get("derived.date_of_birth"))
    age_ok = True
    age_detail = "age not computed"
    if born:
        age = _age_years(born, today)
        age_ok = age >= MIN_AGE
        age_detail = f"derived age {age}"
        if not age_ok:
            dob_messages = list(by_id["derived.date_of_birth"]["review_validation"]["messages"])
            if MSG_AGE not in dob_messages:
                dob_messages.append(MSG_AGE)
            by_id["derived.date_of_birth"]["review_validation"] = {
                "ok": False,
                "messages": dob_messages,
            }

    if not usable:
        for path in ("front.national_id", "back.national_id"):
            current = by_id[path]["review_validation"]["messages"]
            if MSG_NID not in current and MSG_REQUIRED not in current:
                by_id[path]["review_validation"]["messages"] = current + [MSG_NID]
                by_id[path]["review_validation"]["ok"] = False
        all_ok = False
    if not nid_match:
        for path in ("front.national_id", "back.national_id"):
            messages = by_id[path]["review_validation"]["messages"]
            if MSG_NID_MISMATCH not in messages:
                messages = messages + [MSG_NID_MISMATCH]
            by_id[path]["review_validation"]["messages"] = messages
            by_id[path]["review_validation"]["ok"] = False
        all_ok = False

    cross_checks = [
        {
            "name": "usable_nid",
            "passed": usable,
            "detail": "at least one structurally valid national ID" if usable else "no structurally valid national ID",
        },
        {
            "name": "front_back_nid_match",
            "passed": nid_match,
            "detail": "front and back national IDs match" if nid_match else "front and back national IDs differ",
        },
        {
            "name": "age_at_least_18",
            "passed": age_ok,
            "detail": age_detail,
        },
        {
            "name": "expiry_not_past",
            "passed": not expired,
            "detail": "expiry compared with today" if expiry else "expiry missing",
        },
    ]
    if not age_ok or expired:
        all_ok = False

    review_reasons = [
        item["id"]
        for item in fields_out
        if not item["review_validation"]["ok"]
    ]
    review_reasons.extend(check["name"] for check in cross_checks if not check["passed"])
    review_decision = Decision.completed.value if all_ok else (
        Decision.failed.value if (not usable or not nid_match or not age_ok or expired) else Decision.needs_review.value
    )

    record = {
        "review_id": review_id or str(uuid.uuid4()),
        "reviewed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "automated_decision": result.decision.value,
        "automated_reasons": list(result.reasons),
        "automated_checks": [check.model_dump(mode="json") for check in result.checks],
        "review_decision": review_decision,
        "review_reasons": list(dict.fromkeys(review_reasons)),
        "review_complete": all_ok,
        "ocr_ms": dict(result.ocr_ms),
        "front_image": result.front_image.model_dump(mode="json"),
        "back_image": result.back_image.model_dump(mode="json"),
        "fields": fields_out,
        "cross_checks": cross_checks,
    }
    return record, all_ok


def persist_review(record: dict[str, Any], directory: Path | None = None) -> Path:
    folder = directory or REVIEW_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{record['review_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def review_file(review_id: str, directory: Path | None = None) -> Path | None:
    if not _REVIEW_ID_RE.match(review_id):
        return None
    path = (directory or REVIEW_DIR) / f"{review_id}.json"
    return path if path.is_file() else None
