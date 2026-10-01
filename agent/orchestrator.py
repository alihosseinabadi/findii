"""FindII agent orchestrator.

One pipeline for every source:
    ingest text → AI extraction → scoring → dedupe → persist → notify

Sources:
    • Telegram channels/groups (bot admin)
    • Avito searches saved via /addsearch (Scrapling-powered)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Iterable

import config
from ai.extractor import LeadExtractor
from core.db import LeadStore
from core.geomatch import GeoMatcher
from core.models import Lead
from core.scoring import score_lead
from scrapers.avito import Ad, AvitoScraper
from scrapers import osm_places

log = logging.getLogger("findii.agent")


class FindIIAgent:
    def __init__(self, extractor: LeadExtractor, store: LeadStore):
        self.ai = extractor
        self.store = store
        self.avito = AvitoScraper(
            engine=config.SCRAPER_ENGINE,
            delay_range=config.SCRAPER_DELAY_RANGE,
        )
        self.geo = GeoMatcher()
        self.geo_city = ""
        if config.OSM_CITY:
            try:
                self.set_city(config.OSM_CITY)
            except Exception as e:  # noqa: BLE001
                log.warning("OSM city '%s' not ready: %s", config.OSM_CITY, e)
        self._notify: Callable[[Lead], Awaitable[None]] | None = None

    def on_lead(self, callback: Callable[[Lead], Awaitable[None]]) -> None:
        """Register async callback fired for every new qualified lead."""
        self._notify = callback

    # -------------------------------------------------------- map grounding

    def set_city(self, city: str) -> dict:
        """User typed a city → ensure its OSM inventory, activate matcher.

        One-time extract download per city (cached in OSM_CACHE_DIR);
        every later match is offline.
        """
        stats = osm_places.ensure_city(city, config.OSM_CACHE_DIR,
                                       manual_pbf=config.OSM_PBF_PATH)
        inv_path = osm_places.inventory_path(stats["city"], config.OSM_CACHE_DIR)
        self.geo = GeoMatcher(osm_places.load_inventory(inv_path))
        self.geo_city = stats["city"]
        log.info("map layer active: %s", stats)
        return stats

    def geomatch(self, lead: Lead) -> Lead:
        """Pin a lead to a real OSM building. Never invents coordinates."""
        if not self.geo.ready:
            lead.geo_status = "unknown"
            return lead
        m = self.geo.match(lead.raw_text, lead.district or "", lead.city or "")
        lead.geo_status = m["status"]
        lead.latitude = m["lat"]
        lead.longitude = m["lon"]
        lead.osm_ref = m["osm_ref"]
        lead.matched_address = m["matched"]
        return lead

    # ------------------------------------------------------------ ingestion

    async def ingest_text(self, chat_id: int, message_id: int, title: str,
                          text: str, source: str = "telegram",
                          source_url: str = "") -> Lead | None:
        """Full pipeline for one raw text. Returns the stored Lead or None."""
        if len(text) < config.MIN_TEXT_LEN:
            return None

        lead = Lead(
            source_chat_id=chat_id,
            message_id=message_id,
            source=source,
            source_title=title[:120],
            source_url=source_url[:500],
            raw_text=text[:2500],
            content_hash=self.store.content_hash(text),
        )

        data = await self.ai.extract(text)
        if not data.get("is_real_estate"):
            log.info("non-RE content skipped %s/%s", chat_id, message_id)
            return None

        for key in ("deal_type", "property_type", "city", "district", "price",
                    "currency", "area_sqm", "rooms", "floor", "contact",
                    "summary", "urgency"):
            value = data.get(key)
            if value is not None:
                setattr(lead, key, value)
        lead.is_real_estate = True

        lead = self.geomatch(lead)
        lead.score, lead.score_reasons = score_lead(lead)

        if await self.store.is_duplicate_content(lead.content_hash):
            lead.status = "junk"
            lead.score_reasons.append("duplicate content")

        saved, _id = await self.store.save_lead(lead)
        if not saved:
            return None
        if lead.status != "junk" and lead.score >= config.MIN_LEAD_SCORE \
                and self._notify:
            try:
                await self._notify(lead)
            except Exception:  # noqa: BLE001
                log.exception("notify callback failed")
        return lead

    # -------------------------------------------------------- avito scraping

    async def run_search(self, url: str, label: str = "",
                         enrich_details: bool = False) -> dict:
        ads = await self.avito.search(url, max_pages=config.SCRAPE_MAX_PAGES)
        hint = self.avito.deal_hint(url)
        hint_line = f"\nТип сделки (deal type): {hint}" if hint else ""
        new_leads = 0
        qualified = 0
        for ad in ads:
            if enrich_details and config.SCRAPE_ENRICH_DETAILS:
                ad = await self.avito.enrich(ad)
            lead = await self.ingest_text(
                chat_id=0,
                message_id=ad.as_lead_key(),
                title=label or "Avito search",
                text=ad.to_prompt_text() + hint_line,
                source="avito",
                source_url=ad.url,
            )
            if lead:
                new_leads += 1
                if lead.score >= config.MIN_LEAD_SCORE:
                    qualified += 1
        return {"url": url, "label": label, "ads": len(ads),
                "new": new_leads, "qualified": qualified}

    async def run_all_searches(self) -> dict:
        results = []
        searches = await self.store.list_searches(enabled_only=True)
        log.info("running %d saved searches", len(searches))
        for s in searches:
            try:
                res = await self.run_search(s["url"], s.get("label") or "")
                res["id"] = s["id"]
                results.append(res)
                await self.store.touch_search(s["id"])
            except Exception as e:  # noqa: BLE001
                log.error("search %s failed: %s", s["url"], e)
                results.append({"url": s["url"], "label": s.get("label", ""),
                                "error": str(e)})
        summary = {
            "searches": len(results),
            "ads": sum(r.get("ads", 0) for r in results),
            "new": sum(r.get("new", 0) for r in results),
            "qualified": sum(r.get("qualified", 0) for r in results),
            "details": results,
        }
        log.info("scrape cycle done: %s", {k: v for k, v in summary.items()
                                           if k != "details"})
        return summary


class ScrapeScheduler:
    """Periodic scrape loop — runs alongside the Telegram bot."""

    def __init__(self, agent: FindIIAgent):
        self.agent = agent

    async def run_forever(self) -> None:
        interval = max(config.SCRAPE_INTERVAL_MINUTES, 1) * 60
        while True:
            try:
                await self.agent.run_all_searches()
            except Exception:  # noqa: BLE001
                log.exception("scheduler cycle failed")
            await asyncio.sleep(interval)
