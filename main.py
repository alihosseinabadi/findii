"""FindII — AI Lead Agent entrypoint.

Runs three services in one process:
    1. Telegram bot (channel ingestion + commands)
    2. Avito scrape scheduler (Scrapling-powered)
    3. Web CRM (FastAPI kanban pipeline)
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from aiogram.filters import Command

import config
from ai.extractor import LeadExtractor
from agent.orchestrator import FindIIAgent, ScrapeScheduler
from bot.handlers import TelegramBot
from core.db import LeadStore
from crm.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("findii")

BANNER = """
╔════════════════════════════════════════════════╗
║   🤖  FindII — AI Lead Agent for Real Estate   ║
║   scrape → extract → score → CRM → close 💰    ║
╚════════════════════════════════════════════════╝
"""


async def start_crm(store: LeadStore):
    import uvicorn
    app = create_app(store)
    srv = uvicorn.Server(uvicorn.Config(
        app, host=config.CRM_HOST, port=config.CRM_PORT, log_level="warning",
    ))
    log.info("CRM on http://%s:%s", config.CRM_HOST, config.CRM_PORT)
    await srv.serve()


async def main() -> None:
    print(BANNER)

    if not config.TELEGRAM_TOKEN:
        log.critical("TELEGRAM token missing — set it in .env (see .env.example)")
        sys.exit(1)

    store = LeadStore(config.DB_PATH)
    extractor = LeadExtractor()
    agent = FindIIAgent(extractor, store)

    bot_handler = TelegramBot(agent, store)

    bot = Bot(token=config.TELEGRAM_TOKEN)
    dp = Dispatcher()
    bot_handler.bind_bot(bot)

    # telegram ingestion + commands
    dp.channel_post.register(bot_handler.handle_channel_post)
    dp.edited_channel_post.register(bot_handler.handle_channel_post)
    dp.message.register(bot_handler.handle_message)
    dp.callback_query.register(bot_handler.on_lead_mode,
                               lambda c: c.data
                               and c.data.startswith("lead_mode:") or
                               (c.data and c.data.startswith("menu:")))
    for cmd, handler in [
        ("start", bot_handler.cmd_start),
        ("addsearch", bot_handler.cmd_addsearch),
        ("searches", bot_handler.cmd_searches),
        ("delsearch", bot_handler.cmd_delsearch),
        ("togglesearch", bot_handler.cmd_togglesearch),
        ("scrape", bot_handler.cmd_scrape),
        ("market", bot_handler.cmd_leads_scrape),
        ("city", bot_handler.cmd_city),
        ("stats", bot_handler.cmd_stats),
        ("leads", bot_handler.cmd_leads),
        ("mark", bot_handler.cmd_mark),
        ("mini", bot_handler.cmd_mini),
        ("export", bot_handler.cmd_export),
    ]:
        dp.message.register(handler, Command(cmd))

    # periodic avito scraping with telegram digest after each cycle
    scheduler = ScrapeScheduler(agent)

    original_run = agent.run_all_searches

    async def run_with_digest() -> dict:
        summary = await original_run()
        if summary.get("new"):
            try:
                await bot_handler.send_digest(summary)
            except Exception:  # noqa: BLE001
                log.exception("digest failed")
        return summary

    agent.run_all_searches = run_with_digest  # type: ignore[method-assign]

    log.info("AI provider chain: %s | scraper engine: %s",
             extractor.provider or "auto", config.SCRAPER_ENGINE)
    searches = await store.list_searches(enabled_only=True)
    log.info("%d saved searches ready", len(searches))

    await asyncio.gather(
        dp.start_polling(bot),
        scheduler.run_forever(),
        start_crm(store),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 FindII stopped")
