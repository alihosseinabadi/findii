"""FindII domain model — the structured result of AI extraction."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class Lead:
    source_chat_id: int                    # telegram chat id, or 0 for web sources
    message_id: int                        # telegram msg id, or url hash for web sources
    source: str = "telegram"               # telegram | avito | web
    source_title: str = ""                 # channel name / search label
    source_url: str = ""                   # listing URL for scraped sources
    raw_text: str = ""

    is_real_estate: bool = False
    deal_type: str = "unknown"             # sell | buy | rent | lease | unknown
    property_type: str = "unknown"         # apartment | house | villa | land | office | commercial | unknown
    city: str | None = None
    district: str | None = None
    price: float | None = None
    currency: str | None = None
    area_sqm: float | None = None
    rooms: int | None = None
    floor: str | None = None
    contact: str | None = None
    summary: str = ""
    urgency: str = "low"                   # high | medium | low

    score: int = 0
    score_reasons: list[str] = field(default_factory=list)
    status: str = "new"                    # new | contacted | qualified | negotiation | won | lost | junk
    content_hash: str = ""

    id: int | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Lead":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
