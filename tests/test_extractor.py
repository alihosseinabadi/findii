"""Multilingual extraction conformance — EN / RU / RU-translit / Persian.

Guards the regex fallback (works with zero API keys so the CRM sandbox
and bot always function) against regressions: price multipliers, Persian
Indic digits, room slang, spam negation.
"""
import asyncio
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai.extractor import LeadExtractor  # noqa: E402

CASES = [
    # (text, expect_is_real_estate, expect_deal, expect_property, expect_price_or_None)
    ("2k kvartira sdayotsya Tverskaya 5, Moscow, phone +7 916 111-22-33, 80000 RUB/month",
     True, "rent", "apartment", 80000.0),
    ("PRODAM 3-kom kvartira Lenina 12, Rostov, 6.5 mln rub, tel 8 909 555 44 33",
     True, "sell", "apartment", 6500000.0),
    ("Arqa nimaga umumi 120m, tehran, 2.5 mlrd toman",
     True, "unknown", "unknown", 2500000000.0),
    ("hello there this is just random chatter no property",
     False, "unknown", "unknown", None),
    ("اجاره آپارتمان ۷۵ متری سعادت آباد تهران ۵۰ میلیون تومان",
     True, "rent", "apartment", 50000000.0),
    ("اجاره آپارتمان ۷۵ متری سعادت آباد تهران ۵۰ میلیون تومان، تماس ۰۹۱۲۱۱۱۲۲۳۳",
     True, "rent", "apartment", 50000000.0),
    ("Продам 2кв 54 метра Маяковская, 12 млн руб, срочно",
     True, "sell", "apartment", 12000000.0),
    ("خانه ویلایی ۳۰۰ متری با استخر، زعفرانیه تهران، ۴۰ میلیارد تومان فروشی",
     True, "sell", "house", 40000000000.0),
    ("2к квартира сдается у метро Маяковская, телефон 8 495 111 22 33, 150000 rub",
     True, "rent", "apartment", 150000.0),
]


def _run():
    ex = LeadExtractor(provider="regex")

    async def go():
        failures = []
        for text, re_, deal, prop, price in CASES:
            d = await ex.extract(text)
            asserts = [
                (d["is_real_estate"], re_),
                (d.get("deal_type"), deal),
                (d.get("property_type"), prop),
                (d.get("price"), price),
            ]
            case_failures: list[str] = []
            for got, want in asserts:
                if got != want:
                    case_failures.append(f"got {got!r}, want {want!r}")
            if case_failures:
                failures.append((text, case_failures))
                print(f"FAIL {text[:45]!r}: {'; '.join(case_failures)}")
            else:
                print(f"PASS {text[:45]} -> re={d['is_real_estate']} "
                      f"deal={d.get('deal_type')} prop={d.get('property_type')} "
                      f"price={d.get('price')}")
        return failures

    failures = asyncio.run(go())
    if failures:
        print(f"EXTRACTOR TESTS: {len(CASES) - len(failures)}/{len(CASES)} ok")
        return 1
    print(f"ALL {len(CASES)} EXTRACTOR TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())