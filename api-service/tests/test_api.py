"""
Tests for api-service endpoints.

All DB calls are mocked so no real database is required.
"""

import pytest
import psycopg2
from unittest.mock import patch, MagicMock
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def disable_lifespan():
    """Disable the real lifespan (which would try to connect to Postgres)."""

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan
    yield


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Helper — minimal offer row
# ---------------------------------------------------------------------------

def _offer(store="ICA", product="Mjölk", price=12.90):
    return {
        "store_name": store,
        "product_name": product,
        "price": price,
        "unit_price": 12.90,
        "base_unit": "kg",
        "valid_from": "2026-09-01",
        "valid_until": "2026-09-07",
    }


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_healthz_ok(self, client):
        """When DB is healthy, /healthz returns 200 and db='ok'."""
        with patch("app.main.db.healthcheck", return_value=(True, "")):
            resp = client.get("/healthz")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"

    def test_healthz_db_down(self, client):
        """When DB healthcheck raises, /healthz still returns 200 with db='error'."""
        with patch("app.main.db.healthcheck", side_effect=psycopg2.OperationalError("conn refused")):
            resp = client.get("/healthz")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["db"] == "error"

    def test_healthz_db_returns_false(self, client):
        """healthcheck() returning (False, msg) → db='error'."""
        with patch("app.main.db.healthcheck", return_value=(False, "timeout")):
            resp = client.get("/healthz")

        assert resp.status_code == 200
        assert resp.json()["db"] == "error"


# ---------------------------------------------------------------------------
# /stores
# ---------------------------------------------------------------------------


class TestStores:
    def test_stores_returns_list(self, client):
        """GET /stores returns a list in deterministic (sorted) order."""
        with patch("app.main.db.get_stores", return_value=["ICA", "Lidl", "Willys"]):
            resp = client.get("/stores")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        names = [s["name"] for s in body]
        assert names == sorted(names)
        assert names == ["ICA", "Lidl", "Willys"]

    def test_stores_empty(self, client):
        """GET /stores returns empty list when no stores exist."""
        with patch("app.main.db.get_stores", return_value=[]):
            resp = client.get("/stores")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_stores_db_error_returns_503(self, client):
        """DB error in /stores returns 503 without SQL details."""
        with patch("app.main.db.get_stores", side_effect=psycopg2.OperationalError("fail")):
            resp = client.get("/stores")

        assert resp.status_code == 503
        body = resp.json()
        assert "error" in body
        assert "Database unavailable" in body["error"]
        # SQL error detail must not leak
        assert "fail" not in body["error"]


# ---------------------------------------------------------------------------
# /products
# ---------------------------------------------------------------------------


class TestProducts:
    def test_products_pagination(self, client):
        """Pagination fields are correctly returned."""
        items = [_offer("ICA", f"Product {i}", 10.0 + i) for i in range(5)]
        with patch("app.main.db.get_current_offers", return_value=(items, 100)):
            resp = client.get("/products?page=2&page_size=5")

        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 2
        assert body["page_size"] == 5
        assert body["total"] == 100
        assert len(body["items"]) == 5
        # Each item has required fields
        for item in body["items"]:
            for key in ("store", "product", "price", "unit_price", "base_unit",
                        "valid_from", "valid_until"):
                assert key in item

    def test_products_default_pagination(self, client):
        """Without query params, defaults are page=1, page_size=50."""
        with patch("app.main.db.get_current_offers", return_value=([], 0)) as mock_db:
            resp = client.get("/products")

        assert resp.status_code == 200
        mock_db.assert_called_once_with(store=None, page=1, page_size=50)

    def test_products_store_filter_passed_to_db(self, client):
        """store= query param is forwarded to db.get_current_offers."""
        with patch("app.main.db.get_current_offers", return_value=([], 0)) as mock_db:
            client.get("/products?store=Willys")

        mock_db.assert_called_once_with(store="Willys", page=1, page_size=50)

    def test_products_invalid_page(self, client):
        """page=0 returns 422."""
        resp = client.get("/products?page=0")
        assert resp.status_code == 422

    def test_products_invalid_page_size_too_large(self, client):
        """page_size=500 returns 422 (max is 200)."""
        resp = client.get("/products?page_size=500")
        assert resp.status_code == 422

    def test_products_invalid_page_size_zero(self, client):
        """page_size=0 returns 422 (min is 1)."""
        resp = client.get("/products?page_size=0")
        assert resp.status_code == 422

    def test_products_empty_store_param_returns_422(self, client):
        """store= with empty value returns 422."""
        resp = client.get("/products?store=")
        assert resp.status_code == 422

    def test_products_db_error_returns_503(self, client):
        """DB error in /products returns 503 without SQL."""
        with patch("app.main.db.get_current_offers",
                   side_effect=psycopg2.OperationalError("SQL detail")):
            resp = client.get("/products")

        assert resp.status_code == 503
        body = resp.json()
        assert "Database unavailable" in body["error"]
        assert "SQL detail" not in body["error"]


# ---------------------------------------------------------------------------
# /products/{product_name}/history
# ---------------------------------------------------------------------------


class TestProductHistory:
    def test_product_history_found(self, client):
        """Returns 200 with entries when history exists."""
        history = [
            {"store_name": "ICA", "product_name": "Mjölk", "price": 12.90,
             "valid_from": "2026-08-01", "valid_until": "2026-08-07"},
            {"store_name": "ICA", "product_name": "Mjölk", "price": 13.50,
             "valid_from": "2026-09-01", "valid_until": "2026-09-07"},
        ]
        with patch("app.main.db.get_product_history", return_value=history):
            resp = client.get("/products/Mjölk/history")

        assert resp.status_code == 200
        body = resp.json()
        assert body["product"] == "Mjölk"
        assert len(body["entries"]) == 2

    def test_product_history_not_found(self, client):
        """Returns 404 when no history entries exist for the product."""
        with patch("app.main.db.get_product_history", return_value=[]):
            resp = client.get("/products/NonExistentProduct/history")

        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body

    def test_product_history_url_encoded_name(self, client):
        """URL-encoded product name is decoded before querying the DB."""
        with patch("app.main.db.get_product_history", return_value=[]) as mock_db:
            client.get("/products/Nötfärs/history")

        # FastAPI will have decoded the path param before we receive it
        called_name = mock_db.call_args[0][0]
        assert "%" not in called_name  # no raw percent-encoding

    def test_product_history_store_filter(self, client):
        """store= query param is forwarded to db.get_product_history."""
        entry = {"store_name": "Willys", "product_name": "Bröd", "price": 20.0,
                 "valid_from": "2026-09-01", "valid_until": "2026-09-07"}
        with patch("app.main.db.get_product_history", return_value=[entry]) as mock_db:
            resp = client.get("/products/Bröd/history?store=Willys")

        assert resp.status_code == 200
        mock_db.assert_called_once_with("Bröd", store="Willys")

    def test_product_history_db_error_returns_503(self, client):
        """DB error returns 503 without exposing SQL."""
        with patch("app.main.db.get_product_history",
                   side_effect=psycopg2.OperationalError("raw SQL details here")):
            resp = client.get("/products/Mjölk/history")

        assert resp.status_code == 503
        body = resp.json()
        assert "Database unavailable" in body["error"]
        assert "raw SQL details here" not in body["error"]


# ---------------------------------------------------------------------------
# Error response — no SQL injection in body
# ---------------------------------------------------------------------------


class TestErrorNoDbInjection:
    def test_error_no_db_injection(self, client):
        """
        When psycopg2.OperationalError is raised with SQL-like text,
        the 503 response body must not contain that SQL text.
        """
        sql_detail = "ERROR: relation 'price_history' does not exist"
        with patch("app.main.db.get_current_offers",
                   side_effect=psycopg2.OperationalError(sql_detail)):
            resp = client.get("/products")

        assert resp.status_code == 503
        raw_body = resp.text
        assert sql_detail not in raw_body
        assert "price_history" not in raw_body
        assert "Database unavailable" in raw_body

    def test_error_no_db_injection_stores(self, client):
        """Same check for /stores endpoint."""
        sql_detail = "FATAL: password authentication failed for user 'app'"
        with patch("app.main.db.get_stores",
                   side_effect=psycopg2.OperationalError(sql_detail)):
            resp = client.get("/stores")

        assert resp.status_code == 503
        assert "password" not in resp.text
        assert "Database unavailable" in resp.text


# ---------------------------------------------------------------------------
# X-Request-ID middleware
# ---------------------------------------------------------------------------


class TestRequestId:
    def test_response_has_request_id_header(self, client):
        """Every response includes X-Request-ID header."""
        with patch("app.main.db.healthcheck", return_value=(True, "")):
            resp = client.get("/healthz")

        assert "x-request-id" in {k.lower(): v for k, v in resp.headers.items()}

    def test_request_id_echoed_from_client(self, client):
        """If client sends X-Request-ID, the same value is echoed back."""
        rid = "my-custom-request-id-123"
        with patch("app.main.db.healthcheck", return_value=(True, "")):
            resp = client.get("/healthz", headers={"X-Request-ID": rid})

        assert resp.headers.get("x-request-id") == rid
