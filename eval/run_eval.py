"""Extraction quality eval — precision/recall/F1 for is_real_estate + field accuracy.

Runs the LeadExtractor over the labeled dev set (eval/labeled_sample.jsonl)
and reports per-field scores. Works fully offline via the regex provider, and
with --provider mistral/ollama measures the LLM chain the same way:

    python eval/run_eval.py                    # regex provider
    python eval/run_eval.py --provider mistral # needs MISTRAL_API_KEY
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.extractor import LeadExtractor  # noqa: E402

FIELDS = ("deal_type", "property_type", "city", "district", "price",
          "currency", "area_sqm", "rooms", "contact", "urgency")


def load_sample() -> list[dict]:
    path = Path(__file__).parent / "labeled_sample.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def norm(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().rstrip(".")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", default="regex",
                    choices=["regex", "mistral", "ollama"])
    args = ap.parse_args()

async def evaluate_all(args) -> None:
    extractor = LeadExtractor(provider=args.provider)
    sample = load_sample()

    tp = fp = fn = 0
    field_hits: dict[str, int] = {f: 0 for f in FIELDS}
    field_total: dict[str, int] = {f: 0 for f in FIELDS}

    for row in sample:
        pred = await extractor.extract(row["text"])
        truth = row["labels"]

        # binary classification metrics for is_real_estate
        p, t = bool(pred.get("is_real_estate")), bool(truth["is_real_estate"])
        if t and p:
            tp += 1
        elif t and not p:
            fn += 1
        elif not t and p:
            fp += 1

        # field-level exact match (only where ground truth exists)
        for field in FIELDS:
            if field in truth:
                field_total[field] += 1
                pv = pred.get(field)
                tv = truth[field]
                ok = (
                    (isinstance(tv, (int, float)) and isinstance(pv, (int, float))
                     and abs(float(pv) - float(tv)) <= max(abs(tv) * 0.01, 1))
                    or (isinstance(tv, str) and norm(pv) == norm(tv))
                )
                if tv == "contact" and pv:
                    digits = lambda s: "".join(c for c in str(s) if c.isdigit())[-9:]  # noqa: E731
                    ok = bool(digits(pv)) and digits(pv) == digits(tv)
                if ok:
                    field_hits[field] += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    print(f"Provider: {args.provider} | labeled sample: {len(sample)} texts\n")
    print(f"is_real_estate detection:")
    print(f"  precision: {precision:.2f}  recall: {recall:.2f}  f1: {f1:.2f}\n")
    print("Field extraction accuracy (exact-match where labeled):")
    for field in FIELDS:
        if field_total[field]:
            rate = field_hits[field] / field_total[field]
            bar = "#" * round(rate * 20)
            print(f"  {field:>14}: {field_hits[field]:>2}/{field_total[field]:<2} "
                  f"{rate:>5.0%} {bar}")


if __name__ == "__main__":
    ap_cli = argparse.ArgumentParser(description=__doc__)
    ap_cli.add_argument("--provider", default="regex",
                        choices=["regex", "mistral", "ollama"])
    asyncio.run(evaluate_all(ap_cli.parse_args()))
