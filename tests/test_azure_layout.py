from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from egyid.api import create_app
from egyid.azure_layout import (
    AzureLayoutError,
    analyze_layout,
    is_supported_form,
    serialize_analyze_result,
)


def _region(page: int, polygon: list[float]) -> SimpleNamespace:
    return SimpleNamespace(page_number=page, polygon=polygon)


def _sample_result() -> SimpleNamespace:
    return SimpleNamespace(
        model_id="prebuilt-layout",
        api_version="2024-11-30",
        content="Full Name\n☐ Agree",
        pages=[
            SimpleNamespace(
                page_number=1,
                width=8.5,
                height=11.0,
                unit="inch",
                lines=[
                    SimpleNamespace(
                        content="Full Name",
                        polygon=[1.0, 1.0, 3.0, 1.0, 3.0, 1.4, 1.0, 1.4],
                        spans=[SimpleNamespace(offset=0, length=9)],
                    )
                ],
                selection_marks=[
                    SimpleNamespace(
                        state="unselected",
                        confidence=0.98,
                        polygon=[1.0, 2.0, 1.2, 2.0, 1.2, 2.2, 1.0, 2.2],
                    )
                ],
            )
        ],
        tables=[
            SimpleNamespace(
                row_count=1,
                column_count=2,
                bounding_regions=[_region(1, [1.0, 3.0, 6.0, 3.0, 6.0, 4.0, 1.0, 4.0])],
                cells=[
                    SimpleNamespace(
                        row_index=0,
                        column_index=0,
                        row_span=1,
                        column_span=1,
                        kind="columnHeader",
                        content="Plan",
                        bounding_regions=[_region(1, [1.0, 3.0, 3.0, 3.0, 3.0, 4.0, 1.0, 4.0])],
                    ),
                    SimpleNamespace(
                        row_index=0,
                        column_index=1,
                        content="Voice",
                        bounding_regions=[_region(1, [3.0, 3.0, 6.0, 3.0, 6.0, 4.0, 3.0, 4.0])],
                    ),
                ],
            )
        ],
    )


class FakePoller:
    def __init__(self, result) -> None:
        self._result = result

    def result(self):
        return self._result


class FakeClient:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else _sample_result()
        self.error = error
        self.calls: list[tuple] = []

    def begin_analyze_document(self, model_id, body, **kwargs):
        self.calls.append((model_id, body, kwargs))
        if self.error is not None:
            raise self.error
        return FakePoller(self.result)


def test_serialize_analyze_result_maps_layout_fields():
    payload = serialize_analyze_result(_sample_result())
    assert payload["model_id"] == "prebuilt-layout"
    assert payload["content"].startswith("Full Name")
    assert payload["pages"][0]["lines"][0]["content"] == "Full Name"
    assert payload["pages"][0]["lines"][0]["polygon"][0] == {"x": 1.0, "y": 1.0}
    assert payload["pages"][0]["selection_marks"][0]["state"] == "unselected"
    assert payload["tables"][0]["cells"][0]["content"] == "Plan"
    assert payload["tables"][0]["cells"][0]["bounding_regions"][0]["page_number"] == 1


def test_analyze_layout_uses_prebuilt_layout_and_mocked_client():
    client = FakeClient()
    payload = analyze_layout(
        b"%PDF-1.4 form",
        content_type="application/pdf",
        filename="form.pdf",
        client=client,
    )
    assert client.calls[0][0] == "prebuilt-layout"
    assert payload["tables"][0]["row_count"] == 1
    assert payload["pages"][0]["selection_marks"][0]["confidence"] == 0.98


def test_analyze_layout_rejects_empty_and_unsupported_types():
    fake = FakeClient()
    with pytest.raises(ValueError, match="empty document"):
        analyze_layout(b"", content_type="application/pdf", client=fake)
    with pytest.raises(ValueError, match="unsupported form type"):
        analyze_layout(b"hello", content_type="text/plain", filename="notes.txt", client=fake)
    assert fake.calls == []


def test_analyze_layout_maps_azure_http_errors():
    from azure.core.exceptions import HttpResponseError

    error = HttpResponseError(message="InvalidImage")
    error.status_code = 400
    error.error = SimpleNamespace(code="InvalidImage", message="The file could not be read.")
    with pytest.raises(AzureLayoutError) as caught:
        analyze_layout(
            b"%PDF-1.4 form",
            content_type="application/pdf",
            client=FakeClient(error=error),
        )
    assert caught.value.status_code == 422
    assert "could not be read" in str(caught.value)


def test_is_supported_form_accepts_pdf_and_images():
    assert is_supported_form("application/pdf", "form.pdf")
    assert is_supported_form(None, "scan.PNG")
    assert not is_supported_form("text/plain", "form.txt")


def test_layout_api_success_with_mocked_client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr("egyid.api.analyze_layout", lambda *args, **kwargs: analyze_layout(*args, client=fake, **kwargs))
    client = TestClient(create_app())
    response = client.post(
        "/v1/forms/layout",
        files={"document": ("form.pdf", b"%PDF-1.4 sample", "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model_id"] == "prebuilt-layout"
    assert body["pages"][0]["lines"][0]["content"] == "Full Name"
    assert body["tables"][0]["cells"][1]["content"] == "Voice"
    assert fake.calls[0][0] == "prebuilt-layout"


def test_layout_api_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCUMENTINTELLIGENCE_ENDPOINT", raising=False)
    monkeypatch.delenv("DOCUMENTINTELLIGENCE_API_KEY", raising=False)
    monkeypatch.setattr("egyid.azure_layout.ENV_FILE", tmp_path / "missing.env")
    client = TestClient(create_app())
    response = client.post(
        "/v1/forms/layout",
        files={"document": ("form.pdf", b"%PDF-1.4 sample", "application/pdf")},
    )
    assert response.status_code == 503
    assert "DOCUMENTINTELLIGENCE" in response.json()["detail"]


def test_layout_api_invalid_and_empty_files():
    client = TestClient(create_app())
    unsupported = client.post(
        "/v1/forms/layout",
        files={"document": ("notes.txt", b"hello", "text/plain")},
    )
    assert unsupported.status_code == 415
    empty = client.post(
        "/v1/forms/layout",
        files={"document": ("form.pdf", b"", "application/pdf")},
    )
    assert empty.status_code == 422


def test_layout_api_maps_azure_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AzureLayoutError("Azure Document Intelligence request failed.", status_code=502)

    monkeypatch.setattr("egyid.api.analyze_layout", boom)
    client = TestClient(create_app())
    response = client.post(
        "/v1/forms/layout",
        files={"document": ("form.pdf", b"%PDF-1.4 sample", "application/pdf")},
    )
    assert response.status_code == 502
    assert "Azure Document Intelligence" in response.json()["detail"]
