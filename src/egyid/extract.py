from __future__ import annotations

import re
from datetime import date

from egyid.models import BackFields, FieldSource, FieldStatus, FieldValue, FrontFields
from egyid.nid import digits_only, normalize_digits, parse_national_id
from egyid.ocr import OcrLine

HEADER_TOKENS = ("جمهورية", "مصر", "العربية", "بطاقة", "تحقيق", "الشخصية")
EXPIRY_TOKENS = ("سارية", "حتى")
GENDER_MAP = {
    "ذكر": "ذكر",
    "أنثى": "أنثى",
    "انثى": "انثى",
    "Male": "Male",
    "Female": "Female",
    "male": "male",
    "female": "female",
}
RELIGION_VALUES = ("مسلم", "مسلمة", "مسيحي", "مسيحية", "يهودي", "يهودية", "-")
MARITAL_MAP = {
    "أعزب": "أعزب",
    "اعزب": "اعزب",
    "عزباء": "عزباء",
    "متزوج": "متزوج",
    "متزوجة": "متزوجة",
    "مطلق": "مطلق",
    "مطلقة": "مطلقة",
    "أرمل": "أرمل",
    "ارمل": "ارمل",
    "أرملة": "أرملة",
    "ارملة": "ارملة",
    "single": "single",
    "married": "married",
    "divorced": "divorced",
    "widowed": "widowed",
    "Single": "Single",
    "Married": "Married",
    "Divorced": "Divorced",
    "Widowed": "Widowed",
}
FEMALE_TOKENS = frozenset({"أنثى", "انثى", "Female", "female"})
MARRIED_TOKENS = frozenset({"متزوج", "متزوجة", "married", "Married"})

NID_CANDIDATE = re.compile(r"\d(?:[\s.\-]*\d){11,15}")
SERIAL_RE = re.compile(r"\b([A-Z]{2}\s?\d{6,10})\b", re.I)
FULL_DATE_RE = re.compile(r"(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})")
MONTH_DATE_RE = re.compile(r"(\d{4})\s*[/\-.]\s*(\d{1,2})(?!\s*[/\-.]\s*\d)")
LOW_SCORE = 0.35
DOB_YEARS = range(1920, 2016)
ISSUE_YEARS = range(1990, 2036)
EXPIRY_YEARS = range(2015, 2046)


def _norm(text: str) -> str:
    return normalize_digits(text).replace("ـ", "").strip()


def _is_header(text: str) -> bool:
    return any(token in text for token in HEADER_TOKENS)


def _has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def _arabic_letters(text: str) -> str:
    chars = [
        ch
        for ch in text
        if ("\u0600" <= ch <= "\u06FF")
        or ch.isdigit()
        or ch.isspace()
        or ch in "/-.,،()|"
    ]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def _has_latin(text: str) -> bool:
    return any("A" <= ch <= "Z" or "a" <= ch <= "z" for ch in text)


def preserve_extracted_text(text: str) -> str:
    """Whitespace collapse and English digit normalization only. Never translate."""
    return re.sub(r"\s+", " ", normalize_digits(text.replace("ـ", ""))).strip()


def _field(
    value: str | None,
    score: float | None,
    min_ok: float = LOW_SCORE,
    *,
    raw_ocr: str | None = None,
) -> FieldValue:
    if not value:
        return FieldValue.absent()
    if score is not None and score < min_ok:
        return FieldValue.unreadable()
    return FieldValue.present(value, score, raw_ocr=raw_ocr)


def find_national_ids(lines: list[OcrLine]) -> list[tuple[OcrLine, str]]:
    found: list[tuple[OcrLine, str]] = []
    for line in lines:
        for match in NID_CANDIDATE.finditer(_norm(line.text)):
            digits = digits_only(match.group())
            if len(digits) == 14:
                found.append((line, digits))
    return found


def _best_nid(lines: list[OcrLine], prefer_cy: tuple[float, float] | None = None) -> FieldValue:
    matches = find_national_ids(lines)
    if not matches:
        loose = [line for line in lines if 10 <= len(digits_only(line.text)) <= 16]
        if loose:
            best = max(loose, key=lambda item: item.score)
            return FieldValue.unreadable()
        return FieldValue.absent()

    def rank(item: tuple[OcrLine, str]) -> float:
        line, _ = item
        parsed = parse_national_id(item[1])
        bonus = 0.3 if parsed and parsed.ok else 0.0
        if prefer_cy:
            lo, hi = prefer_cy
            if lo <= line.cy <= hi:
                bonus += 0.2
        return line.score + bonus

    line, digits = max(matches, key=rank)
    parsed = parse_national_id(digits)
    if parsed is None or not parsed.ok:
        return FieldValue.unreadable()
    return FieldValue.present(parsed.raw, line.score, raw_ocr=line.text)


def _parse_iso_date(year: str, month: str, day: str | None = None) -> str | None:
    try:
        y, m = int(year), int(month)
        if day is None:
            if 1 <= m <= 12:
                return f"{y:04d}-{m:02d}"
            return None
        d = int(day)
        date(y, m, d)
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None


def compact_date_from_digits(digit_str: str) -> tuple[str | None, str | None]:
    """Parse YYYYMMDD or YYYYMM only. Never infer missing digits."""
    if len(digit_str) == 8:
        return _parse_iso_date(digit_str[:4], digit_str[4:6], digit_str[6:8]), None
    if len(digit_str) == 6:
        return None, _parse_iso_date(digit_str[:4], digit_str[4:6])
    return None, None


def slash_as_one_month(digit_str: str) -> str | None:
    """Map OCR YYYY1MM to YYYY-MM when the extra 1 is a '/' substitute."""
    if len(digit_str) != 7 or digit_str[4] != "1":
        return None
    iso = _parse_iso_date(digit_str[:4], digit_str[5:7])
    if iso and int(iso[:4]) in ISSUE_YEARS:
        return iso
    return None


def slash_as_one_full(digit_str: str) -> str | None:
    """Map OCR YYYY1MM1DD to YYYY-MM-DD when the extra 1s are '/' substitutes."""
    if len(digit_str) != 10 or digit_str[4] != "1" or digit_str[7] != "1":
        return None
    iso = _parse_iso_date(digit_str[:4], digit_str[5:7], digit_str[8:10])
    if iso and int(iso[:4]) in EXPIRY_YEARS:
        return iso
    return None


def _full_dates(text: str) -> list[str]:
    found: list[str] = []
    for match in FULL_DATE_RE.finditer(_norm(text)):
        iso = _parse_iso_date(*match.groups())
        if iso:
            found.append(iso)
    if found:
        return found
    digits = digits_only(text)
    if len(digits) == 14:
        return []
    full, _ = compact_date_from_digits(digits)
    return [full] if full else []


def _month_dates(text: str) -> list[str]:
    if FULL_DATE_RE.search(_norm(text)) or len(digits_only(text)) == 8:
        return []
    found: list[str] = []
    for match in MONTH_DATE_RE.finditer(_norm(text)):
        iso = _parse_iso_date(match.group(1), match.group(2))
        if iso:
            found.append(iso)
    if found:
        return found
    digits = digits_only(text)
    if len(digits) == 14:
        return []
    _, month = compact_date_from_digits(digits)
    return [month] if month else []


def extract_front(lines: list[OcrLine]) -> FrontFields:
    fields = FrontFields()
    fields.national_id = _best_nid(lines, prefer_cy=(0.52, 0.85))

    serials: list[tuple[OcrLine, str]] = []
    for line in lines:
        match = SERIAL_RE.search(_norm(line.text).upper())
        if match:
            serials.append((line, match.group(1).replace(" ", "").upper()))
    if serials:
        line, serial = min(serials, key=lambda item: (item[0].cy < 0.7, -item[0].score))
        fields.card_serial = _field(serial, line.score, raw_ocr=line.text)
    else:
        bottom_left = [ln for ln in lines if ln.cy > 0.75 and ln.cx < 0.45]
        fields.card_serial = (
            FieldValue.unreadable()
            if bottom_left
            else FieldValue.absent()
        )

    dob_hits: list[tuple[OcrLine, str]] = []
    dob_region: list[OcrLine] = []
    for line in lines:
        if line.cy <= 0.55 or line.cx >= 0.45:
            continue
        if SERIAL_RE.search(_norm(line.text).upper()):
            continue
        for iso in _full_dates(line.text):
            if int(iso[:4]) in DOB_YEARS:
                dob_hits.append((line, iso))
        if digits_only(line.text):
            dob_region.append(line)
    if dob_hits:
        line, iso = min(dob_hits, key=lambda item: (item[0].cx, -item[0].score))
        fields.date_of_birth_printed = _field(iso, line.score, raw_ocr=line.text)
    else:
        fields.date_of_birth_printed = (
            FieldValue.unreadable()
            if dob_region
            else FieldValue.absent()
        )

    header_lines = [ln for ln in lines if _is_header(ln.text)]
    fields.header_present = (
        FieldValue.present("true", max(ln.score for ln in header_lines))
        if header_lines
        else FieldValue.unreadable()
    )

    skip_ids = {id(ln) for ln, _ in find_national_ids(lines)}
    arabic_right = [
        ln
        for ln in lines
        if _has_arabic(ln.text)
        and not _is_header(ln.text)
        and id(ln) not in skip_ids
        and ln.cx >= 0.38
        and ln.cy < 0.62
        and _arabic_letters(ln.text)
    ]
    arabic_right.sort(key=lambda ln: ln.cy)
    if len(arabic_right) >= 2:
        split = 1
        if len(arabic_right) >= 3 and arabic_right[1].cy - arabic_right[0].cy < 0.1:
            split = 2
        name_lines = arabic_right[:split]
        addr_lines = arabic_right[split:]
        fields.full_name_ar = _field(
            " ".join(preserve_extracted_text(ln.text) for ln in name_lines),
            min(ln.score for ln in name_lines),
            raw_ocr=" ".join(ln.text for ln in name_lines),
        )
        if addr_lines:
            fields.address_ar = _field(
                " ".join(preserve_extracted_text(ln.text) for ln in addr_lines),
                min(ln.score for ln in addr_lines),
                raw_ocr=" ".join(ln.text for ln in addr_lines),
            )
        else:
            fields.address_ar = FieldValue.unreadable()
    elif len(arabic_right) == 1:
        fields.full_name_ar = _field(
            preserve_extracted_text(arabic_right[0].text),
            arabic_right[0].score,
            raw_ocr=arabic_right[0].text,
        )
        fields.address_ar = FieldValue.unreadable()
    else:
        fields.full_name_ar = FieldValue.absent()
        fields.address_ar = FieldValue.absent()

    return fields


def extract_issue_date(lines: list[OcrLine]) -> FieldValue:
    month_hits: list[tuple[OcrLine, str]] = []
    blobs: list[OcrLine] = []
    for line in lines:
        for iso in _month_dates(line.text):
            if int(iso[:4]) in ISSUE_YEARS:
                month_hits.append((line, iso))
        digit_len = len(digits_only(line.text))
        if 6 <= digit_len <= 8:
            blobs.append(line)
    if month_hits:
        line, iso = min(month_hits, key=lambda item: (item[0].cy, item[0].cx))
        return _field(iso, line.score, raw_ocr=line.text)
    slash_hits: list[tuple[OcrLine, str]] = []
    for line in lines:
        iso = slash_as_one_month(digits_only(line.text))
        if iso:
            slash_hits.append((line, iso))
    if slash_hits:
        line, iso = min(slash_hits, key=lambda item: (item[0].cy, item[0].cx))
        return _field(iso, line.score, raw_ocr=line.text)
    if blobs:
        return FieldValue.unreadable()
    return FieldValue.absent()


def extract_expiry_date(lines: list[OcrLine]) -> FieldValue:
    full_hits: list[tuple[OcrLine, str]] = []
    for line in lines:
        for iso in _full_dates(line.text):
            if int(iso[:4]) in EXPIRY_YEARS:
                full_hits.append((line, iso))
    expiry_line = next(
        (ln for ln in lines if any(tok in ln.text for tok in EXPIRY_TOKENS) and full_hits),
        None,
    )
    if full_hits:
        if expiry_line:
            token_dates = [
                iso for iso in _full_dates(expiry_line.text) if int(iso[:4]) in EXPIRY_YEARS
            ]
            iso = token_dates[-1] if token_dates else full_hits[-1][1]
            return _field(iso, expiry_line.score, raw_ocr=expiry_line.text)
        line, iso = max(full_hits, key=lambda item: item[0].cy)
        return _field(iso, line.score, raw_ocr=line.text)
    slash_hits: list[tuple[OcrLine, str]] = []
    for line in lines:
        iso = slash_as_one_full(digits_only(line.text))
        if iso:
            slash_hits.append((line, iso))
    if slash_hits:
        token_hits = [item for item in slash_hits if any(tok in item[0].text for tok in EXPIRY_TOKENS)]
        line, iso = (token_hits[-1] if token_hits else max(slash_hits, key=lambda item: item[0].cy))
        return _field(iso, line.score, raw_ocr=line.text)
    blobs = [
        ln
        for ln in lines
        if 6 <= len(digits_only(ln.text)) <= 10 and len(digits_only(ln.text)) != 14
    ]
    if blobs:
        return FieldValue.unreadable()
    if any(tok in ln.text for ln in lines for tok in EXPIRY_TOKENS):
        return FieldValue.unreadable()
    return FieldValue.absent()


def merge_present(preferred: FieldValue, fallback: FieldValue) -> FieldValue:
    """Use a crop result when it produced a value; otherwise keep full-image OCR."""
    if preferred.status == FieldStatus.present:
        return preferred
    if fallback.status == FieldStatus.present:
        return fallback
    if preferred.status == FieldStatus.unreadable:
        return preferred
    return fallback


def with_extraction_trace(
    field: FieldValue, extraction_source: str, raw_ocr: str | None = None
) -> FieldValue:
    """Attach OCR origin without changing the parsed value."""
    if field.status != FieldStatus.present:
        return field
    return field.model_copy(
        update={
            "extraction_source": extraction_source,
            "raw_ocr": field.raw_ocr or raw_ocr,
        }
    )


def extract_mapped_keyword(
    lines: list[OcrLine],
    mapping: dict[str, str] | tuple[str, ...],
    *,
    unread_if_text: bool = False,
) -> FieldValue:
    hits: list[tuple[int, float, str, str]] = []
    for line in lines:
        for token in mapping:
            if token in line.text:
                value = mapping[token] if isinstance(mapping, dict) else token
                hits.append((len(token), line.score, value, line.text))
    if hits:
        _, score, value, raw = max(hits, key=lambda item: (item[0], item[1]))
        return _field(value, score, raw_ocr=raw)
    if unread_if_text and any(_has_arabic(ln.text) or digits_only(ln.text) for ln in lines):
        return FieldValue.unreadable()
    return FieldValue.absent()


def extract_back(lines: list[OcrLine], barcode_found: bool, barcode_score: float) -> BackFields:
    fields = BackFields()
    printed = [ln for ln in lines if ln.cy < 0.68]
    fields.national_id = _best_nid(printed or lines, prefer_cy=(0.0, 0.28))
    fields.barcode_present = (
        FieldValue.present("true", min(1.0, barcode_score), FieldSource.visual)
        if barcode_found
        else FieldValue.unreadable(
            min(1.0, barcode_score) if barcode_score else None,
            FieldSource.visual,
        )
    )

    issue_lines = [ln for ln in printed if ln.cy < 0.28 and ln.cx < 0.50]
    expiry_lines = [
        ln for ln in printed if any(tok in ln.text for tok in EXPIRY_TOKENS) or ln.cy > 0.40
    ]
    fields.issue_date = extract_issue_date(issue_lines)
    fields.expiry_date = extract_expiry_date(expiry_lines)
    fields.gender_printed = extract_mapped_keyword(printed, GENDER_MAP)
    fields.religion_printed = extract_mapped_keyword(printed, RELIGION_VALUES)
    fields.marital_status_ar = extract_mapped_keyword(printed, MARITAL_MAP)

    used = set()
    for line in printed:
        blob = line.text
        if _is_header(blob) or any(tok in blob for tok in EXPIRY_TOKENS):
            used.add(id(line))
        if any(tok in blob for tok in GENDER_MAP) or any(tok in blob for tok in MARITAL_MAP):
            used.add(id(line))
        if any(tok in blob for tok in RELIGION_VALUES):
            used.add(id(line))
        if find_national_ids([line]) or _month_dates(blob) or _full_dates(blob):
            used.add(id(line))

    profession_lines = [
        ln
        for ln in printed
        if id(ln) not in used
        and (_has_arabic(ln.text) or _has_latin(ln.text))
        and 0.10 <= ln.cy <= 0.48
    ]
    profession_lines.sort(key=lambda ln: ln.cy)
    if profession_lines:
        fields.profession_ar = _field(
            " ".join(preserve_extracted_text(ln.text) for ln in profession_lines),
            min(ln.score for ln in profession_lines),
            raw_ocr=" ".join(ln.text for ln in profession_lines),
        )
    else:
        fields.profession_ar = FieldValue.absent()

    female = fields.gender_printed.value in FEMALE_TOKENS
    married = fields.marital_status_ar.value in MARRIED_TOKENS
    if female and married:
        fields.husband_name_ar = FieldValue.unreadable()
    else:
        fields.husband_name_ar = FieldValue.not_applicable()

    return fields
