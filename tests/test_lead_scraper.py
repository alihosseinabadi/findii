"""OSM lead-scraper conformance against the cached city maps."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE)

import config  # noqa: E402

config.OSM_CACHE_DIR = os.path.join(BASE, "data", "osm")

from scrapers.lead_scraper import run_demand  # noqa: E402

CASES = [
    ("فروشگاه تهران", "Tehran", "Grocery", 500),    # Tehran map cached
    ("hotel moscow", "Moscow", "Hotel", 500),        # Moscow map cached
    ("cafe moscow", "Moscow", "Cafe", 300),
    ("tehran", "Tehran", "All Businesses", 5000),    # city-only -> directory
    # landmark geo-scoping: same category, tiny radius -> far fewer rows
    ("cafe red square moscow", "Moscow", "Cafe", 1),
    ("grocery arbat moscow", "Moscow", "Grocery", 1),
]


def main() -> int:
    failures = 0
    for q, city, cat, min_rows in CASES:
        r = run_demand(q, config.OSM_CACHE_DIR, os.path.join(BASE, "data", "exports"))
        if r.get("error"):
            print(f"FAIL {q!r}: {r['error']}")
            failures += 1
            continue
        files = r.get("files", {})
        if not (files.get("xlsx") and files.get("csv")):
            print(f"FAIL {q!r}: export files missing")
            failures += 1
            continue
        for p in (files["xlsx"], files["csv"]):
            if not os.path.exists(p) or os.path.getsize(p) == 0:
                print(f"FAIL {q!r}: {os.path.basename(p)} empty/missing")
                failures += 1
        n = r.get("count", 0)
        status = "PASS" if n >= min_rows else "FAIL"
        if status == "FAIL":
            failures += 1
        print(f"{status} {q!r} -> city={city} cat={cat} count={n} "
              f"map={r.get('map_buildings')} files ok")
    if failures:
        print(f"LEAD SCRAPER: {len(CASES) - failures}/{len(CASES)} ok")
        return 1
    print(f"ALL {len(CASES)} LEAD SCRAPER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())