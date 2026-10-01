"""FindII storage: leads, saved searches, CRM notes. Single SQLite file."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import os
import sqlite3
from datetime import datetime, timezone

from core.models import Lead

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT DEFAULT 'telegram',
    source_chat_id  INTEGER NOT NULL DEFAULT 0,
    message_id      INTEGER NOT NULL DEFAULT 0,
    source_title    TEXT DEFAULT '',
    source_url      TEXT DEFAULT '',
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
    content_hash    TEXT UNIQUE,
    created_at      TEXT,
    geo_status      TEXT DEFAULT 'unknown',
    latitude        REAL,
    longitude       REAL,
    osm_ref         TEXT DEFAULT '',
    matched_address TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_msg ON leads(source_chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score);

CREATE TABLE IF NOT EXISTS searches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT NOT NULL UNIQUE,
    label      TEXT DEFAULT '',
    enabled    INTEGER DEFAULT 1,
    last_run   TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    author     TEXT DEFAULT 'crm',
    body       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LeadStore:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = asyncio.Lock()
        self._conn.executescript(SCHEMA)
        # migrate pre-geo databases: add map-grounding columns if missing
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(leads)")}
        for col, ddl in (("geo_status", "TEXT DEFAULT 'unknown'"),
                         ("latitude", "REAL"), ("longitude", "REAL"),
                         ("osm_ref", "TEXT DEFAULT ''"),
                         ("matched_address", "TEXT DEFAULT ''")):
            if col not in existing:
                self._conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")
        self._conn.commit()

    # ------------------------------------------------------------ helpers

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(" ".join(text.split()).lower().encode()).hexdigest()[:16]

    @staticmethod
    def _lead_from_row(row) -> Lead:
        d = dict(row)
        d["is_real_estate"] = bool(d.get("is_real_estate"))
        reasons_raw = d.pop("score_reasons", "") or ""
        lead = Lead(
            **{k: v for k, v in d.items() if k in Lead.__dataclass_fields__}
        )
        lead.score_reasons = [r.strip() for r in reasons_raw.split(",") if r.strip()]
        return lead

    # -------------------------------------------------------------- leads

    async def save_lead(self, lead: Lead) -> tuple[bool, int | None]:
        """Insert lead. Returns (saved?, db id). False if duplicate."""
        async with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM leads WHERE source_chat_id=? AND message_id=?",
                (lead.source_chat_id, lead.message_id),
            )
            row = cur.fetchone()
            if row:
                return False, row["id"]
            try:
                d = lead.to_dict()
                d.pop("id", None)
                cur = self._conn.execute(
                    """INSERT INTO leads
                    (source, source_chat_id, message_id, source_title, source_url,
                     raw_text, is_real_estate, deal_type, property_type, city, district,
                     price, currency, area_sqm, rooms, floor, contact, summary, urgency,
                     score, score_reasons, status, content_hash, created_at,
                     geo_status, latitude, longitude, osm_ref, matched_address)
                    VALUES
                    (:source,:source_chat_id,:message_id,:source_title,:source_url,
                     :raw_text,:is_real_estate,:deal_type,:property_type,:city,:district,
                     :price,:currency,:area_sqm,:rooms,:floor,:contact,:summary,:urgency,
                     :score,:score_reasons_str,:status,:content_hash,:created_at,
                     :geo_status,:latitude,:longitude,:osm_ref,:matched_address)""",
                    {**d, "is_real_estate": int(lead.is_real_estate),
                     "score_reasons_str": ", ".join(lead.score_reasons)},
                )
                self._conn.commit()
                return True, cur.lastrowid
            except sqlite3.IntegrityError:
                return False, None

    async def get_lead(self, lead_id: int) -> Lead | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM leads WHERE id=?", (lead_id,)
            ).fetchone()
            return self._lead_from_row(row) if row else None

    async def list_leads(self, status: str | None = None, min_score: int = 0,
                         query: str = "", limit: int = 200) -> list[Lead]:
        sql = "SELECT * FROM leads WHERE score >= ?"
        params: list = [min_score]
        if status:
            sql += " AND status=?"
            params.append(status)
        if query:
            sql += " AND (raw_text LIKE ? OR city LIKE ? OR district LIKE ? OR summary LIKE ? OR source_url LIKE ?)"
            like = f"%{query}%"
            params += [like] * 5
        sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
        params.append(limit)
        async with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [self._lead_from_row(r) for r in rows]

    async def recent_leads(self, limit: int = 10, min_score: int = 0) -> list[Lead]:
        return await self.list_leads(min_score=min_score, limit=limit)

    async def set_status(self, key_a: int, key_b: int, status: str) -> bool:
        """Accept either (lead_db_id, ignored) or (source_chat_id, message_id)."""
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE leads SET status=? WHERE id=?", (status, key_a)
            )
            if cur.rowcount == 0:
                cur = self._conn.execute(
                    "UPDATE leads SET status=? WHERE source_chat_id=? AND message_id=?",
                    (status, key_a, key_b),
                )
            self._conn.commit()
            return cur.rowcount > 0

    async def delete_lead(self, lead_id: int) -> bool:
        async with self._lock:
            cur = self._conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def is_duplicate_content(self, content_hash: str) -> bool:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM leads WHERE content_hash=? LIMIT 1", (content_hash,)
            )
            return bool(cur.fetchone())

    async def stats(self) -> dict:
        async with self._lock:
            total = self._conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
            hot = self._conn.execute(
                "SELECT COUNT(*) c FROM leads WHERE score >= 80").fetchone()["c"]
            today = self._conn.execute(
                "SELECT COUNT(*) c FROM leads WHERE created_at >= date('now')").fetchone()["c"]
            by_status = dict(self._conn.execute(
                "SELECT status s, COUNT(*) c FROM leads GROUP BY status").fetchall())
            by_deal = dict((r["s"], r["c"]) for r in self._conn.execute(
                "SELECT deal_type s, COUNT(*) c FROM leads GROUP BY deal_type").fetchall())
            by_source = dict(self._conn.execute(
                "SELECT source s, COUNT(*) c FROM leads GROUP BY source").fetchall())
            won = by_status.get("won", 0)
            contacted = sum(v for k, v in by_status.items() if k != "new")
            return {"total": total, "hot": hot, "today": today,
                    "by_status": by_status, "by_deal": by_deal,
                    "by_source": by_source,
                    "conversion": round(100 * won / contacted, 1) if contacted else 0.0}

    async def export_csv(self, path: str) -> int:
        async with self._lock:
            cur = self._conn.execute("SELECT * FROM leads ORDER BY created_at DESC")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerows(rows)
            return len(rows)

    # ------------------------------------------------------------ searches

    async def add_search(self, url: str, label: str = "") -> tuple[bool, int]:
        async with self._lock:
            row = self._conn.execute(
                "SELECT id FROM searches WHERE url=?", (url,)).fetchone()
            if row:
                return False, row["id"]
            cur = self._conn.execute(
                "INSERT INTO searches (url, label) VALUES (?,?)",
                (url, label))
            self._conn.commit()
            return True, cur.lastrowid

    async def list_searches(self, enabled_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM searches"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        async with self._lock:
            return [dict(r) for r in self._conn.execute(sql).fetchall()]

    async def del_search(self, search_id: int) -> bool:
        async with self._lock:
            cur = self._conn.execute("DELETE FROM searches WHERE id=?", (search_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def toggle_search(self, search_id: int) -> bool:
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE searches SET enabled = 1 - enabled WHERE id=?", (search_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def touch_search(self, search_id: int) -> None:
        async with self._lock:
            self._conn.execute(
                "UPDATE searches SET last_run=? WHERE id=?", (utcnow(), search_id))
            self._conn.commit()

    # --------------------------------------------------------------- notes

    async def add_note(self, lead_id: int, body: str, author: str = "crm") -> int:
        async with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notes (lead_id, author, body) VALUES (?,?,?)",
                (lead_id, author, body))
            self._conn.commit()
            return cur.lastrowid or 0

    async def list_notes(self, lead_id: int) -> list[dict]:
        async with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notes WHERE lead_id=? ORDER BY id DESC",
                (lead_id,)).fetchall()
            return [dict(r) for r in rows]
