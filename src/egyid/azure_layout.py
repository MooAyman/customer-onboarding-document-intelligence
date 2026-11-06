"""Azure Document Intelligence prebuilt-layout experiment.

Isolated from the National ID RapidOCR pipeline. Credentials come only from
environment variables (or a local .env file that is never committed).
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from egyid.logging_utils import get_logger

logger = get_logger(__name__)

ENV_ENDPOINT = "DOCUMENTINTELLIGENCE_ENDPOINT"
ENV_API_KEY = "DOCUMENTINTELLIGENCE_API_KEY"
LAYOUT_MODEL = "prebuilt-layout"
ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

FORM_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "application/octet-stream",
    }
)
FORM_SUFFIXES = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
)


class AzureLayoutConfigError(Exception):
    """Endpoint or API key is missing, or the SDK is not installed."""


class AzureLayoutError(Exception):
    """Azure Document Intelligence rejected or failed the request."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        self.status_code = status_code
        super().__init__(message)


def _load_local_env() -> None:
    if not ENV_FILE.is_file():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("'").strip('"')
        if name and name not in os.environ:
            os.environ[name] = value


def load_credentials() -> tuple[str, str]:
    _load_local_env()
    endpoint = (os.environ.get(ENV_ENDPOINT) or "").strip()
    key = (os.environ.get(ENV_API_KEY) or "").strip()
    if not endpoint or not key:
        raise AzureLayoutConfigError(
            f"Set {ENV_ENDPOINT} and {ENV_API_KEY} before analyzing forms."
        )
    return endpoint, key


def is_supported_form(content_type: str | None, filename: str | None) -> bool:
    media = (content_type or "").split(";")[0].strip().lower()
    if media in FORM_CONTENT_TYPES:
        return True
    suffix = Path(filename or "").suffix.lower()
    return suffix in FORM_SUFFIXES


def create_client(endpoint: str | None = None, api_key: str | None = None) -> Any:
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError as exc:
        raise AzureLayoutConfigError(
            "Install azure-ai-documentintelligence to analyze application forms."
        ) from exc
    resolved_endpoint, resolved_key = (
        (endpoint, api_key) if endpoint and api_key else load_credentials()
    )
    return DocumentIntelligenceClient(
        endpoint=resolved_endpoint,
        credential=AzureKeyCredential(resolved_key),
    )


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _polygon(polygon: Any) -> list[dict[str, float]]:
    values = list(polygon or [])
    points: list[dict[str, float]] = []
    for index in range(0, len(values) - 1, 2):
        points.append({"x": float(values[index]), "y": float(values[index + 1])})
    return points


def _bounding_regions(regions: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for region in regions or []:
        out.append(
            {
                "page_number": _get(region, "page_number"),
                "polygon": _polygon(_get(region, "polygon")),
            }
        )
    return out


def _spans(spans: Any) -> list[dict[str, int]]:
    out: list[dict[str, int]] = []
    for span in spans or []:
        out.append(
            {
                "offset": int(_get(span, "offset") or 0),
                "length": int(_get(span, "length") or 0),
            }
        )
    return out


def serialize_analyze_result(result: Any) -> dict[str, Any]:
    """Map Azure AnalyzeResult to a JSON-safe layout payload."""

    pages_out: list[dict[str, Any]] = []
    for page in _get(result, "pages") or []:
        lines = []
        for line in _get(page, "lines") or []:
            lines.append(
                {
                    "content": _get(line, "content") or "",
                    "polygon": _polygon(_get(line, "polygon")),
                    "spans": _spans(_get(line, "spans")),
                }
            )
        marks = []
        for mark in _get(page, "selection_marks") or []:
            marks.append(
                {
                    "state": _get(mark, "state"),
                    "confidence": _get(mark, "confidence"),
                    "polygon": _polygon(_get(mark, "polygon")),
                }
            )
        pages_out.append(
            {
                "page_number": _get(page, "page_number"),
                "width": _get(page, "width"),
                "height": _get(page, "height"),
                "unit": _get(page, "unit"),
                "lines": lines,
                "selection_marks": marks,
            }
        )

    tables_out: list[dict[str, Any]] = []
    for table in _get(result, "tables") or []:
        cells = []
        for cell in _get(table, "cells") or []:
            cells.append(
                {
                    "row_index": _get(cell, "row_index"),
                    "column_index": _get(cell, "column_index"),
                    "row_span": _get(cell, "row_span") or 1,
                    "column_span": _get(cell, "column_span") or 1,
                    "kind": _get(cell, "kind"),
                    "content": _get(cell, "content") or "",
                    "bounding_regions": _bounding_regions(_get(cell, "bounding_regions")),
                }
            )
        tables_out.append(
            {
                "row_count": _get(table, "row_count"),
                "column_count": _get(table, "column_count"),
                "bounding_regions": _bounding_regions(_get(table, "bounding_regions")),
                "cells": cells,
            }
        )

    return {
        "model_id": _get(result, "model_id") or LAYOUT_MODEL,
        "api_version": _get(result, "api_version"),
        "content": _get(result, "content") or "",
        "pages": pages_out,
        "tables": tables_out,
    }


def _azure_error_status(exc: Any) -> int:
    status = int(getattr(exc, "status_code", 0) or 0)
    code = ""
    inner = getattr(exc, "error", None)
    if inner is not None:
        code = str(getattr(inner, "code", "") or "")
    if status in {400, 415} or code in {"InvalidImage", "InvalidRequest", "InvalidContent"}:
        return 422
    if status in {401, 403}:
        return 503
    return 502


def _azure_error_message(exc: Any) -> str:
    inner = getattr(exc, "error", None)
    if inner is not None:
        message = str(getattr(inner, "message", "") or "").strip()
        if message:
            return message
    message = str(getattr(exc, "message", "") or "").strip()
    return message or "Azure Document Intelligence request failed."


def analyze_layout(
    document: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    if not document:
        raise ValueError("empty document")
    if not is_supported_form(content_type, filename):
        raise ValueError("unsupported form type; upload a PDF or image")

    analyzer = client or create_client()
    media = (content_type or "").split(";")[0].strip().lower() or "application/octet-stream"
    try:
        poller = analyzer.begin_analyze_document(
            LAYOUT_MODEL,
            body=io.BytesIO(document),
            content_type=media,
        )
        result = poller.result()
    except AzureLayoutError:
        raise
    except Exception as exc:
        try:
            from azure.core.exceptions import HttpResponseError
        except ImportError:
            HttpResponseError = ()  # type: ignore[misc, assignment]
        if isinstance(exc, HttpResponseError):
            logger.warning("Azure layout analysis failed: %s", _azure_error_message(exc))
            raise AzureLayoutError(
                _azure_error_message(exc),
                status_code=_azure_error_status(exc),
            ) from exc
        logger.warning("Azure layout analysis failed: %s", type(exc).__name__)
        raise AzureLayoutError("Azure Document Intelligence request failed.") from exc

    payload = serialize_analyze_result(result)
    logger.info(
        "Azure layout analyzed: pages=%s tables=%s",
        len(payload["pages"]),
        len(payload["tables"]),
    )
    return payload
