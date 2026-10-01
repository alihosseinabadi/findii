import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\ali hosseinabadi\findii")
from scrapers import osm_places
from core.geomatch import GeoMatcher
inv = osm_places.load_inventory(
    r"C:\Users\ali hosseinabadi\findii\data\osm\Moscow.inventory.json")
m = GeoMatcher(inv)
print("matcher ready:", m.ready, m.stats)
tests = [
    ("Tverskaya 5, Moscow. 2br for sale. +7 916 111-22-33", "", "Moscow"),
    ("office for rent on Arbat, 60 m2", "", "Moscow"),
    ("2br apartment, nice district, no address given", "Tverskoy", "Moscow"),
    ("villa with pool, call for details", "", ""),
]
for t, d, c in tests:
    print("IN :", t)
    print("OUT:", m.match(t, d, c))
    print()
