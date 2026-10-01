"""OSM business-lead scraper: city + category -> Excel + CSV sales kit.

Turns a parsed demand (ai.query_router) into a real lead list using the
offline city inventory built from a BBBike/Geofabrik .osm.pbf:

    ensure_city -> load inventory -> filter by category tags (kind field)
                 -> (optional) district filter -> dedupe -> export.

Exports follow the Chegovara sales-kit shape:
  - .xlsx with LEADS (bilingual columns) + SUMMARY tab
  - .csv UTF-8 (BOM for Excel compatibility)
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime
from typing import Any

from ai.query_router import parse
from analyst.report import build_analyst
from scrapers import osm_places

GROUP_COUNTS = {}  # filled during scan for the summary tab


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _matches_tags(rule: dict[str, Any], kind: str) -> bool:
    if not rule.get("tags") or not kind:
        return False
    for key, value in rule["tags"]:
        if value is None:
            continue
        if _norm(str(value)) == _norm(str(kind)):
            return True
    return False


# kinds that are real businesses/POIs worth a sales kit (vs residential or
# infrastructure). kind comes from shop/amenity/office/leisure/tourism/craft.
_NON_BUSINESS_KINDS = {
    "yes", "apartments", "residential", "house", "detached",
    "semidetached_house", "terrace", "dormitory", "warehouse", "industrial",
    "garage", "garages", "shed", "farm", "greenhouse", "silo", "hut",
    "static_caravan", "bunker", "cabin", "construction", "gatehouse",
    "services", "military", "ruins", "bridge", "tunnel", "water",
    "natural", "place", "yes", "hall", "colonnade", "civic",
}


def _is_business(kind: str) -> bool:
    if not kind:
        return False
    if kind in _NON_BUSINESS_KINDS:
        return False
    # building=yes with a name and a POI tag resolves to that POI kind
    # (shop/amenity/etc. take precedence in the inventory builder)
    return True


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2)
         * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _matches_lexicon(rule: dict[str, Any], low_name: str) -> bool:
    for lex in rule["lex"]:
        lex_low = _norm(lex)
        if len(lex_low) >= 5 and lex_low in low_name:
            return True
    return False


def _resolve(city: str, district: str, category: str,
             tags: tuple[str, ...], inventory: dict[str, Any],
             landmark: dict[str, Any] | None = None
             ) -> tuple[list[dict], str]:
    """Filter + rank the inventory buildings for a category query."""
    all_biz = category == "All Businesses" or tags in (("all",), ("all", ""))
    rules = [r for r in __import__("ai.query_router", fromlist=["CATEGORY_RULES"]).CATEGORY_RULES
             if _norm(r.get("cat", "")) == _norm(category)]
    rule = rules[0] if rules else None
    if not all_biz and (not rule or not tags):
        return [], "unknown category mapping — no tags to filter by"
    d_low = _norm(district)
    lm = landmark or {}
    lm_name = _norm(lm.get("name", ""))
    lm_lat, lm_lon = lm.get("lat"), lm.get("lon")
    lm_radius = float(lm.get("radius_km", 0) or 0)
    hits: list[dict] = []
    seen: set[str] = set()
    for b in inventory.get("buildings", []):
        kind = _norm(b.get("kind", ""))
        name = b.get("name", "") or ""
        low_name = _norm(name)
        if all_biz:
            if not low_name or low_name.startswith("addr"):
                continue
            if not _is_business(kind):
                continue
        elif not (_matches_tags(rule, kind) and not low_name.startswith("addr")):
            continue
        # district guard: match against suburb / street / city / name
        if d_low and not lm:
            blob = _norm(" ".join([
                b.get("addr_suburb", ""), b.get("street", ""),
                b.get("addr_city", ""), name]))
            if d_low not in blob:
                continue
        # landmark radius guard: keep only leads near the focal point
        if lm and lm_lat is not None and b.get("lat") is not None:
            km = _haversine_km(lm_lat, lm_lon, b["lat"], b["lon"])
            if km > lm_radius:
                continue
        osm = b.get("osm", "")
        if osm and osm in seen:
            continue
        seen.add(osm)
        street = b.get("street", "") or ""
        house = b.get("house", "") or ""
        address = f"{street}{(', ' + house) if house else ''}".strip()
        dist_km = None
        if lm_lat is not None and b.get("lat") is not None:
            dist_km = round(_haversine_km(lm_lat, lm_lon, b["lat"], b["lon"]), 2)
        hits.append({
            "name": name,
            "category": category,
            "group": (rule or {}).get("group", "") or ("Businesses" if all_biz else ""),
            "district": (lm_name if lm_name else
                         b.get("addr_suburb", "") or district),
            "address": address or b.get("addr_city", ""),
            "distance_km": dist_km,
            "phone": b.get("phone", ""),
            "website": b.get("website", ""),
            "lat": b.get("lat"),
            "lon": b.get("lon"),
            "osm": osm,
            "osm_url": f"https://www.openstreetmap.org/{osm}" if osm else "",
        })
    # rank: with contact info first, then named, then the rest
    hits.sort(key=lambda r: (0 if r["phone"] else 1,
                             0 if r["name"] else 1,
                             (r["name"] or "").lower()))
    return hits, ""


def scrape_city(city: str, district: str, category: str, tags: tuple[str, ...],
                cache_dir: str, pbf_override: str = "",
                landmark: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one scrape: ensure city map, filter, return {query, rows, files}."""
    q = {"city": city or "", "district": district or "",
         "category": category or "", "tags": tags,
         "landmark": landmark or {}, "rows": 0}
    stats = osm_places.ensure_city(city, cache_dir, manual_pbf=pbf_override)
    inv_path = osm_places.inventory_path(stats["city"], cache_dir)
    with open(inv_path, encoding="utf-8") as f:
        inventory = json.load(f)
    rows, err = _resolve(city, district, category, tags, inventory, landmark)
    return {"query": q, "city": stats["city"], "buildings": len(inventory.get("buildings", [])),
            "rows": rows, "error": err}


# ---------------------------------------------------------------------- oo
class LeadExport:
    """Writes the sales kit: XLSX (LEADS + SUMMARY) + CSV."""

    HEADERS = ("Name", "Category", "Group", "District", "Address", "Distance km",
               "Phone", "Website", "Latitude", "Longitude", "OSM ID", "OSM URL")
    # header -> row dict key
    _FIELD = {h: h.lower() for h in HEADERS}
    _FIELD["OSM ID"] = "osm"
    _FIELD["OSM URL"] = "osm_url"
    _FIELD["Latitude"] = "lat"
    _FIELD["Longitude"] = "lon"

    def __init__(self, query: dict[str, Any], rows: list[dict], out_dir: str):
        self.query = query
        self.rows = rows
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cat = re.sub(r"[^a-z0-9]+", "_", (query.get("category") or "leads").lower())
        base = f"{cat}_{query.get('city') or ''}_{stamp}".strip("_")
        self.xlsx_path = os.path.join(out_dir, f"{base}.xlsx")
        self.csv_path = os.path.join(out_dir, f"{base}.csv")

    def _write_csv(self):
        with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(self.HEADERS)
            for r in self.rows:
                w.writerow([r.get(self._FIELD[h], "") for h in self.HEADERS])
        return self.csv_path

    def _write_xlsx(self):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        # ---- LEADS
        ws = wb.active
        ws.title = "LEADS"
        header_fill = PatternFill("solid", fgColor="B98A2F")
        head = ws.cell(row=1, column=1, value="Sales Kit — "
                      f"{self.query.get('category')} in {self.query.get('city')}")
        head.font = Font(bold=True, size=13)
        ws.cell(row=2, column=1, value=f"rows: {len(self.rows)} · "
                f"generated: {datetime.now().isoformat(timespec='minutes')}")
        ws.cell(row=2, column=1).font = Font(italic=True, size=9)
        for c, h in enumerate(self.HEADERS, start=1):
            cell = ws.cell(row=4, column=c, value=h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
        for i, r in enumerate(self.rows, start=5):
            for c, h in enumerate(self.HEADERS, start=1):
                ws.cell(row=i, column=c, value=r.get(self._FIELD[h], ""))
        for c in range(1, len(self.HEADERS) + 1):
            ws.column_dimensions[get_column_letter(c)].width = 18
        # ---- SUMMARY
        ws2 = wb.create_sheet("SUMMARY")
        ws2.cell(row=1, column=1, value="Query").font = Font(bold=True)
        ws2["B1"] = self.query.get("raw", self.query.get("category"))
        ws2.cell(row=2, column=1, value="City").font = Font(bold=True)
        ws2["B2"] = self.query.get("city", "")
        ws2.cell(row=3, column=1, value="District").font = Font(bold=True)
        ws2["B3"] = self.query.get("district", "—")
        ws2.cell(row=4, column=1, value="Category").font = Font(bold=True)
        ws2["B4"] = self.query.get("category", "")
        ws2.cell(row=6, column=1, value="Group breakdown").font = Font(bold=True)
        ws2["A7"] = "Group"; ws2["B7"] = "Count"
        rr = 8
        for g, n in sorted(GROUP_COUNTS.items()):
            ws2.cell(row=rr, column=1, value=g)
            ws2.cell(row=rr, column=2, value=n)
            rr += 1
        wb.save(self.xlsx_path)
        return self.xlsx_path

    def save(self) -> dict[str, str]:
        return {"xlsx": self._write_xlsx(), "csv": self._write_csv()}


def run_demand(query_text: str, cache_dir: str, out_dir: str,
               pbf_override: str = "") -> dict[str, Any]:
    """End-to-end helper: parse -> scrape -> export. Returns files + rows + parsed."""
    parsed = parse(query_text)
    if not parsed.get("city") or not parsed.get("tags"):
        return {"error": parsed.get("note", "unresolved query"),
                "parsed": parsed}
    scrape = scrape_city(parsed["city"], parsed.get("district", ""),
                         parsed.get("category", ""), parsed.get("tags", ()),
                         cache_dir, pbf_override,
                         parsed.get("landmark") or None)
    if scrape["error"] or not scrape["rows"]:
        return {**scrape, "parsed": parsed}
    global GROUP_COUNTS
    GROUP_COUNTS = {}
    for r in scrape["rows"]:
        GROUP_COUNTS[r["group"]] = GROUP_COUNTS.get(r["group"], 0) + 1
    exp = LeadExport({**parsed, "rows": len(scrape["rows"])}, scrape["rows"], out_dir)
    files = exp.save()
    try:
        analyst_files = build_analyst(
            {**parsed, "rows": len(scrape["rows"])}, scrape["rows"],
            {"map_buildings": scrape["buildings"]}, out_dir)
    except Exception:  # noqa: BLE001 — analyst is a bonus, never breaks the kit
        analyst_files = {}
    return {"parsed": parsed, "rows": scrape["rows"],
            "count": len(scrape["rows"]), "files": files,
            "analyst": analyst_files,
            "city": scrape["city"], "map_buildings": scrape["buildings"]}