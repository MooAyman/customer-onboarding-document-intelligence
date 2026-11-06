from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from egyid.models import ImageValidation, QualityReport

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}
MAX_BYTES = 15 * 1024 * 1024
MIN_SIDE = 400
BLUR_THRESHOLD = 45.0
DARK_THRESHOLD = 38.0
BRIGHT_THRESHOLD = 235.0
GLARE_VALUE = 250
GLARE_DELTA = 20
GLARE_RATIO_THRESHOLD = 0.008
HIGHLIGHT_VALUE = 245
GLARE_MAX_COMPONENT = 0.15
GLARE_MIN_COMPONENT = 0.001


@dataclass(frozen=True)
class LoadedImage:
    array: np.ndarray
    content_type: str
    issues: list[str]

    @property
    def ok(self) -> bool:
        return not self.issues


def load_image(data: bytes, declared_type: str | None = None) -> LoadedImage:
    issues: list[str] = []
    if not data:
        return LoadedImage(np.zeros((1, 1, 3), np.uint8), "application/octet-stream", ["empty_file"])
    if len(data) > MAX_BYTES:
        issues.append("file_too_large")

    try:
        import io

        with Image.open(io.BytesIO(data)) as pil:
            fmt = (pil.format or "").upper()
            if fmt not in ALLOWED_FORMATS:
                issues.append("unsupported_format")
            rgb = pil.convert("RGB")
            array = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
    except (UnidentifiedImageError, OSError, ValueError):
        return LoadedImage(
            np.zeros((1, 1, 3), np.uint8),
            declared_type or "application/octet-stream",
            issues + ["unreadable_image"],
        )

    content_type = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
        "TIFF": "image/tiff",
    }.get(fmt, declared_type or "application/octet-stream")

    if array.size == 0:
        issues.append("empty_pixels")
    return LoadedImage(array, content_type, issues)


def highlight_ratio(gray: np.ndarray, threshold: int = HIGHLIGHT_VALUE) -> float:
    """Fraction of near-white pixels. High on white plastic, not specific to glare."""
    return float(np.mean(gray >= threshold))


def specular_glare_ratio(gray: np.ndarray) -> float:
    """Localized clipped highlights whose surround is darker.

    Uniform white plastic has near-white neighbors, so it does not count.
    High-edge patches (text, barcode) are ignored.
    """
    if gray.size == 0:
        return 0.0
    bright = (gray >= GLARE_VALUE).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    if num <= 1:
        return 0.0
    area = gray.shape[0] * gray.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
    edges = cv2.Canny(gray, 60, 160)
    glare_pixels = 0
    for i in range(1, num):
        comp_area = int(stats[i, cv2.CC_STAT_AREA])
        frac = comp_area / area
        if frac < GLARE_MIN_COMPONENT or frac > GLARE_MAX_COMPONENT:
            continue
        mask = (labels == i).astype(np.uint8)
        if float(np.mean(edges[mask > 0] > 0)) > 0.18:
            continue
        ring = (cv2.dilate(mask, kernel) > 0) & (mask == 0)
        if int(np.count_nonzero(ring)) < 20:
            continue
        core_mean = float(np.mean(gray[mask > 0]))
        ring_mean = float(np.mean(gray[ring]))
        if core_mean - ring_mean >= GLARE_DELTA:
            glare_pixels += comp_area
    return glare_pixels / area


def assess_quality(image: np.ndarray) -> QualityReport:
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    glare_ratio = specular_glare_ratio(gray)
    aspect = w / max(h, 1)
    too_small = min(w, h) < MIN_SIDE
    too_blurry = blur < BLUR_THRESHOLD
    too_dark = brightness < DARK_THRESHOLD
    too_bright = brightness > BRIGHT_THRESHOLD
    glare = glare_ratio >= GLARE_RATIO_THRESHOLD

    issues: list[str] = []
    if too_small:
        issues.append("resolution_too_low")
    if too_blurry:
        issues.append("too_blurry")
    if too_dark:
        issues.append("too_dark")
    if too_bright:
        issues.append("too_bright")
    if glare:
        issues.append("glare_detected")

    return QualityReport(
        width=w,
        height=h,
        blur_score=round(blur, 2),
        brightness=round(brightness, 2),
        glare_ratio=round(glare_ratio, 4),
        too_small=too_small,
        too_blurry=too_blurry,
        too_dark=too_dark,
        too_bright=too_bright,
        glare_detected=glare,
        aspect_ratio=round(aspect, 3),
        issues=issues,
    )


# Shared ID-1 back crops. Normalized to the image, not tuned per sample.
GENDER_CROP = (0.68, 0.26, 0.92, 0.44)
RELIGION_CROP = (0.48, 0.26, 0.72, 0.44)
EXPIRY_CROP = (0.12, 0.40, 0.90, 0.58)


def crop_normalized(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    """Crop a normalized (x1, y1, x2, y2) box. Does not modify the source array."""
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    xa, ya = int(w * x1), int(h * y1)
    xb, yb = int(w * x2), int(h * y2)
    xa, ya = max(0, xa), max(0, ya)
    xb, yb = min(w, max(xa + 1, xb)), min(h, max(ya + 1, yb))
    return image[ya:yb, xa:xb]


def barcode_present(image: np.ndarray) -> tuple[bool, float]:
    """Heuristic PDF417-like density in the lower third. Does not decode payload."""
    h, w = image.shape[:2]
    band = image[int(h * 0.62) :, :]
    if band.size == 0:
        return False, 0.0
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    edges = cv2.Canny(gray, 60, 160)
    density = float(np.mean(edges > 0))
    col_var = float(np.var(np.mean(gray, axis=0)))
    score = min(1.0, density * 4.0 + min(col_var / 800.0, 0.4))
    return score >= 0.18, round(score, 3)


@dataclass(frozen=True)
class CardDetection:
    ok: bool
    score: float
    detail: str


def _edge_density(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(np.mean(cv2.Canny(gray, 60, 160) > 0))


def _mean_saturation(image: np.ndarray) -> float:
    if image.ndim != 3:
        return 0.0
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 1]))


def lower_column_variance(image: np.ndarray) -> float:
    """Column variance in the lower band. High on ID bottoms and PDF417 barcodes."""
    h, w = image.shape[:2]
    band = image[int(h * 0.58) :, :]
    if band.size == 0:
        return 0.0
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    return float(np.var(np.mean(gray, axis=0)))


def looks_like_id_card(image: np.ndarray, side: str = "front") -> CardDetection:
    """Reject scenes and unrelated pages. Do not require readable OCR or tight crops.

    Tuned for Egyptian National ID photos: ID-1 cards, phone 4:3 shots, and
    stacked front+back scans. Quality issues are not a reason to fail.
    """
    h, w = image.shape[:2]
    if min(h, w) < 2:
        return CardDetection(False, 0.0, "empty")
    long_asp = max(w, h) / max(min(w, h), 1)
    edges = _edge_density(image)
    sat = _mean_saturation(image)
    col_var = lower_column_variance(image)
    portrait_ok, portrait_score = portrait_present(image)
    barcode_ok, barcode_score = barcode_present(image)
    score = min(
        1.0,
        (0.35 if 1.12 <= long_asp <= 1.95 else 0.0)
        + min(col_var / 400.0, 0.35)
        + (0.15 if portrait_ok else 0.0)
        + (0.15 if barcode_ok else 0.0),
    )

    if edges < 0.004:
        return CardDetection(False, score, "no_card_texture")
    if edges > 0.25:
        return CardDetection(False, score, "not_a_document")
    if sat > 130:
        return CardDetection(False, score, "not_a_document")
    if long_asp > 2.15:
        return CardDetection(False, score, "not_id_shape")

    structure = col_var >= 70
    id1_shape = 1.45 <= long_asp <= 1.85
    if side == "back":
        side_cue = barcode_score >= 0.18 or portrait_score >= 0.16
    else:
        side_cue = portrait_score >= 0.16 or barcode_score >= 0.18
    if structure or (id1_shape and side_cue):
        return CardDetection(True, score, "id_card")
    return CardDetection(False, score, "not_national_id")


def portrait_present(image: np.ndarray) -> tuple[bool, float]:
    """Heuristic: a portrait-sized high-contrast patch in the upper-left.

    This is a completeness check, not face matching or liveness.
    """
    h, w = image.shape[:2]
    roi = image[int(h * 0.05) : int(h * 0.62), 0 : int(w * 0.36)]
    if roi.size == 0:
        return False, 0.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    contrast = float(np.std(gray) / 128.0)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float(np.mean(edges > 0))
    score = min(1.0, contrast * 0.6 + edge_density * 2.0)
    return score >= 0.16, round(score, 3)


def to_image_validation(loaded: LoadedImage, quality: QualityReport | None = None) -> ImageValidation:
    return ImageValidation(
        ok=loaded.ok,
        content_type=loaded.content_type,
        issues=list(loaded.issues) + (quality.issues if quality else []),
        quality=quality,
    )
