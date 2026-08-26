import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import ALLOWED_IDS
from ocr_utils import process_passport_image

router = Router()
logger = logging.getLogger(__name__)


def _is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_IDS


@router.message(CommandStart())
async def start_handler(message: Message):
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    await message.answer(
        "📷 Пришлите чёткое фото разворота паспорта (страница с фотографией "
        "и машиночитаемой зоной внизу — двумя строками с символами «<»).\n\n"
        "Поддерживаются:\n"
        "• 🇷🇺 Внутренний паспорт РФ (серия/номер)\n"
        "• 🌍 Загранпаспорт РФ (с полоской внизу)\n"
        "• 🌍 Загранпаспорта других стран\n\n"
        "Я распознаю данные и пришлю их текстом."
    )


@router.message(F.photo)
async def photo_handler(message: Message, bot: Bot):
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    await message.answer("🔎 Распознаю паспорт... Это может занять 10-30 секунд.")

    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)

        # ✅ ИСПРАВЛЕНО: правильная загрузка файла
        file_bytes = await bot.download_file(file.file_path)
        image_data = file_bytes.getvalue()  # ✅ получаем bytes

        # Запускаем OCR в отдельном потоке
        result = await asyncio.to_thread(process_passport_image, image_data)

        text = format_result(result)
        await message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.exception("Ошибка распознавания")
        await message.answer(
            f"⚠️ Не удалось распознать фото.\n\n"
            f"Советы:\n"
            f"• Снимайте при хорошем освещении\n"
            f"• Уберите блики\n"
            f"• Держите паспорт ровно\n"
            f"• Вся страница должна быть в кадре\n\n"
            f"Ошибка: {str(e)[:150]}"
        )


def format_result(result: dict) -> str:
    mrz = result.get("mrz")
    extra = result.get("extra", {})
    is_russian_internal = result.get("is_russian_internal", False)

    lines = []

    # Заголовок
    if is_russian_internal:
        lines.append("🇷🇺 <b>Внутренний паспорт РФ</b>")
    else:
        lines.append("🌍 <b>Загранпаспорт</b>")

    lines.append("=" * 30)

    # MRZ данные
    if mrz and mrz.get("checks_passed"):
        lines.append("✅ Данные из MRZ (проверены контрольными суммами):")
    elif mrz:
        lines.append("⚠️ Данные из MRZ (контрольная сумма НЕ сошлась):")
    else:
        lines.append("⚠️ MRZ не найдена. Проверьте фото.")

    if mrz:
        fio = mrz.get("surname", "")
        if mrz.get("given_names"):
            fio += f" {mrz['given_names']}"
        lines.append(f"👤 ФИО: {fio}")

        if extra.get("middle_name"):
            lines.append(f"👤 Отчество: {extra['middle_name']}")

        lines.append(f"📅 Дата рождения: {mrz.get('dob', '—')}")
        lines.append(f"📅 Срок действия до: {mrz.get('expiry', '—')}")
        lines.append(f"🔢 Номер документа: {mrz.get('doc_number', '—')}")
        lines.append(f"🌍 Страна выдачи: {mrz.get('issuing_country', '—')}")
        lines.append(f"🌍 Гражданство: {mrz.get('nationality', '—')}")
        lines.append(f"⚧ Пол: {mrz.get('sex', '—')}")

    # Дополнительные поля (если есть)
    if extra.get("birth_place"):
        lines.append(f"📍 Место рождения: {extra['birth_place']}")

    if extra.get("issue_date"):
        lines.append(f"📅 Дата выдачи: {extra['issue_date']}")

    if extra.get("series"):
        lines.append(f"🔢 Серия: {extra['series']}")

    if extra.get("internal_number"):
        lines.append(f"🔢 Номер: {extra['internal_number']}")

    lines.append("\n" + "=" * 30)
    lines.append("ℹ️ <i>Данные из MRZ надёжны. Остальные поля проверьте по фото.</i>")

    return "\n".join(lines)