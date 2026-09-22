"""
Tests for ingestion-service: parse_offer() and run_ingestion() logic.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from requests.exceptions import RequestException

from app.ingestion import parse_offer, run_ingestion, STORES, QUERIES


# ---------------------------------------------------------------------------
# parse_offer tests
# ---------------------------------------------------------------------------


def _make_raw(
    heading="Mjölk",
    price=12.90,
    size_from=1.0,
    factor=1.0,
    symbol="kg",
    include_quantity=True,
):
    """Build a minimal raw offer dict suitable for parse_offer()."""
    raw = {
        "heading": heading,
        "description": "Hel mjölk 1 l",
        "pricing": {"price": price},
        "dealer": {"name": "ICA Supermarket"},
        "run_from": "2026-09-01",
        "run_till": "2026-09-07",
    }
    if include_quantity:
        raw["quantity"] = {
            "unit": {"si": {"factor": factor, "symbol": symbol}},
            "size": {"from": size_from},
        }
    return raw


class TestParseOfferWithUnitPrice:
    def test_parse_offer_with_unit_price(self):
        """Valid offer with full quantity data returns correct unit_price."""
        raw = _make_raw(price=39.80, size_from=1.0, factor=1.0, symbol="kg")
        result = parse_offer(raw)

        assert result["name"] == "Mjölk"
        assert result["price"] == 39.80
        assert result["unit_price"] == 39.80  # 39.80 / (1.0 * 1.0)
        assert result["base_unit"] == "kg"
        assert result["business"] == "ICA Supermarket"
        assert result["valid_from"] == "2026-09-01"
        assert result["valid_until"] == "2026-09-07"

    def test_parse_offer_unit_price_with_conversion_factor(self):
        """SI factor != 1 (e.g. grams→kg) computes unit_price correctly."""
        # 500 g of butter for 35.00 kr → 70.00 kr/kg
        raw = _make_raw(price=35.00, size_from=500.0, factor=0.001, symbol="kg")
        result = parse_offer(raw)

        assert result["unit_price"] == round(35.00 / (500.0 * 0.001), 2)
        assert result["base_unit"] == "kg"


class TestParseOfferMissingQuantity:
    def test_parse_offer_missing_quantity(self):
        """Offer without quantity block returns unit_price=None."""
        raw = _make_raw(include_quantity=False)
        result = parse_offer(raw)

        assert result["unit_price"] is None
        assert result["base_unit"] is None
        assert result["name"] == "Mjölk"
        assert result["price"] == 12.90

    def test_parse_offer_empty_quantity(self):
        """Offer with empty quantity dict returns unit_price=None."""
        raw = _make_raw(include_quantity=False)
        raw["quantity"] = {}
        result = parse_offer(raw)

        assert result["unit_price"] is None


class TestParseOfferMalformedPrice:
    def test_parse_offer_malformed_price_string(self):
        """Non-numeric price (string) does not crash; unit_price stays None."""
        raw = _make_raw(include_quantity=True)
        raw["pricing"] = {"price": "not-a-number"}
        result = parse_offer(raw)

        # price itself may come through as the raw string value since
        # parse_offer doesn't validate price type, but unit_price must be None
        # because the computation will fail (can't divide string by float)
        assert result["unit_price"] is None

    def test_parse_offer_none_price(self):
        """None price returns unit_price=None without crashing."""
        raw = _make_raw(include_quantity=True)
        raw["pricing"] = {"price": None}
        result = parse_offer(raw)

        assert result["price"] is None
        assert result["unit_price"] is None

    def test_parse_offer_missing_pricing_block(self):
        """Missing pricing block entirely returns price=None without crash."""
        raw = _make_raw(include_quantity=False)
        del raw["pricing"]
        result = parse_offer(raw)

        assert result["price"] is None
        assert result["unit_price"] is None


class TestParseOfferZeroSize:
    def test_parse_offer_zero_size_from(self):
        """size_from=0 must not cause ZeroDivisionError; unit_price=None."""
        raw = _make_raw(price=10.00, size_from=0.0, factor=1.0)
        result = parse_offer(raw)

        assert result["unit_price"] is None

    def test_parse_offer_zero_factor(self):
        """factor=0 must not cause ZeroDivisionError; unit_price=None."""
        raw = _make_raw(price=10.00, size_from=1.0, factor=0.0)
        result = parse_offer(raw)

        assert result["unit_price"] is None


# ---------------------------------------------------------------------------
# run_ingestion tests
# ---------------------------------------------------------------------------

def _make_raw_offer(offer_id="offer-001", name="Bröd", price=15.00):
    return {
        "id": offer_id,
        "heading": name,
        "description": "Levain 500g",
        "pricing": {"price": price},
        "dealer": {"name": "Lidl"},
        "run_from": "2026-09-01",
        "run_till": "2026-09-07",
    }


class TestRunIngestionDeduplication:
    def test_run_ingestion_deduplication(self):
        """
        If fetch_offers returns the same offer twice (same id and content),
        insert_offer must be called exactly once.
        """
        duplicate = _make_raw_offer("dup-001", "Mjölk", 12.90)

        with patch("app.ingestion.fetch_offers", return_value=[duplicate, duplicate]) as mock_fetch, \
             patch("app.ingestion.db.insert_offer") as mock_insert:

            result = run_ingestion(db_pool=MagicMock(), api_url="http://fake", run_id=1)

        # fetch_offers called once per (store, query) combination
        assert mock_fetch.call_count == len(STORES) * len(QUERIES)

        # Despite many calls returning a duplicate pair, each unique offer
        # should only be inserted once.
        # All calls return the same offer_id → only 1 insert total.
        assert mock_insert.call_count == 1
        assert result["offers_stored"] == 1

    def test_run_ingestion_deduplication_same_content_different_id(self):
        """
        Two offers with different IDs but identical content key are deduped.
        """
        offer_a = _make_raw_offer("id-aaa", "Bröd", 20.00)
        offer_b = {**offer_a, "id": "id-bbb"}  # same content, different id

        with patch("app.ingestion.fetch_offers", return_value=[offer_a, offer_b]), \
             patch("app.ingestion.db.insert_offer") as mock_insert:

            run_ingestion(db_pool=MagicMock(), api_url="http://fake", run_id=2)

        # offer_a and offer_b map to the same content_key → only 1 insert
        assert mock_insert.call_count == 1


class TestRunIngestionApiFailure:
    def test_run_ingestion_api_failure_single_task(self):
        """
        If fetch_offers raises RequestException for some tasks, other tasks
        are still processed and insert_offer is called for their offers.
        """
        good_offer = _make_raw_offer("good-001", "Ägg", 39.90)

        call_count = {"n": 0}

        def selective_fetch(lat, lng, query, api_url):
            call_count["n"] += 1
            # Fail the very first call, succeed for all others
            if call_count["n"] == 1:
                raise RequestException("Timeout")
            return [good_offer]

        with patch("app.ingestion.fetch_offers", side_effect=selective_fetch), \
             patch("app.ingestion.db.insert_offer") as mock_insert:

            result = run_ingestion(db_pool=MagicMock(), api_url="http://fake", run_id=3)

        total_tasks = len(STORES) * len(QUERIES)  # 7 * 10 = 70
        assert call_count["n"] == total_tasks

        # 1 failure recorded
        assert result["errors"] == 1

        # Remaining tasks still inserted (deduped: all return same offer_id
        # so after first successful insert the rest are deduped → 1 insert)
        assert mock_insert.call_count >= 1

    def test_run_ingestion_all_failures(self):
        """If all fetch_offers calls fail, errors == total tasks and offers_stored == 0."""
        with patch("app.ingestion.fetch_offers", side_effect=RequestException("down")), \
             patch("app.ingestion.db.insert_offer") as mock_insert:

            result = run_ingestion(db_pool=MagicMock(), api_url="http://fake", run_id=4)

        assert result["offers_stored"] == 0
        assert result["errors"] == len(STORES) * len(QUERIES)
        mock_insert.assert_not_called()
