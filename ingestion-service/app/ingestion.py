"""
ingestion-service — Etilbudsavis/Tjek offer ingestion.

Ports all logic from the original fetch_prices.py, adapted to run inside
the microservice (PostgreSQL pool, structured logging, advisory lock handled
in main.py's background thread).
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from requests.exceptions import RequestException

from app import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# All 7 Karlskrona stores with (lat, lng) coordinates.
STORES = {
    "Lidl": (56.16927, 15.58494),
    "Hemköp": (56.1624, 15.5882),
    "Willys": (56.1737, 15.5878),
    "City Gross": (56.1967311, 15.6129799),
    "ICA Supermarket Cityhallen": (56.16107, 15.58253),
    "ICA Maxi Stormarknad": (56.19597, 15.64119),
    "Coop X:-tra": (56.2169643, 15.6422651),
}

# Common grocery search terms used to approximate "all current deals".
QUERIES = [
    "mjölk", "ägg", "bröd", "tomat", "kyckling",
    "ost", "smör", "kaffe", "bananer", "nötfärs",
]

RADIUS_METERS = "3000"
MAX_WORKERS = 5


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_offers(lat, lng, query, api_url):
    """
    Call the Etilbudsavis search endpoint and return the raw list of offers.

    Raises requests.RequestException on network/HTTP errors — callers
    should catch and handle gracefully.
    """
    params = {
        "r_lat": lat,
        "r_lng": lng,
        "r_radius": RADIUS_METERS,
        "r_locale": "sv_SE",
        "api_av": "0.3.0",
        "query": query,
        "offset": "0",
        "limit": "24",
    }
    resp = requests.get(api_url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data) if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_offer(raw):
    """
    Normalise a raw offer object from the Etilbudsavis API.

    Unit price is computed from pricing.price and quantity (package size
    × SI conversion factor) — this matches the store's own printed
    "jämförpris" exactly (verified against two real offers in 2026-08-09
    testing; see project-plan.md).

    Returns a dict with keys: name, description, price, unit_price,
    base_unit, business, valid_from, valid_until.
    If price is non-numeric or quantity data is missing/zero, unit_price
    is returned as None.
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
            size_from_f = float(size_from)
            factor = float(si["factor"])
            if size_from_f > 0 and factor > 0:
                base_qty = size_from_f * factor  # converts to SI unit (e.g. kg)
                unit_price = round(price / base_qty, 2)
                base_unit = si.get("symbol")
        except (TypeError, ValueError, ZeroDivisionError):
            pass  # piece-based or missing size data — leave as None

    return {
        "name": name,
        "description": description,
        "price": price,
        "unit_price": unit_price,
        "base_unit": base_unit,
        "business": business,
        "valid_from": valid_from,
        "valid_until": valid_until,
    }


# ---------------------------------------------------------------------------
# Main ingestion loop
# ---------------------------------------------------------------------------


def run_ingestion(api_url, run_id):
    """
    Fetch offers for all stores × all queries with a thread pool.

    Deduplicates by offer_id AND content_key before inserting.
    Uses db.insert_offer() for each new offer.

    Returns a dict: {offers_stored: int, errors: int}.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()

    # Load categories once for the entire run
    categories = db.load_categories()
    logger.info("Loaded %s categories for run_id=%s", len(categories), run_id)

    seen_offer_ids: set = set()
    seen_content_keys: set = set()
    lock = threading.Lock()  # guards both sets and DB writes

    offers_stored = 0
    errors = 0

    tasks = [
        (store_name, lat, lng, query)
        for store_name, (lat, lng) in STORES.items()
        for query in QUERIES
    ]

    def run_task(store_name, lat, lng, query):
        """Returns number of new offers stored for this task; 0 on error."""
        nonlocal errors
        try:
            raw_offers = fetch_offers(lat, lng, query, api_url)
        except RequestException as exc:
            logger.error(
                "Fetch failed (store=%s query=%s run_id=%s): %s",
                store_name, query, run_id, exc,
            )
            with lock:
                errors += 1
            return 0

        stored_this_task = 0
        for raw in raw_offers:
            offer_id = raw.get("id") or raw.get("publicId")
            parsed = parse_offer(raw)

            # Use the offer's actual publishing business, not the searched
            # store name — see fetch_prices.py and project-plan.md for why.
            actual_store = parsed["business"] or f"Unknown (near {store_name})"
            content_key = (
                actual_store,
                parsed["name"],
                parsed["price"],
                parsed["valid_until"],
            )

            with lock:
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
                        actual_store,
                        parsed,
                        json.dumps(raw, ensure_ascii=False),
                        fetched_at,
                        category=category,
                    )
                    stored_this_task += 1
                except Exception as db_exc:  # noqa: BLE001
                    logger.error(
                        "DB insert failed (store=%s run_id=%s): %s",
                        actual_store, run_id, db_exc,
                    )
                    errors += 1

        return stored_this_task

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(run_task, *t): t for t in tasks}
        for future in as_completed(futures):
            try:
                count = future.result()
                with lock:
                    offers_stored += count
            except Exception as exc:  # noqa: BLE001
                logger.error("Unexpected worker error (run_id=%s): %s", run_id, exc)
                with lock:
                    errors += 1

    logger.info(
        "Ingestion complete: run_id=%s offers_stored=%s errors=%s",
        run_id, offers_stored, errors,
    )
    return {"offers_stored": offers_stored, "errors": errors}
