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
    # если список пуст — на всякий случай никого не пускаем
    return user_id in ALLOWED_IDS


@router.message(CommandStart())
async def start_handler(message: Message):
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    await message.answer(
        "Пришлите чёткое фото разворота паспорта (страница с фотографией "
        "и машиночитаемой зоной внизу — двумя строками с символами «<»).\n\n"
        "Я распознаю данные и пришлю их текстом."
    )


@router.message(F.photo)
async def photo_handler(message: Message, bot: Bot):
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    await message.answer("🔎 Распознаю, это может занять до минуты на бесплатном хостинге...")

    try:
        photo = message.photo[-1]  # самое большое разрешение
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        image_bytes = file_bytes.read()

        # OCR — блокирующая операция, уводим в отдельный поток,
        # чтобы не подвешивать event loop и не грузить остальных
        result = await asyncio.to_thread(process_passport_image, image_bytes)

        text = format_result(result)
        await message.answer(text)

    except Exception as e:
        logger.exception("Ошибка распознавания")
        try:
            await message.answer(
                "⚠️ Не удалось распознать фото. Попробуйте переснять при хорошем "
                "освещении, без бликов, чтобы вся страница была в кадре.\n\n"
                f"Техническая причина: {str(e)[:200]}"
            )
        except Exception:
            # если даже сообщение об ошибке не отправилось (например, из-за
            # спецсимволов в тексте исключения) — шлём совсем простой текст
            await message.answer("⚠️ Не удалось распознать фото. Попробуйте переснять фото.")


def format_result(result: dict) -> str:
    mrz = result.get("mrz")
    extra = result.get("extra", {})

    lines = []

    if mrz and mrz.get("checks_passed"):
        lines.append("✅ Данные из машиночитаемой зоны (проверены контрольными суммами):")
    elif mrz:
        lines.append("⚠️ Данные из машиночитаемой зоны (контрольная сумма НЕ сошлась — проверьте вручную):")
    else:
        lines.append("⚠️ Машиночитаемая зона не найдена/не распознана. Переснимите фото так, "
                      "чтобы обе строки с «<<<» внизу паспорта были чётко видны.")

    if mrz:
        fio = mrz["surname"]
        if mrz["given_names"]:
            fio += f" {mrz['given_names']}"
        lines.append(f"Фамилия Имя: {fio}")
        if extra.get("middle_name"):
            lines.append(f"Отчество: {extra['middle_name']}")
        lines.append(f"Дата рождения: {mrz['dob'] or '—'}")
        lines.append(f"Дата окончания действия: {mrz['expiry'] or '—'}")
        lines.append(f"Номер паспорта/документа: {mrz['doc_number'] or '—'}")
        lines.append(f"Страна выпуска паспорта: {mrz['issuing_country'] or '—'}")
        lines.append(f"Гражданство (nationality code): {mrz['nationality'] or '—'}")
        lines.append(f"Пол: {mrz['sex'] or '—'}")

    lines.append(f"Место рождения: {extra.get('birth_place') or '— не распознано, проверьте вручную'}")
    lines.append(f"Дата выдачи (начала действия): {extra.get('issue_date') or '— не распознано, проверьте вручную'}")
    lines.append("Страна рождения: — MRZ не хранит это поле отдельно от гражданства; "
                  "определяйте по месту рождения/визуально.")

    lines.append("\nℹ️ Поля из машиночитаемой зоны надёжны почти всегда. Остальные поля "
                  "(место/дата выдачи) — проверяйте по фото, распознавание текста страницы менее точное.")

    return "\n".join(lines)