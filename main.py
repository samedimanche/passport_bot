import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import BOT_TOKEN, WEBHOOK_PATH, WEBHOOK_URL, PORT
from handlers import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def health(request: web.Request):
    return web.Response(text="OK")


async def on_startup(bot: Bot):
    if WEBHOOK_URL and WEBHOOK_URL.startswith("http"):
        await bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook установлен: %s", WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_HOST не задан — вебхук не установлен!")


def create_app() -> web.Application:
    if not BOT_TOKEN:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)

    app = web.Application()
    app.router.add_get("/", health)  # для UptimeRobot / проверки Render

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT)