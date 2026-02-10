import asyncio
import logging
import os

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.enums import ContentType

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Check your .env file.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dispatcher & Router
# ---------------------------------------------------------------------------

dp = Dispatcher()
router = Router(name="service_messages")
dp.include_router(router)

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.message(F.content_type.in_({ContentType.NEW_CHAT_MEMBERS, ContentType.LEFT_CHAT_MEMBER}))
async def delete_service_message(message: Message) -> None:
    """Delete join / leave service messages to keep the chat clean."""
    try:
        await message.delete()
        logger.info(
            "Deleted service message (type=%s) in chat %s",
            message.content_type,
            message.chat.id,
        )
    except Exception as exc:
        logger.error(
            "Failed to delete message in chat %s: %s. "
            "Make sure the bot has admin rights with 'Delete Messages' permission.",
            message.chat.id,
            exc,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------



async def start_web_server() -> None:
    from aiohttp import web
    
    async def handle(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()
    logger.info(f"Web server started on port {os.getenv('PORT', 8080)}")


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    logger.info("Bot is starting…")
    
    # Start the dummy web server
    await start_web_server()
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
