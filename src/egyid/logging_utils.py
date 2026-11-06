from __future__ import annotations

import logging
import re

_DIGIT_RUN = re.compile(r"\d[\d\s.\-]{6,}\d")


def redact(text: str) -> str:
    """Strip likely ID numbers and long digit runs from log text."""
    return _DIGIT_RUN.sub("[REDACTED]", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact(formatted)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
