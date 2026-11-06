from __future__ import annotations

from datetime import date, datetime

from egyid.models import (
    BackFields,
    CheckResult,
    Decision,
    DerivedFields,
    FieldSource,
    FieldStatus,
    FieldValue,
    FrontFields,
    ImageValidation,
    Severity,
)
from egyid.nid import parse_national_id

MIN_AGE = 18
ISSUE_EXPIRY_MONTHS = (70, 90)
_GENDER_CANON = {
    "ذكر": "ذكر",
    "male": "ذكر",
    "Male": "ذكر",
    "أنثى": "أنثى",
    "انثى": "أنثى",
    "female": "أنثى",
    "Female": "أنثى",
}


def canonicalize_gender(value: str | None) -> str:
    text = (value or "").strip()
    return _GENDER_CANON.get(text, text)


def _iso_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            parsed = datetime.strptime(value, fmt).date()
            return parsed
        except ValueError:
            continue
    return None


def _age_years(born: date, today: date) -> int:
    years = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        years -= 1
    return years


def derive_from_nid(front: FrontFields, back: BackFields) -> DerivedFields:
    derived = DerivedFields()
    parsed = None
    for candidate in (front.national_id, back.national_id):
        if candidate.status != FieldStatus.present or not candidate.value:
            continue
        candidate_parsed = parse_national_id(candidate.value)
        if candidate_parsed and candidate_parsed.ok:
            parsed = candidate_parsed
            break
    if parsed is None:
        return derived
    if parsed.date_of_birth:
        derived.date_of_birth = FieldValue.present(
            parsed.date_of_birth, 1.0, FieldSource.derived
        )
    if parsed.gender:
        derived.gender = FieldValue.present(parsed.gender, 1.0, FieldSource.derived)
    if parsed.governorate_code:
        derived.birth_governorate_code = FieldValue.present(
            parsed.governorate_code, 1.0, FieldSource.derived
        )
        derived.birth_governorate = FieldValue.present(
            parsed.governorate, 1.0, FieldSource.derived
        )
    return derived


def run_checks(
    front: FrontFields,
    back: BackFields,
    derived: DerivedFields,
    front_image: ImageValidation,
    back_image: ImageValidation,
    today: date | None = None,
) -> tuple[list[CheckResult], DerivedFields, Decision, list[str]]:
    """Telecom onboarding policy: fail, review, or ignore. Optional fields never decide."""
    today = today or date.today()
    checks: list[CheckResult] = []

    def add(name: str, passed: bool, detail: str, severity: Severity = Severity.error) -> None:
        checks.append(CheckResult(name=name, passed=passed, severity=severity, detail=detail))

    def ignore(name: str, field: FieldValue, detail: str | None = None) -> None:
        add(
            name,
            True,
            detail or f"optional status={field.status.value}",
            Severity.info,
        )

    def review_if_missing(name: str, field: FieldValue) -> None:
        if field.status in {FieldStatus.present, FieldStatus.not_applicable}:
            add(name, True, f"status={field.status.value}", Severity.info)
        else:
            add(name, False, f"status={field.status.value}", Severity.warning)

    front_load_ok = "unreadable_image" not in front_image.issues and "empty_file" not in front_image.issues
    back_load_ok = "unreadable_image" not in back_image.issues and "empty_file" not in back_image.issues
    add("front_image_readable", front_load_ok, "front image decoded" if front_load_ok else "front image has load issues")
    add("back_image_readable", back_load_ok, "back image decoded" if back_load_ok else "back image has load issues")
    add(
        "front_quality",
        not (front_image.quality.issues if front_image.quality else []),
        "front quality acceptable" if not (front_image.quality.issues if front_image.quality else []) else "front quality issues",
        Severity.warning,
    )
    add(
        "back_quality",
        not (back_image.quality.issues if back_image.quality else []),
        "back quality acceptable" if not (back_image.quality.issues if back_image.quality else []) else "back quality issues",
        Severity.warning,
    )

    review_if_missing("front_name", front.full_name_ar)
    review_if_missing("front_address", front.address_ar)
    review_if_missing("front_serial", front.card_serial)
    review_if_missing("front_portrait", front.portrait_present)
    ignore("front_national_id", front.national_id, "presence recorded; usable_nid decides")
    ignore("front_dob_printed", front.date_of_birth_printed)
    ignore("front_header", front.header_present)

    ignore("back_national_id", back.national_id, "presence recorded; usable_nid decides")
    ignore("back_issue_date", back.issue_date)
    ignore("back_profession", back.profession_ar)
    ignore("back_gender", back.gender_printed)
    ignore("back_religion", back.religion_printed)
    ignore("back_marital_status", back.marital_status_ar)
    ignore("back_husband_name", back.husband_name_ar)
    ignore("back_barcode", back.barcode_present)
    review_if_missing("back_expiry", back.expiry_date)

    front_nid = parse_national_id(front.national_id.value) if front.national_id.status == FieldStatus.present else None
    back_nid = parse_national_id(back.national_id.value) if back.national_id.status == FieldStatus.present else None
    front_ok = bool(front_nid and front_nid.ok)
    back_ok = bool(back_nid and back_nid.ok)
    add("front_nid_structure", front_ok, "front national ID structure ok" if front_ok else "front national ID structure invalid", Severity.info)
    add("back_nid_structure", back_ok, "back national ID structure ok" if back_ok else "back national ID structure invalid", Severity.info)
    add(
        "usable_nid",
        front_ok or back_ok,
        "at least one structurally valid national ID" if (front_ok or back_ok) else "no structurally valid national ID",
    )
    if front_ok != back_ok:
        add("nid_single_side", False, "only one side has a valid national ID", Severity.warning)
    else:
        add("nid_single_side", True, "both sides valid or neither", Severity.info)

    both_present = (
        front.national_id.status == FieldStatus.present
        and back.national_id.status == FieldStatus.present
        and bool(front.national_id.value)
        and bool(back.national_id.value)
    )
    if both_present:
        nid_match = front.national_id.value == back.national_id.value
        add(
            "front_back_nid_match",
            nid_match,
            "front and back national IDs match" if nid_match else "front and back national IDs differ",
        )
    else:
        add("front_back_nid_match", True, "cross-check skipped; both sides not present", Severity.info)

    if (
        front.date_of_birth_printed.status == FieldStatus.present
        and derived.date_of_birth.status == FieldStatus.present
    ):
        dob_match = front.date_of_birth_printed.value == derived.date_of_birth.value
        add(
            "printed_derived_dob_match",
            dob_match,
            "printed DOB compared with derived DOB",
            Severity.warning,
        )
    else:
        add("printed_derived_dob_match", True, "printed DOB cross-check skipped", Severity.info)

    if (
        back.gender_printed.status == FieldStatus.present
        and derived.gender.status == FieldStatus.present
    ):
        gender_match = canonicalize_gender(back.gender_printed.value) == canonicalize_gender(
            derived.gender.value
        )
        add(
            "printed_derived_gender_match",
            gender_match,
            "printed gender compared with derived gender",
            Severity.warning,
        )
    else:
        add("printed_derived_gender_match", True, "printed gender cross-check skipped", Severity.info)

    born = _iso_date(derived.date_of_birth.value) if derived.date_of_birth.status == FieldStatus.present else None
    if born:
        age = _age_years(born, today)
        add(
            "age_at_least_18",
            age >= MIN_AGE,
            f"derived age {age}",
        )
    else:
        add("age_at_least_18", True, "age not computed; no derived DOB", Severity.info)

    expiry = _iso_date(back.expiry_date.value) if back.expiry_date.status == FieldStatus.present else None
    if expiry:
        expired = expiry < today
        derived.is_expired = FieldValue.present("true" if expired else "false", 1.0, FieldSource.derived)
        add("expiry_not_past", not expired, "expiry compared with today")
    else:
        derived.is_expired = FieldValue.unreadable()
        add("expiry_not_past", True, "expiry missing or unreadable; not treated as expired", Severity.info)

    issue = _iso_date(back.issue_date.value) if back.issue_date.status == FieldStatus.present else None
    if issue and expiry:
        months = (expiry.year - issue.year) * 12 + (expiry.month - issue.month)
        add(
            "issue_expiry_window",
            ISSUE_EXPIRY_MONTHS[0] <= months <= ISSUE_EXPIRY_MONTHS[1],
            "issue-to-expiry interval near seven years",
            Severity.warning,
        )
    else:
        add("issue_expiry_window", True, "interval not computed", Severity.info)

    add("barcode_not_decoded", True, "payload verification not claimed", Severity.info)

    blocking = [c for c in checks if not c.passed and c.severity == Severity.error]
    warnings = [c for c in checks if not c.passed and c.severity == Severity.warning]
    reasons = [c.name for c in blocking + warnings]

    unreadable = (not front_image.ok and "unreadable_image" in front_image.issues) or (
        not back_image.ok and "unreadable_image" in back_image.issues
    )
    if unreadable or blocking:
        decision = Decision.failed
    elif warnings:
        decision = Decision.needs_review
    else:
        decision = Decision.completed

    return checks, derived, decision, reasons
