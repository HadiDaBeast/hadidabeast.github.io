"""
ingestion-service — PostgreSQL access layer.

Uses a single persistent psycopg2 connection created at import time.
All queries use parameterised arguments.
"""

import logging
import os

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

conn = psycopg2.connect(os.environ["DATABASE_URL"])

# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------


def init_schema():
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Ingestion runs
# ---------------------------------------------------------------------------


def create_ingestion_run():
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (status) VALUES ('queued') RETURNING id"
        )
        row = cur.fetchone()
    conn.commit()
    return row[0]


def mark_ingestion_run_started(run_id):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status='running', started_at=now() WHERE id=%s",
            (run_id,),
        )
    conn.commit()


def mark_ingestion_run_succeeded(run_id, offers_stored):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
            SET status='succeeded', completed_at=now(), offers_stored=%s
            WHERE id=%s
            """,
            (offers_stored, run_id),
        )
    conn.commit()


def mark_ingestion_run_failed(run_id, error_message):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
            SET status='failed', completed_at=now(), error_message=%s
            WHERE id=%s
            """,
            (error_message, run_id),
        )
    conn.commit()


def get_last_ingestion_run_status():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, status, started_at, completed_at, offers_stored, error_message, created_at
            FROM ingestion_runs
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if row is None:
        return {"status": "no_runs"}
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------


def load_categories():
    with conn.cursor() as cur:
        cur.execute(
            "SELECT category, keywords FROM product_categories ORDER BY array_length(keywords, 1) DESC"
        )
        rows = cur.fetchall()
    return [(row[0], [kw.lower() for kw in row[1]]) for row in rows]


def categorize(product_name, categories):
    if not product_name:
        return None
    name_lower = product_name.lower()
    for category, keywords in categories:
        for kw in keywords:
            if kw in name_lower:
                return category
    return None


# ---------------------------------------------------------------------------
# Offer persistence
# ---------------------------------------------------------------------------


def insert_offer(store_name, parsed, raw_json, fetched_at, category=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO price_history
                (store_name, product_name, description, price, unit_price,
                 base_unit, business, valid_from, valid_until, fetched_at, raw_json, category)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (store_name, product_name, price, valid_from, valid_until)
            DO NOTHING
            """,
            (
                store_name,
                parsed.get("name"),
                parsed.get("description"),
                parsed.get("price"),
                parsed.get("unit_price"),
                parsed.get("base_unit"),
                parsed.get("business"),
                parsed.get("valid_from"),
                parsed.get("valid_until"),
                fetched_at,
                raw_json,
                category,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------


def get_current_offers():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, store_name, product_name, description, price,
                   unit_price, base_unit, business, valid_from, valid_until, fetched_at
            FROM price_history
            WHERE (valid_until IS NULL OR valid_until >= now())
              AND (valid_from IS NULL OR valid_from <= now())
            ORDER BY product_name, price
            """
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_offers():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM price_history ORDER BY product_name, valid_from"
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_last_fetch_time():
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(fetched_at) FROM price_history")
        row = cur.fetchone()
    if row and row[0]:
        val = row[0]
        return val.isoformat() if hasattr(val, "isoformat") else str(val)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row):
    result = {}
    for k, v in dict(row).items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
