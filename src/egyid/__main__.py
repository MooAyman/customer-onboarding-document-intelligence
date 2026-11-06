from __future__ import annotations

import argparse
import json
from pathlib import Path

from egyid.models import FieldStatus
from egyid.pipeline import process_id_pair


def _redacted_view(result) -> dict:
    """CLI default: statuses and checks only, no field values."""

    def statuses(model) -> dict[str, str]:
        out = {}
        for name in type(model).model_fields:
            field = getattr(model, name)
            if hasattr(field, "status"):
                out[name] = field.status.value
        return out

    return {
        "decision": result.decision.value,
        "reasons": result.reasons,
        "front_image_issues": result.front_image.issues,
        "back_image_issues": result.back_image.issues,
        "front": statuses(result.front),
        "back": statuses(result.back),
        "derived": statuses(result.derived),
        "failed_checks": [
            {"name": c.name, "severity": c.severity.value, "detail": c.detail}
            for c in result.checks
            if not c.passed
        ],
        "ocr_ms": result.ocr_ms,
        "classification": {
            "label": result.classification.label.value,
            "evidence": result.classification.evidence,
            "detail": result.classification.detail,
        },
        "present_fields": [
            name
            for section in (result.front, result.back, result.derived)
            for name in type(section).model_fields
            if getattr(getattr(section, name), "status", None) == FieldStatus.present
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Egyptian National ID front and back images.")
    parser.add_argument("--front", required=True, type=Path)
    parser.add_argument("--back", required=True, type=Path)
    parser.add_argument(
        "--show-values",
        action="store_true",
        help="Include extracted values. Default redacts values for local PII safety.",
    )
    args = parser.parse_args()
    result = process_id_pair(args.front.read_bytes(), args.back.read_bytes())
    payload = result.model_dump_public() if args.show_values else _redacted_view(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
