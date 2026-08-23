"""Telegram layer: channel ingestion + commands + lead notifications."""
from __future__ import annotations

import html
import logging
import os
import random

from aiogram.types import FSInputFile, Message

import config
from agent.orchestrator import FindIIAgent
from core.db import LeadStore
from scrapers.avito import AvitoScraper

log = logging.getLogger("findii.bot")

VALID_STATUSES = ("new", "contacted", "qualified", "negotiation", "won", "lost", "junk")


def score_emoji(score: int) -> str:
    if score >= 80:
        return "🔥"
    if score >= 60:
        return "✅"
    return "📋"


class TelegramBot:
    def __init__(self, agent: FindIIAgent, store: LeadStore):
        self.agent = agent
        self.store = store
        self.avito_helper = AvitoScraper()
        self._bot = None

    def bind_bot(self, bot) -> None:
        self._bot = bot
        self.agent.on_lead(self._notify_lead)

    # --------------------------------------------------------- notifications

    async def _notify_lead(self, lead) -> None:
        if not config.DESTINATION_CHAT_ID or not self._bot:
            return
        link = f"\n🔗 {lead.source_url}" if lead.source_url else ""
        crm = (f"\n🗂 CRM: {config.CRM_PUBLIC_URL}/lead/{lead.id}"
               if config.CRM_PUBLIC_URL and lead.id else "")
        text = (
            f"{score_emoji(lead.score)} <b>NEW LEAD</b> — score <b>{lead.score}/100</b>\n"
            f"🏷 {html.escape((lead.deal_type or '').title())} · "
            f"{html.escape(lead.property_type or '')} · via {html.escape(lead.source)}\n"
            f"📍 {html.escape(lead.city or '—')}"
            + (f", {html.escape(lead.district)}" if lead.district else "") + "\n"
            + (f"💰 {lead.price:,.0f} {html.escape(lead.currency or '')}\n" if lead.price else "")
            + (f"📐 {lead.area_sqm or '—'} m² · 🛏 {lead.rooms or '—'}\n" if lead.area_sqm or lead.rooms else "")
            + (f"📞 <code>{html.escape(lead.contact)}</code>\n" if lead.contact else "")
            + (f"📝 {html.escape((lead.summary or '')[:200])}\n" if lead.summary else "")
            + link + crm
        )
        await self._bot.send_message(config.DESTINATION_CHAT_ID, text, parse_mode="HTML")

    async def send_digest(self, summary: dict) -> None:
        if not config.DESTINATION_CHAT_ID or not self._bot:
            return
        lines = [f"🕷 <b>Scrape cycle</b> — {summary['searches']} searches"]
        for r in summary.get("details", []):
            if r.get("error"):
                lines.append(f"⚠️ {html.escape(r.get('label') or r['url'])}: error")
            else:
                lines.append(
                    f"• {html.escape(r.get('label') or r['url'])[:60]} → "
                    f"{r['ads']} ads, <b>{r['new']} new</b>, {r['qualified']} qualified"
                )
        await self._bot.send_message(config.DESTINATION_CHAT_ID,
                                     "\n".join(lines), parse_mode="HTML")

    # ------------------------------------------------------------ ingestion

    async def handle_channel_post(self, message: Message) -> None:
        text = message.text or message.caption or ""
        title = message.chat.title or str(message.chat.id)
        await self.agent.ingest_text(message.chat.id, message.message_id,
                                     title, text, source="telegram")

    async def handle_message(self, message: Message) -> None:
        chat_id = message.chat.id
        if config.SOURCE_CHANNELS and chat_id not in config.SOURCE_CHANNELS:
            return  # group sources are gated by env config
        text = message.text or message.caption or ""
        title = message.chat.title or str(message.chat.id)
        await self.agent.ingest_text(chat_id, message.message_id, title, text)

    # -------------------------------------------------------------- helpers

    @staticmethod
    def is_admin(user_id: int | None) -> bool:
        return bool(user_id) and user_id in config.ADMIN_IDS

    @staticmethod
    def _crm_link(lead) -> str:
        base = config.CRM_PUBLIC_URL.rstrip("/")
        return f"{base}/lead/{lead.id}" if base and lead.id else ""

    # ------------------------------------------------------------- commands

    async def cmd_start(self, message: Message) -> None:
        await message.answer(
            "🤖 <b>FindII — AI Lead Agent</b> 🏠\n\n"
            "I monitor <b>Telegram channels</b> and run your saved "
            "<b>Avito searches</b>, extract structured leads with AI, score them "
            "(0–100) and push the hot ones here + to the web CRM.\n\n"
            "<b>Sources</b>\n"
            "/addsearch &lt;avito-url&gt; [label] — save an Avito search\n"
            "/searches / /delsearch n / /togglesearch n\n"
            "/scrape — run all searches now\n"
            "<i>(channels: just add me as admin)</i>\n\n"
            "<b>CRM</b>\n"
            "/stats — pipeline statistics\n"
            "/leads [n] — top leads\n"
            "/mark &lt;id&gt; &lt;status&gt; — update pipeline stage\n"
            "/export — CSV export of all leads\n\n"
            f"Alert threshold: score ≥ <b>{config.MIN_LEAD_SCORE}</b>"
        )

    async def cmd_addsearch(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2 or "avito.ru" not in parts[1]:
            await message.answer(
                "Usage: <code>/addsearch https://www.avito.ru/moskva/kvartiry/prodam-2-komnatnye my-label</code>"
            )
            return
        url = self.avito_helper.normalize_url(parts[1])
        label = parts[2] if len(parts) > 2 else url.split("/")[3].replace("-", " ")
        created, sid = await self.store.add_search(url, label)
        await message.answer(
            f"{'✅ search saved' if created else 'ℹ️ already saved'} (id {sid})\n"
            f"<code>{html.escape(url)}</code>\nlabel: {html.escape(label)}"
        )

    async def cmd_searches(self, message: Message) -> None:
        rows = await self.store.list_searches(enabled_only=False)
        if not rows:
            await message.answer("No saved searches yet — use /addsearch")
            return
        lines = [
            f"{'' if r['enabled'] else '⏸ '}<b>{r['id']}.</b> "
            f"{html.escape(r['label'] or '—')} — <code>{html.escape(r['url'])}</code>"
            + (f" (last run {r['last_run']})" if r["last_run"] else "")
            for r in rows
        ]
        await message.answer("\n".join(lines), parse_mode="HTML",
                             disable_web_page_preview=True)

    async def cmd_delsearch(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        try:
            sid = int((message.text or "").split()[1])
        except (IndexError, ValueError):
            await message.answer("Usage: /delsearch &lt;id&gt;")
            return
        ok = await self.store.del_search(sid)
        await message.answer("✅ deleted" if ok else "not found")

    async def cmd_togglesearch(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        try:
            sid = int((message.text or "").split()[1])
        except (IndexError, ValueError):
            await message.answer("Usage: /togglesearch &lt;id&gt;")
            return
        ok = await self.store.toggle_search(sid)
        await message.answer("✅ toggled" if ok else "not found")

    async def cmd_scrape(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        await message.answer("🕷 Running all saved searches…")
        summary = await self.agent.run_all_searches()
        s = summary
        await message.answer(
            f"Done: {s['searches']} searches · {s['ads']} ads scanned · "
            f"<b>{s['new']} new leads</b> · {s['qualified']} above threshold"
        )

    async def cmd_stats(self, message: Message) -> None:
        s = await self.store.stats()
        status_line = ", ".join(f"{k}: {v}" for k, v in s["by_status"].items()) or "—"
        source_line = ", ".join(f"{k}: {v}" for k, v in s["by_source"].items()) or "—"
        await message.answer(
            "📊 <b>Pipeline stats</b>\n"
            f"Total leads: <b>{s['total']}</b> · today: <b>{s['today']}</b> · "
            f"hot (≥80): <b>{s['hot']}</b>\n"
            f"Conversion new→won: <b>{s['conversion']}%</b>\n"
            f"Sources → {html.escape(source_line)}\n"
            f"Statuses → {html.escape(status_line)}"
        )

    async def cmd_leads(self, message: Message) -> None:
        limit = 5
        parts = (message.text or "").split()
        if len(parts) > 1 and parts[1].isdigit():
            limit = min(int(parts[1]), 20)
        leads = await self.store.recent_leads(limit=limit)
        if not leads:
            await message.answer("No leads yet.")
            return
        lines = []
        for l in leads:
            price_str = f"{l.price:,.0f} {l.currency or ''}".strip() if l.price else "—"
            lines.append(
                f"{score_emoji(l.score)} <b>{l.score}/100</b> [{l.status}] #{l.id} "
                f"{html.escape(l.deal_type)} {html.escape(l.property_type)} — "
                f"{html.escape(l.city or '—')} · {price_str}\n"
                f"   └ {html.escape((l.summary or l.raw_text)[:90])}"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")

    async def cmd_mark(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer(
                f"Usage: /mark &lt;lead_id&gt; {'|'.join(VALID_STATUSES)}")
            return
        try:
            lead_id = int(parts[1])
            status = parts[2].lower()
            assert status in VALID_STATUSES
        except Exception:
            await message.answer("Invalid arguments.")
            return
        ok = await self.store.set_status(lead_id, 0, status)
        await message.answer("✅ updated" if ok else "❌ lead not found")

    async def cmd_export(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        path = os.path.join(os.path.dirname(config.DB_PATH) or ".",
                            f"findii_export_{random.randint(1000, 9999)}.csv")
        count = await self.store.export_csv(path)
        await message.answer_document(FSInputFile(path),
                                      caption=f"📦 {count} leads exported")
        os.remove(path)
