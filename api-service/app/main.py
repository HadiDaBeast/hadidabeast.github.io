import os
from pathlib import Path
from urllib.parse import unquote

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db

load_dotenv()

APP_VERSION = os.environ.get("APP_VERSION", "dev")
API_PORT = int(os.environ.get("API_PORT", "8001"))
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="api-service", version=APP_VERSION)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/version")
def version():
    return {"version": APP_VERSION, "service": "api-service"}


@app.get("/stores")
def stores():
    return [{"name": s} for s in db.get_stores()]


@app.get("/api/stores")
def api_stores():
    return stores()


@app.get("/products")
def products(store=None, page=1, page_size=50):
    store_filter = store.strip() if store else None
    page = int(page or 1)
    page_size = int(page_size or 50)
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


@app.get("/api/products")
def api_products(store=None, page=1, page_size=50):
    return products(store=store, page=page, page_size=page_size)


@app.get("/categories")
def categories():
    return [{"category": c} for c in db.get_categories()]


@app.get("/api/categories")
def api_categories():
    return categories()


@app.get("/categories/{category}")
def category_offers(category):
    offers = db.get_offers_by_category(category)
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


@app.get("/api/categories/{category}")
def api_category_offers(category):
    return category_offers(category)


@app.get("/products/{product_name}/history")
def product_history(product_name, store=None):
    decoded_name = unquote(product_name)
    store_filter = store.strip() if store else None
    entries = db.get_product_history(decoded_name, store=store_filter)
    return {"product": decoded_name, "entries": entries}


@app.get("/api/products/{product_name}/history")
def api_product_history(product_name, store=None):
    return product_history(product_name, store=store)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
