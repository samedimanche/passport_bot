import os

# Токен бота от @BotFather (задаётся в переменных окружения на хостинге)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Telegram user_id пользователей, которым разрешено пользоваться ботом.
# Формат в переменной окружения: "111111111,222222222"
ALLOWED_IDS = {
    int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()
}

# Публичный адрес вашего сервиса на Render, например:
# https://my-passport-bot.onrender.com
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")

# Путь вебхука делаем секретным (включает токен), чтобы никто чужой не постучался
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Порт, который выдаёт Render (сам подставляет переменную PORT)
PORT = int(os.getenv("PORT", "10000"))

# Языки распознавания. Добавляйте нужные пакеты в Dockerfile, если нужны другие.
OCR_LANGS = os.getenv("OCR_LANGS", "rus+eng+deu+fra+spa+ita+ukr")