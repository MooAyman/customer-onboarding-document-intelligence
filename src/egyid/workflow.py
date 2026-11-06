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


def duplicate_messages(kinds: tuple[str, ...] | list[str]) -> list[str]:
    return [DUPLICATE_KIND_MESSAGES[kind] for kind in kinds if kind in DUPLICATE_KIND_MESSAGES]


class UploadRejected(ValueError):
    """Customer must replace the images. No reviewer application is created."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__(" ".join(messages))


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


def customer_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "status": record["status"],
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
            "available": record["status"] == "approved" and record["stage"] == STAGE_NEXT,
            "placeholder": True,
            "detail": "This stage is not implemented yet.",
        },
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
    nids = nids_from_extraction(extraction)
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
        "customer_message": _message(status),
        "customer_id": owner,
    }
    try:
        if existing:
            return store.update(app_id, nids=nids, images=images, replace_pairs=temps, **payload)
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
    )
    return updated, review, True


def image_path(record: dict[str, Any], side: str) -> Path | None:
    raw = record.get("front_path" if side == "front" else "back_path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None
