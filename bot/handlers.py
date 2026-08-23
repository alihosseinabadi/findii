"""Telegram handlers: channel monitoring, lead pipeline and admin commands."""
from __future__ import annotations

import html
import logging
import os
import random

from aiogram.types import FSInputFile, Message

import config
from core.db import LeadStore
from core.models import Lead
from core.scoring import score_lead
from ai.extractor import LeadExtractor

log = logging.getLogger("realstate.bot")

VALID_STATUSES = ("new", "contacted", "won", "lost", "junk")


def score_emoji(score: int) -> str:
    if score >= 80:
        return "🔥"
    if score >= 60:
        return "✅"
    return "📋"


class TelegramBot:
    def __init__(self, extractor: LeadExtractor, store: LeadStore):
        self.ai = extractor
        self.store = store

    # ------------------------------------------------------------- pipeline

    async def process_text(self, chat_id: int, message_id: int,
                           title: str, text: str) -> Lead | None:
        """Dedupe -> extract -> score -> persist. Returns saved Lead or None."""
        if len(text) < config.MIN_TEXT_LEN:
            return None

        content_hash = self.store.content_hash(text)
        lead = Lead(
            source_chat_id=chat_id,
            message_id=message_id,
            source_title=title,
            raw_text=text[:2000],
            content_hash=content_hash,
        )

        data = await self.ai.extract(text)
        if not data.get("is_real_estate"):
            log.info("skipped non-RE message %s/%s", chat_id, message_id)
            return None

        for key in ("deal_type", "property_type", "city", "district", "price",
                    "currency", "area_sqm", "rooms", "floor", "contact",
                    "summary", "urgency"):
            setattr(lead, key, data.get(key))
        lead.is_real_estate = True

        lead.score, lead.score_reasons = score_lead(lead)

        if await self.store.is_duplicate_content(content_hash):
            lead.status = "junk"
            lead.score_reasons.append("duplicate content")

        await self.store.save_lead(lead)
        return lead

    def format_card(self, lead: Lead, msg_link: str = "") -> str:
        esc = html.escape
        price_line = (
            f"💰 {lead.price:,.0f} {esc(lead.currency or '')}" if lead.price else "💰 —"
        )
        floor_part = f"🏢 {esc(str(lead.floor))}" if lead.floor else ""
        lines = [
            f"{score_emoji(lead.score)} <b>NEW LEAD</b> — score <b>{lead.score}/100</b>",
            f"🏷 {esc((lead.deal_type or '').title())} · {esc(lead.property_type or '')}",
            f"📍 {esc(lead.city or '—')}"
            + (f", {esc(lead.district)}" if lead.district else ""),
            price_line,
            f"📐 {lead.area_sqm or '—'} m² · 🛏 {lead.rooms or '—'} rooms {floor_part}".rstrip(),
            f"📞 <code>{esc(lead.contact)}</code>" if lead.contact else "",
            f"📝 {esc(lead.summary)}" if lead.summary else "",
            f"⚡ urgency: {esc(lead.urgency)}",
            f"🔗 Source: {esc(lead.source_title)}" + (f" → {msg_link}" if msg_link else ""),
        ]
        return "\n".join(line for line in lines if line.strip())

    @staticmethod
    def msg_link(chat: Message.chat, message_id: int) -> str:
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}/{message_id}"
        if getattr(chat, "invite_link", None):
            return chat.invite_link
        return ""

    # ------------------------------------------------------------- ingestion

    async def _source_allowed(self, chat_id: int) -> bool:
        if config.SOURCE_CHANNELS:
            return chat_id in config.SOURCE_CHANNELS
        channels = await self.store.list_channels()
        if not channels:
            return True  # no sources configured -> monitor every group we're in
        return any(cid == chat_id for cid, _ in channels)

    async def handle_channel_post(self, message: Message) -> None:
        text = message.text or message.caption or ""
        title = message.chat.title or str(message.chat.id)
        link = self.msg_link(message.chat, message.message_id)
        await self._run_pipeline(message, text, title, link)

    async def handle_message(self, message: Message) -> None:
        if message.chat.type == "private":
            return  # private chats are command-only
        if not await self._source_allowed(message.chat.id):
            return
        text = message.text or message.caption or ""
        title = message.chat.title or str(message.chat.id)
        link = self.msg_link(message.chat, message.message_id)
        await self._run_pipeline(message, text, title, link)

    async def _run_pipeline(self, message: Message, text: str,
                            title: str, link: str) -> None:
        try:
            lead = await self.process_text(
                message.chat.id, message.message_id, title, text
            )
        except Exception:
            log.exception("pipeline failed for %s/%s",
                          message.chat.id, message.message_id)
            return
        if lead and lead.status != "junk" and lead.score >= config.MIN_LEAD_SCORE \
                and config.DESTINATION_CHAT_ID:
            card = self.format_card(lead, link)
            await message.bot.send_message(
                config.DESTINATION_CHAT_ID, card, parse_mode="HTML"
            )

    # -------------------------------------------------------------- helpers

    @staticmethod
    def is_admin(user_id: int | None) -> bool:
        return bool(user_id) and user_id in config.ADMIN_IDS

    # ------------------------------------------------------------- commands

    async def cmd_start(self, message: Message) -> None:
        await message.answer(
            "🏠 <b>RealState Lead AI</b>\n\n"
            "Add me as <b>admin</b> to your real-estate channels — I read every "
            "post, extract structured leads with AI, score them and forward the "
            "hot ones to your sales team.\n\n"
            "<b>Commands</b>\n"
            "/stats — pipeline statistics\n"
            "/leads [n] — top leads\n"
            "/mark &lt;chat_id&gt;.&lt;msg_id&gt; &lt;status&gt; — update a lead\n"
            "/export — export all leads as CSV\n"
            "/channels — list monitored sources\n"
            "/addchannel &lt;chat_id&gt; [title] — start monitoring a source\n"
            "/rmchannel &lt;chat_id&gt; — stop monitoring a source\n\n"
            f"Alert threshold: score ≥ <b>{config.MIN_LEAD_SCORE}</b> "
            "(MIN_LEAD_SCORE in .env)"
        )

    async def cmd_stats(self, message: Message) -> None:
        s = await self.store.stats()
        status_line = ", ".join(f"{k}: {v}" for k, v in s["by_status"].items()) or "—"
        deal_line = ", ".join(f"{k}: {v}" for k, v in s["by_deal"].items() if k) or "—"
        await message.answer(
            "📊 <b>Pipeline stats</b>\n"
            f"Total leads stored: <b>{s['total']}</b>\n"
            f"🔥 Hot leads (≥80): <b>{s['hot']}</b>\n"
            f"Statuses → {html.escape(status_line)}\n"
            f"Deal types → {html.escape(deal_line)}"
        )

    async def cmd_leads(self, message: Message) -> None:
        limit = 5
        parts = (message.text or "").split()
        if len(parts) > 1 and parts[1].isdigit():
            limit = min(int(parts[1]), 20)
        leads = await self.store.recent_leads(limit=limit)
        if not leads:
            await message.answer("No leads yet. Add me to channels and wait 👀")
            return
        lines = []
        for l in leads:
            price_str = (
                f"{l.price:,.0f} {l.currency or ''}".strip() if l.price else "—"
            )
            lines.append(
                f"{score_emoji(l.score)} <b>{l.score}/100</b> [{l.status}] "
                f"{html.escape(l.deal_type)} {html.escape(l.property_type)} — "
                f"{html.escape(l.city or '—')} · {price_str}\n"
                f"   └ {html.escape((l.summary or '')[:90])}"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")

    async def cmd_mark(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        parts = (message.text or "").split()
        if len(parts) < 3 or "." not in parts[1]:
            await message.answer(
                "Usage: /mark &lt;chat_id&gt;.&lt;msg_id&gt; new|contacted|won|lost|junk"
            )
            return
        try:
            cid_str, mid_str = parts[1].split(".", 1)
            chat_id, msg_id = int(cid_str), int(mid_str)
            status = parts[2].lower()
            assert status in VALID_STATUSES
        except Exception:
            await message.answer("Invalid arguments.")
            return
        ok = await self.store.set_status(chat_id, msg_id, status)
        await message.answer("✅ updated" if ok else "❌ lead not found")

    async def cmd_export(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        path = os.path.join(
            os.path.dirname(config.DB_PATH) or ".",
            f"leads_export_{random.randint(1000, 9999)}.csv",
        )
        count = await self.store.export_csv(path)
        await message.answer_document(
            FSInputFile(path), caption=f"📦 {count} leads exported"
        )
        os.remove(path)

    async def cmd_channels(self, message: Message) -> None:
        rows = await self.store.list_channels()
        static = ", ".join(map(str, config.SOURCE_CHANNELS)) or "none"
        dynamic = "\n".join(
            f"• <code>{cid}</code> {html.escape(title or '')}" for cid, title in rows
        ) or "none"
        await message.answer(
            f"📡 Env SOURCE_CHANNELS: {static}\nDB-monitored channels:\n{dynamic}"
        )

    async def cmd_addchannel(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        parts = (message.text or "").split(maxsplit=2)
        try:
            chat_id = int(parts[1])
        except (IndexError, ValueError):
            await message.answer("Usage: /addchannel &lt;chat_id&gt; [title]")
            return
        title = parts[2] if len(parts) > 2 else ""
        await self.store.add_channel(chat_id, title)
        await message.answer(f"✅ now monitoring <code>{chat_id}</code>")

    async def cmd_rmchannel(self, message: Message) -> None:
        if not self.is_admin(message.from_user and message.from_user.id):
            await message.answer("Admins only.")
            return
        parts = (message.text or "").split()
        try:
            chat_id = int(parts[1])
        except (IndexError, ValueError):
            await message.answer("Usage: /rmchannel &lt;chat_id&gt;")
            return
        ok = await self.store.remove_channel(chat_id)
        await message.answer("✅ removed" if ok else "not found")
