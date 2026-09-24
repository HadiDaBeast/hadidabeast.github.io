import json
from datetime import datetime, timezone

import requests
from requests.exceptions import RequestException

from app import db

BUSINESS_PUBLIC_ID = "c371GA"
STORE_NAME = "Willys"
PAGE_SIZE = 50
MAX_PAGES = 100


def fetch_page(offset, api_url):
    params = {
        "dealer_id": BUSINESS_PUBLIC_ID,
        "limit": PAGE_SIZE,
        "offset": offset,
    }
    response = requests.get(
        api_url,
        params=params,
        timeout=20,
        headers={"User-Agent": "Prispulsen/1.0"},
    )

    if not response.ok:
        raise RuntimeError(
            f"Tjek returned HTTP {response.status_code}: {response.text[:1000]}"
        )

    data = response.json()

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("data", "offers", "results"):
            if isinstance(data.get(key), list):
                return data[key]

    raise RuntimeError(f"Unexpected Tjek response shape: {type(data).__name__}")


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
            size_from_f = float(size_from)
            factor_f = float(factor)
            if size_from_f > 0 and factor_f > 0:
                unit_price = round(price / (size_from_f * factor_f), 2)
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
        "business": dealer.get("name") or branding.get("name"),
        "valid_from": raw.get("run_from"),
        "valid_until": raw.get("run_till"),
    }


def run_willys_ingestion(api_url, run_id):
    fetched_at = datetime.now(timezone.utc).isoformat()
    categories = db.load_categories()

    seen_offer_ids: set = set()
    seen_content_keys: set = set()

    total_stored = 0
    errors = 0

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE

        try:
            raw_offers = fetch_page(offset, api_url)
        except (RequestException, RuntimeError):
            errors += 1
            break

        if not raw_offers:
            break

        for raw in raw_offers:
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

            if parsed["business"] and parsed["business"].lower() != "willys":
                continue

            offer_id = raw.get("id") or raw.get("publicId")
            content_key = (
                STORE_NAME,
                parsed["name"],
                parsed["price"],
                parsed["valid_from"],
                parsed["valid_until"],
            )

            if offer_id and offer_id in seen_offer_ids:
                continue
            if content_key in seen_content_keys:
                continue

            if offer_id:
                seen_offer_ids.add(offer_id)
            seen_content_keys.add(content_key)

            category = db.categorize(parsed.get("name"), categories)

            try:
                db.insert_offer(
                    STORE_NAME,
                    parsed,
                    json.dumps(raw, ensure_ascii=False),
                    fetched_at,
                    category=category,
                )
                total_stored += 1
            except Exception:
                errors += 1

        if len(raw_offers) < PAGE_SIZE:
            break

    return {"offers_stored": total_stored, "errors": errors}
