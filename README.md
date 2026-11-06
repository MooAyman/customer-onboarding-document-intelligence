# Egyptian National ID Document Intelligence

**A production-style Document Intelligence system for Egyptian National ID onboarding.**

Upload the front and back of a national ID. The pipeline reads Arabic print with on-device OCR, extracts structured fields from card geometry, decodes the 14-digit NID, classifies the document, applies onboarding policy, and routes the case — automatically or through a human reviewer. After identity is accepted, the customer downloads a blank application form, fills it by hand, and uploads the completed page. Azure Document Intelligence `prebuilt-layout` analyzes the form, a field mapper extracts structured values, and those values are cross-checked against the verified National ID.

Built for telecom-style customer onboarding: fast intake, explainable decisions, and an auditable review path for both the identity document and the application form.

FastAPI · RapidOCR · Azure Document Intelligence · OpenCV · Pydantic · SQLite · Human-in-the-loop

---

## Capabilities

- **Dual-side Arabic OCR** — RapidOCR with Arabic PP-OCRv5 mobile recognition, running locally on CPU via ONNX Runtime
- **Geometry-aware extraction** — name, address, national ID, gender, religion, expiry, and serial mapped from ID-1 layout and card geometry
- **NID intelligence** — the 14-digit national ID is parsed into date of birth, gender, and birth governorate, then cross-checked with printed fields
- **Document classification** — multi-signal labeling (`target_id` / `non_target` / `uncertain`) from OCR evidence and visual card cues
- **Deterministic policy** — age ≥ 18, expiry, NID agreement across sides, field presence, and image quality, each with a named check
- **Duplicate control** — SHA-256 image identity plus canonical NID claims, with a safe resubmit path for the same application
- **Human review** — reviewer console with live validation, original OCR preserved, corrections stored separately
- **Application form processing** — after ID approval, download a blank form, upload the completed PDF or image, and map `full_name`, `national_id`, `gender`, `mobile`, and `speed`
- **Stage 1 cross-validation** — form name, National ID, and gender compared against reviewer-approved ID values when present, otherwise against original extraction
- **Independent form review** — form `pending_review` / `approved` / `rejected` / `resubmission_required` does not change the National ID decision
- **Transactional persistence** — SQLite applications, uploaded images and forms, and backup-and-restore on file replace

## Pipeline

```
Front + back images
        │
        ▼
  Image inspect & quality
        │
        ▼
  RapidOCR  →  layout extraction  →  NID decode
        │
        ▼
  Document classification
        │
        ▼
  Onboarding validation
        │
        ├── completed  →  approved  →  application form
        ├── failed     →  rejected
        └── needs_review → reviewer console → accept / reject / resubmit
```

When the National ID is `approved`, the case moves to stage `application_form`:

```
Blank form download  →  handwritten completion  →  form upload
        │
        ▼
  Azure prebuilt-layout  →  field mapping  →  Stage 1 cross-check
        │
        ├── no review flags  →  form approved
        └── review flags     →  form pending_review → form reviewer
                                      │
                                      ├── accept
                                      ├── reject
                                      └── request form resubmission
```

| Module | Responsibility |
| --- | --- |
| `api.py` | HTTP surface for customer and reviewer |
| `workflow.py` | Application lifecycle and decision mapping |
| `pipeline.py` | End-to-end processing of an ID pair |
| `imaging.py` | Decode, quality heuristics, targeted back-side crops |
| `ocr.py` | Arabic OCR engine |
| `extract.py` | Field assignment from text, position, and keywords |
| `nid.py` | National ID structure and derived attributes |
| `classify.py` | Document-type classification |
| `validate.py` | Policy checks and machine decision |
| `review.py` | Reviewer Accept gates |
| `azure_layout.py` | Azure Document Intelligence `prebuilt-layout` client |
| `form_map.py` | Layout JSON → application fields and Stage 1 cross-check |
| `store.py` | SQLite, uploads, duplicate claims, resubmit backups |

OCR output is the system of record. Reviewer edits live in `corrections`, so extraction and human judgment stay separable. Application-form corrections are stored the same way, separately from the original mapped fields.

## Interfaces

| Surface | URL |
| --- | --- |
| Customer portal | http://127.0.0.1:8000/ |
| Reviewer dashboard | http://127.0.0.1:8000/review |
| Health | http://127.0.0.1:8000/health |
| Form layout (`prebuilt-layout`) | `POST /v1/forms/layout` |

**Customer journey.** The portal submits a front/back ID pair with a `customer_id` stored in the browser. Status is `approved`, `pending_review`, `rejected`, or `resubmission_required`. The page reloads saved applications and polls while the ID or the form is `pending_review`. After the ID is approved, the application-form panel appears: download the blank PDF, upload the completed form, and follow `form_status` (`pending_review`, `approved`, `rejected`, or `resubmission_required`). A new form upload is accepted only when no form is on file or the reviewer requested form resubmission. If the reviewer requested new ID images, the customer resubmits front and back on the same application.

**Reviewer journey.** The dashboard lists applications with ID `status` and `form_status`. Opening a case shows front and back images, extracted ID fields, automated checks, and — when a form has been submitted — the form file, mapped fields, confidence, statuses, and review flags. ID actions (accept, reject, request resubmission) apply only while the ID is `pending_review`. Form actions (accept form, reject form, request form resubmission) apply only while the form is `pending_review`. Each decision is independent.

A CLI is available for the National ID pipeline without the browser.

## Quick start

**Python 3.11–3.13**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
# set DOCUMENTINTELLIGENCE_ENDPOINT and DOCUMENTINTELLIGENCE_API_KEY in .env
.\.venv\Scripts\python.exe -m uvicorn egyid.api:app --app-dir src --host 127.0.0.1 --port 8000
```

Linux / macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# set DOCUMENTINTELLIGENCE_ENDPOINT and DOCUMENTINTELLIGENCE_API_KEY in .env
python -m uvicorn egyid.api:app --app-dir src --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ for the customer portal and http://127.0.0.1:8000/review for the reviewer dashboard.

OCR models load on first ID inference. Form upload and `POST /v1/forms/layout` read Azure credentials from the environment or local `.env`.

### Two-stage demo

1. Start the server with the commands above.
2. In the customer portal, upload National ID front and back images and submit.
3. If status is `pending_review`, open the reviewer dashboard, inspect the card, correct fields if needed, and accept (or reject / request resubmission).
4. When the ID is `approved`, download the blank application form from the customer portal, fill it by hand, and upload the completed PDF or image.
5. If the form is `pending_review`, the reviewer inspects mapped fields, applies corrections, and accepts, rejects, or requests a new form. The National ID decision stays unchanged.
6. The customer portal shows `form_status` and the form message for that application.

### CLI

```powershell
.\.venv\Scripts\python.exe -m egyid --front path\to\front.jpg --back path\to\back.jpg
```

Field values are redacted by default. Pass `--show-values` to print the extracted payload.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The suite covers NID decoding, extraction geometry, classification, validation policy, duplicate and resubmit flows, review gates, API contracts, application-form mapping, form review decisions, Stage 1 cross-check, and mocked Azure layout analysis — including optional real-OCR and Playwright paths when local ID samples are present.

## Application form processing

After identity is accepted (`status=approved`, `stage=application_form`), the customer downloads a blank application PDF and uploads the completed form (PDF or image).

`submit_application_form` sends the document to Azure AI Document Intelligence `prebuilt-layout` (the same client as `POST /v1/forms/layout`). The layout JSON — page lines, tables, selection marks, and polygons — is mapped in `form_map.py` without a second Azure call.

Mapped fields:

| Field | Source |
| --- | --- |
| `full_name` | Name line beside الإسم / الاسم |
| `national_id` | 14-digit value beside رقم إثبات الشخصية / الرقم القومي |
| `gender` | Selection marks on the النوع row (left = أنثى, right = ذكر) |
| `mobile` | 11-digit 01… value beside رقم الهاتف المحمول |
| `speed` | Handwritten 30 / 70 / 100 Mbps choice, ignoring the printed option trio |

Each field carries a status (`present`, `unreadable`, `absent`) with optional confidence. Review flags include `gender_low_confidence` and, against Stage 1, distinct `*_missing`, `*_unreadable`, `*_unverified`, and `*_mismatch` flags for name, National ID, and gender. Missing or unreadable values are never treated as a successful match.

The Stage 1 identity snapshot prefers reviewer `final_value` on `front.full_name_ar`, `front.national_id` / `back.national_id`, and `derived.gender`. It falls back to the original extracted values when no correction exists.

If any review flag is set, `form_status` becomes `pending_review`. If none are set, the form is stored as `approved`. A second upload returns HTTP 409 unless `form_status` is `resubmission_required`. Resubmitting the National ID clears previous form JSON, form status, and the stored form file.

Standalone layout analysis remains available:

```powershell
curl.exe -s -X POST "http://127.0.0.1:8000/v1/forms/layout" -F "document=@path\to\form.pdf;type=application/pdf"
```

That endpoint returns full text, page lines, tables, selection marks, and bounding polygons. It does not attach the result to an application.

## Form human review

The reviewer dashboard loads form fields from `GET /v1/review/applications/{application_id}`: extracted values, statuses, confidence, review flags, and the uploaded file at `form_url`. The reviewer can edit `full_name`, `national_id`, `gender`, `mobile`, and `speed`.

`POST /v1/review/applications/{application_id}/form/decision` accepts:

| `action` | Form status | Notes |
| --- | --- | --- |
| `accept` | `approved` | Corrections are persisted; original mapped fields are unchanged |
| `reject` | `rejected` | `reason` is required |
| `resubmit` | `resubmission_required` | `reason` is required; the customer may upload a new form |

The National ID `status` is not updated by a form decision. ID accept / reject / resubmit continues to use `POST /v1/review/applications/{application_id}/decision` and applies only while the ID is `pending_review`.

Form corrections are stored on the form record under `corrections`, separately from `fields`. The customer portal reads `form_status`, `application_form.message`, and `application_form.can_resubmit`.

## API reference

Customer application responses include `status`, `form_status`, `stage`, `customer_message`, `can_resubmit`, `next_stage` (`available`, `blank_form_url`, `submit_url`, `can_upload`), and `application_form` (`status`, `fields`, `review_flags`, `needs_review`, `corrections`, `reviewer_action`, `reviewer_reason`, `can_resubmit`, `message`).

Reviewer summaries include `status`, `form_status`, `automated_decision`, and `applicant_name`. Reviewer detail adds `extraction`, ID `corrections`, `review`, image URLs, `form`, and `form_url`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness (`{"status": "ok"}`) |
| `GET` | `/` | Customer portal |
| `GET` | `/review` | Reviewer dashboard |
| `POST` | `/v1/applications` | Create application (`front`, `back`, `customer_id`). Returns the customer view. Duplicate images or NID → 409. Unreadable/non-ID uploads → 422. |
| `GET` | `/v1/applications/{id}` | Customer view for one application |
| `GET` | `/v1/customers/{customer_id}/applications` | Applications for that customer |
| `POST` | `/v1/applications/{id}/resubmit` | Replace ID images while `status` is `resubmission_required`. Clears any previous form. |
| `GET` | `/v1/applications/{id}/front` | Stored front image |
| `GET` | `/v1/applications/{id}/back` | Stored back image |
| `GET` | `/v1/applications/{id}/form/blank` | Blank application PDF. Requires ID `approved` and stage `application_form` (otherwise 409). |
| `POST` | `/v1/applications/{id}/form` | Upload completed form (`document`). Runs Azure layout, maps fields, cross-checks Stage 1, persists form status. Already submitted → 409. Missing Azure credentials → 503. |
| `GET` | `/v1/applications/{id}/form` | Uploaded form file |
| `GET` | `/v1/review/applications` | Reviewer queue (`?status=` filters ID status) |
| `GET` | `/v1/review/applications/{id}` | Reviewer detail, including form fields when present |
| `POST` | `/v1/review/applications/{id}/decision` | ID decision JSON `{ "action", "corrections", "reason" }` with `action` = `accept` \| `reject` \| `resubmit`. Reject and resubmit require `reason`. Incomplete accept → 422. Not pending → 409. |
| `POST` | `/v1/review/applications/{id}/form/decision` | Form decision with the same JSON shape. Reject and resubmit require `reason`. Does not change ID `status`. Not pending → 409. |
| `POST` | `/v1/forms/layout` | Ungated `prebuilt-layout` analysis of a PDF or image |
| `POST` | `/v1/id-documents` | Run the ID pipeline on a front/back pair without creating an application |
| `POST` | `/v1/reviews` | Build and persist a review JSON from extraction + corrections |
| `GET` | `/v1/reviews/{review_id}` | Download a persisted review JSON |

## Configuration

Azure Document Intelligence is required for form upload and `POST /v1/forms/layout`. Copy `.env.example` to `.env` and set:

```
DOCUMENTINTELLIGENCE_ENDPOINT=https://YOUR-RESOURCE.cognitiveservices.azure.com/
DOCUMENTINTELLIGENCE_API_KEY=
```

Values are read from the process environment, or from a local `.env` if the variable is not already set (`azure_layout.py`). `.env` is gitignored. `.env.example` contains placeholders only.

National ID OCR does not use these variables; RapidOCR runs locally.

## Architecture highlights

**Staged and deterministic.** Imaging, OCR, extraction, classification, and policy are separate stages. Every decision is a named check with a severity, so a case can be explained line by line.

**Layout as signal.** Egyptian ID print sits in stable bands. Extraction uses normalized coordinates, title-relative name geometry, and Arabic keyword maps — including dedicated crops for gender, religion, and expiry on the back.

**Classification from multiple signals.** The classifier combines card title, expiry phrasing, printed gender and religion, serial pattern, portrait and barcode cues, and document-type markers.

**Onboarding as a workflow.** Applications move through inspect → process → persist → approve / reject / review / resubmit, then into application-form download, layout analysis, mapping, and an independent form decision. Image and form replace is write-then-commit with backups, so a failed update restores the previous files.

**Independent stage decisions.** National ID `status` and form `form_status` are stored and reviewed separately. A form that needs review does not reopen an accepted ID.

**Privacy by construction.** National ID photographs are processed locally. ID photographs, SQLite, and `artifacts/` are gitignored. Azure credentials stay in untracked `.env`. The CLI redacts values unless explicitly requested.

## Project layout

```
src/egyid/     Document Intelligence pipeline, Azure layout, form mapper, and API
static/        Customer and reviewer consoles
tests/         End-to-end and unit coverage
scripts/       Synthetic field helpers
```

## License

Private project. All rights reserved.
