"""FindII configuration — environment / .env driven."""
import os
import re

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> list[int]:
    return [int(x) for x in re.findall(r"-?\d+", raw or "")]


# ── Telegram ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM", "")
DESTINATION_CHAT_ID = int(os.getenv("DESTINATION_CHAT_ID") or 0)
SOURCE_CHANNELS: list[int] = _parse_ids(os.getenv("SOURCE_CHANNELS", ""))
ADMIN_IDS: list[int] = _parse_ids(os.getenv("ADMIN_IDS", ""))

# ── AI provider chain (auto = mistral → ollama → regex) ───────────────────
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# ── Scraping (Scrapling) ──────────────────────────────────────────────────
SCRAPER_ENGINE = os.getenv("SCRAPER_ENGINE", "auto")       # auto | http | stealth
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))
SCRAPE_MAX_PAGES = int(os.getenv("SCRAPE_MAX_PAGES", "2"))
SCRAPE_ENRICH_DETAILS = os.getenv("SCRAPE_ENRICH_DETAILS", "false").lower() == "true"
_delay = os.getenv("SCRAPER_DELAY_RANGE", "1.5-4.0").split("-")
SCRAPER_DELAY_RANGE = (float(_delay[0]), float(_delay[1]))

# ── Web CRM ───────────────────────────────────────────────────────────────
CRM_HOST = os.getenv("CRM_HOST", "0.0.0.0")
CRM_PORT = int(os.getenv("CRM_PORT", "8080"))
CRM_PASSWORD = os.getenv("CRM_PASSWORD", "")               # empty → auth disabled
CRM_PUBLIC_URL = os.getenv("CRM_PUBLIC_URL", "")           # e.g. https://findii.example.com

# ── OSM map layer (offline grounding: type a city → map activates) ──────
OSM_CITY = os.getenv("OSM_CITY", "")                      # e.g. Moscow — or set live via /city
OSM_CACHE_DIR = os.getenv("OSM_CACHE_DIR", "data/osm")
OSM_PBF_PATH = os.getenv("OSM_PBF_PATH", "")              # manual .osm.pbf overrides auto-download

# ── Lead-engine exports (OSM business-lead Excel/CSV) ───────────────────
LEADS_OUT_DIR = os.getenv("LEADS_OUT_DIR", "data/exports")

# ── Tuning ────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "data/findii.db")
MIN_LEAD_SCORE = int(os.getenv("MIN_LEAD_SCORE", "50"))
MIN_TEXT_LEN = int(os.getenv("MIN_TEXT_LEN", "25"))

STATUSES = ("new", "contacted", "qualified", "negotiation", "won", "lost", "junk")
