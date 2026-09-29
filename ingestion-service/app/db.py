import os

import psycopg2
import psycopg2.extras

conn = psycopg2.connect(os.environ["DATABASE_URL"])


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


def _row_to_dict(row):
    result = {}
    for k, v in dict(row).items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
