"""
Распознавание паспорта:
1) Предобработка фото (для качества OCR).
2) Полный текст страницы (Tesseract) — для доп. полей (место рождения, дата выдачи и т.п.)
3) Поиск и разбор MRZ (машиночитаемой зоны) — самые надёжные поля, с проверкой
   контрольных цифр по стандарту ICAO 9303 (используется во всех паспортах мира).
"""

import re
import io
from datetime import datetime

import cv2
import numpy as np
import pytesseract
from PIL import Image

from config import OCR_LANGS

MRZ_CHARS = "A-Z0-9<"
MRZ_LINE_RE = re.compile(rf"^[{MRZ_CHARS}]{{40,44}}$")

WEIGHTS = [7, 3, 1]


def _check_digit(data: str) -> int:
    total = 0
    for i, ch in enumerate(data):
        if ch.isdigit():
            v = int(ch)
        elif ch == "<":
            v = 0
        else:
            v = ord(ch) - 55  # A=10 ... Z=35
        total += v * WEIGHTS[i % 3]
    return total % 10


def _preprocess(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    # увеличиваем, если фото маленькое — заметно улучшает OCR
    h, w = img.shape[:2]
    if max(h, w) < 1800:
        scale = 1800 / max(h, w)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    gray = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )
    return gray


def _full_text(gray: np.ndarray) -> str:
    return pytesseract.image_to_string(gray, lang=OCR_LANGS)


def _mrz_text(gray: np.ndarray) -> str:
    # MRZ обычно в нижних ~25% страницы разворота
    h = gray.shape[0]
    bottom = gray[int(h * 0.65):, :]
    config = "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
    return pytesseract.image_to_string(bottom, lang="eng", config=config)


def _extract_mrz_lines(raw: str):
    candidates = [l.replace(" ", "").upper() for l in raw.splitlines()]
    lines = [l for l in candidates if MRZ_LINE_RE.match(l)]
    return lines


def _pad(line: str, length: int = 44) -> str:
    return (line + "<" * length)[:length]


def _parse_date(yy_mm_dd: str):
    try:
        yy = int(yy_mm_dd[0:2])
        mm = int(yy_mm_dd[2:4])
        dd = int(yy_mm_dd[4:6])
        # эвристика: если yy > текущий год-2000+1 -> считаем что это 19хх
        current_yy = datetime.utcnow().year % 100
        century = 2000 if yy <= current_yy + 1 else 1900
        return f"{dd:02d}.{mm:02d}.{century + yy}"
    except Exception:
        return None


def parse_mrz(lines):
    """Разбор двухстрочной MRZ (формат TD3, паспорта). Возвращает dict или None."""
    if len(lines) < 2:
        return None

    # берём последние две валидные строки подряд
    line1, line2 = _pad(lines[-2]), _pad(lines[-1])

    if not line1.startswith(("P<", "P")):
        return None

    try:
        issuing_country = line1[2:5].replace("<", "")
        names_part = line1[5:].split("<<", 1)
        surname = names_part[0].replace("<", " ").strip()
        given_names = names_part[1].replace("<", " ").strip() if len(names_part) > 1 else ""

        doc_number = line2[0:9].replace("<", "")
        doc_number_check = line2[9]
        nationality = line2[10:13].replace("<", "")
        dob_raw = line2[13:19]
        dob_check = line2[19]
        sex = line2[20].replace("<", "не указан")
        expiry_raw = line2[21:27]
        expiry_check = line2[27]

        ok_doc = str(_check_digit(doc_number.ljust(9, "<"))) == doc_number_check
        ok_dob = str(_check_digit(dob_raw)) == dob_check
        ok_exp = str(_check_digit(expiry_raw)) == expiry_check

        return {
            "surname": surname,
            "given_names": given_names,
            "doc_number": doc_number,
            "nationality": nationality,
            "issuing_country": issuing_country,
            "dob": _parse_date(dob_raw),
            "expiry": _parse_date(expiry_raw),
            "sex": sex,
            "checks_passed": all([ok_doc, ok_dob, ok_exp]),
        }
    except Exception:
        return None


BIRTH_PLACE_KEYWORDS = [
    r"место рождения[:\s]*([A-ZА-ЯЁ0-9,.\- ]{2,40})",
    r"place of birth[:\s]*([A-Z0-9,.\- ]{2,40})",
    r"geburtsort[:\s]*([A-Z0-9,.\- ]{2,40})",
]

ISSUE_DATE_KEYWORDS = [
    r"дата выдачи[:\s]*(\d{2}[./]\d{2}[./]\d{2,4})",
    r"date of issue[:\s]*(\d{2}[./]\d{2}[./]\d{2,4})",
]

MIDDLE_NAME_KEYWORDS = [
    r"отчество[:\s]*([А-ЯЁ][а-яё]+)",
]


def extract_extra_fields(full_text: str) -> dict:
    text_low = full_text.lower()
    result = {"birth_place": None, "issue_date": None, "middle_name": None}

    for pattern in BIRTH_PLACE_KEYWORDS:
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m:
            result["birth_place"] = m.group(1).strip(" .,")
            break

    for pattern in ISSUE_DATE_KEYWORDS:
        m = re.search(pattern, text_low, re.IGNORECASE)
        if m:
            result["issue_date"] = m.group(1)
            break

    for pattern in MIDDLE_NAME_KEYWORDS:
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m:
            result["middle_name"] = m.group(1).strip()
            break

    return result


def process_passport_image(image_bytes: bytes) -> dict:
    gray = _preprocess(image_bytes)

    mrz_raw = _mrz_text(gray)
    mrz_lines = _extract_mrz_lines(mrz_raw)
    mrz_data = parse_mrz(mrz_lines)

    full_text = _full_text(gray)
    extra = extract_extra_fields(full_text)

    return {
        "mrz": mrz_data,
        "extra": extra,
        "raw_text": full_text,
    }