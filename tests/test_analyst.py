"""Analyst layer conformance: md paper + visual html + png charts."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE)

import config  # noqa: E402

config.OSM_CACHE_DIR = os.path.join(BASE, "data", "osm")

from scrapers.lead_scraper import run_demand  # noqa: E402


def main() -> int:
    r = run_demand("فروشگاه تهران", config.OSM_CACHE_DIR,
                   os.path.join(BASE, "data", "exports"))
    if r.get("error"):
        print(f"FAIL demo scrape: {r['error']}")
        return 1
    an = r.get("analyst") or {}
    required = ("md", "html", "png_districts", "png_groups",
                "png_density", "png_coverage")
    missing = [k for k in required if not an.get(k)
               or not os.path.exists(an[k]) or os.path.getsize(an[k]) == 0]
    if missing:
        print(f"FAIL analyst artifacts missing/empty: {missing}")
        return 1
    md = open(an["md"], encoding="utf-8").read()
    html = open(an["html"], encoding="utf-8").read()
    checks = {
        "md has title": "# Analyst paper" in md,
        "md has breakdown": "Leads by district" in md or "districts" in md.lower(),
        "html is self-contained (base64 img)": "data:image/png;base64" in html,
        "html has lead table": "<table>" in html,
        "count surfaced": f"{r['count']}" in html,
    }
    ok = all(checks.values())
    for name, passed in checks.items():
        print(("PASS" if passed else "FAIL") + " " + name)
    print(f"ALL ANALYST TESTS {'PASSED' if ok and not missing else 'FAILED'}")
    return 0 if ok and not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())