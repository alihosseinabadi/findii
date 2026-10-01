"""OSM place inventory — the offline map layer for FindII.

Flow: user types a city (``/city Moscow``) → we fetch that city's OSM
extract (BBBike, cached locally) → stream it with pyosmium into a compact
JSON inventory of real buildings + addresses → every Telegram/Avito lead
is then matched against REAL map objects (see core/geomatch.py).

No APIs at match time: after the one-time extract download, grounding is
100% offline. A manual ``.osm.pbf`` path always overrides auto-download.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from difflib import SequenceMatcher

log = logging.getLogger("findii.osm")

BBBIKE_BASE = "https://download.bbbike.org/osm/bbbike"

# chat slang / transliterations → canonical city name
CITY_ALIASES = {
    "msk": "Moscow", "moskva": "Moscow", "moscow": "Moscow",
    "piter": "SaintPetersburg", "spb": "SaintPetersburg",
    "saintpetersburg": "SaintPetersburg",
    "teheran": "Tehran", "tehran": "Tehran",
    "dubai": "Dubai", "kyiv": "Kyiv", "kiev": "Kyiv",
    "minsk": "Minsk", "tbilisi": "Tbilisi", "istanbul": "Istanbul",
}


def canonical_city(city: str, cache_dir: str = "") -> tuple[str, str]:
    """Resolve a typed city to its canonical name.

    Returns (canonical, note): note is '' on exact hit, otherwise explains
    the guess, e.g. "interpreted 'msocow' as Moscow". Typo-matching only
    considers maps already cached locally — never guesses a download.
    """
    typed = (city or "").strip()
    slug = re.sub(r"[^A-Za-z]+", "", typed)
    if CITY_ALIASES.get(slug.lower()):
        canon = CITY_ALIASES[slug.lower()]
        return canon, ("" if canon.lower() == slug.lower() else
                       f"interpreted '{typed}' as {canon}")
    cached: list[str] = []
    try:
        cached = [f[:-15] for f in os.listdir(cache_dir or ".")
                  if f.endswith(".inventory.json")]
    except OSError:
        pass
    for name in cached:
        if name.lower() == slug.lower():
            return name, ""
    best, best_ratio = "", 0.0
    for name in cached:
        r = SequenceMatcher(None, slug.lower(), name.lower()).ratio()
        if r > best_ratio:
            best, best_ratio = name, r
    if best and best_ratio > 0.7:
        return best, f"interpreted '{typed}' as {best}"
    return slug or typed, ""

# tags that make an object a real-estate-relevant place
BUILDING_VALUES = {
    "apartments", "residential", "house", "detached", "semidetached_house",
    "terrace", "dormitory", "hotel", "office", "commercial", "retail",
    "supermarket", "warehouse", "industrial", "yes",
}
POI_KEYS = ("amenity", "shop", "office", "leisure", "tourism")


def city_slugs(city: str) -> list[str]:
    """Candidate BBBike slugs for a city name, e.g. 'Moscow' → ['Moscow']."""
    base = re.sub(r"[^A-Za-z]+", "", city.strip())
    if not base:
        return []
    variants = [base, base.capitalize()]
    # common transliterations
    alt = {"Moskva": "Moscow", "Moskow": "Moscow", "Piter": "SaintPetersburg",
           "SPb": "SaintPetersburg", "Tehran": "Tehran", "Dubaï": "Dubai"}
    if base in alt:
        variants.insert(0, alt[base])
    seen, out = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def extract_path(city: str, cache_dir: str) -> str:
    slug = re.sub(r"[^A-Za-z]+", "", city.strip()) or "city"
    return os.path.join(cache_dir, f"{slug}.osm.pbf")


def inventory_path(city: str, cache_dir: str) -> str:
    slug = re.sub(r"[^A-Za-z]+", "", city.strip()) or "city"
    return os.path.join(cache_dir, f"{slug}.inventory.json")


def download_extract(city: str, cache_dir: str) -> str:
    """Download a city extract (BBBike). Returns local .pbf path or raises."""
    dest = extract_path(city, cache_dir)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        log.info("OSM extract cached: %s", dest)
        return dest
    os.makedirs(cache_dir, exist_ok=True)
    last_err: Exception | None = None
    for slug in city_slugs(city):
        url = f"{BBBIKE_BASE}/{slug}/{slug}.osm.pbf"
        try:
            log.info("downloading OSM extract %s ...", url)
            req = urllib.request.Request(url, headers={"User-Agent": "FindII/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(dest) > 0:
                log.info("saved %s (%.1f MB)", dest, os.path.getsize(dest) / 1e6)
                return dest
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("extract %s failed: %s", url, e)
            if os.path.exists(dest):
                os.remove(dest)
    raise RuntimeError(
        f"no OSM extract found for city '{city}' on BBBike (tried {city_slugs(city)}): "
        f"{last_err}. Drop a manual .osm.pbf anywhere and set OSM_PBF_PATH to it."
    )


class _InventoryHandler:
    """pyosmium handler — single streaming pass, no full-file load."""

    def __init__(self):
        import osmium
        self._osmium = osmium

        class H(osmium.SimpleHandler):
            pass

        self.h = H()
        self.buildings: list[dict] = []
        h = self.h
        h.node = self.node
        h.way = self.way

    def node(self, n):
        if n.location.valid():
            self._process("node", n.id, n.tags, n.location.lat, n.location.lon)

    def way(self, w):
        try:
            nodes = [nd for nd in w.nodes if nd.location.valid()]
        except Exception:  # noqa: BLE001
            return
        if not nodes:
            return
        lat = sum(nd.location.lat for nd in nodes) / len(nodes)
        lon = sum(nd.location.lon for nd in nodes) / len(nodes)
        self._process("way", w.id, w.tags, lat, lon)

    def _process(self, otype, oid, tags, lat, lon):
        tags = dict(tags)
        building = (tags.get("building") or "").strip()
        is_building = building in BUILDING_VALUES
        has_addr = bool((tags.get("addr:street") or "").strip()
                        and (tags.get("addr:housenumber") or "").strip())
        is_poi = any(tags.get(k) for k in POI_KEYS) and bool((tags.get("name") or "").strip())
        # named stations/squares: listings say "near metro X" far more often
        # than they quote a station's address
        is_station = bool((tags.get("name") or "").strip()) and (
            (tags.get("railway") or "") in ("station", "halt") or
            (tags.get("public_transport") or "") == "station" or
            (tags.get("place") or "") in ("square",))
        if not ((is_building and (has_addr or tags.get("name"))) or (has_addr) or is_poi or is_station):
            return
        contact = (
            tags.get("phone") or tags.get("contact:phone") or
            tags.get("mobile") or tags.get("contact:mobile") or ""
        ).strip()
        website = (
            tags.get("website") or tags.get("contact:website") or ""
        ).strip()
        self.buildings.append({
            "street": (tags.get("addr:street") or "").strip(),
            "house": (tags.get("addr:housenumber") or "").strip(),
            "addr_city": (tags.get("addr:city") or "").strip(),
            "addr_suburb": (tags.get("addr:suburb")
                            or tags.get("addr:neighbourhood")
                            or tags.get("addr:neighborhood") or "").strip(),
            "name": (tags.get("name") or "").strip(),
            "kind": building or tags.get("railway") or tags.get("place")
                    or tags.get("amenity") or tags.get("shop")
                    or tags.get("office") or tags.get("tourism")
                    or tags.get("leisure") or tags.get("craft") or "place",
            "phone": contact,
            "website": website,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "osm": f"{otype}/{oid}",
        })

    def apply(self, path: str):
        self.h.apply_file(path, locations=True, idx="sparse_mem_array")
        return self.buildings


def build_inventory(pbf_path: str, out_json: str, city: str) -> dict:
    """Stream a PBF into a compact JSON inventory. Returns stats dict."""
    handler = _InventoryHandler()
    buildings = handler.apply(pbf_path)
    streets = sorted({b["street"] for b in buildings if b["street"]})
    inv = {"city": city, "buildings": buildings, "streets": streets}
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(inv, f, ensure_ascii=False)
    stats = {"city": city, "buildings": len(buildings), "streets": len(streets),
             "inventory": out_json}
    log.info("inventory built: %s", stats)
    return stats


def ensure_city(city: str, cache_dir: str, manual_pbf: str = "") -> dict:
    """Make sure a city's inventory exists locally. Returns stats dict.

    The typed name is first resolved (aliases + typo-tolerant match over
    cached maps), so 'msocow' just works when Moscow is cached.
    """
    city, guess_note = canonical_city(city, cache_dir)
    inv_path = inventory_path(city, cache_dir)
    if os.path.exists(inv_path) and os.path.getsize(inv_path) > 0:
        with open(inv_path, encoding="utf-8") as f:
            inv = json.load(f)
        return {"city": city, "buildings": len(inv.get("buildings", [])),
                "streets": len(inv.get("streets", [])),
                "inventory": inv_path, "cached": True, "note": guess_note}
    try:
        pbf = manual_pbf if manual_pbf and os.path.exists(manual_pbf) \
            else download_extract(city, cache_dir)
    except RuntimeError:
        raise RuntimeError(
            f"no map found for '{city}' — check the spelling "
            f"(e.g. Moscow, Tehran, Dubai)")
    stats = build_inventory(pbf, inv_path, city)
    stats["cached"] = False
    stats["note"] = guess_note
    return stats


def load_inventory(inv_path: str) -> dict:
    with open(inv_path, encoding="utf-8") as f:
        return json.load(f)
