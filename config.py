"""RealState Lead AI — configuration loaded from environment / .env"""
import os
import re

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> list[int]:
    return [int(x) for x in re.findall(r"-?\d+", raw or "")]


TELEGRAM_TOKEN = os.getenv("TELEGRAM", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

# AI provider chain: "auto" = mistral -> ollama -> regex
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto")  # auto | mistral | ollama | regex
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# Where qualified leads get delivered (chat id of your team channel/group)
DESTINATION_CHAT_ID = int(os.getenv("DESTINATION_CHAT_ID") or 0)

# Comma separated chat ids the bot should monitor (channels/groups)
SOURCE_CHANNELS: list[int] = _parse_ids(os.getenv("SOURCE_CHANNELS", ""))

ADMIN_IDS: list[int] = _parse_ids(os.getenv("ADMIN_IDS", ""))

DB_PATH = os.getenv("DB_PATH", "data/realstate.db")
MIN_LEAD_SCORE = int(os.getenv("MIN_LEAD_SCORE", "50"))
MIN_TEXT_LEN = int(os.getenv("MIN_TEXT_LEN", "25"))
