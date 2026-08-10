"""
Fetch all current Willys offers for Karlskrona.

Willys business publicId: c371GA
"""

import json
from datetime import datetime, timezone

import requests

import db


API_URL = "https://api.etilbudsavis.dk/v2/offers"

STORE_NAME = "Willys"
BUSINESS_PUBLIC_ID = "c371GA"

PAGE_SIZE = 50
MAX_PAGES = 100


def fetch_page(offset):
    params = {
        "dealer_id": BUSINESS_PUBLIC_ID,
        "limit": PAGE_SIZE,
        "offset": offset,
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=20,
        headers={
            "User-Agent": "Prispulsen/1.0"
        },
    )

    if not response.ok:
        raise RuntimeError(
            f"Tjek returned HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("data", "offers", "results"):
            if isinstance(data.get(key), list):
                return data[key]

    raise RuntimeError(
        f"Unexpected Tjek response shape: {type(data).__name__}"
    )


def parse_offer(raw):
    pricing = raw.get("pricing") or {}
    price = pricing.get("price")

    quantity = raw.get("quantity") or {}
    unit = quantity.get("unit") or {}
    size = quantity.get("size") or {}
    si = unit.get("si") or {}

    unit_price = None
    base_unit = None

    size_from = size.get("from")
    factor = si.get("factor")

    if price is not None and size_from and factor:
        try:
            size_from = float(size_from)
            factor = float(factor)

            if size_from > 0 and factor > 0:
                unit_price = round(
                    price / (size_from * factor),
                    2
                )
                base_unit = si.get("symbol")

        except (TypeError, ValueError, ZeroDivisionError):
            pass

    dealer = raw.get("dealer") or {}
    branding = raw.get("branding") or {}

    return {
        "name": raw.get("heading"),
        "description": raw.get("description"),
        "price": price,
        "unit_price": unit_price,
        "base_unit": base_unit,
        "business": (
            dealer.get("name")
            or branding.get("name")
        ),
        "valid_from": raw.get("run_from"),
        "valid_until": raw.get("run_till"),
    }


def main():
    db.init_db()

    fetched_at = datetime.now(timezone.utc).isoformat()

    seen_offer_ids = set()
    seen_content_keys = set()

    total_received = 0
    total_willys = 0
    total_stored = 0

    for page in range(MAX_PAGES):

        offset = page * PAGE_SIZE

        print(
            f"Fetching Willys offers: "
            f"offset={offset} ..."
        )

        try:
            offers = fetch_page(offset)

        except requests.RequestException as exc:
            raise SystemExit(
                f"Request failed: {exc}"
            )

        except RuntimeError as exc:
            raise SystemExit(str(exc))

        if not offers:
            print("No more offers.")
            break

        total_received += len(offers)

        page_stored = 0

        for raw in offers:

            dealer = raw.get("dealer") or {}
            branding = raw.get("branding") or {}

            business_id = (
                raw.get("dealer_id")
                or raw.get("businessPublicId")
                or dealer.get("publicId")
                or branding.get("publicId")
            )

            if business_id != BUSINESS_PUBLIC_ID:
                continue

            parsed = parse_offer(raw)

            if (
                parsed["business"]
                and parsed["business"].lower() != "willys"
            ):
                continue

            total_willys += 1

            offer_id = (
                raw.get("id")
                or raw.get("publicId")
            )

            content_key = (
                STORE_NAME,
                parsed["name"],
                parsed["price"],
                parsed["valid_from"],
                parsed["valid_until"],
            )

            if (
                offer_id
                and offer_id in seen_offer_ids
            ):
                continue

            if content_key in seen_content_keys:
                continue

            if offer_id:
                seen_offer_ids.add(offer_id)

            seen_content_keys.add(content_key)

            db.insert_offer(
                STORE_NAME,
                parsed,
                json.dumps(
                    raw,
                    ensure_ascii=False
                ),
                fetched_at,
            )

            total_stored += 1
            page_stored += 1

        print(
            f"  received={len(offers)}, "
            f"willys={total_willys}, "
            f"new_stored={page_stored}"
        )

        if len(offers) < PAGE_SIZE:
            break

    print()
    print("=== Willys fetch complete ===")
    print(f"Offers received: {total_received}")
    print(f"Willys offers:   {total_willys}")
    print(f"New DB rows:     {total_stored}")


if __name__ == "__main__":
    main()