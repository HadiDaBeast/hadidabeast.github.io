"""
api-service — FastAPI application entry point.

Endpoints
---------
GET  /healthz                          Health check (DB state reported, never 500)
GET  /stores                           All distinct store names, sorted
GET  /products                         Paginated current offers (store filter optional)
GET  /products/{product_name}/history  Price history for a product
GET  /version                          Version info
"""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import unquote

import psycopg2
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import db

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
APP_VERSION = os.environ.get("APP_VERSION", "dev")
API_PORT = int(os.environ.get("API_PORT", "8001"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s",
)
logger = logging.getLogger(__name__)

# Use a plain adapter for the logger so we can inject request_id into records
_log_adapter = logging.LoggerAdapter(logger, {"request_id": "-"})


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB connection pool; close it on shutdown."""
    if not DATABASE_URL:
        _log_adapter.warning("DATABASE_URL is not set — DB unavailable")
    else:
        try:
            db.init_pool(DATABASE_URL, retry_timeout=30)
        except Exception as exc:  # noqa: BLE001
            _log_adapter.error("DB startup failed: %s", exc)
            raise

    yield

    if db._pool is not None:
        try:
            db._pool.closeall()
            _log_adapter.info("DB pool closed")
        except Exception as exc:  # noqa: BLE001
            _log_adapter.warning("Error closing DB pool: %s", exc)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="api-service",
    version=APP_VERSION,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware — X-Request-ID + structured access log
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_id_and_logging(request: Request, call_next):
    """
    1. Attach X-Request-ID to every response (pass-through or generate).
    2. Log method, path, status, and duration.

    DATABASE_URL and passwords are never logged here.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start = time.monotonic()

    response: Response = await call_next(request)

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id

    logger.info(
        "%s %s → %s  (%.1f ms)  request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )

    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(psycopg2.Error)
async def psycopg2_error_handler(request: Request, exc: psycopg2.Error):
    """Never expose raw SQL errors — return a generic 503."""
    request_id = request.headers.get("X-Request-ID", "-")
    logger.error("DB error (request_id=%s): %s", request_id, type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"error": "Database unavailable", "request_id": request_id},
    )


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    request_id = request.headers.get("X-Request-ID", "-")
    return JSONResponse(
        status_code=422,
        content={"error": str(exc), "request_id": request_id},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    request_id = request.headers.get("X-Request-ID", "-")
    logger.error(
        "Unhandled exception (request_id=%s): %s: %s",
        request_id, type(exc).__name__, exc,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "request_id": request_id},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz", status_code=200)
def healthz(request: Request):
    """
    Always returns 200.  The 'db' field is 'ok' if the pool is working,
    'error' otherwise — never raises.
    """
    request_id = request.headers.get("X-Request-ID", "-")
    try:
        ok, _ = db.healthcheck()
        db_status = "ok" if ok else "error"
    except Exception:  # noqa: BLE001
        db_status = "error"

    return {"status": "ok", "version": APP_VERSION, "db": db_status}


@app.get("/version")
def version():
    return {"version": APP_VERSION, "service": "api-service"}


@app.get("/stores")
def stores(request: Request):
    """Return all distinct store names from price_history, sorted by name."""
    request_id = request.headers.get("X-Request-ID", "-")
    try:
        store_list = db.get_stores()
    except psycopg2.Error as exc:
        logger.error("DB error in /stores (request_id=%s): %s", request_id, type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"error": "Database unavailable", "request_id": request_id},
        )
    # get_stores() already returns sorted results; return as list of objects
    return [{"name": s} for s in store_list]


@app.get("/products")
def products(
    request: Request,
    store: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """
    Return paginated currently-valid offers.

    Query params:
      store     — optional exact store name filter (non-empty string)
      page      — 1-based page number (≥ 1)
      page_size — results per page (1–200, default 50)
    """
    request_id = request.headers.get("X-Request-ID", "-")

    # Reject empty-string store param (caller passed store= with no value)
    if store is not None and store.strip() == "":
        return JSONResponse(
            status_code=422,
            content={
                "error": "store parameter must be a non-empty string",
                "request_id": request_id,
            },
        )

    store_filter = store.strip() if store else None

    try:
        items_raw, total = db.get_current_offers(
            store=store_filter, page=page, page_size=page_size
        )
    except psycopg2.Error as exc:
        logger.error("DB error in /products (request_id=%s): %s", request_id, type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"error": "Database unavailable", "request_id": request_id},
        )

    items = [
        {
            "store": row.get("store_name"),
            "product": row.get("product_name"),
            "price": row.get("price"),
            "unit_price": row.get("unit_price"),
            "base_unit": row.get("base_unit"),
            "valid_from": row.get("valid_from"),
            "valid_until": row.get("valid_until"),
        }
        for row in items_raw
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/products/{product_name}/history")
def product_history(
    product_name: str,
    request: Request,
    store: Optional[str] = Query(default=None),
):
    """
    Return all price history entries for the given product name.

    product_name is URL-decoded automatically by FastAPI; we also manually
    unquote to handle double-encoding edge cases.
    Returns 404 if no entries are found.
    """
    request_id = request.headers.get("X-Request-ID", "-")
    decoded_name = unquote(product_name)

    if store is not None and store.strip() == "":
        return JSONResponse(
            status_code=422,
            content={
                "error": "store parameter must be a non-empty string",
                "request_id": request_id,
            },
        )

    store_filter = store.strip() if store else None

    try:
        entries = db.get_product_history(decoded_name, store=store_filter)
    except psycopg2.Error as exc:
        logger.error(
            "DB error in /products/.../history (request_id=%s): %s",
            request_id, type(exc).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={"error": "Database unavailable", "request_id": request_id},
        )

    if not entries:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"No history found for product: {decoded_name!r}",
                "request_id": request_id,
            },
        )

    return {"product": decoded_name, "entries": entries}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
