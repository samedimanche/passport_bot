import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

ALLOWED_IDS = {
    int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()
}

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

PORT = int(os.getenv("PORT", "10000"))

# Добавляем русский и английский по умолчанию, остальные опционально
OCR_LANGS = os.getenv("OCR_LANGS", "rus+eng")

TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")