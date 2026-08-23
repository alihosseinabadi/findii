"""SQLite storage for leads and monitored channels."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import os
import sqlite3
from typing import Iterable

from core.models import Lead

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    source_chat_id  INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    source_title    TEXT DEFAULT '',
    raw_text        TEXT DEFAULT '',
    is_real_estate  INTEGER DEFAULT 0,
    deal_type       TEXT,
    property_type   TEXT,
    city            TEXT,
    district        TEXT,
    price           REAL,
    currency        TEXT,
    area_sqm        REAL,
    rooms           INTEGER,
    floor           TEXT,
    contact         TEXT,
    summary         TEXT,
    urgency         TEXT,
    score           INTEGER DEFAULT 0,
    score_reasons   TEXT DEFAULT '',
    status          TEXT DEFAULT 'new',
    content_hash    TEXT,
    created_at      TEXT,
    PRIMARY KEY (source_chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS channels (
    chat_id INTEGER PRIMARY KEY,
    title   TEXT,
    added_at TEXT
);
"""


class LeadStore:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = asyncio.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(" ".join(text.split()).lower().encode()).hexdigest()[:16]

    async def save_lead(self, lead: Lead) -> bool:
        """Insert lead. Returns False if already stored (dedupe)."""
        async with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM leads WHERE source_chat_id=? AND message_id=?",
                (lead.source_chat_id, lead.message_id),
            )
            if cur.fetchone():
                return False
            d = lead.to_dict()
            self._conn.execute(
                """INSERT INTO leads VALUES
                (:source_chat_id,:message_id,:source_title,:raw_text,:is_real_estate,
                 :deal_type,:property_type,:city,:district,:price,:currency,:area_sqm,
                 :rooms,:floor,:contact,:summary,:urgency,:score,:score_reasons_str,
                 :status,:content_hash,:created_at)""",
                {**d, "is_real_estate": int(lead.is_real_estate),
                 "score_reasons_str": ", ".join(lead.score_reasons)},
            )
            self._conn.commit()
            return True

    async def is_duplicate_content(self, content_hash: str) -> bool:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM leads WHERE content_hash=? LIMIT 1", (content_hash,)
            )
            return bool(cur.fetchone())

    async def recent_leads(self, limit: int = 10, min_score: int = 0) -> list[Lead]:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM leads WHERE score >= ? ORDER BY score DESC, created_at DESC LIMIT ?",
                (min_score, limit),
            )
            cols = [c[0] for c in cur.description]
            return [self._row_to_lead(dict(zip(cols, row))) for row in cur.fetchall()]

    async def set_status(self, source_chat_id: int, message_id: int, status: str) -> bool:
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE leads SET status=? WHERE source_chat_id=? AND message_id=?",
                (status, source_chat_id, message_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def stats(self) -> dict:
        async with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            hot = self._conn.execute("SELECT COUNT(*) FROM leads WHERE score >= 80").fetchone()[0]
            by_status = dict(
                self._conn.execute(
                    "SELECT status, COUNT(*) FROM leads GROUP BY status"
                ).fetchall()
            )
            by_deal = dict(
                self._conn.execute(
                    "SELECT deal_type, COUNT(*) FROM leads GROUP BY deal_type"
                ).fetchall()
            )
            return {"total": total, "hot": hot, "by_status": by_status, "by_deal": by_deal}

    async def export_csv(self, path: str) -> int:
        async with self._lock:
            cur = self._conn.execute("SELECT * FROM leads ORDER BY created_at DESC")
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                writer.writerows(rows)
            return len(rows)

    async def add_channel(self, chat_id: int, title: str = "") -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO channels (chat_id, title, added_at) VALUES (?,?,datetime('now'))",
                (chat_id, title),
            )
            self._conn.commit()

    async def remove_channel(self, chat_id: int) -> bool:
        async with self._lock:
            cur = self._conn.execute("DELETE FROM channels WHERE chat_id=?", (chat_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def list_channels(self) -> list[tuple[int, str]]:
        async with self._lock:
            cur = self._conn.execute("SELECT chat_id, title FROM channels")
            return cur.fetchall()

    @staticmethod
    def _row_to_lead(row: dict) -> Lead:
        row["is_real_estate"] = bool(row.get("is_real_estate"))
        reasons = [r.strip() for r in (row.pop("score_reasons", "") or "").split(",") if r.strip()]
        known = {k for k in Lead.__dataclass_fields__ if k in row}
        data = {k: row[k] for k in known}
        data["score_reasons"] = reasons
        return Lead(**data)
