"""Query router conformance: free-text demand -> structured scrape request."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai.query_router import parse  # noqa: E402

CASES = [
    # query, expect_city, expect_district, expect_category
    ("new york bakery store", "NewYork", "", "Bakery"),
    ("bakery in manhattan new york", "NewYork", "Manhattan", "Bakery"),
    ("cafe near arbat moscow", "Moscow", "Arbat", "Cafe"),
    ("قهوه در تهران", "Tehran", "", "Cafe"),
    ("restaurant in tehran", "Tehran", "", "Restaurant"),
    ("pharmacy in queens new york", "NewYork", "Queens", "Pharmacy"),
    ("فروشگاه تهران", "Tehran", "", "Grocery"),
    ("hotel in downtown moscow", "Moscow", "", "Hotel"),
    ("gym in brooklyn new york", "NewYork", "Brooklyn", "Gym"),
    ("fitness club in zafaraniyeh tehran", "Tehran", "zafaraniyeh", "Gym"),
    ("moscow", "Moscow", "", "All Businesses"),
    ("new york", "NewYork", "", "All Businesses"),
    ("تهران", "Tehran", "", "All Businesses"),
    # area-after-city + landmark geo-scoping
    ("moscow red square supermarket", "Moscow", "Red Square", "Grocery"),
    ("hotel +7 arbat moscow", "Moscow", "Arbat", "Hotel"),
    ("cafe центральный VDNKh moscow", "Moscow", "VDNKh", "Cafe"),
    ("supermarket times square new york", "NewYork", "Times Square", "Grocery"),
]


def main() -> int:
    failures = []
    for q, city, district, cat in CASES:
        p = parse(q)
        checks = [(p.get("city"), city, "city"), (p.get("district"), district, "district"),
                  (p.get("category"), cat, "category")]
        bad = [f"{k} got {g!r} want {w!r}" for g, w, k in checks if g != w]
        if bad:
            failures.append((q, bad))
            print(f"FAIL {q!r}: {'; '.join(bad)}")
        else:
            print(f"PASS {q!r} -> city={city} district={district or '—'} cat={cat}")
    if failures:
        print(f"QUERY ROUTER: {len(CASES) - len(failures)}/{len(CASES)} ok")
        return 1
    print(f"ALL {len(CASES)} QUERY ROUTER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())