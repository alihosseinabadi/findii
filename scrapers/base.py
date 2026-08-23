"""Scrapling-powered fetch layer with engine fallback.

Engine chain per request:
    1. "http"     — Scrapling Fetcher (TLS-impersonated, fast)
    2. "stealth"  — Scrapling StealthyFetcher (headless stealth browser,
                    bypasses Cloudflare-class protections)

Parsing is always done through `scrapling.parser.Selector`, so tests can run
fully offline against fixture HTML.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

log = logging.getLogger("findii.scraper")


class BaseScraper:
    ENGINE_HTTP = "http"
    ENGINE_STEALTH = "stealth"

    def __init__(self, engine: str = "auto", delay_range: tuple[float, float] = (1.5, 4.0)):
        self.engine = engine
        self.delay_range = delay_range
        self._stealth_cls: Any | None = None

    # ------------------------------------------------------------- fetching

    async def fetch(self, url: str) -> Any:
        """Fetch URL -> Scrapling response. Falls back http -> stealth on block."""
        engines = [self.ENGINE_STEALTH] if self.engine == self.ENGINE_STEALTH else [
            self.ENGINE_HTTP]
        if self.engine == "auto":
            engines = [self.ENGINE_HTTP, self.ENGINE_STEALTH]

        last_err: Exception | None = None
        for i, engine in enumerate(engines):
            await asyncio.sleep(random.uniform(*self.delay_range))
            try:
                resp = await self._fetch_with(engine, url)
                status = getattr(resp, "status", 200)
                if status in (403, 429) and i < len(engines) - 1:
                    log.warning("%s blocked (%s) via %s — escalating", url, status, engine)
                    continue
                return resp
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("fetch via %s failed: %s", engine, e)
        raise RuntimeError(f"all fetch engines failed for {url}: {last_err}")

    async def _fetch_with(self, engine: str, url: str) -> Any:
        from scrapling.fetchers import Fetcher  # cheap import once installed

        if engine == self.ENGINE_HTTP:
            return await asyncio.to_thread(
                Fetcher.get, url, impersonate="chrome", timeout=30,
            )
        if engine == self.ENGINE_STEALTH:
            from scrapling.fetchers import StealthyFetcher
            return await asyncio.to_thread(
                StealthyFetcher.fetch, url, headless=True,
            )
        raise ValueError(f"unknown engine {engine}")

    # ------------------------------------------------------------- parsing

    @staticmethod
    def parse(html: str) -> Any:
        """Offline-parse HTML with Scrapling's parser (used by tests too)."""
        from scrapling.parser import Selector
        return Selector(html)

    @staticmethod
    def first_text(el: Any, selectors: list[str]) -> str:
        for sel in selectors:
            try:
                found = el.css(sel)
            except Exception:  # noqa: BLE001
                continue
            if found:
                txt = found[0].text
                if txt and str(txt).strip():
                    return " ".join(str(txt).split())
                content = found[0].attrib.get("content")
                if content:
                    return content.strip()
        return ""

    @staticmethod
    def first_attr(el: Any, selectors: list[str], attr: str) -> str:
        for sel in selectors:
            try:
                found = el.css(sel)
            except Exception:  # noqa: BLE001
                continue
            if found and found[0].attrib.get(attr):
                return str(found[0].attrib[attr]).strip()
        return ""
