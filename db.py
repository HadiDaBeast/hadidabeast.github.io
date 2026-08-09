"""
Local SQLite storage for Prispulsen.

Table is named `price_history` because, now that we're building toward
the real weekly pipeline, rows ACCUMULATE across runs rather than being
wiped each time — that's what makes the price-history graphs possible
later. A UNIQUE constraint on (store, product, price, valid_from,
valid_until) means re-running the fetch mid-week (e.g. while testing)
just no-ops on already-seen rows instead of creating duplicate history;
genuinely new weeks naturally get new valid_from/valid_until values, so
they insert as new rows.

Full raw offer JSON is preserved per row so any future field-mapping fix
can reprocess stored data without re-fetching.

The fuller normalized schema (stores/products/store_products/
publications — see project-plan.md) is still a possible future
refinement; this flat table is deliberately kept simple for now.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "prispulsen.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT NOT NULL,
            product_name TEXT,
            description TEXT,
            price REAL,
            unit_price REAL,
            base_unit TEXT,
            business TEXT,
            valid_from TEXT,
            valid_until TEXT,
            fetched_at TEXT NOT NULL,
            raw_json TEXT,
            UNIQUE (store_name, product_name, price, valid_from, valid_until)
        )
    """)
    conn.commit()
    conn.close()


def clear_all():
    """Wipes everything. NOT called by the normal fetch flow anymore —
    use clear_db.py when you actually want to start over (e.g. during
    development, or if the schema changes)."""
    conn = get_connection()
    conn.execute("DELETE FROM price_history")
    conn.commit()
    conn.close()


def insert_offer(store_name, parsed, raw_json, fetched_at):
    """INSERT OR IGNORE: if this exact (store, product, price, valid_from,
    valid_until) combination already exists — e.g. from an earlier fetch
    this same week — it's silently skipped rather than duplicated."""
    conn = get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO price_history (store_name, product_name,
            description, price, unit_price, base_unit, business,
            valid_from, valid_until, fetched_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        store_name, parsed["name"], parsed["description"], parsed["price"],
        parsed["unit_price"], parsed["base_unit"], parsed["business"],
        parsed["valid_from"], parsed["valid_until"], fetched_at, raw_json,
    ))
    conn.commit()
    conn.close()


def get_current_offers():
    """Offers whose validity window covers today — i.e. what should show
    as 'current deals' on the page, regardless of how much history has
    piled up underneath.

    Uses a plain string comparison on the YYYY-MM-DD prefix rather than
    SQLite's date() function — our stored timestamps look like
    "2026-08-16T21:59:59+0000" (no colon in the offset), which SQLite's
    date parser can silently fail on, making date() return NULL and
    filtering out every row. Since both sides are zero-padded ISO dates
    in UTC, a substring comparison sorts identically to a real date
    comparison without needing to parse anything.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM price_history
        WHERE substr(valid_until, 1, 10) >= date('now')
        ORDER BY product_name, price
    """).fetchall()
    conn.close()
    return rows


def get_all_offers():
    """Full history, unfiltered — used for export/graphing, not the live page."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM price_history ORDER BY product_name, valid_from"
    ).fetchall()
    conn.close()
    return rows


def get_last_fetch_time():
    conn = get_connection()
    row = conn.execute("SELECT MAX(fetched_at) AS t FROM price_history").fetchone()
    conn.close()
    return row["t"] if row else None