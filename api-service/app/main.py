"""
api-service — FastAPI application entry point.

Endpoints
---------
GET  /healthz                          Health check
GET  /stores                           All distinct store names, sorted
GET  /products                         Paginated current offers (store filter optional)
GET  /products/{product_name}/history  Price history for a product
GET  /categories                       All distinct categories with current offers
GET  /categories/{category}            Current offers for a category
GET  /version                          Version info
"""

import logging
import os
from typing import Optional
from urllib.parse import unquote

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from app import db

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
APP_VERSION = os.environ.get("APP_VERSION", "dev")
API_PORT = int(os.environ.get("API_PORT", "8001"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="api-service", version=APP_VERSION)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/version")
def version():
    return {"version": APP_VERSION, "service": "api-service"}


@app.get("/stores")
def stores():
    return [{"name": s} for s in db.get_stores()]


@app.get("/products")
def products(
    store: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    store_filter = store.strip() if store else None
    items_raw, total = db.get_current_offers(store=store_filter, page=page, page_size=page_size)
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
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/categories")
def categories():
    return [{"category": c} for c in db.get_categories()]


@app.get("/categories/{category}")
def category_offers(category: str):
    offers = db.get_offers_by_category(category)
    if not offers:
        return JSONResponse(
            status_code=404,
            content={"error": f"No current offers found for category: {category!r}"},
        )
    return {
        "category": category,
        "count": len(offers),
        "items": [
            {
                "store": o.get("store_name"),
                "product": o.get("product_name"),
                "price": o.get("price"),
                "unit_price": o.get("unit_price"),
                "base_unit": o.get("base_unit"),
                "valid_from": o.get("valid_from"),
                "valid_until": o.get("valid_until"),
            }
            for o in offers
        ],
    }


@app.get("/products/{product_name}/history")
def product_history(
    product_name: str,
    store: Optional[str] = Query(default=None),
):
    decoded_name = unquote(product_name)
    store_filter = store.strip() if store else None
    entries = db.get_product_history(decoded_name, store=store_filter)
    if not entries:
        return JSONResponse(
            status_code=404,
            content={"error": f"No history found for product: {decoded_name!r}"},
        )
    return {"product": decoded_name, "entries": entries}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
