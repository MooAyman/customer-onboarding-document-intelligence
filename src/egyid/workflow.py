from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from egyid.imaging import assess_quality, load_image, looks_like_id_card
from egyid.models import Decision
from egyid.nid import canonical_nid, nids_from_extraction
from egyid.pipeline import process_id_pair
from egyid.review import build_review
from egyid.store import Store, sha256_hex

STAGE_ID = "id_verification"
STAGE_NEXT = "application_form"
ROOT = Path(__file__).resolve().parents[2]
BLANK_FORM_CANDIDATES = (
    ROOT / "static" / "forms" / "dsl-application.pdf",
    ROOT / "Azure Doc" / "PG1 DSL Contract V Router Installment-1.pdf",
)
FORM_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
FORM_KEYS = ("full_name", "national_id", "gender", "mobile", "speed")
FORM_PENDING = "pending_review"
FORM_APPROVED = "approved"
FORM_REJECTED = "rejected"
FORM_RESUBMIT = "resubmission_required"
FORM_BLOCKED = frozenset({FORM_PENDING, FORM_APPROVED, FORM_REJECTED})
FORM_MESSAGES = {
    FORM_PENDING: "Your application form is pending human review.",
    FORM_APPROVED: "Your application form was accepted.",
    FORM_REJECTED: "Your application form was rejected.",
    FORM_RESUBMIT: "Please upload a clearer completed application form.",
}
CUSTOMER_MESSAGES = {
    "approved": "Your identity document was verified. You can continue to the next onboarding stage.",
    "pending_review": "Your application is pending human review. This page updates automatically.",
    "rejected": "Your application was rejected. You cannot submit another application with the same National ID.",
    "resubmission_required": "Please upload new front and back ID images and resubmit.",
}
UPLOAD_FAIL = {
    "empty_file": "the file is empty",
    "unreadable_image": "the file is corrupted or unreadable",
    "unsupported_format": "the file type is not a supported image",
    "file_too_large": "the file is too large",
    "empty_pixels": "the image has no pixel data",
}
SEVERE_QUALITY = {
    "resolution_too_low": "the photo is too small. Upload a larger, clearer image",
}
DUPLICATE_NID_MESSAGE = (
    "This National ID has already been submitted. "
    "You cannot create another application with the same ID. "
    "Open the existing application, or resubmit images if a reviewer asked for new photos."
)
DUPLICATE_KIND_MESSAGES = {
    "front": "This front image has already been submitted in another application.",
    "back": "This back image has already been submitted in another application.",
    "nid": DUPLICATE_NID_MESSAGE,
}
NON_TARGET_MESSAGE = (
    "The uploaded images are not an Egyptian national ID card. "
    "Please upload photos of the front and back of an Egyptian national ID."
)


def duplicate_messages(kinds: tuple[str, ...] | list[str]) -> list[str]:
    return [DUPLICATE_KIND_MESSAGES[kind] for kind in kinds if kind in DUPLICATE_KIND_MESSAGES]


class UploadRejected(ValueError):
    """Customer must replace the images. No reviewer application is created."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__(" ".join(messages))


class FormConflictError(PermissionError):
    """A completed form is already on file and cannot be overwritten."""

    def __init__(self, form_status: str) -> None:
        self.form_status = form_status
        super().__init__("application form already submitted")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def status_from_automated(decision: str) -> str:
    if decision == Decision.completed.value:
        return "approved"
    if decision == Decision.failed.value:
        return "rejected"
    return "pending_review"


def nids_from_review(review: dict[str, Any] | None) -> list[str]:
    found: list[str] = []
    for field in (review or {}).get("fields") or []:
        if field.get("id") not in {"front.national_id", "back.national_id"}:
            continue
        nid = canonical_nid(field.get("final_value"))
        if nid and nid not in found:
            found.append(nid)
    return found


def _message(status: str, reason: str | None = None) -> str:
    base = CUSTOMER_MESSAGES[status]
    extra = (reason or "").strip()
    if extra and status in {"rejected", "resubmission_required"}:
        return f"{base} {extra}"
    return base


def _stage(status: str) -> str:
    return STAGE_NEXT if status == "approved" else STAGE_ID


def blank_form_path() -> Path | None:
    for path in BLANK_FORM_CANDIDATES:
        if path.is_file():
            return path
    return None


def _review_finals(record: dict[str, Any]) -> dict[str, str | None]:
    finals: dict[str, str | None] = {}
    for field in (record.get("review") or {}).get("fields") or []:
        field_id = field.get("id")
        if field_id:
            finals[field_id] = field.get("final_value")
    return finals


def _extracted_value(section: dict[str, Any], key: str) -> str | None:
    value = (section.get(key) or {}).get("value")
    text = str(value).strip() if value is not None else ""
    return text or None


def id_snapshot(record: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    extraction = record.get("extraction") or {}
    front = extraction.get("front") or {}
    back = extraction.get("back") or {}
    derived = extraction.get("derived") or {}
    finals = _review_finals(record)
    name = (finals.get("front.full_name_ar") or "").strip() or _extracted_value(front, "full_name_ar")
    nid = (
        canonical_nid(finals.get("front.national_id"))
        or canonical_nid(finals.get("back.national_id"))
        or canonical_nid(_extracted_value(front, "national_id"))
        or canonical_nid(_extracted_value(back, "national_id"))
        or None
    )
    gender = (
        (finals.get("derived.gender") or "").strip()
        or (finals.get("back.gender_printed") or "").strip()
        or _extracted_value(derived, "gender")
    )
    return name, nid, gender


def form_view(record: dict[str, Any]) -> dict[str, Any] | None:
    form = record.get("form")
    if not form:
        return None
    status = record.get("form_status") or form.get("status")
    return {
        "status": status,
        "fields": form.get("fields"),
        "review_flags": form.get("review_flags") or [],
        "needs_review": status == FORM_PENDING,
        "corrections": form.get("corrections") or {},
        "reviewer_action": form.get("reviewer_action"),
        "reviewer_reason": form.get("reviewer_reason"),
        "reviewed_at": form.get("reviewed_at"),
        "can_resubmit": status == FORM_RESUBMIT,
        "message": FORM_MESSAGES.get(status or ""),
    }


def customer_view(record: dict[str, Any]) -> dict[str, Any]:
    approved = record["status"] == "approved" and record["stage"] == STAGE_NEXT
    form = form_view(record)
    form_status = (form or {}).get("status")
    can_upload_form = approved and (form is None or form.get("can_resubmit"))
    return {
        "id": record["id"],
        "status": record["status"],
        "form_status": form_status,
        "stage": record["stage"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "processed_at": record["processed_at"],
        "reviewed_at": record["reviewed_at"],
        "automated_decision": record["automated_decision"],
        "reviewer_action": record["reviewer_action"],
        "reviewer_reason": record["reviewer_reason"],
        "customer_message": record["customer_message"],
        "next_stage": {
            "key": STAGE_NEXT,
            "label": "Application form",
            "available": approved,
            "placeholder": False,
            "detail": "Download the blank form, fill it by hand, then upload the completed page.",
            "blank_form_url": f"/v1/applications/{record['id']}/form/blank" if approved else None,
            "submit_url": f"/v1/applications/{record['id']}/form" if can_upload_form else None,
            "can_upload": can_upload_form,
        },
        "application_form": form,
        "can_resubmit": record["status"] == "resubmission_required",
        "customer_id": record.get("customer_id"),
    }


def reviewer_summary(record: dict[str, Any]) -> dict[str, Any]:
    extraction = record.get("extraction") or {}
    name = ((extraction.get("front") or {}).get("full_name_ar") or {}).get("value")
    return {
        "id": record["id"],
        "status": record["status"],
        "stage": record["stage"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "automated_decision": record["automated_decision"],
        "automated_reasons": record.get("automated_reasons") or [],
        "reviewer_action": record["reviewer_action"],
        "form_status": record.get("form_status") or (record.get("form") or {}).get("status"),
        "applicant_name": name,
    }


def reviewer_detail(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **reviewer_summary(record),
        "reviewed_at": record["reviewed_at"],
        "reviewer_reason": record["reviewer_reason"],
        "customer_message": record["customer_message"],
        "extraction": record["extraction"],
        "corrections": record.get("corrections") or {},
        "review": record.get("review"),
        "front_url": f"/v1/applications/{record['id']}/front",
        "back_url": f"/v1/applications/{record['id']}/back",
        "front_filename": record.get("front_filename"),
        "back_filename": record.get("back_filename"),
        "form": form_view(record),
        "form_url": f"/v1/applications/{record['id']}/form" if record.get("form_path") else None,
    }


def inspect_upload(data: bytes, content_type: str | None, side: str) -> list[str]:
    """Reject missing/corrupt/unsupported/tiny images. Quality warnings still go to HITL."""
    loaded = load_image(data, content_type)
    messages: list[str] = []
    for issue in loaded.issues:
        if issue in UPLOAD_FAIL:
            messages.append(f"{side.capitalize()} image: {UPLOAD_FAIL[issue]}. Please upload a better photo.")
    if loaded.ok:
        quality = assess_quality(loaded.array)
        if quality.too_small:
            messages.append(
                f"{side.capitalize()} image: {SEVERE_QUALITY['resolution_too_low']}."
            )
        elif not looks_like_id_card(loaded.array, side).ok:
            messages.append(
                f"{side.capitalize()} image: this does not look like an Egyptian national ID card. "
                f"Please upload a photo of the {side} of the card."
            )
    return messages


def submit_application(
    store: Store,
    front_bytes: bytes,
    back_bytes: bytes,
    *,
    front_filename: str | None,
    back_filename: str | None,
    front_content_type: str | None,
    back_content_type: str | None,
    application_id: str | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    app_id = application_id or str(uuid.uuid4())
    existing = store.get(app_id) if application_id else None
    if application_id and existing is None:
        raise KeyError(app_id)
    if existing and existing["status"] != "resubmission_required":
        raise PermissionError("application is not waiting for resubmission")
    if existing and customer_id and existing.get("customer_id") and existing["customer_id"] != customer_id:
        raise PermissionError("application belongs to another customer")
    owner = (existing or {}).get("customer_id") or customer_id

    problems = inspect_upload(front_bytes, front_content_type, "front") + inspect_upload(
        back_bytes, back_content_type, "back"
    )
    if problems:
        raise UploadRejected(problems)

    images = {"front": sha256_hex(front_bytes), "back": sha256_hex(back_bytes)}
    store.reject_duplicates([], images, existing["id"] if existing else None)

    result = process_id_pair(
        front_bytes,
        back_bytes,
        front_content_type=front_content_type,
        back_content_type=back_content_type,
    )
    extraction = result.model_dump_public()
    class_label = ((extraction.get("classification") or {}).get("label"))
    nids = [] if class_label == "non_target" else nids_from_extraction(extraction)
    if existing:
        nids = list(dict.fromkeys([*store.claimed_nids(app_id), *nids]))
    store.reject_duplicates(nids, images, existing["id"] if existing else None)

    front_path = store.image_dest(app_id, "front", front_filename)
    back_path = store.image_dest(app_id, "back", back_filename)
    temps: list[tuple[Path, Path]] = []
    try:
        temps.append((store.write_temp(front_path, front_bytes), front_path))
        temps.append((store.write_temp(back_path, back_bytes), back_path))
    except Exception:
        store.discard_files([tmp for tmp, _dest in temps])
        raise
    status = status_from_automated(result.decision.value)
    if "document_type_non_target" in result.reasons:
        customer_message = NON_TARGET_MESSAGE
    else:
        customer_message = _message(status)
    payload = {
        "id": app_id,
        "status": status,
        "stage": _stage(status),
        "processed_at": _now(),
        "reviewed_at": None,
        "front_path": front_path,
        "back_path": back_path,
        "front_filename": front_filename,
        "back_filename": back_filename,
        "extraction": extraction,
        "corrections": {},
        "review": None,
        "automated_decision": result.decision.value,
        "automated_reasons": list(result.reasons),
        "reviewer_action": None,
        "reviewer_reason": None,
        "customer_message": customer_message,
        "customer_id": owner,
    }
    if existing:
        payload["form"] = None
        payload["form_path"] = None
        payload["form_status"] = None
    stale_forms = store.form_paths(app_id) if existing else []
    try:
        if existing:
            updated = store.update(app_id, nids=nids, images=images, replace_pairs=temps, **payload)
            store.discard_files(stale_forms)
            return updated
        store.replace_images(temps)
        try:
            return store.create(payload, nids=nids, images=images)
        except Exception:
            store.discard_files([front_path, back_path])
            raise
    except Exception:
        store.discard_files([tmp for tmp, _dest in temps])
        raise


def apply_reviewer_decision(
    store: Store,
    application_id: str,
    action: str,
    corrections: dict[str, str] | None = None,
    reason: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    record = store.get(application_id)
    if record is None:
        raise KeyError(application_id)
    if record["status"] != "pending_review":
        raise PermissionError("application is not pending review")
    if action not in {"accept", "reject", "resubmit"}:
        raise ValueError("unsupported reviewer action")

    review, complete = build_review(record["extraction"], corrections)
    note = (reason or "").strip()
    if action in {"reject", "resubmit"} and not note:
        raise ValueError("reason is required")
    if action == "accept" and not complete:
        return record, review, False

    if action == "accept":
        status = "approved"
        reviewer_action = "accepted"
    elif action == "reject":
        status = "rejected"
        reviewer_action = "rejected"
    else:
        status = "resubmission_required"
        reviewer_action = "resubmission_required"

    nids = nids_from_review(review) if action == "accept" else None
    clear_form = action in {"reject", "resubmit"}
    stale_forms = store.form_paths(application_id) if clear_form else []
    extra: dict[str, Any] = {}
    if clear_form:
        extra.update({"form": None, "form_path": None, "form_status": None})
    updated = store.update(
        application_id,
        nids=nids,
        status=status,
        stage=_stage(status),
        reviewed_at=_now(),
        corrections=corrections or {},
        review=review,
        reviewer_action=reviewer_action,
        reviewer_reason=note or None,
        customer_message=_message(status, note),
        expected_status="pending_review",
        **extra,
    )
    if clear_form:
        store.discard_files(stale_forms)
    return updated, review, True


def image_path(record: dict[str, Any], side: str) -> Path | None:
    raw = record.get("front_path" if side == "front" else "back_path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def uploaded_form_path(record: dict[str, Any]) -> Path | None:
    raw = record.get("form_path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _current_form_status(record: dict[str, Any]) -> str | None:
    return record.get("form_status") or (record.get("form") or {}).get("status")


def _form_record(
    mapped: Any,
    status: str,
    *,
    corrections: dict[str, str] | None = None,
    reviewer_action: str | None = None,
    reviewer_reason: str | None = None,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    from egyid.form_map import form_payload

    payload = form_payload(mapped)
    payload["status"] = status
    payload["needs_review"] = status == FORM_PENDING
    payload["corrections"] = corrections or {}
    payload["reviewer_action"] = reviewer_action
    payload["reviewer_reason"] = reviewer_reason
    payload["reviewed_at"] = reviewed_at
    return payload


def _clean_form_corrections(raw: dict[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in (raw or {}).items():
        if key not in FORM_KEYS:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def submit_application_form(
    store: Store,
    application_id: str,
    document: bytes,
    *,
    filename: str | None,
    content_type: str | None,
    analyzer=None,
) -> dict[str, Any]:
    from egyid.azure_layout import analyze_layout
    from egyid.form_map import map_application_fields

    record = store.get(application_id)
    if record is None:
        raise KeyError(application_id)
    if record["status"] != "approved" or record["stage"] != STAGE_NEXT:
        raise PermissionError("identity is not accepted")
    current_status = _current_form_status(record)
    if current_status in FORM_BLOCKED:
        raise FormConflictError(current_status)
    if not document:
        raise ValueError("empty document")

    run_layout = analyzer or analyze_layout
    layout = run_layout(document, content_type=content_type, filename=filename)
    name, nid, gender = id_snapshot(record)
    mapped = map_application_fields(layout, id_name=name, id_nid=nid, id_gender=gender)
    form_status = FORM_PENDING if mapped.needs_review else FORM_APPROVED
    payload = _form_record(mapped, form_status)

    folder = store.upload_dir / application_id
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "form.pdf").suffix.lower() or ".pdf"
    if suffix not in FORM_SUFFIXES:
        suffix = ".pdf"
    dest = folder / f"form{suffix}"
    leftovers = [path for path in store.form_paths(application_id) if path.resolve() != dest.resolve()]
    tmp = store.write_temp(dest, document)
    try:
        updated = store.update(
            application_id,
            form=payload,
            form_path=str(dest),
            form_status=form_status,
            replace_pairs=[(tmp, dest)],
        )
    except Exception:
        store.discard_files([tmp])
        raise
    store.discard_files(leftovers)
    return updated


def apply_form_decision(
    store: Store,
    application_id: str,
    action: str,
    corrections: dict[str, str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    record = store.get(application_id)
    if record is None:
        raise KeyError(application_id)
    if not record.get("form"):
        raise PermissionError("application form is not pending review")
    if _current_form_status(record) != FORM_PENDING:
        raise PermissionError("application form is not pending review")
    if action not in {"accept", "reject", "resubmit"}:
        raise ValueError("unsupported reviewer action")
    note = (reason or "").strip()
    if action in {"reject", "resubmit"} and not note:
        raise ValueError("reason is required")

    if action == "accept":
        form_status = FORM_APPROVED
        reviewer_action = "accepted"
    elif action == "reject":
        form_status = FORM_REJECTED
        reviewer_action = "rejected"
    else:
        form_status = FORM_RESUBMIT
        reviewer_action = "resubmission_required"

    form = dict(record["form"])
    form["fields"] = form.get("fields") or {}
    form["corrections"] = _clean_form_corrections(corrections)
    form["status"] = form_status
    form["needs_review"] = False
    form["reviewer_action"] = reviewer_action
    form["reviewer_reason"] = note or None
    form["reviewed_at"] = _now()
    return store.update(
        application_id,
        form=form,
        form_status=form_status,
        expected_form_status=FORM_PENDING,
    )
