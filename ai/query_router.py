"""Query router: free-text demand -> structured scrape request.

Rule-based NLP in two stages:
  1. normalize  -> mechanical text fix: leading "in/for/near", plural forms,
                   "store"/"shop" noise, geographic fluff ("in the city of").
  2. residues   -> split demand into {city, district, country, category,
                   keyword} by bilingual lexicons, exactly the "make the
                   text understandable from NLP to artificial language"
                   step in the product brief.

No network + no LLM: deterministic so requests are reproducible offline.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# ── city / country / district lexicons (+ typo-tolerant aliases) ──────────

# country -> primary city we resolve to (BBBike has city extracts, not
# countries, so we anchor a country request at its largest city)
COUNTRY_CITY = {
    "united states": "NewYork", "usa": "NewYork", "us": "NewYork",
    "america": "NewYork", "united kingdom": "London", "uk": "London",
    "england": "London", "germany": "Berlin", "deutschland": "Berlin",
    "france": "Paris", "italy": "Rome", "spain": "Madrid", "russia": "Moscow",
    "iran": "Tehran", "turkey": "Istanbul", "uae": "Dubai",
    "united arab emirates": "Dubai", "georgia": "Tbilisi",
    "belarus": "Minsk", "ukraine": "Kyiv", "canada": "Toronto",
    "australia": "Sydney", "china": "Shanghai", "japan": "Tokyo",
    "india": "Mumbai", "brazil": "RioDeJaneiro", "egypt": "Cairo",
    "azerbaijan": "Baku", "armenia": "Yerevan",
}

KNOWN_CITIES = {
    "moscow": "Moscow", "moskva": "Moscow", "msk": "Moscow",
    "saint petersburg": "SaintPetersburg", "piter": "SaintPetersburg",
    "spb": "SaintPetersburg", "london": "London", "paris": "Paris",
    "berlin": "Berlin", "rome": "Rome", "milan": "Milan", "madrid": "Madrid",
    "tehran": "Tehran", "teheran": "Tehran", "istanbul": "Istanbul",
    "dubai": "Dubai", "tbilisi": "Tbilisi", "baku": "Baku",
    "yerevan": "Yerevan", "minsk": "Minsk", "kyiv": "Kyiv", "kiev": "Kyiv",
    "new york": "NewYork", "nyc": "NewYork",
    "los angeles": "LosAngeles", "chicago": "Chicago", "houston": "Houston",
    "toronto": "Toronto", "sydney": "Sydney", "melbourne": "Melbourne",
    "shanghai": "Shanghai", "beijing": "Beijing", "tokyo": "Tokyo",
    "mumbai": "Mumbai", "delhi": "Delhi", "cairo": "Cairo",
    "rio de janeiro": "RioDeJaneiro", "sao paulo": "SaoPaulo",
    "warsaw": "Warsaw", "prague": "Prague", "vienna": "Vienna",
    "amsterdam": "Amsterdam", "brussels": "Brussels", "stockholm": "Stockholm",
    "oslo": "Oslo", "helsinki": "Helsinki", "copenhagen": "Copenhagen",
    "zurich": "Zurich", "berne": "Bern", "bucharest": "Bucharest",
    "sofia": "Sofia", "athens": "Athens", "lisbon": "Lisbon",
    "ghent": "Gent", "antwerp": "Antwerpen", "mumbai": "Mumbai",
    # Persian city names → canonical slug (BBBike extract name)
    "تهران": "Tehran", "شیراز": "Shiraz", "اصفهان": "Isfahan",
    "مشهد": "Mashhad", "تبریز": "Tabriz", "کرج": "Karaj",
    "مسکو": "Moscow", "دبی": "Dubai", "استانبول": "Istanbul",
    "لندن": "London", "پاریس": "Paris", "نیویورک": "NewYork",
    "برلین": "Berlin", "آنکارا": "Ankara",
}

# OSM tag maps for business types: (key, value) matches, optionally a second
# (key, value) pair — first hit wins, ordered most specific first.
# Each entry: lexicons (EN/RU/FA) -> tag rule(s) + display category.

# Landmark knowledge base: neighbourhoods / squares / famous streets per city.
# (label -> list of {canon, aliases, lat, lon, radius_km}). Used after the map
# is downloaded to geo-scope a demand ("moscow red square supermarket" only
# returns supermarkets inside the Red Square radius, not all Moscow).
LANDMARKS: dict[str, list[dict[str, Any]]] = {
    "Moscow": [
        {"canon": "Red Square", "aliases": ("red square", "красная площадь",
                                            "krasnaya ploshchad", "кремль"),
         "lat": 55.7539, "lon": 37.6208, "radius_km": 1.6},
        {"canon": "Arbat", "aliases": ("arbat", "арбат", "старый арбат"),
         "lat": 55.7498, "lon": 37.5911, "radius_km": 1.2},
        {"canon": "Patriarshie Ponds", "aliases": ("patriarshie", "patriarch",
                                                    "патриаршие"),
         "lat": 55.7606, "lon": 37.5902, "radius_km": 1.2},
        {"canon": "Kitay-gorod", "aliases": ("kitay", "китай-город", "kitaj"),
         "lat": 55.7558, "lon": 37.6280, "radius_km": 1.3},
        {"canon": "Tverskaya", "aliases": ("tverskaya", "тверская", "tver"),
         "lat": 55.7664, "lon": 37.6064, "radius_km": 1.1},
        {"canon": "VDNKh", "aliases": ("vdnh", "вднх", "vvc"),
         "lat": 55.8263, "lon": 37.6372, "radius_km": 2.5},
        {"canon": "Arbat (New)", "aliases": ("новый арбат", "new arbat", "prospekt kalinina"),
         "lat": 55.7532, "lon": 37.5917, "radius_km": 1.2},
    ],
    "SaintPetersburg": [
        {"canon": "Nevsky Prospekt", "aliases": ("nevsky", "невский", "nevskiy"),
         "lat": 59.9343, "lon": 30.3351, "radius_km": 1.8},
        {"canon": "Palace Square", "aliases": ("palace square", "дворцовая",
                                               "dvortsovaya"),
         "lat": 59.9398, "lon": 30.3146, "radius_km": 1.2},
    ],
    "Tehran": [
        {"canon": "Tajrish", "aliases": ("tajrish", "تجریش", "تاجريش"),
         "lat": 35.8018, "lon": 51.4270, "radius_km": 2.0},
        {"canon": "Saadat Abad", "aliases": ("saadat abad", "سعادت آباد", "سعادت‌آباد"),
         "lat": 35.7901, "lon": 51.3952, "radius_km": 1.8},
        {"canon": "Vanak", "aliases": ("vanak", "ونک"),
         "lat": 35.7565, "lon": 51.4102, "radius_km": 1.4},
        {"canon": "Azadi", "aliases": ("azadi", "آزادی", "آزادي"),
         "lat": 35.6997, "lon": 51.3381, "radius_km": 1.6},
        {"canon": "Valiasr", "aliases": ("valiasr", "ولیعصر", "وليعصر",
                                          "vali asr"),
         "lat": 35.7110, "lon": 51.4070, "radius_km": 2.5},
        {"canon": "Niavaran", "aliases": ("niavaran", "نیاوران"),
         "lat": 35.8125, "lon": 51.4721, "radius_km": 1.8},
        {"canon": "Pasdaran", "aliases": ("pasdaran", "پاسداران"),
         "lat": 35.7678, "lon": 51.4499, "radius_km": 1.8},
    ],
    "NewYork": [
        {"canon": "Manhattan", "aliases": ("manhattan", "манхэттен"),
         "lat": 40.7831, "lon": -73.9712, "radius_km": 12.0},
        {"canon": "Brooklyn", "aliases": ("brooklyn", "бруклин"),
         "lat": 40.6782, "lon": -73.9442, "radius_km": 11.0},
        {"canon": "Queens", "aliases": ("queens", "куинс"),
         "lat": 40.7282, "lon": -73.7949, "radius_km": 12.0},
        {"canon": "Times Square", "aliases": ("times square", "таймс-сквер"),
         "lat": 40.7580, "lon": -73.9855, "radius_km": 1.0},
        {"canon": "Wall Street", "aliases": ("wall street", "уолл-стрит"),
         "lat": 40.7074, "lon": -74.0113, "radius_km": 0.8},
        {"canon": "Central Park", "aliases": ("central park", "центральный парк"),
         "lat": 40.7829, "lon": -73.9654, "radius_km": 2.0},
    ],
}


def resolve_area(city: str, area_text: str) -> dict[str, Any]:
    """Match a free area string against the landmark knowledge base for a
    city. Returns {name, lat, lon, radius_km} or {} (city doesn't matter if
    no area)."""
    if not area_text:
        return {}
    low = _norm(area_text)
    for lm in LANDMARKS.get(city, []):
        for a in (lm.get("canon", ""),) + tuple(lm.get("aliases", ())):
            a_low = _norm(a)
            if not a_low:
                continue
            if a_low == low or (len(a_low) >= 5 and a_low in low) \
                    or (len(low) >= 5 and low in a_low):
                return {"name": lm["canon"], "lat": lm["lat"],
                        "lon": lm["lon"], "radius_km": lm["radius_km"]}
    return {}


CATEGORY_RULES: list[dict[str, Any]] = [
    {"lex": ("bakery", "boulangerie", "bread", "baker", "пекарн", "نان"),
     "tags": (("shop", "bakery"), ("craft", "baker"), ("amenity", "bakery")),
     "cat": "Bakery",
     "group": "Food & Beverage"},
    {"lex": ("cafe", "coffee", "café", "кафе", "кофе", "کافه", "قهوه"),
     "tags": (("amenity", "cafe"), ("shop", "coffee")),
     "cat": "Cafe",
     "group": "Food & Beverage"},
    {"lex": ("restaurant", "diner", "ресторан", "رستوران"),
     "tags": (("amenity", "restaurant"),),
     "cat": "Restaurant",
     "group": "Food & Beverage"},
    {"lex": ("fast food", "fastfood", "burger", "pizza", "фаст", "بورگر", "پیتزا"),
     "tags": (("amenity", "fast_food"),),
     "cat": "Fast Food",
     "group": "Food & Beverage"},
    {"lex": ("grocer", "supermarket", "convenience", "market", "продукт", "суперма", "فروشگاه", "سوپرمارکت"),
     "tags": (("shop", "supermarket"), ("shop", "convenience"), ("shop", "grocery"),
              ("shop", "greengrocer"), ("shop", "deli"), ("amenity", "marketplace")),
     "cat": "Grocery",
     "group": "Retail"},
    {"lex": ("pharmacy", "drugstore", "drug store", "аптек", "داروخانه"),
     "tags": (("amenity", "pharmacy"), ("shop", "chemist")),
     "cat": "Pharmacy",
     "group": "Health & Medical"},
    {"lex": ("clinic", "medical", "doctor", "клиник", "поликлиник", "پزشک", "درمانگاه"),
     "tags": (("amenity", "clinic"), ("healthcare", "clinic"), ("amenity", "doctors"),
              ("healthcare", "centre"), ("healthcare", "center")),
     "cat": "Medical",
     "group": "Health & Medical"},
    {"lex": ("dent", "стоматолог", "دندان"),
     "tags": (("amenity", "dentist"), ("healthcare", "dentist")),
     "cat": "Dentist",
     "group": "Health & Medical"},
    {"lex": ("hospital", "больниц", "بیمارستان"),
     "tags": (("amenity", "hospital"), ("healthcare", "hospital")),
     "cat": "Hospital",
     "group": "Health & Medical"},
    {"lex": ("hair", "barber", "barbershop", "парикмахер", "آرایشگاه"),
     "tags": (("shop", "hairdresser"), ("shop", "barber")),
     "cat": "Beauty",
     "group": "Beauty & Care"},
    {"lex": ("beauty", "spa", "nail", "салон", "красот", "спа", "زیبایی"),
     "tags": (("shop", "beauty"), ("shop", "spa"), ("amenity", "spa"),
              ("leisure", "beauty_salon")),
     "cat": "Beauty",
     "group": "Beauty & Care"},
    {"lex": ("gym", "fitness", "sport", "club", "тренажер", "фитнес", "تجربی", "ورزشی"),
     "tags": (("leisure", "fitness_centre"), ("sport", "fitness"), ("sport", "gym"),
              ("sport", "bodybuilding"), ("leisure", "sports_centre"),
              ("sport", "crossfit"), ("sport", "yoga"), ("sport", "pilates")),
     "cat": "Gym",
     "group": "Fitness & Sports"},
    {"lex": ("hotel", "motel", "hostel", "отель", "гостиниц", "هتل"),
     "tags": (("tourism", "hotel"), ("tourism", "guest_house"), ("tourism", "hostel"),
              ("tourism", "motel"), ("tourism", "apartment"), ("tourism", "hotel")),
     "cat": "Hotel",
     "group": "Hospitality"},
    {"lex": ("school", "kindergarten", "школ", "детск", "مدرسه", "کودکستان"),
     "tags": (("amenity", "school"), ("amenity", "kindergarten"), ("amenity", "college"),
              ("amenity", "university")),
     "cat": "School",
     "group": "Education"},
    {"lex": ("university", "universit", "университ", "دانشگاه"),
     "tags": (("amenity", "university"),),
     "cat": "University",
     "group": "Education"},
    {"lex": ("office", "cowork", "офис", "کازار", "کرایه"),
     "tags": (("office", "company"), ("office", "coworking"),
              ("office", "administrative")),
     "cat": "Office",
     "group": "Office & Corporate"},
    {"lex": ("car", "auto", "mechanic", "авто", "ماشین"),
     "tags": (("shop", "car_repair"), ("shop", "car_dealer"), ("shop", "car")),
     "cat": "Auto",
     "group": "Automotive"},
    {"lex": ("clothes", "cloth", "fashion", "одежд", "پوشاک"),
     "tags": (("shop", "clothes"), ("shop", "fashion")),
     "cat": "Fashion",
     "group": "Retail"},
    {"lex": ("furniture", "мебель", "مبلمان"),
     "tags": (("shop", "furniture"),),
     "cat": "Furniture",
     "group": "Retail"},
    {"lex": ("store", "shop", "points of sale"),
     "tags": (("shop", None), ("amenity", "marketplace")),
     "cat": "Shop",
     "group": "Retail"},
]

# stop-/geo- words to strip before category matching
_GEO_WORDS = {
    "in", "of", "for", "near", "around", "district", "area", "zone", "city",
    "town", "region", "state", "country", "downtown", "uptown", "центр",
    "район", "город", "мест", "در", "در", "نزدیک", "منطقه", "شهر",
}

# makes a token look like a "in X" tail, e.g. "moscow", "nyc", "downtown X"
_CITY_HINT_RE = re.compile(
    r"\b(in|near|for|of|around)?\s*"
    r"([a-z\u0400-\u04ff\u0600-\u06ff]{2,35})$", re.IGNORECASE)


def _mean_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _best_lexicon_hit(word: str, lexicon: tuple[str, ...]) -> str | None:
    low = word.lower()
    for w in lexicon:
        if _mean_ratio(low, w) >= 0.82 or (len(w) >= 5 and w in low):
            return w
    return None


def normalize(text: str) -> str:
    """Stage 1 — mechanical text fix (NLP pre-clean):
    collapse case, strip noisy geo/stop fluff, keep order."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    t = t.replace("\u00a0", " ").replace("\u2009", "")
    # "for/for sale", "in the city of X" style fillers
    t = re.sub(r"\b(for|to|on|at|of|the|and|also)\b", " ", t)
    t = re.sub(r"\b(in|near|around|downtown|uptown)\b", " ", t)
    # Persian/FA stopwords (در/نزدیک/منطقه entirely, شهر keeps intent)
    t = re.sub(r"\b(در|نزدیک|به|برای|منطقه|نواحی|کنار)\b", " ", t)
    t = t.replace("store", " ").replace("shop", " ").replace("stores", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse(query: str) -> dict[str, Any]:
    """Stage 2 — normalize then slice a free-text demand.

    Returns {raw, normalized, city, district, country, category, cat_group,
             tags, keyword, note} — 'tags' feeds the OSM inventory filter.
    """
    raw = (query or "").strip()
    low = raw.lower()
    norm = normalize(raw)
    out: dict[str, Any] = {"raw": raw, "normalized": norm,
                            "city": "", "district": "", "area": "",
                            "country": "", "category": "",
                            "cat_group": "", "tags": (), "keyword": "",
                            "landmark": {}, "note": ""}

    # 1) category (business type) first
    matched_cat = None
    for rule in CATEGORY_RULES:
        for lex in rule["lex"]:
            if lex in low or (len(lex) >= 5 and lex.replace(" ", "") in low.replace(" ", "")):
                out["category"] = rule["cat"]
                out["cat_group"] = rule["group"]
                out["tags"] = tuple(rule["tags"])
                out["keyword"] = lex
                matched_cat = rule
                break
        if matched_cat:
            break
    if not matched_cat:
        out["tags"] = ()
        out["keyword"] = norm
        if not any(k in low for k in ("all", "все", "تمام", "همه")):
            out["note"] = "no known business type — treating whole query as keyword"

    # 2) city (most specific)
    city_name, city_canon = "", ""
    for name, canon in sorted(KNOWN_CITIES.items(), key=lambda kv: -len(kv[0])):
        if name in low:
            city_name, city_canon = name, canon
            break
    if city_name:
        out["city"] = city_canon
    else:
        for cn, city in sorted(COUNTRY_CITY.items(), key=lambda kv: -len(kv[0])):
            if cn in low:
                out["country"] = cn
                out["city"] = city
                city_name = cn
                out["note"] = f"country '{cn}' → anchored at {city}"
                break
    if not out["city"] and out["raw"]:
        # typo-tolerant last-token city guess against known cities
        tail = low.split()[-1] if low.split() else ""
        if tail:
            best, ratio = "", 0.0
            for name, canon in KNOWN_CITIES.items():
                r = _mean_ratio(tail, name)
                if r > ratio:
                    best, ratio = canon, r
            if best and ratio >= 0.62:
                out["city"] = best
                city_name = tail
                out["note"] = f"'{tail}' → guessed {best}"

    # 2b) city-only (or "all") demand -> all business types in that city
    if out["city"] and not out["category"]:
        out["category"] = "All Businesses"
        out["cat_group"] = "Businesses"
        out["tags"] = ("all",)
        out["keyword"] = "*"
        out["note"] = (f"city-only demand → full business directory of "
                       f"{out['city']}")

    # 3) area: whatever remains after stripping category + city tokens.
    #    "moscow red square supermarket" -> area "red square"
    #    "bakery in manhattan new york"  -> area "manhattan"
    if out["city"] and norm:
        norm_low = _norm(norm)
        # list of tokens that are category lexicons or the city name itself
        drop: set[str] = set()
        if matched_cat:
            for lex in matched_cat["lex"]:
                lx = _norm(lex)
                if len(lx) >= 3 and (lx in norm_low or
                                     lx.replace(" ", "") in norm_low.replace(" ", "")):
                    drop.add(lx)
        cn_low = _norm(city_name or "")
        if cn_low:
            drop.add(cn_low)
        # reconstruct area by removing dropped substrings, case-insensitively
        area_work = norm_low
        for d in sorted(drop, key=len, reverse=True):
            area_work = re.sub(re.escape(d), " ", area_work)
            area_work = re.sub(r"\s+", " ", area_work).strip()
        # strip leftover generic venue-suffix words
        area_work = re.sub(r"\b(club|center|centre|space|area|district|zone|region)\b",
                           " ", area_work)
        area_work = re.sub(r"\s+", " ", area_work).strip(" ,-")
        area_work = re.sub(r"\s+", " ", area_work).strip()
        if area_work and area_work != _norm(out["category"] or ""):
            out["area"] = area_work[:80]
            lm = resolve_area(out["city"], area_work)
            if lm:
                out["landmark"] = lm
                out["district"] = lm["name"]
                out["note"] = (f"area '{area_work}' → {lm['name']} "
                               f"(±{lm['radius_km']} km)")
            else:
                out["district"] = area_work[:80]

    if not out["category"] and not out["city"] and not out["district"]:
        out["note"] = "unresolved query — provide a city and business type"
    return out


def display(query: dict[str, Any]) -> str:
    """Human-readable summary of the parsed demand."""
    parts = [
        f"city: {query['city'] or '?'}",
        f"district: {query['district'] or '—'}",
        f"category: {query['category'] or '?'}",
        f"group: {query['cat_group'] or '—'}",
    ]
    if query["country"]:
        parts.append(f"country: {query['country']}")
    if query["note"]:
        parts.append(f"note: {query['note']}")
    return " · ".join(parts)