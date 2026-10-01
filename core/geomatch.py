"""Geo-matcher: pin a lead's free-text address to REAL OSM map objects.

Input:  raw listing text (+ AI-extracted district/city hints).
Output: status Confirmed | Probable | Uncertain (+ lat/lon + OSM ref).

Fully offline — works against the JSON inventory built by
scrapers/osm_places.py. Never invents coordinates: no match → Uncertain
with empty lat/lon.
"""
from __future__ import annotations

import difflib
import re

STREET_WORDS = (
    "street", "st", "avenue", "ave", "boulevard", "blvd", "lane", "ln",
    "square", "prospekt", "prospect", "ulitsa", "ul", "pereulok",
    "shosse", "naberezhnaya", "bulvar", "ploshchad",
    "улица", "проспект", "переулок", "шоссе", "набережная",
    "бульвар", "площадь", "проезд",
    "خیابان", "بلوار", "میدان", "کوچه",
)


def normalize(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[\"'«».,;:()\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Cyrillic → Latin (so "Тверская" and "Tverskaya" share one canonical key)
_RU_LAT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
           "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l",
           "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
           "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
           "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
           "ю": "yu", "я": "ya"}


def canon(s: str) -> str:
    """Canonical match key: normalized + Cyrillic folded to Latin."""
    return "".join(_RU_LAT.get(ch, ch) for ch in normalize(s))


# street-type words (already canonical form) — stripped for core-name matching
_TYPE_WORDS = {"ulitsa", "prospekt", "pereulok", "shosse", "naberezhnaya",
               "bulvar", "ploschad", "proezd", "street", "avenue", "boulevard",
               "lane", "square", "ul", "ave", "blvd", "ln", "st"}


def core_name(street_key: str) -> str:
    """'tverskaya ulitsa' → 'tverskaya' so bare names still match."""
    parts = [w for w in street_key.split() if w not in _TYPE_WORDS]
    return " ".join(parts)


def _house_variants(house: str) -> set[str]:
    h = house.strip().lower().replace("ё", "е")
    out = {h, h.replace(" ", ""), re.sub(r"^0+", "", h)}
    out |= {x for x in out if x}
    return out


class GeoMatcher:
    def __init__(self, inventory: dict | None = None):
        self.inventory = inventory or {"city": "", "buildings": [], "streets": []}
        self._street_index: dict[str, str] = {}  # canonical → original
        for s in self.inventory.get("streets", []):
            self._street_index.setdefault(canon(s), s)
        # canonical street per building for fast comparison
        for b in self.inventory.get("buildings", []):
            b["_st"] = canon(b.get("street", ""))
            b["_core"] = core_name(b["_st"])

    @property
    def ready(self) -> bool:
        return bool(self.inventory.get("buildings"))

    @property
    def stats(self) -> dict:
        return {"city": self.inventory.get("city", ""),
                "buildings": len(self.inventory.get("buildings", [])),
                "streets": len(self.inventory.get("streets", []))}

    def match(self, text: str, district: str = "", city: str = "") -> dict:
        """Return {status, lat, lon, osm_ref, matched} — offline only."""
        blank = {"status": "Uncertain", "lat": None, "lon": None,
                 "osm_ref": "", "matched": ""}
        if not self.ready:
            return blank
        hay = canon(f"{text} {district} {city}")
        if not hay:
            return blank
        # phone numbers contain digit runs ("916 111-22-33") that would
        # false-match house numbers — scrub them before house matching
        hay_nophone = re.sub(r"\+?[\d][\d\s\-()]{7,}\d", " ", hay)
        hay_nophone = re.sub(r"\s+", " ", hay_nophone).strip()

        # 1) exact street + house number → Confirmed
        for b in self.inventory["buildings"]:
            st = b.get("_st") or canon(b.get("street", ""))
            core = b.get("_core") or core_name(st)
            if not st or (st not in hay and not (core and core in hay)):
                continue
            houses = _house_variants(b["house"]) if b["house"] else set()
            if houses and any(re.search(r"(?<![\d\w])" + re.escape(h) + r"(?![\d\w])", hay_nophone)
                              for h in houses):
                return {"status": "Confirmed", "lat": b["lat"], "lon": b["lon"],
                        "osm_ref": b["osm"],
                        "matched": f"{b['street']} {b['house']}".strip()}

        # 2) street (or bare core name) only → Probable (street centroid)
        seen_keys: set[str] = set()
        for b in self.inventory["buildings"]:
            bst = b.get("_st") or ""
            bcore = b.get("_core") or ""
            hit = bst if bst and bst in hay else (bcore if bcore and bcore in hay else "")
            if not hit or hit in seen_keys:
                continue
            seen_keys.add(hit)
            pts = [(x["lat"], x["lon"], x["osm"])
                   for x in self.inventory["buildings"]
                   if (x.get("_st") or "") == hit or (x.get("_core") or "") == hit]
            if pts:
                disp = next((y.get("street", "") or hit for y in self.inventory["buildings"]
                             if (y.get("_st") or "") == hit), hit)
                return {"status": "Probable",
                        "lat": round(sum(p[0] for p in pts) / len(pts), 6),
                        "lon": round(sum(p[1] for p in pts) / len(pts), 6),
                        "osm_ref": pts[0][2], "matched": disp}

        # 2b) named place / metro station ("near Shabolovskaya") → Probable.
        # Runs after street logic so street addresses always win.
        for b in self.inventory["buildings"]:
            nm = canon(b.get("name", ""))
            if len(nm) >= 5 and nm in hay:
                return {"status": "Probable", "lat": b["lat"], "lon": b["lon"],
                        "osm_ref": b["osm"], "matched": b.get("name", "")}

        # 3) fuzzy street OR place name (typos: first/middle/last word) → Probable.
        # Only words from the raw text qualify — district/city hints are too
        # coarse for fuzzy matching and would blur nearby names together.
        words = [w for w in canon(text).split() if len(w) > 4]
        targets: list[tuple[str, str, float | None, float | None, str]] = []
        for st_key, st_orig in self._street_index.items():
            if st_key:
                targets.append((st_key, st_orig, None, None, ""))
        seen_names: set[str] = set()
        for b in self.inventory["buildings"]:
            nm = canon(b.get("name", ""))
            if len(nm) >= 5 and nm not in seen_names:
                seen_names.add(nm)
                targets.append((nm, b.get("name", ""), b["lat"], b["lon"], b["osm"]))
        for tkey, tlabel, tlat, tlon, tosm in targets:
            key_words = [w for w in tkey.split() if len(w) > 4] or [tkey]
            for w in words:
                best = max(difflib.SequenceMatcher(None, w, kw).ratio()
                           for kw in key_words)
                if best > 0.8:
                    if tlat is None:  # street → centroid
                        pts = [(b["lat"], b["lon"], b["osm"])
                               for b in self.inventory["buildings"]
                               if (b.get("_st") or canon(b.get("street", ""))) == tkey]
                        if not pts:
                            continue
                        tlat = round(sum(p[0] for p in pts) / len(pts), 6)
                        tlon = round(sum(p[1] for p in pts) / len(pts), 6)
                        tosm = pts[0][2]
                    return {"status": "Probable", "lat": tlat, "lon": tlon,
                            "osm_ref": tosm, "matched": tlabel + " (fuzzy)"}

        # 4) district/city keyword only → Uncertain (honest: no coordinates)
        inv_city = canon(self.inventory.get("city", ""))
        if (inv_city and inv_city in hay) or (district and canon(district) in hay):
            return {"status": "Uncertain", "lat": None, "lon": None,
                    "osm_ref": "", "matched": (district or city or "").strip()}
        return blank
