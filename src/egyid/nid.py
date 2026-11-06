from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass

GOVERNORATE_CODES: dict[str, str] = {
    "01": "القاهرة",
    "02": "الإسكندرية",
    "03": "بورسعيد",
    "04": "السويس",
    "11": "دمياط",
    "12": "الدقهلية",
    "13": "الشرقية",
    "14": "القليوبية",
    "15": "كفر الشيخ",
    "16": "الغربية",
    "17": "المنوفية",
    "18": "البحيرة",
    "19": "الإسماعيلية",
    "21": "الجيزة",
    "22": "بني سويف",
    "23": "الفيوم",
    "24": "المنيا",
    "25": "أسيوط",
    "26": "سوهاج",
    "27": "قنا",
    "28": "أسوان",
    "29": "الأقصر",
    "31": "البحر الأحمر",
    "32": "الوادي الجديد",
    "33": "مطروح",
    "34": "شمال سيناء",
    "35": "جنوب سيناء",
    "88": "خارج الجمهورية",
}
GOVERNORATE_NAMES_EN: dict[str, str] = {
    "01": "Cairo",
    "02": "Alexandria",
    "03": "Port Said",
    "04": "Suez",
    "11": "Damietta",
    "12": "Dakahlia",
    "13": "Sharqia",
    "14": "Qalyubia",
    "15": "Kafr El Sheikh",
    "16": "Gharbia",
    "17": "Monufia",
    "18": "Beheira",
    "19": "Ismailia",
    "21": "Giza",
    "22": "Beni Suef",
    "23": "Faiyum",
    "24": "Minya",
    "25": "Asyut",
    "26": "Sohag",
    "27": "Qena",
    "28": "Aswan",
    "29": "Luxor",
    "31": "Red Sea",
    "32": "New Valley",
    "33": "Matruh",
    "34": "North Sinai",
    "35": "South Sinai",
    "88": "Outside the republic",
}


def code_for_governorate_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    for code, arabic in GOVERNORATE_CODES.items():
        if arabic == text:
            return code
    for code, english in GOVERNORATE_NAMES_EN.items():
        if english == text:
            return code
    return ""

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def digits_only(text: str) -> str:
    return "".join(ch for ch in normalize_digits(text) if ch.isdigit())


def canonical_nid(value: str | None) -> str:
    digits = digits_only(value or "")
    return digits if len(digits) == 14 else ""


def nids_from_extraction(extraction: dict | None) -> list[str]:
    found: list[str] = []
    data = extraction or {}
    for side in ("front", "back"):
        value = ((data.get(side) or {}).get("national_id") or {}).get("value")
        nid = canonical_nid(value)
        if nid and nid not in found:
            found.append(nid)
    return found


@dataclass(frozen=True)
class NationalIdParts:
    raw: str
    century_digit: str
    date_of_birth: str
    gender: str
    governorate_code: str
    governorate: str
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def _valid_date(year: int, month: int, day: int) -> bool:
    if month < 1 or month > 12:
        return False
    return 1 <= day <= monthrange(year, month)[1]


def parse_national_id(value: str | None) -> NationalIdParts | None:
    """Validate structure only. Do not apply unofficial check-digit algorithms."""
    if not value:
        return None
    raw = digits_only(value)
    issues: list[str] = []
    if len(raw) != 14:
        return NationalIdParts(
            raw=raw,
            century_digit="",
            date_of_birth="",
            gender="",
            governorate_code="",
            governorate="",
            issues=("length_not_14",),
        )

    century_digit = raw[0]
    if century_digit == "2":
        year = 1900 + int(raw[1:3])
    elif century_digit == "3":
        year = 2000 + int(raw[1:3])
    else:
        year = 0
        issues.append("invalid_century")

    month = int(raw[3:5])
    day = int(raw[5:7])
    dob = ""
    if year and _valid_date(year, month, day):
        dob = f"{year:04d}-{month:02d}-{day:02d}"
    else:
        issues.append("invalid_birth_date")

    gov_code = raw[7:9]
    gov_name = GOVERNORATE_CODES.get(gov_code, "")
    if not gov_name:
        issues.append("invalid_governorate")

    gender = "ذكر" if int(raw[12]) % 2 == 1 else "أنثى"

    return NationalIdParts(
        raw=raw,
        century_digit=century_digit,
        date_of_birth=dob,
        gender=gender,
        governorate_code=gov_code,
        governorate=gov_name,
        issues=tuple(issues),
    )
