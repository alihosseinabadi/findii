"""Rule-based lead scoring on top of AI extraction results."""
from __future__ import annotations

import re

from core.models import Lead

URGENCY_WORDS = (
    "urgent", "asap", "today", "immediately", "cash", "deadline",
    "hot", "last chance", "moving out", "leaving",
)
PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{8,}\d")


def score_lead(lead: Lead) -> tuple[int, list[str]]:
    """Return (score 0-100, human readable reasons)."""
    score = 0
    reasons: list[str] = []

    if not lead.is_real_estate:
        return 0, ["not real estate content"]

    score += 15
    reasons.append("real estate listing")

    if lead.deal_type != "unknown":
        score += 10
        reasons.append(f"deal type: {lead.deal_type}")

    if lead.property_type != "unknown":
        score += 10
        reasons.append(f"property: {lead.property_type}")

    if lead.contact and PHONE_RE.search(lead.contact):
        score += 30
        reasons.append("has phone contact")
    elif lead.contact:
        score += 10
        reasons.append("has contact info")

    if lead.price:
        score += 15
        reasons.append("price specified")

    if lead.city or lead.district:
        score += 10
        reasons.append("location specified")

    if lead.area_sqm:
        score += 5
        reasons.append("area specified")

    if lead.rooms:
        score += 5
        reasons.append("rooms specified")

    if any(w in (lead.raw_text or "").lower() for w in URGENCY_WORDS):
        score += 10
        reasons.append("urgency signals in text")

    if lead.urgency == "high":
        score += 10
        reasons.append("AI flagged high urgency")

    return min(score, 100), reasons
