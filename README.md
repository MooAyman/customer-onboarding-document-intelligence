# Egyptian National ID Document Intelligence

**A production-style Document Intelligence system for Egyptian National ID onboarding.**

Upload the front and back of a national ID. The pipeline reads Arabic print with on-device OCR, extracts structured fields from card geometry, decodes the 14-digit NID, classifies the document, applies onboarding policy, and routes the case — automatically or through a human reviewer.

Built for telecom-style customer onboarding: fast intake, explainable decisions, and an auditable review path.

FastAPI · RapidOCR · OpenCV · Pydantic · SQLite · Human-in-the-loop

---

## Capabilities

- **Dual-side Arabic OCR** — RapidOCR with Arabic PP-OCRv5 mobile recognition, running locally on CPU via ONNX Runtime
- **Geometry-aware extraction** — name, address, national ID, gender, religion, expiry, and serial mapped from ID-1 layout and card geometry
- **NID intelligence** — the 14-digit national ID is parsed into date of birth, gender, and birth governorate, then cross-checked with printed fields
- **Document classification** — multi-signal labeling (`target_id` / `non_target` / `uncertain`) from OCR evidence and visual card cues
- **Deterministic policy** — age ≥ 18, expiry, NID agreement across sides, field presence, and image quality, each with a named check
- **Duplicate control** — SHA-256 image identity plus canonical NID claims, with a safe resubmit path for the same application
- **Human review** — reviewer console with live validation, original OCR preserved, corrections stored separately
- **Transactional persistence** — SQLite applications, uploaded images, and backup-and-restore on image replace

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
        ├── completed  →  approved
        ├── failed     →  rejected
        └── needs_review → reviewer console
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
| `store.py` | SQLite, uploads, duplicate claims, resubmit backups |

OCR output is the system of record. Reviewer edits live in `corrections`, so extraction and human judgment stay separable.

## Interfaces

| Surface | URL |
| --- | --- |
| Customer intake | http://127.0.0.1:8000/ |
| Reviewer console | http://127.0.0.1:8000/review |
| Health | http://127.0.0.1:8000/health |

The customer UI submits a front/back pair and tracks application status. The reviewer UI opens the case, inspects both images, applies corrections, and records accept, reject, or resubmission.

A CLI is available for the same pipeline without the browser.

## Quick start

**Python 3.11–3.13**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn egyid.api:app --app-dir src --host 127.0.0.1 --port 8000
```

Linux / macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m uvicorn egyid.api:app --app-dir src --host 127.0.0.1 --port 8000
```

OCR models load on first inference.

### CLI

```powershell
.\.venv\Scripts\python.exe -m egyid --front path\to\front.jpg --back path\to\back.jpg
```

Field values are redacted by default. Pass `--show-values` to print the extracted payload.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The suite covers NID decoding, extraction geometry, classification, validation policy, duplicate and resubmit flows, review gates, and API contracts — including optional real-OCR and Playwright paths when local ID samples are present.

## Architecture highlights

**Staged and deterministic.** Imaging, OCR, extraction, classification, and policy are separate stages. Every decision is a named check with a severity, so a case can be explained line by line.

**Layout as signal.** Egyptian ID print sits in stable bands. Extraction uses normalized coordinates, title-relative name geometry, and Arabic keyword maps — including dedicated crops for gender, religion, and expiry on the back.

**Classification from multiple signals.** The classifier combines card title, expiry phrasing, printed gender and religion, serial pattern, portrait and barcode cues, and document-type markers.

**Onboarding as a workflow.** Applications move through inspect → process → persist → approve / reject / review / resubmit. Image replace is write-then-commit with backups, so a failed resubmit restores the previous files.

**Privacy by construction.** Processing is local. ID photographs, SQLite, and `artifacts/` are gitignored. The CLI redacts values unless explicitly requested.

## Project layout

```
src/egyid/     Document Intelligence pipeline and API
static/        Customer and reviewer consoles
tests/         End-to-end and unit coverage
scripts/       Synthetic field helpers
```

## License

Private project. All rights reserved.
