"""
Exports price_history from the local SQLite DB into static JSON files,
which the GitHub Pages frontend fetches directly — no live backend or
database needed at serving time (see project-plan.md for why).

Output structure (under public/data/):
    current.json          — this week's active offers (what the live
                             table shows)
    products.json          — index of every product we have any history
                             for, with a filename-safe slug
    history/<slug>.json    — full price history for one product, across
                             all stores and weeks, for graphing
    meta.json               — last-updated timestamp

Run with:
    python export.py
"""

import json
import re
import unicodedata
from pathlib import Path
from collections import defaultdict

import db

OUTPUT_DIR = Path(__file__).parent / "public" / "data"


def slugify(name, used_slugs):
    """Turn a product name into a safe, unique filename."""
    slug = name.strip().lower()
    slug = unicodedata.normalize("NFKD", slug)
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"\s+", "-", slug).strip("-") or "unknown"

    # Guard against different names slugifying to the same string
    # (e.g. names differing only in punctuation we just stripped).
    base_slug = slug
    n = 2
    while slug in used_slugs:
        slug = f"{base_slug}-{n}"
        n += 1
    used_slugs.add(slug)
    return slug


def row_to_dict(row):
    return {
        "store": row["store_name"],
        "product": row["product_name"],
        "price": row["price"],
        "unit_price": row["unit_price"],
        "base_unit": row["base_unit"],
        "valid_from": row["valid_from"],
        "valid_until": row["valid_until"],
    }


def main():
    db.init_db()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "history").mkdir(exist_ok=True)

    # 1. Current deals — what the live table/page shows.
    current = [row_to_dict(r) for r in db.get_current_offers()]
    with open(OUTPUT_DIR / "current.json", "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    print(f"Wrote current.json ({len(current)} offers)")

    # 2. Full history, grouped by product name, for per-product graphs.
    # NOTE: grouped by raw product_name string — no cross-store product
    # matching yet (see project-plan.md open questions). "Tomater" at
    # one store and "Tomat" at another currently count as different
    # products; that's the next real piece of work, not this script.
    all_rows = db.get_all_offers()
    by_product = defaultdict(list)
    for row in all_rows:
        if row["product_name"]:
            by_product[row["product_name"]].append(row_to_dict(row))

    used_slugs = set()
    product_index = []
    for product_name, entries in by_product.items():
        slug = slugify(product_name, used_slugs)
        entries.sort(key=lambda e: e["valid_from"] or "")
        with open(OUTPUT_DIR / "history" / f"{slug}.json", "w", encoding="utf-8") as f:
            json.dump(
                {"product": product_name, "entries": entries},
                f, ensure_ascii=False, indent=2,
            )
        product_index.append({
            "name": product_name, "slug": slug, "entry_count": len(entries),
        })

    product_index.sort(key=lambda p: p["name"])
    with open(OUTPUT_DIR / "products.json", "w", encoding="utf-8") as f:
        json.dump(product_index, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(product_index)} per-product history files + products.json")

    # 3. Metadata — last update time, for the frontend to display.
    meta = {"last_updated": db.get_last_fetch_time()}
    with open(OUTPUT_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("Wrote meta.json")


if __name__ == "__main__":
    main()
