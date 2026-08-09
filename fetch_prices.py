"""
Fetches current offers for all 7 Karlskrona stores and stores them in
the local SQLite DB. Rows ACCUMULATE across runs (deduped by store +
product + price + validity window — see db.py) rather than being wiped,
so running this weekly builds up real price history over time.

IMPORTANT LIMITATION: the API is search-by-keyword, not "give me this
store's full flyer". So "all current deals" is approximated here by
searching a basket of common grocery terms (QUERIES below) across each
store's location. This is a reasonable v1 approach, but isn't a true
full-flyer pull — worth revisiting once/if a full-publication endpoint
is confirmed (see project-plan.md open questions).

Run directly for a one-off fetch:
    pip install requests
    python fetch_prices.py
"""

import requests
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import db

API_URL = "https://api.etilbudsavis.dk/v2/offers/search"

# Coordinates collected during planning — see project-plan.md
STORES = {
    "Lidl": (56.16927, 15.58494),
    "Hemköp": (56.1624, 15.5882),
    "Willys": (56.1737, 15.5878),
    "City Gross": (56.1967311, 15.6129799),
    "ICA Supermarket Cityhallen": (56.16107, 15.58253),
    "ICA Maxi Stormarknad": (56.19597, 15.64119),
    "Coop X:-tra": (56.2169643, 15.6422651),
}

# Basket of common grocery search terms — stand-in for "all current
# deals" until a full-flyer-browse endpoint is confirmed.
QUERIES = [
    "mjölk", "ägg", "bröd", "tomat", "kyckling",
    "ost", "smör", "kaffe", "bananer", "nötfärs",
]

RADIUS_METERS = "3000"
MAX_WORKERS = 5  # modest cap — polite to an unofficial API, not maxed out


def fetch_offers(lat, lng, query):
    params = {
        "r_lat": lat, "r_lng": lng, "r_radius": RADIUS_METERS,
        "r_locale": "sv_SE", "api_av": "0.3.0", "query": query,
        "offset": "0", "limit": "24",
    }
    resp = requests.get(API_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data) if isinstance(data, dict) else data


def parse_offer(raw):
    """Extract normalized fields from a raw offer object.

    CONFIRMED real schema (2026-08-09, verified against actual data —
    see project-plan.md): this endpoint has NO unitPrice/baseUnit field.
    Instead, price-per-kg must be computed from `pricing.price` and
    `quantity` (package size + SI conversion factor). Verified this
    computation exactly matches the store's own printed "jämförpris" on
    two real offers (39.80 kr/kg and 40.00 kr/kg), so it's reliable.
    """
    name = raw.get("heading")
    description = raw.get("description")

    pricing = raw.get("pricing") or {}
    price = pricing.get("price")

    dealer = raw.get("dealer") or {}
    branding = raw.get("branding") or {}
    business = dealer.get("name") or branding.get("name")

    valid_from = raw.get("run_from")
    valid_until = raw.get("run_till")

    unit_price = None
    base_unit = None
    quantity = raw.get("quantity") or {}
    unit = quantity.get("unit") or {}
    size = quantity.get("size") or {}
    si = unit.get("si") or {}
    size_from = size.get("from")

    if price is not None and size_from and si.get("factor"):
        try:
            size_from = float(size_from)
            factor = float(si["factor"])
            if size_from > 0 and factor > 0:
                base_qty = size_from * factor  # converts to the SI unit (e.g. kg)
                unit_price = round(price / base_qty, 2)
                base_unit = si.get("symbol")
        except (TypeError, ValueError, ZeroDivisionError):
            pass  # some offers are piece-based / lack size data — leave as None

    return {
        "name": name, "description": description, "price": price,
        "unit_price": unit_price, "base_unit": base_unit,
        "business": business, "valid_from": valid_from,
        "valid_until": valid_until,
    }


def main():
    db.init_db()
    fetched_at = datetime.now(timezone.utc).isoformat()

    # Two layers of dedup: by offer ID (catches the same object returned
    # twice), AND by content fingerprint — store+product+price+validity —
    # since the API appears to sometimes give the same real flyer item a
    # different ID per occurrence (e.g. printed on two catalog pages).
    # The content fingerprint is what actually matters to the user: no
    # value in showing "Willys — Baguette — 12.20 kr" twice regardless of
    # why the duplicate exists underneath.
    seen_offer_ids = set()
    seen_content_keys = set()
    lock = threading.Lock()  # guards both sets + sqlite writes
    total = 0

    tasks = [
        (store_name, lat, lng, query)
        for store_name, (lat, lng) in STORES.items()
        for query in QUERIES
    ]

    def run_task(store_name, lat, lng, query):
        nonlocal total
        try:
            offers = fetch_offers(lat, lng, query)
        except requests.RequestException as e:
            print(f"  ERROR ({store_name}, {query}): {e}")
            return

        for raw in offers:
            offer_id = raw.get("id") or raw.get("publicId")
            parsed = parse_offer(raw)
            # IMPORTANT: use the offer's actual publishing business
            # (parsed["business"]), not `store_name` (the location we
            # searched around) — see project-plan.md for why.
            actual_store = parsed["business"] or f"Unknown (near {store_name})"
            content_key = (
                actual_store, parsed["name"], parsed["price"], parsed["valid_until"],
            )

            with lock:
                if offer_id and offer_id in seen_offer_ids:
                    continue
                if content_key in seen_content_keys:
                    continue
                if offer_id:
                    seen_offer_ids.add(offer_id)
                seen_content_keys.add(content_key)
                db.insert_offer(
                    actual_store, parsed,
                    json.dumps(raw, ensure_ascii=False), fetched_at,
                )
                total += 1

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(run_task, *t) for t in tasks]
        for i, future in enumerate(as_completed(futures), 1):
            future.result()  # re-raises any exception from a worker thread
            print(f"  {i}/{len(tasks)} requests done", end="\r")

    print(f"\nDone. Stored {total} offers.")


if __name__ == "__main__":
    main()
