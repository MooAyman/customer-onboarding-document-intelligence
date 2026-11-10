# Security and privacy

This project processes identity documents. Treat every image, PDF, database, and Azure response as sensitive.

## Do not publish

- Real National ID photographs or scans
- Filled application forms
- `.env` files, Azure keys, or other credentials
- `artifacts/`, SQLite databases, or `uploads/`
- Azure Document Intelligence JSON that may contain handwritten PII

## Local data

ID OCR runs on-device. Application-form layout analysis is sent to Azure when you call form upload or `POST /v1/forms/layout`. Use sample or synthetic documents you are allowed to process. Do not attach real IDs to GitHub issues or pull requests.

Azure secrets are read on the backend from the process environment or a gitignored `.env` file. They are never sent to the Customer Portal or Reviewer Dashboard.

## Reporting

If you find a security issue in this repository, open a private report with the repository owner rather than filing a public issue with sample documents.
