from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from egyid.pipeline import process_id_pair
from egyid.review import MSG_NID_MISMATCH, build_review, persist_review, review_file
from egyid.store import DuplicateSubmissionError, Store, get_store
from egyid.workflow import (
    UploadRejected,
    apply_reviewer_decision,
    customer_view,
    duplicate_messages,
    image_path,
    reviewer_detail,
    reviewer_summary,
    submit_application,
)

ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "application/octet-stream",
}

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
_CUSTOMER_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _require_customer_id(customer_id: str) -> str:
    value = (customer_id or "").strip().lower()
    if not _CUSTOMER_RE.match(value):
        raise HTTPException(status_code=400, detail="invalid customer id")
    return value


class ReviewIn(BaseModel):
    extraction: dict[str, Any]
    corrections: dict[str, str] = Field(default_factory=dict)


class DecisionIn(BaseModel):
    action: str
    corrections: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None


def _check_type(upload: UploadFile, side: str) -> None:
    if upload.content_type and upload.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail=f"unsupported {side} content type")


def _duplicate_rejected(exc: DuplicateSubmissionError) -> JSONResponse:
    messages = duplicate_messages(exc.kinds)
    return JSONResponse(
        status_code=409,
        content={
            "detail": "\n".join(messages),
            "reasons": messages,
            "customer_message": "\n".join(messages),
            "duplicate": list(exc.kinds),
        },
    )


def _upload_rejected(exc: UploadRejected) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Please upload clearer front and back ID images.",
            "reasons": exc.messages,
            "customer_message": "\n".join(exc.messages),
        },
    )


def create_app(store: Store | None = None) -> FastAPI:
    db = store or get_store()
    app = FastAPI(
        title="Egyptian National ID Intelligence",
        version="0.1.0",
        description="Staged document intelligence for Egyptian National ID front and back images.",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    def customer_ui() -> FileResponse:
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="ui not found")
        return FileResponse(index)

    @app.get("/review")
    def reviewer_ui() -> FileResponse:
        page = STATIC_DIR / "reviewer.html"
        if not page.is_file():
            raise HTTPException(status_code=404, detail="ui not found")
        return FileResponse(page)

    @app.post("/v1/id-documents")
    async def process_id_documents(
        front: UploadFile = File(..., description="National ID front image"),
        back: UploadFile = File(..., description="National ID back image including barcode"),
    ) -> dict:
        _check_type(front, "front")
        _check_type(back, "back")
        result = process_id_pair(
            await front.read(),
            await back.read(),
            front_content_type=front.content_type,
            back_content_type=back.content_type,
        )
        return result.model_dump_public()

    @app.post("/v1/applications")
    async def create_application(
        front: UploadFile = File(...),
        back: UploadFile = File(...),
        customer_id: str = Form(...),
    ) -> dict:
        _check_type(front, "front")
        _check_type(back, "back")
        try:
            record = submit_application(
                db,
                await front.read(),
                await back.read(),
                front_filename=front.filename,
                back_filename=back.filename,
                front_content_type=front.content_type,
                back_content_type=back.content_type,
                customer_id=_require_customer_id(customer_id),
            )
        except UploadRejected as exc:
            return _upload_rejected(exc)
        except DuplicateSubmissionError as exc:
            return _duplicate_rejected(exc)
        return customer_view(record)

    @app.get("/v1/applications/{application_id}")
    def get_application(application_id: str) -> dict:
        record = db.get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="application not found")
        return customer_view(record)

    @app.get("/v1/customers/{customer_id}/applications")
    def list_customer_applications(customer_id: str) -> dict:
        owner = _require_customer_id(customer_id)
        records = db.list_for_customer(owner)
        return {"customer_id": owner, "applications": [customer_view(item) for item in records]}

    @app.post("/v1/applications/{application_id}/resubmit")
    async def resubmit_application(
        application_id: str,
        front: UploadFile = File(...),
        back: UploadFile = File(...),
        customer_id: str = Form(...),
    ) -> dict:
        _check_type(front, "front")
        _check_type(back, "back")
        try:
            record = submit_application(
                db,
                await front.read(),
                await back.read(),
                front_filename=front.filename,
                back_filename=back.filename,
                front_content_type=front.content_type,
                back_content_type=back.content_type,
                application_id=application_id,
                customer_id=_require_customer_id(customer_id),
            )
        except UploadRejected as exc:
            return _upload_rejected(exc)
        except DuplicateSubmissionError as exc:
            return _duplicate_rejected(exc)
        except KeyError:
            raise HTTPException(status_code=404, detail="application not found") from None
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return customer_view(record)

    @app.get("/v1/applications/{application_id}/{side}")
    def get_application_image(application_id: str, side: str) -> FileResponse:
        if side not in {"front", "back"}:
            raise HTTPException(status_code=404, detail="image not found")
        record = db.get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="application not found")
        path = image_path(record, side)
        if path is None:
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(path)

    @app.get("/v1/review/applications")
    def list_review_applications(status: str | None = None) -> dict:
        records = db.list_all(status)
        return {"applications": [reviewer_summary(item) for item in records]}

    @app.get("/v1/review/applications/{application_id}")
    def get_review_application(application_id: str) -> dict:
        record = db.get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="application not found")
        return reviewer_detail(record)

    @app.post("/v1/review/applications/{application_id}/decision")
    def decide_application(application_id: str, payload: DecisionIn) -> JSONResponse:
        try:
            record, review, applied = apply_reviewer_decision(
                db,
                application_id,
                payload.action,
                payload.corrections,
                payload.reason,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="application not found") from None
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not applied:
            reasons = (review or {}).get("review_reasons") or []
            field_messages = [
                message
                for item in (review or {}).get("fields") or []
                for message in (item.get("review_validation") or {}).get("messages") or []
            ]
            if "front_back_nid_match" in reasons:
                detail = MSG_NID_MISMATCH
            elif field_messages:
                detail = field_messages[0]
            else:
                detail = "review incomplete"
            return JSONResponse(
                status_code=422,
                content={"detail": detail, "review": review},
            )
        return JSONResponse(
            {
                "application": reviewer_detail(record),
                "customer": customer_view(record),
                "review": review,
            }
        )

    @app.post("/v1/reviews")
    def create_review(payload: ReviewIn) -> JSONResponse:
        record, complete = build_review(payload.extraction, payload.corrections)
        if not complete:
            return JSONResponse(
                status_code=422,
                content={"detail": "review incomplete", "review": record},
            )
        persist_review(record)
        return JSONResponse(
            {
                "review_id": record["review_id"],
                "download_url": f"/v1/reviews/{record['review_id']}",
                "review": record,
            }
        )

    @app.get("/v1/reviews/{review_id}")
    def download_review(review_id: str) -> FileResponse:
        path = review_file(review_id)
        if path is None:
            raise HTTPException(status_code=404, detail="review not found")
        return FileResponse(
            path,
            media_type="application/json",
            filename=f"{review_id}.json",
        )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
