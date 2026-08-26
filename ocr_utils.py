"""
Распознавание паспортов с улучшенной обработкой.
Поддерживает:
- Внутренние паспорта РФ
- Загранпаспорта РФ (с полоской MRZ)
- Загранпаспорта других стран
"""

import re
import logging
from datetime import datetime

import cv2
import numpy as np
import pytesseract
from PIL import Image

from config import OCR_LANGS, TESSERACT_CMD

logger = logging.getLogger(__name__)

if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# Регулярки для разных форматов
MRZ_CHARS = "A-Z0-9<"
MRZ_LINE_RE = re.compile(rf"^[{MRZ_CHARS}]{{36,44}}$")

# Веса для контрольных сумм ICAO
WEIGHTS = [7, 3, 1]


def _check_digit(data: str) -> int:
    """Расчёт контрольной цифры по ICAO 9303"""
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
    """Улучшенная предобработка для OCR"""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Не удалось декодировать изображение")

    # Оптимальный размер для OCR
    h, w = img.shape[:2]
    target_size = 1800
    if max(h, w) < target_size:
        scale = target_size / max(h, w)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    elif max(h, w) > 3000:
        scale = 3000 / max(h, w)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    # Конвертация в серый
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Улучшение контраста
    gray = cv2.equalizeHist(gray)

    # Удаление шума
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # Адаптивная бинаризация
    gray = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 8
    )

    # Увеличение для улучшения OCR
    kernel = np.ones((1, 1), np.uint8)
    gray = cv2.dilate(gray, kernel, iterations=1)

    return gray


def _full_text(gray: np.ndarray) -> str:
    """Распознавание всего текста на странице"""
    # Пробуем разные PSM для лучшего результата
    configs = [
        "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя0123456789:.,/ -",
        "--psm 4 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя0123456789:.,/ -"
    ]

    for config in configs:
        try:
            text = pytesseract.image_to_string(gray, lang=OCR_LANGS, config=config)
            if len(text.strip()) > 20:
                return text
        except:
            continue

    return pytesseract.image_to_string(gray, lang=OCR_LANGS)


def _mrz_text(gray: np.ndarray) -> str:
    """Распознавание только MRZ зоны (нижняя часть)"""
    h = gray.shape[0]
    # Берём нижние 40% страницы
    bottom = gray[int(h * 0.6):, :]

    # Специальная конфигурация для MRZ
    config = "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
    return pytesseract.image_to_string(bottom, lang="eng", config=config)


def _extract_mrz_lines(raw: str) -> list:
    """Извлечение строк MRZ из текста"""
    candidates = []
    for line in raw.splitlines():
        # Убираем пробелы и спецсимволы
        cleaned = re.sub(r'[^A-Z0-9<]', '', line.upper())
        if len(cleaned) >= 36 and MRZ_LINE_RE.match(cleaned):
            candidates.append(cleaned)
    return candidates


def _pad(line: str, length: int = 44) -> str:
    """Дополнение строки до нужной длины"""
    return (line + "<" * length)[:length]


def _parse_date(yy_mm_dd: str) -> str:
    """Парсинг даты из MRZ (формат YYMMDD)"""
    try:
        if len(yy_mm_dd) != 6:
            return None
        yy = int(yy_mm_dd[0:2])
        mm = int(yy_mm_dd[2:4])
        dd = int(yy_mm_dd[4:6])

        if mm < 1 or mm > 12 or dd < 1 or dd > 31:
            return None

        current_yy = datetime.utcnow().year % 100
        # Если год в прошлом больше чем на 10 лет - считаем 19xx
        century = 2000 if yy <= current_yy + 10 else 1900
        if yy > current_yy + 20:
            century = 1900

        return f"{dd:02d}.{mm:02d}.{century + yy}"
    except Exception as e:
        logger.debug(f"Ошибка парсинга даты: {e}")
        return None


def parse_mrz(lines: list) -> dict:
    """
    Парсинг MRZ (машиночитаемой зоны).
    Поддерживает TD3 (загранпаспорта) и TD1 (внутренние паспорта РФ)
    """
    if len(lines) < 2:
        return None

    # Берём две последние подходящие строки
    line1 = _pad(lines[-2])
    line2 = _pad(lines[-1])

    # Проверяем формат
    is_td1 = line1[0].isdigit() and len(line1) == 44  # TD1 (внутренний РФ)
    is_td3 = line1.startswith(("P<", "P"))  # TD3 (загран)

    if not is_td1 and not is_td3:
        logger.debug(f"Неизвестный формат MRZ: {line1[:5]}")
        return None

    try:
        if is_td3:
            # TD3 - загранпаспорт
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
                "type": "passport"
            }

        elif is_td1:
            # TD1 - внутренний паспорт РФ (или другой ID)
            # Формат: 2 строки по 44 символа
            doc_number = line1[0:9].replace("<", "")
            doc_check = line1[9]
            issuing_country = line1[10:13].replace("<", "")
            dob_raw = line1[13:19]
            dob_check = line1[19]
            sex = line1[20].replace("<", "не указан")
            expiry_raw = line1[21:27]
            expiry_check = line1[27]
            nationality = line1[28:31].replace("<", "")

            # Вторая строка TD1
            surname = line2[0:30].replace("<", " ").strip()
            given_names = line2[30:].replace("<", " ").strip()

            ok_doc = str(_check_digit(doc_number.ljust(9, "<"))) == doc_check
            ok_dob = str(_check_digit(dob_raw)) == dob_check
            ok_exp = str(_check_digit(expiry_raw)) == expiry_check

            return {
                "surname": surname,
                "given_names": given_names,
                "doc_number": doc_number,
                "nationality": nationality or "RUS",
                "issuing_country": issuing_country or "RUS",
                "dob": _parse_date(dob_raw),
                "expiry": _parse_date(expiry_raw),
                "sex": sex,
                "checks_passed": all([ok_doc, ok_dob, ok_exp]),
                "type": "id_card"
            }

    except Exception as e:
        logger.error(f"Ошибка парсинга MRZ: {e}")
        return None

    return None


# Паттерны для извлечения дополнительных полей
PATTERNS = {
    "birth_place": [
        r"(?:место рождения|place of birth|geburtsort)[:\s]*([^\n]{2,50})",
        r"рожд[её]н[а]?\s+(?:в\s+)?([^\n]{2,50})",
    ],
    "issue_date": [
        r"(?:дата выдачи|date of issue|ausstellungsdatum)[:\s]*(\d{2}[./]\d{2}[./]\d{2,4})",
        r"выдан[а]?\s+(\d{2}[./]\d{2}[./]\d{2,4})",
    ],
    "middle_name": [
        r"(?:отчество|patronymic)[:\s]*([А-ЯЁ][а-яё]+(?:[ -][А-ЯЁ][а-яё]+)?)",
    ],
    "series": [
        r"(?:серия|series)[:\s]*(\d{4})",
        r"серия\s+([А-ЯЁ0-9]{2,4})",
    ],
    "internal_number": [
        r"(?:номер|number)[:\s]*(\d{6})",
        r"номер\s+(\d{6})",
    ],
    "code": [
        r"код подразделения[:\s]*(\d{3}-\d{3})",
    ],
}


def extract_extra_fields(full_text: str) -> dict:
    """Извлечение дополнительных полей из текста страницы"""
    result = {}
    text_lower = full_text.lower()

    for field, patterns in PATTERNS.items():
        for pattern in patterns:
            # Для поля извлекаем с учётом регистра
            if field in ["series", "internal_number", "code"]:
                m = re.search(pattern, full_text, re.IGNORECASE)
            else:
                m = re.search(pattern, full_text, re.IGNORECASE)

            if m:
                value = m.group(1).strip(" .,:;")
                if value:
                    result[field] = value
                    break

    # Если нашли серию и номер - определяем как внутренний паспорт
    if "series" in result and "internal_number" in result:
        result["is_russian_internal"] = True

    return result


def process_passport_image(image_bytes: bytes) -> dict:
    """
    Основная функция обработки изображения паспорта
    """
    try:
        # Предобработка
        gray = _preprocess(image_bytes)

        # Распознавание MRZ
        mrz_raw = _mrz_text(gray)
        logger.debug(f"MRZ raw: {mrz_raw[:100]}")

        mrz_lines = _extract_mrz_lines(mrz_raw)
        logger.debug(f"MRZ lines found: {len(mrz_lines)}")

        mrz_data = parse_mrz(mrz_lines)

        # Распознавание полного текста для доп. полей
        full_text = _full_text(gray)
        extra = extract_extra_fields(full_text)

        # Определяем тип паспорта
        is_russian_internal = extra.get("is_russian_internal", False)

        # Если MRZ не найден, пытаемся распознать внутренний паспорт РФ по шаблону
        if not mrz_data and not is_russian_internal:
            # Проверяем на наличие серии и номера в тексте
            if "series" in extra and "internal_number" in extra:
                is_russian_internal = True
                extra["is_russian_internal"] = True

        return {
            "mrz": mrz_data,
            "extra": extra,
            "is_russian_internal": is_russian_internal,
            "raw_text": full_text[:500],  # для отладки
        }

    except Exception as e:
        logger.exception("Ошибка в process_passport_image")
        return {
            "mrz": None,
            "extra": {},
            "is_russian_internal": False,
            "error": str(e)
        }