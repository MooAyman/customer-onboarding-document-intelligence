from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class FieldStatus(str, Enum):
    present = "present"
    unreadable = "unreadable"
    absent = "absent"
    not_applicable = "not_applicable"


class FieldSource(str, Enum):
    printed = "printed"
    derived = "derived"
    visual = "visual"


class Decision(str, Enum):
    completed = "completed"
    needs_review = "needs_review"
    failed = "failed"


class Severity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"


class FieldValue(BaseModel):
    value: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: FieldStatus = FieldStatus.absent
    source: FieldSource = FieldSource.printed
    raw_ocr: str | None = None
    extraction_source: str | None = None

    @classmethod
    def present(
        cls,
        value: str,
        confidence: float | None = None,
        source: FieldSource = FieldSource.printed,
        raw_ocr: str | None = None,
        extraction_source: str | None = None,
    ) -> FieldValue:
        return cls(
            value=value,
            confidence=confidence,
            status=FieldStatus.present,
            source=source,
            raw_ocr=raw_ocr,
            extraction_source=extraction_source,
        )

    @classmethod
    def unreadable(
        cls,
        confidence: float | None = None,
        source: FieldSource = FieldSource.printed,
    ) -> FieldValue:
        return cls(status=FieldStatus.unreadable, confidence=confidence, source=source)

    @classmethod
    def absent(cls) -> FieldValue:
        return cls(status=FieldStatus.absent)

    @classmethod
    def not_applicable(cls) -> FieldValue:
        return cls(status=FieldStatus.not_applicable)


class CheckResult(BaseModel):
    name: str
    passed: bool
    severity: Severity
    detail: str


class QualityReport(BaseModel):
    width: int
    height: int
    blur_score: float
    brightness: float
    glare_ratio: float
    too_small: bool
    too_blurry: bool
    too_dark: bool
    too_bright: bool
    glare_detected: bool
    aspect_ratio: float
    issues: list[str] = Field(default_factory=list)


class ImageValidation(BaseModel):
    ok: bool
    content_type: str | None = None
    issues: list[str] = Field(default_factory=list)
    quality: QualityReport | None = None


class FrontFields(BaseModel):
    full_name_ar: FieldValue = Field(default_factory=FieldValue.absent)
    address_ar: FieldValue = Field(default_factory=FieldValue.absent)
    date_of_birth_printed: FieldValue = Field(default_factory=FieldValue.absent)
    national_id: FieldValue = Field(default_factory=FieldValue.absent)
    card_serial: FieldValue = Field(default_factory=FieldValue.absent)
    header_present: FieldValue = Field(default_factory=FieldValue.absent)
    portrait_present: FieldValue = Field(default_factory=FieldValue.absent)


class BackFields(BaseModel):
    national_id: FieldValue = Field(default_factory=FieldValue.absent)
    issue_date: FieldValue = Field(default_factory=FieldValue.absent)
    profession_ar: FieldValue = Field(default_factory=FieldValue.absent)
    gender_printed: FieldValue = Field(default_factory=FieldValue.absent)
    religion_printed: FieldValue = Field(default_factory=FieldValue.absent)
    marital_status_ar: FieldValue = Field(default_factory=FieldValue.absent)
    husband_name_ar: FieldValue = Field(default_factory=FieldValue.not_applicable)
    expiry_date: FieldValue = Field(default_factory=FieldValue.absent)
    barcode_present: FieldValue = Field(default_factory=FieldValue.absent)


class DerivedFields(BaseModel):
    date_of_birth: FieldValue = Field(default_factory=FieldValue.absent)
    gender: FieldValue = Field(default_factory=FieldValue.absent)
    birth_governorate_code: FieldValue = Field(default_factory=FieldValue.absent)
    birth_governorate: FieldValue = Field(default_factory=FieldValue.absent)
    is_expired: FieldValue = Field(default_factory=FieldValue.absent)


class IdDocumentResult(BaseModel):
    decision: Decision
    reasons: list[str]
    front_image: ImageValidation
    back_image: ImageValidation
    front: FrontFields
    back: BackFields
    derived: DerivedFields
    checks: list[CheckResult]
    ocr_ms: dict[str, int] = Field(default_factory=dict)

    def model_dump_public(self) -> dict[str, Any]:
        """Full structured result for the API consumer."""
        return self.model_dump(mode="json")
