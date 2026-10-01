"""Offline tests for the OSM map layer: inventory build + geo-matcher + scoring.

Runs with plain python (no pytest needed):
    python tests/test_geomatch.py
No network. Uses tests/fixture_moscow.osm only.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import osm_places
from core.geomatch import GeoMatcher, canon
from core.models import Lead
from core.scoring import score_lead

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_moscow.osm")


def build_test_inventory():
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "Moscow.inventory.json")
    stats = osm_places.build_inventory(FIXTURE, out, "Moscow")
    assert stats["buildings"] == 5, stats
    assert stats["streets"] == 3, stats  # Tverskaya, Arbat, Nevsky
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def test_confirmed_exact_address():
    m = GeoMatcher(build_test_inventory())
    r = m.match("Продается 2-комн квартира, Tverskaya 1, Moscow. Срочно!")
    assert r["status"] == "Confirmed", r
    assert r["lat"] == 55.7558 and r["lon"] == 37.6173, r
    assert r["osm_ref"] == "node/1", r


def test_confirmed_second_building():
    m = GeoMatcher(build_test_inventory())
    r = m.match("rent office Arbat 10, 50 sqm")
    assert r["status"] == "Confirmed", r
    assert r["osm_ref"] == "node/2", r


def test_probable_street_only():
    m = GeoMatcher(build_test_inventory())
    r = m.match("Сдается комната на Арбате")  # street known, but latin index...
    # 'Арбат' transliteration won't hit latin 'Arbat'; use latin text:
    r = m.match("room for rent on Arbat street, Moscow")
    assert r["status"] == "Probable", r
    assert r["lat"] is not None and r["lon"] is not None, r


def test_uncertain_district_only():
    m = GeoMatcher(build_test_inventory())
    r = m.match("2br apartment, great district", district="Tverskoy", city="Moscow")
    assert r["status"] == "Uncertain", r
    assert r["lat"] is None, r  # never invent coordinates


def test_uncertain_no_match():
    m = GeoMatcher(build_test_inventory())
    r = m.match("villa for sale, nowhere special")
    assert r["status"] == "Uncertain", r
    assert r["lat"] is None, r


def test_empty_inventory_safe():
    m = GeoMatcher()
    assert not m.ready
    r = m.match("Tverskaya 1, Moscow")
    assert r["status"] == "Uncertain" and r["lat"] is None, r


def test_scoring_bonus():
    base = Lead(source_chat_id=1, message_id=1, is_real_estate=True,
                deal_type="sell", property_type="apartment",
                price=10_000_000, city="Moscow")
    s0, _ = score_lead(base)
    assert s0 < 90, s0  # headroom below the 100 cap
    base.geo_status = "Confirmed"
    s1, reasons = score_lead(base)
    assert s1 == s0 + 10, (s0, s1)
    assert any("map-verified" in r for r in reasons), reasons
    base.geo_status = "Probable"
    s2, _ = score_lead(base)
    assert s2 == s0 + 5, (s0, s2)


def test_city_slugs():
    assert osm_places.city_slugs("Moscow") == ["Moscow"]
    assert "Moscow" in osm_places.city_slugs("Moskva")


def test_canon_transliteration():
    assert canon("Tverskaya") == "tverskaya"
    assert canon("Тверская улица") == "tverskaya ulitsa"
    assert canon("Арбат") == "arbat"


def test_confirmed_cyrillic_query():
    inv = {"city": "Moscow", "streets": ["Тверская улица"],
           "buildings": [{"street": "Тверская улица", "house": "5",
                          "lat": 55.76, "lon": 37.61, "osm": "node/9"}]}
    m = GeoMatcher(inv)
    assert m.match("Tverskaya 5, Moscow")["status"] == "Confirmed"
    r = m.match("Квартира, Тверская 5")
    assert r["status"] == "Confirmed" and r["osm_ref"] == "node/9", r


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")
