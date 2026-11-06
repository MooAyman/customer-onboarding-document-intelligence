from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from egyid.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OcrLine:
    text: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def width(self) -> float:
        return max(self.x2 - self.x1, 1e-6)


class OcrEngine(Protocol):
    def run(self, image: np.ndarray) -> list[OcrLine]: ...


class RapidArabicOcr:
    """CPU RapidOCR with Arabic recognition. Models load on first call."""

    def __init__(self) -> None:
        self._engine = None

    def _load(self):
        if self._engine is not None:
            return self._engine
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        # PP-OCRv6 bundled small rec has no Arabic route; v5 mobile does.
        self._engine = RapidOCR(
            params={
                "Rec.lang_type": LangRec.ARABIC,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            }
        )
        return self._engine

    def run(self, image: np.ndarray) -> list[OcrLine]:
        engine = self._load()
        result = engine(image)
        if result is None:
            return []
        boxes = getattr(result, "boxes", None)
        txts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if boxes is None or txts is None:
            return []
        h, w = image.shape[:2]
        lines: list[OcrLine] = []
        for box, text, score in zip(boxes, txts, scores or [1.0] * len(txts)):
            pts = np.asarray(box, dtype=float).reshape(-1, 2)
            xs, ys = pts[:, 0], pts[:, 1]
            lines.append(
                OcrLine(
                    text=str(text).strip(),
                    score=float(score),
                    x1=float(xs.min() / w),
                    y1=float(ys.min() / h),
                    x2=float(xs.max() / w),
                    y2=float(ys.max() / h),
                )
            )
        return [line for line in lines if line.text]


_ENGINE: RapidArabicOcr | None = None


def default_engine() -> RapidArabicOcr:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidArabicOcr()
    return _ENGINE
