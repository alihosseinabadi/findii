"""Avito.ru real-estate search scraper built on Scrapling.

Search URLs are plain Avito URLs (the same ones you'd open in a browser), e.g.
    https://www.avito.ru/moskva/kvartiry/prodam-2-komnatnye
    https://www.avito.ru/sochi/kvartiry/sdam/na-dlitelnyy-srok?pricemax=60000

FindII paginates them, parses listing cards and hands each ad to the AI
pipeline. Selectors are centralised here; Avito markup changes are handled by
updating SELECTORS only — no other code touches the page structure.
"""
from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from scrapers.base import BaseScraper

log = logging.getLogger("findii.avito")


@dataclass
class Ad:
    title: str = ""
    price_text: str = ""
    price: float | None = None
    currency: str = "RUB"
    address: str = ""
    url: str = ""
    description: str = ""
    params: list[str] = field(default_factory=list)

    def as_lead_key(self) -> int:
        """Stable pseudo message_id derived from the listing URL."""
        return zlib.crc32(self.url.encode())

    def to_prompt_text(self) -> str:
        parts = [self.title]
        if self.price_text:
            parts.append(f"Цена: {self.price_text}")
        if self.address:
            parts.append(f"Адрес: {self.address}")
        if self.params:
            parts.append(" · ".join(self.params))
        if self.description:
            parts.append(self.description[:1500])
        return "\n".join(p for p in parts if p)


class AvitoScraper(BaseScraper):
    BASE = "https://www.avito.ru"

    # Centralised selectors — adjust here when Avito changes its markup.
    SELECTORS = {
        "item": ['[data-marker="item"]', "div[data-marker='catalog-serp'] article"],
        "title": ['a[data-marker="item-title"]', 'h3[itemprop="name"] a', 'a[href*="/kvartir"]'],
        "price_meta": ['meta[itemprop="price"]'],
        "price_text": ['[data-marker="item-price"]', 'span[data-marker="item-price"]',
                       'p[data-marker="item-price"]', '[class*="price-text"]'],
        "address": ['[data-marker="specific-address"]', '[data-marker="item-address"]',
                    '[class*="geo-address"]'],
        "params": ['li[data-marker="item-specific-params"]'],
        "description": ['div[data-marker="item-description"]', '[itemprop="description"]'],
        "item_params_block": ['ul[data-marker="item-view/item-params"]', '[class*="item-params"]'],
    }

    PRICE_RE = re.compile(r"([\d\s\u00a0.,]+)\s*(₽|руб|rub|usd|\$|€|eur|тг|tenge)?", re.IGNORECASE)

    # ------------------------------------------------------------- searching

    def build_page_url(self, url: str, page: int) -> str:
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        if page > 1:
            q["p"] = [str(page)]
        return urlunparse(parsed._replace(query=urlencode(q, doseq=True)))

    async def search(self, url: str, max_pages: int = 2) -> list[Ad]:
        ads: dict[str, Ad] = {}
        for page in range(1, max_pages + 1):
            page_url = self.build_page_url(url, page)
            try:
                resp = await self.fetch(page_url)
            except RuntimeError as e:
                log.error("search page failed: %s", e)
                break
            found = self.parse_search(resp.body.decode(errors="ignore")
                                      if isinstance(resp.body, bytes) else str(resp.body))
            new = [a for a in found if a.url not in ads]
            log.info("%s p%d → %d ads (%d new)", page_url, page, len(found), len(new))
            for ad in new:
                ads[ad.url] = ad
            if not new or page == max_pages:
                break
            await self._polite_pause()
        return list(ads.values())

    async def enrich(self, ad: Ad) -> Ad:
        """Fetch detail page for full description + item params."""
        try:
            resp = await self.fetch(ad.url)
            html = resp.body.decode(errors="ignore") if isinstance(resp.body, bytes) else str(resp.body)
            page = self.parse(html)
            desc = self.first_text(page, self.SELECTORS["description"])
            if desc:
                ad.description = desc
            block = page.css(self.SELECTORS["item_params_block"][0]) \
                if page.css(self.SELECTORS["item_params_block"]) else None
            if block:
                ad.params = [
                    " ".join(str(li.text).split())
                    for li in block[0].css("li")[:12] if li.text
                ]
        except Exception as e:  # noqa: BLE001
            log.warning("enrich failed for %s: %s", ad.url, e)
        return ad

    async def _polite_pause(self) -> None:
        import asyncio
        import random
        await asyncio.sleep(random.uniform(2.0, 5.0))

    # -------------------------------------------------------------- parsing

    def parse_search(self, html: str) -> list[Ad]:
        page = self.parse(html)
        ads: list[Ad] = []
        for el in page.css(self.SELECTORS["item"][0]) or \
                  page.css(self.SELECTORS["item"][1]):
            href = self.first_attr(el, self.SELECTORS["title"], "href")
            if not href:
                continue
            ad = Ad(
                title=self.first_text(el, self.SELECTORS["title"]),
                price_text=self._extract_price_text(el),
                address=self.first_text(el, self.SELECTORS["address"]),
                url=urljoin(self.BASE, href),
            )
            ad.price, ad.currency = self.parse_price(ad.price_text)
            if ad.title or ad.url:
                ads.append(ad)
        return ads

    def _extract_price_text(self, el) -> str:
        meta = self.first_attr(el, self.SELECTORS["price_meta"], "content")
        if meta and meta.strip() and meta != "0":
            return f"{meta} ₽"
        return self.first_text(el, self.SELECTORS["price_text"])

    @classmethod
    def parse_price(cls, text: str) -> tuple[float | None, str]:
        """Returns (value, currency) — (None, 'RUB') when unparseable."""
        if not text:
            return None, "RUB"
        m = cls.PRICE_RE.search(text.replace("\u2009", "").replace("\u00a0", ""))
        if not m:
            return None, "RUB"
        raw = m.group(1).strip(" .,\u00a0 ")
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return None, "RUB"
        try:
            value = float(digits)
        except ValueError:
            return None, "RUB"
        cur = (m.group(2) or "₽").lower()
        if any(c in cur for c in ("usd", "$")):
            currency = "USD"
        elif any(c in cur for c in ("eur", "€")):
            currency = "EUR"
        elif any(c in cur for c in ("тг", "tenge")):
            currency = "KZT"
        else:
            currency = "RUB"
        return value, currency

    @staticmethod
    def deal_hint(url: str) -> str | None:
        """Deal-type signal from the search URL path (prodam/sdam/...)."""
        low = url.lower()
        if "/sdam" in low or "arenda" in low or "снять" in low:
            return "rent"
        if "/prodam" in low or "prodazha" in low or "купить" in low or "kupit" in low:
            return "sell"
        if "/kuplyu" in low:
            return "buy"
        return None

    @staticmethod
    def normalize_url(url: str) -> str:
        """Strip tracking params so /addsearch dedupes cleanly."""
        parsed = urlparse(url.strip())
        keep = {k: v for k, v in parse_qs(parsed.query).items()
                if k in ("priceMin", "priceMax", "pricemin", "pricemax")}
        path = parsed.path.rstrip("/")
        return urlunparse(parsed._replace(query=urlencode(keep, doseq=True),
                                          netloc=parsed.netloc or "www.avito.ru",
                                          path=path))
