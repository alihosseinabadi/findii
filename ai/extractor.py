"""Lead extraction with a provider chain: Mistral -> Ollama -> regex fallback."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

import config
from ai.prompts import SYSTEM_PROMPT

log = logging.getLogger("realstate.ai")

PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{8,}\d")
PRICE_RE = re.compile(
    r"(\d[\d,\.\s]{2,12})\s*(usd|eur|eur|\$|€|£|toman|toman|rub|₽|aed|₺)", re.IGNORECASE
)


class LeadExtractor:
    """Extracts structured lead data from raw channel text."""

    def __init__(self, provider: str | None = None):
        self.provider = (provider or config.AI_PROVIDER or "auto").lower()
        self._mistral: Any = None
        if self.provider in ("auto", "mistral") and config.MISTRAL_API_KEY:
            try:
                from mistralai import Mistral  # mistralai >= 1.0
                self._mistral = Mistral(api_key=config.MISTRAL_API_KEY)
            except Exception as e:  # pragma: no cover
                log.warning("Mistral SDK unavailable (%s) — will try next provider", e)

    # ------------------------------------------------------------------ public

    async def extract(self, text: str) -> dict:
        """Return extraction dict for `text` using first working provider."""
        order = {
            "mistral": ["mistral"],
            "ollama": ["ollama"],
            "regex": ["regex"],
        }.get(self.provider, ["mistral", "ollama", "regex"])

        for name in order:
            try:
                if name == "regex":
                    return self._extract_regex(text)
                raw = await asyncio.wait_for(
                    self._ask_llm(name, text), timeout=45
                )
                data = self._parse_json(raw)
                if data is not None:
                    data["_provider"] = name
                    return data
            except Exception as e:
                log.warning("provider %s failed: %s", name, e)
        return self._extract_regex(text)

    # ----------------------------------------------------------------- llms

    async def _ask_llm(self, provider: str, text: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:4000]},
        ]
        if provider == "mistral":
            assert self._mistral is not None
            resp = await asyncio.to_thread(
                self._mistral.chat.complete,
                model=config.MISTRAL_MODEL,
                messages=messages,
                temperature=0.1,
            )
            return resp.choices[0].message.content or ""
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    f"{config.OLLAMA_BASE_URL}/api/chat",
                    json={
                        "model": config.OLLAMA_MODEL,
                        "messages": messages,
                        "stream": False,
                        "format": "json",
                    },
                )
                r.raise_for_status()
                return r.json()["message"]["content"]
        raise ValueError(f"unknown provider {provider}")

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        if not raw:
            return None
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        data["is_real_estate"] = bool(data.get("is_real_estate"))
        return data

    # ------------------------------------------------------- regex fallback

    @staticmethod
    def _deal_type(low: str) -> str:
        for d in ("rent", "lease", "buy"):
            if d in low:
                return d
        if "sale" in low or "sell" in low:
            return "sell"
        return "unknown"

    @staticmethod
    def _extract_regex(text: str) -> dict:
        """No-API fallback so the bot still catches obvious leads."""
        low = text.lower()
        keywords = ("sale", "sell", "rent", "lease", "apartment", "villa", "house",
                    "land", "m²", "sqm", "bedroom", "rooms", "property", "real estate")
        phone_m = PHONE_RE.search(text)
        price_m = PRICE_RE.search(text.replace("\u00a0", " "))
        price = None
        currency = None
        if price_m:
            try:
                price = float(price_m.group(1).replace(" ", "").replace(",", ""))
            except ValueError:
                price = None
            currency = price_m.group(2).upper().replace("$", "USD").replace("€", "EUR")
        return {
            "is_real_estate": any(k in low for k in keywords),
            "deal_type": LeadExtractor._deal_type(low),
            "property_type": next(
                (p for p in ("apartment", "villa", "land", "office", "commercial", "house")
                 if p in low), "unknown"),
            "city": None, "district": None,
            "price": price, "currency": currency,
            "area_sqm": None, "rooms": None, "floor": None,
            "contact": phone_m.group(0).strip() if phone_m else None,
            "summary": text.strip().replace("\n", " ")[:140],
            "urgency": "high" if any(w in low for w in ("urgent", "asap", "today")) else "low",
            "_provider": "regex",
        }
