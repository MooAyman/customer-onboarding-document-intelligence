"""Map Azure prebuilt-layout JSON to Vodafone DSL application fields.

Isolated from the National ID RapidOCR pipeline. Does not call Azure.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from egyid.models import FieldStatus, FieldValue
from egyid.nid import canonical_nid, digits_only, normalize_digits, parse_national_id

HEADER_Y_MAX = 5.6
LOW_CONFIDENCE = 0.70
NAME_LABELS = ("الإسم", "الاسم")
NID_LABELS = ("رقم إثبات الشخصية", "الرقم القومي")
MOBILE_LABELS = ("رقم الهاتف المحمول", "الهاتف المحمول")
SPEED_LABELS = ("السرعة",)
PRINTED_SPEED_OPTIONS = re.compile(
    r"30\s*Mbps\s*[-–]\s*70\s*Mbps\s*[-–]\s*100\s*Mbps",
    re.I,
)
SPEED_CHOICE = re.compile(r"(?:^|[^\d])(30|70|100)(?:\s*M|\s*Mbps|\b)", re.I)
MARK_TOKEN = re.compile(r":(?:un)?selected:", re.I)
HAS_ARABIC = re.compile(r"[\u0600-\u06FF]")
_NAME_NORM = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
    }
)


class ApplicationFormResult(BaseModel):
    full_name: FieldValue = Field(default_factory=FieldValue.absent)
    national_id: FieldValue = Field(default_factory=FieldValue.absent)
    gender: FieldValue = Field(default_factory=FieldValue.absent)
    mobile: FieldValue = Field(default_factory=FieldValue.absent)
    speed: FieldValue = Field(default_factory=FieldValue.absent)
    review_flags: list[str] = Field(default_factory=list)
    needs_review: bool = False


def form_payload(result: ApplicationFormResult) -> dict[str, Any]:
    dumped = result.model_dump(mode="json")
    return {
        "fields": {
            "full_name": dumped["full_name"],
            "national_id": dumped["national_id"],
            "gender": dumped["gender"],
            "mobile": dumped["mobile"],
            "speed": dumped["speed"],
        },
        "review_flags": dumped["review_flags"],
        "needs_review": dumped["needs_review"],
    }


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _centroid(polygon: Any) -> tuple[float, float] | None:
    points = list(polygon or [])
    if not points:
        return None
    if isinstance(points[0], dict):
        xs = [float(p["x"]) for p in points]
        ys = [float(p["y"]) for p in points]
    else:
        xs = [float(points[i]) for i in range(0, len(points) - 1, 2)]
        ys = [float(points[i]) for i in range(1, len(points), 2)]
    if not xs or not ys:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _clean(text: Any) -> str:
    value = MARK_TOKEN.sub(" ", str(text or ""))
    value = value.replace("#", " ")
    return re.sub(r"\s+", " ", normalize_digits(value)).strip()


def _strip_label(text: str, labels: tuple[str, ...]) -> str | None:
    compact = _clean(text)
    if not compact:
        return None
    for label in labels:
        if compact.startswith(label):
            return compact[len(label) :].lstrip(" :：-–").strip()
        if label in compact:
            after = compact.split(label, 1)[1].lstrip(" :：-–").strip()
            return after
    return None


def _has_label(text: str, labels: tuple[str, ...]) -> bool:
    compact = _clean(text)
    return any(label in compact for label in labels)


def _arabic_value(text: str) -> str:
    cleaned = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _iter_lines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in payload.get("pages") or []:
        for line in page.get("lines") or []:
            center = _centroid(line.get("polygon"))
            out.append(
                {
                    "content": _clean(line.get("content")),
                    "cx": center[0] if center else 0.0,
                    "cy": center[1] if center else 0.0,
                    "page": page.get("page_number"),
                }
            )
    return out


def _iter_cells(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for table in payload.get("tables") or []:
        for cell in table.get("cells") or []:
            regions = cell.get("bounding_regions") or []
            region = regions[0] if regions else {}
            center = _centroid(region.get("polygon") if region else None)
            out.append(
                {
                    "content": _clean(cell.get("content")),
                    "cx": center[0] if center else 0.0,
                    "cy": center[1] if center else 0.0,
                    "page": region.get("page_number", 1),
                }
            )
    return out


def _iter_marks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in payload.get("pages") or []:
        for mark in page.get("selection_marks") or []:
            center = _centroid(mark.get("polygon"))
            state = str(mark.get("state") or "").lower()
            confidence = mark.get("confidence")
            out.append(
                {
                    "state": state,
                    "confidence": float(confidence) if confidence is not None else None,
                    "cx": center[0] if center else 0.0,
                    "cy": center[1] if center else 0.0,
                    "page": page.get("page_number"),
                }
            )
    return out


def _header_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("page", 1) == 1 and item["cy"] <= HEADER_Y_MAX
    ]


def _map_name(lines: list[dict[str, Any]], cells: list[dict[str, Any]]) -> FieldValue:
    candidates: list[str] = []
    for item in _header_items(lines + cells):
        if not _has_label(item["content"], NAME_LABELS):
            continue
        remainder = _strip_label(item["content"], NAME_LABELS) or ""
        value = _arabic_value(remainder)
        if value:
            candidates.append(value)
    if not candidates:
        return FieldValue.absent()
    # Longest remainder is the full name line, not a split table cell.
    value = max(candidates, key=len)
    return FieldValue.present(value, raw_ocr=value, extraction_source="azure_layout")


def _digit_runs(text: str) -> list[str]:
    return re.findall(r"\d+", digits_only(text))


def _map_national_id(lines: list[dict[str, Any]], cells: list[dict[str, Any]]) -> FieldValue:
    runs: list[str] = []
    raw_bits: list[str] = []
    for item in _header_items(lines + cells):
        if not _has_label(item["content"], NID_LABELS) and not re.fullmatch(r"\d+", item["content"] or ""):
            continue
        if re.fullmatch(r"\d+", item["content"] or "") and not any(
            _has_label(other["content"], NID_LABELS) and abs(other["cy"] - item["cy"]) < 0.25
            for other in _header_items(lines + cells)
        ):
            continue
        raw_bits.append(item["content"])
        runs.extend(_digit_runs(item["content"]))
    if not runs:
        return FieldValue.absent()
    fourteen = [run for run in runs if len(run) == 14]
    if fourteen:
        parsed = parse_national_id(fourteen[0])
        if parsed and parsed.ok:
            nid = canonical_nid(fourteen[0])
            return FieldValue.present(
                nid, raw_ocr=" ".join(raw_bits), extraction_source="azure_layout"
            )
        return FieldValue.unreadable(raw_ocr=" ".join(raw_bits))
    return FieldValue.unreadable(raw_ocr=" ".join(raw_bits))


def _map_mobile(lines: list[dict[str, Any]], cells: list[dict[str, Any]]) -> FieldValue:
    for item in _header_items(lines + cells):
        if not _has_label(item["content"], MOBILE_LABELS):
            continue
        remainder = _strip_label(item["content"], MOBILE_LABELS) or ""
        digits = digits_only(remainder)
        if not digits:
            return FieldValue.absent()
        if len(digits) == 11 and digits.startswith("01"):
            return FieldValue.present(
                digits, raw_ocr=remainder, extraction_source="azure_layout"
            )
        return FieldValue.unreadable(raw_ocr=remainder)
    return FieldValue.absent()


def _map_speed(lines: list[dict[str, Any]]) -> FieldValue:
    header = _header_items(lines)
    speed_labels = [item for item in header if _has_label(item["content"], SPEED_LABELS)]
    if not speed_labels:
        return FieldValue.absent()
    label_y = speed_labels[0]["cy"]
    found: list[str] = []
    for item in header:
        text = item["content"]
        if PRINTED_SPEED_OPTIONS.search(text):
            continue
        if abs(item["cy"] - label_y) > 0.45:
            continue
        match = SPEED_CHOICE.search(text)
        if match:
            found.append(match.group(1))
    if not found:
        return FieldValue.absent()
    value = f"{found[0]} Mbps"
    return FieldValue.present(value, raw_ocr=found[0], extraction_source="azure_layout")


def _map_gender(lines: list[dict[str, Any]], marks: list[dict[str, Any]]) -> tuple[FieldValue, list[str]]:
    flags: list[str] = []
    gender_lines = [
        item
        for item in _header_items(lines)
        if "ذكر" in item["content"] or "انثى" in item["content"] or "أنثى" in item["content"]
    ]
    if not gender_lines:
        return FieldValue.absent(), flags
    label_y = sum(item["cy"] for item in gender_lines) / len(gender_lines)
    row_marks = [
        mark
        for mark in marks
        if mark.get("page", 1) == 1 and abs(mark["cy"] - label_y) < 0.35
    ]
    if len(row_marks) < 2:
        return FieldValue.absent(), flags
    ordered = sorted(row_marks, key=lambda mark: mark["cx"])
    female, male = ordered[0], ordered[-1]
    female_on = female["state"] == "selected"
    male_on = male["state"] == "selected"
    if male_on == female_on:
        if not male_on:
            return FieldValue.absent(), flags
        return FieldValue.unreadable(), flags
    chosen = male if male_on else female
    value = "ذكر" if male_on else "أنثى"
    confidence = chosen["confidence"]
    field = FieldValue.present(
        value,
        confidence=confidence,
        raw_ocr=value,
        extraction_source="azure_layout",
    )
    if confidence is not None and confidence < LOW_CONFIDENCE:
        flags.append("gender_low_confidence")
    return field, flags


def _norm_name(value: str | None) -> str:
    text = (value or "").translate(_NAME_NORM)
    return re.sub(r"\s+", " ", text).strip()


def _name_overlap(form_name: str, id_name: str) -> bool:
    form_tokens = set(_norm_name(form_name).split())
    id_tokens = set(_norm_name(id_name).split())
    return bool(form_tokens and id_tokens and form_tokens & id_tokens)


def _field_compare_flags(
    *,
    prefix: str,
    form_status: FieldStatus,
    form_value: str | None,
    id_value: str | None,
    matched: bool,
    compare: bool,
) -> list[str]:
    if form_status == FieldStatus.unreadable:
        return [f"{prefix}_unreadable"]
    if form_status != FieldStatus.present or not (form_value or "").strip():
        return [f"{prefix}_missing"]
    if not compare:
        return []
    if not (id_value or "").strip():
        return [f"{prefix}_unverified"]
    if not matched:
        return [f"{prefix}_mismatch"]
    return []


def _cross_check(
    result: ApplicationFormResult,
    *,
    id_name: str | None,
    id_nid: str | None,
    id_gender: str | None,
) -> None:
    compare = any(value for value in (id_name, id_nid, id_gender))
    flags = list(result.review_flags)
    form_nid = (
        canonical_nid(result.national_id.value)
        if result.national_id.status == FieldStatus.present
        else ""
    )
    stage_nid = canonical_nid(id_nid)
    flags.extend(
        _field_compare_flags(
            prefix="national_id",
            form_status=result.national_id.status,
            form_value=form_nid or result.national_id.value,
            id_value=stage_nid or id_nid,
            matched=bool(form_nid and stage_nid and form_nid == stage_nid),
            compare=compare,
        )
    )
    flags.extend(
        _field_compare_flags(
            prefix="name",
            form_status=result.full_name.status,
            form_value=result.full_name.value,
            id_value=id_name,
            matched=_name_overlap(result.full_name.value or "", id_name or ""),
            compare=compare,
        )
    )
    flags.extend(
        _field_compare_flags(
            prefix="gender",
            form_status=result.gender.status,
            form_value=result.gender.value,
            id_value=id_gender,
            matched=bool(
                result.gender.value and id_gender and result.gender.value == id_gender
            ),
            compare=compare,
        )
    )
    result.review_flags = list(dict.fromkeys(flags))
    result.needs_review = bool(result.review_flags)


def map_application_fields(
    payload: dict[str, Any],
    *,
    id_name: str | None = None,
    id_nid: str | None = None,
    id_gender: str | None = None,
) -> ApplicationFormResult:
    """Extract application fields from a serialized Azure layout payload."""

    lines = _iter_lines(payload)
    cells = _iter_cells(payload)
    marks = _iter_marks(payload)
    gender, flags = _map_gender(lines, marks)
    result = ApplicationFormResult(
        full_name=_map_name(lines, cells),
        national_id=_map_national_id(lines, cells),
        gender=gender,
        mobile=_map_mobile(lines, cells),
        speed=_map_speed(lines),
        review_flags=flags,
    )
    _cross_check(result, id_name=id_name, id_nid=id_nid, id_gender=id_gender)
    return result
