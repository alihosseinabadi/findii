"""Telegram layer: channel ingestion + commands + lead notifications."""
from __future__ import annotations

import asyncio
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
            + (f"🗺 map-verified: {html.escape(lead.matched_address)}"
               f" ({lead.latitude}, {lead.longitude})\n"
               if lead.geo_status == "Confirmed" and lead.latitude else "")
            + (f"🗺 ~{html.escape(lead.matched_address)} (street-matched)\n"
               if lead.geo_status == "Probable" and lead.matched_address else "")
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
            "/market &lt;city+business&gt; — scotch an OSM business market to Excel+CSV\n"
            "/city &lt;name&gt; — pull that city's map, pin leads to real buildings\n"
            "<i>(channels: just add me as admin)</i>\n\n"
            "<b>CRM</b>\n"
            "/stats — pipeline statistics\n"
            "/leads [n] — top leads\n"
            "/mark &lt;id&gt; &lt;status&gt; — update pipeline stage\n"
            "/mini — open the Mini App 📱\n"
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

    async def cmd_leads_scrape(self, message: Message) -> None:
        """/market <query> — scrape a city+business demand to Excel+CSV."""
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer(
                'Usage: <code>/market moscow red square supermarket</code>\n'
                'I parse the area + business (NLP), fetch that city\'s OSM '
                'map, geo-scope to the landmark/neighbourhood, and export '
                'Excel + CSV + analyst report. Bare city = all businesses.')
            return
        q = parts[1].strip()
        await message.answer(f"🔍 parsing <b>{html.escape(q)}</b>… "
                             f"(map download on first ask for a city can take a minute)")
        import tempfile
        from scrapers.lead_scraper import run_demand
        try:
            out_dir = tempfile.mkdtemp(prefix="findii_leads_")
            result = await asyncio.to_thread(
                run_demand, q, config.OSM_CACHE_DIR, out_dir, config.OSM_PBF_PATH)
        except Exception as e:  # noqa: BLE001
            await message.answer(f"❌ failed: {html.escape(str(e)[:300])}")
            return
        if result.get("error"):
            await message.answer(f"⚠️ {html.escape(result['error'])}")
            return
        files = result.get("files", {})
        anal = result.get("analyst") or {}
        count = result.get("count", 0)
        p = result.get("parsed", {})
        try:
            if files.get("xlsx") and os.path.exists(files["xlsx"]):
                await message.answer_document(FSInputFile(files["xlsx"]),
                    caption=f"📦 <b>{p.get('category', 'Leads')}</b> in "
                            f"{p.get('city', '')} — {count} leads "
                            f"(Excel sales kit)")
            if files.get("csv") and os.path.exists(files["csv"]):
                await message.answer_document(FSInputFile(files["csv"]),
                    caption=f"{count} leads · CSV")
            if anal.get("html") and os.path.exists(anal["html"]):
                await message.answer_document(FSInputFile(anal["html"]),
                    caption=f"📊 <b>Analyst + visual report</b> — {count} "
                            f"{p.get('category', '')} in {p.get('city', '')} "
                            f"(open in any browser)")
            if anal.get("png_density") and os.path.exists(anal["png_density"]):
                await message.answer_photo(FSInputFile(anal["png_density"]),
                    caption=f"🗺 {p.get('category', '')} density · "
                            f"{p.get('city', '')} · {count} leads")
            if anal.get("md") and os.path.exists(anal["md"]):
                await message.answer_document(FSInputFile(anal["md"]),
                    caption="Analyst paper (.md)")
        finally:
            import shutil
            shutil.rmtree(os.path.dirname(files.get("xlsx", "")), ignore_errors=True)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📈 Monthly report",
                                 callback_data="lead_mode:monthly"),
            InlineKeyboardButton(text="🧭 Dashboard / CRM",
                                 callback_data="lead_mode:dashboard"),
            InlineKeyboardButton(text="🔁 New scrape",
                                 callback_data="lead_mode:new"),
        ]])
        await message.answer(
            f"✅ <b>{count}</b> <b>{html.escape(p.get('category', ''))}</b> "
            f"leads from {result.get('map_buildings', 0)} real "
            f"{html.escape(result.get('city', ''))} map places.\n\n"
            "Analyst paper + visual report attached above. Go further?",
            reply_markup=kb)

    async def on_lead_mode(self, callback_query) -> None:
        """Follow-up after a /market scrape: monthly report, dashboard, or
        a fresh scrape. Analyst paper is auto-attached with the export."""
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        mode = (callback_query.data or "").replace("lead_mode:", "")
        label = {"monthly": "📈 monthly report",
                 "dashboard": "🧭 dashboard / CRM",
                 "new": "🔁 new scrape"}.get(mode, mode)
        if mode == "new":
            await callback_query.message.answer(
                "Send <code>/market &lt;city+business&gt;</code> — e.g. "
                "<code>/market hotel in downtown moscow</code>. Every scrape "
                "comes back as Excel + CSV + analyst paper + visual report.")
            await callback_query.answer()
            return
        msg = {
            "monthly": "The monthly report engine — counts by category, "
                       "new vs closed, month-over-month trends — is the next "
                       "milestone built on top of the analyst layer you just "
                       "got. It reuses the same export pipeline.",
            "dashboard": "A live dashboard / CRM view of these leads is "
                         "exactly the /board + Mini App we already ship — "
                         "open /mini from the main menu.",
        }.get(mode, "Picked — that mode is coming in the next milestone.")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 Back to menu", callback_data="menu:start"),
        ]])
        await callback_query.message.answer(f"<b>{label}</b>\n{msg}", reply_markup=kb)
        await callback_query.answer()

    async def cmd_stats(self, message: Message) -> None:
        s = await self.store.stats()
        status_line = ", ".join(f"{k}: {v}" for k, v in s["by_status"].items()) or "—"
        source_line = ", ".join(f"{k}: {v}" for k, v in s["by_source"].items()) or "—"
        geo = self.agent.geo.stats if hasattr(self.agent, "geo") else {}
        map_line = (f"\n🗺 map: {geo.get('city') or '—'} · "
                    f"{geo.get('buildings', 0)} buildings · {geo.get('streets', 0)} streets"
                    if geo.get("buildings") else "\n🗺 map: not set — use /city &lt;name&gt;")
        await message.answer(
            "📊 <b>Pipeline stats</b>\n"
            f"Total leads: <b>{s['total']}</b> · today: <b>{s['today']}</b> · "
            f"hot (≥80): <b>{s['hot']}</b>\n"
            f"Conversion new→won: <b>{s['conversion']}%</b>\n"
            f"Sources → {html.escape(source_line)}\n"
            f"Statuses → {html.escape(status_line)}"
            + map_line
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

    async def cmd_city(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("Usage: <code>/city Moscow</code> — type a city, I pull its map.")
            return
        city = parts[1].strip()
        await message.answer(f"🗺 pulling the map for <b>{html.escape(city)}</b>… (one-time download, then offline)")
        try:
            stats = await asyncio.to_thread(self.agent.set_city, city)
        except Exception as e:  # noqa: BLE001
            await message.answer(f"❌ map failed: {html.escape(str(e)[:300])}")
            return
        await message.answer(
            f"✅ map active: <b>{html.escape(stats['city'])}</b>\n"
            f"🏢 {stats['buildings']} buildings · 🛣 {stats['streets']} streets\n"
            f"Every new lead is now pinned to a real building."
        )

    async def cmd_mini(self, message: Message) -> None:
        base = (config.CRM_PUBLIC_URL or "").rstrip("/")
        if not base.startswith("https://"):
            await message.answer(
                "📱 The Mini App needs a public HTTPS address.\n"
                "Set <code>CRM_PUBLIC_URL=https://your-domain</code> in .env, "
                "restart me, then tap /mini again.")
            return
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📱 Open FindII Mini App",
                                 web_app=WebAppInfo(url=f"{base}/mini"))]])
        await message.answer("Your leads, in your pocket 👇", reply_markup=kb)

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
