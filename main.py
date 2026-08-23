"""RealState Lead AI — Telegram bot that turns channel text into qualified leads."""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.filters import Command

import config
from ai.extractor import LeadExtractor
from bot.handlers import TelegramBot
from core.db import LeadStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("realstate")

BANNER = """
╔══════════════════════════════════════════╗
║   🏠  RealState Lead AI  🤖              ║
║   channel text → structured leads       ║
╚══════════════════════════════════════════╝
"""


async def main() -> None:
    print(BANNER)

    if not config.TELEGRAM_TOKEN:
        log.critical("TELEGRAM token missing — set it in .env (see .env.example)")
        sys.exit(1)

    store = LeadStore(config.DB_PATH)
    extractor = LeadExtractor()
    bot_handler = TelegramBot(extractor, store)

    log.info("AI provider chain: %s", extractor.provider or "auto")
    channels = await store.list_channels()
    if config.SOURCE_CHANNELS or channels:
        log.info("monitoring %s env + %s db sources",
                 len(config.SOURCE_CHANNELS), len(channels))

    bot = Bot(token=config.TELEGRAM_TOKEN)
    dp = Dispatcher()

    dp.channel_post.register(bot_handler.handle_channel_post)
    dp.edited_channel_post.register(bot_handler.handle_channel_post)
    dp.message.register(bot_handler.handle_message)

    for cmd, handler in [
        ("start", bot_handler.cmd_start),
        ("stats", bot_handler.cmd_stats),
        ("leads", bot_handler.cmd_leads),
        ("mark", bot_handler.cmd_mark),
        ("export", bot_handler.cmd_export),
        ("channels", bot_handler.cmd_channels),
        ("addchannel", bot_handler.cmd_addchannel),
        ("rmchannel", bot_handler.cmd_rmchannel),
    ]:
        dp.message.register(handler, Command(cmd))

    log.info("✅ Bot is ready — polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 RealState Lead AI stopped")
