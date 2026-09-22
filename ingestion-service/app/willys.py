"""
ingestion-service — Willys (Tjek) offer ingestion.

Ports all logic from fetch_willys.py, adapted to use the PostgreSQL pool
and structured logging used inside the microservice.
"""

import json
import logging
from datetime import datetime, timezone

import requests
from requests.exceptions import RequestException

from app import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BUSINESS_PUBLIC_ID = "c371GA"  # Willys Karlskrona dealer ID on Tjek
STORE_NAME = "Willys"

PAGE_SIZE = 50
MAX_PAGES = 100


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_page(offset: int, api_url: str) -> list:
    """
    Fetch a single page of offers from the Tjek API.

    Raises requests.RequestException on network errors and RuntimeError on
    unexpected response shapes — callers should handle these.
    """
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


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_offer(raw: dict) -> dict:
    """
    Normalise a raw Willys offer object from the Tjek API.

    Applies the same unit-price computation as the generic ingestion
    module — price / (size_from × SI factor).

    Returns a dict with: name, description, price, unit_price, base_unit,
    business, valid_from, valid_until.
    """
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


# ---------------------------------------------------------------------------
# Main ingestion loop
# ---------------------------------------------------------------------------


def run_willys_ingestion(db_pool, api_url: str, run_id: int) -> dict:
    """
    Paginate through all Willys offers from the Tjek API and store new ones.

    Pages up to MAX_PAGES × PAGE_SIZE offers.  Deduplicates by offer_id
    AND content_key before inserting via db.insert_offer().

    Returns a dict: {offers_stored: int, errors: int}.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()

    seen_offer_ids: set = set()
    seen_content_keys: set = set()

    total_received = 0
    total_stored = 0
    errors = 0

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE

        logger.info(
            "Fetching Willys page: offset=%s run_id=%s", offset, run_id
        )

        try:
            raw_offers = fetch_page(offset, api_url)
        except RequestException as exc:
            logger.error(
                "Willys request failed at offset=%s run_id=%s: %s",
                offset, run_id, exc,
            )
            errors += 1
            break  # can't continue pagination without knowing offset state
        except RuntimeError as exc:
            logger.error(
                "Willys unexpected response at offset=%s run_id=%s: %s",
                offset, run_id, exc,
            )
            errors += 1
            break

        if not raw_offers:
            logger.info("No more Willys offers at offset=%s", offset)
            break

        total_received += len(raw_offers)
        page_stored = 0

        for raw in raw_offers:
            # Verify this offer belongs to the Willys business
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

            # Skip offers attributed to a different brand name
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

            try:
                db.insert_offer(
                    STORE_NAME,
                    parsed,
                    json.dumps(raw, ensure_ascii=False),
                    fetched_at,
                )
                page_stored += 1
                total_stored += 1
            except Exception as db_exc:  # noqa: BLE001
                logger.error(
                    "DB insert failed for Willys offer run_id=%s: %s",
                    run_id, db_exc,
                )
                errors += 1

        logger.info(
            "Willys page done: offset=%s received=%s new_stored=%s run_id=%s",
            offset, len(raw_offers), page_stored, run_id,
        )

        if len(raw_offers) < PAGE_SIZE:
            break  # last page

    logger.info(
        "Willys ingestion complete: run_id=%s total_received=%s "
        "total_stored=%s errors=%s",
        run_id, total_received, total_stored, errors,
    )
    return {"offers_stored": total_stored, "errors": errors}
