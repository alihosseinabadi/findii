import asyncio
import os
import sys
import tempfile

sys.path.insert(0, r"C:\Users\ali hosseinabadi\findii")

from core.db import LeadStore
from core.geomatch import GeoMatcher
from core.models import Lead
from core.scoring import score_lead
from scrapers import osm_places


async def main():
    tmp = tempfile.mkdtemp()
    # build inventory from the Moscow fixture
    inv_path = os.path.join(tmp, "Moscow.inventory.json")
    osm_places.build_inventory(
        r"C:\Users\ali hosseinabadi\findii\tests\fixture_moscow.osm",
        inv_path, "Moscow")
    geo = GeoMatcher(osm_places.load_inventory(inv_path))

    # simulate an ingested Telegram listing (regex-style extracted fields)
    lead = Lead(source_chat_id=-100123, message_id=42, source="telegram",
                source_title="msk arenda", is_real_estate=True,
                deal_type="rent", property_type="apartment", city="Moscow",
                price=80000, currency="RUB", rooms=2,
                contact="+7 916 000-00-00",
                raw_text="Сдаётся 2к квартира, Tverskaya 7, Moscow. +7 916 000-00-00",
                summary="2br rent")
    m = geo.match(lead.raw_text, "", lead.city or "")
    lead.geo_status, lead.latitude, lead.longitude = m["status"], m["lat"], m["lon"]
    lead.osm_ref, lead.matched_address = m["osm_ref"], m["matched"]
    lead.score, lead.score_reasons = score_lead(lead)

    store = LeadStore(os.path.join(tmp, "e2e.db"))
    saved, lid = await store.save_lead(lead)
    back = await store.get_lead(lid)
    assert saved and back.geo_status == "Confirmed", back
    assert back.latitude == 55.76 and back.osm_ref == "node/3", back
    assert any("map-verified" in r for r in back.score_reasons), back.score_reasons
    print("E2E OK: score", back.score, "| geo", back.geo_status,
          "| matched", back.matched_address, "| osm", back.osm_ref)

    # migration check: real pre-geo DB (full old schema, minus geo columns)
    import sqlite3
    oldp = os.path.join(tmp, "old.db")
    c = sqlite3.connect(oldp)
    c.execute("""CREATE TABLE leads (id INTEGER PRIMARY KEY, source TEXT,
              source_chat_id INTEGER, message_id INTEGER, source_title TEXT,
              source_url TEXT, raw_text TEXT, is_real_estate INTEGER,
              deal_type TEXT, property_type TEXT, city TEXT, district TEXT,
              price REAL, currency TEXT, area_sqm REAL, rooms INTEGER,
              floor TEXT, contact TEXT, summary TEXT, urgency TEXT,
              score INTEGER, score_reasons TEXT, status TEXT,
              content_hash TEXT UNIQUE, created_at TEXT)""")
    c.execute("INSERT INTO leads (source_chat_id, message_id, content_hash) VALUES (1, 1, 'abc')")
    c.commit()
    c.close()
    s2 = LeadStore(oldp)
    cols = {r[1] for r in s2._conn.execute("PRAGMA table_info(leads)")}
    assert {"geo_status", "latitude", "longitude", "osm_ref", "matched_address"} <= cols
    print("MIGRATION OK: old DB upgraded, existing data untouched")

asyncio.run(main())
