#!/usr/bin/env python3
"""
SQLite → PostgreSQL data migration for Prispulsen.

Reads all rows from prispulsen.db (SQLite price_history table) and
inserts them into the PostgreSQL price_history table using
INSERT ... ON CONFLICT DO NOTHING, making the script idempotent.

Date normalisation:
  SQLite stores timestamps as TEXT in formats like:
    "2026-08-11T00:00:00+0000"   (no colon in UTC offset)
    "2026-08-11 00:00:00"        (space separator, no tz)
    "2026-08-11T00:00:00+00:00"  (ISO 8601 proper)
    "2026-08-11"                 (date only)

  PostgreSQL TIMESTAMPTZ accepts ISO 8601, so we normalise all variants
  to "YYYY-MM-DDTHH:MM:SS+HH:MM" before inserting.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/dbname python migrate_sqlite.py
    # Optionally override the SQLite path:
    SQLITE_PATH=/path/to/prispulsen.db DATABASE_URL=... python migrate_sqlite.py
"""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = Path(
    os.environ.get("SQLITE_PATH", Path(__file__).parent.parent / "prispulsen.db")
)
BATCH_SIZE = 500  # rows per INSERT batch


# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------


def normalise_timestamp(value: str | None) -> str | None:
    """
    Normalise a SQLite TEXT timestamp to a form PostgreSQL TIMESTAMPTZ accepts.

    Returns None if the value is None, empty, or cannot be parsed.
    """
    if not value:
        return None

    v = value.strip()

    # Replace space separator with T
    v = re.sub(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", r"\1T\2", v)

    # Insert colon into bare numeric UTC offset: +0000 → +00:00, +0200 → +02:00
    v = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", v)

    # Date-only: append midnight UTC
    if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        v = v + "T00:00:00+00:00"

    # Bare datetime without timezone: assume UTC
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", v):
        v = v + "+00:00"

    return v


# ---------------------------------------------------------------------------
# PostgreSQL connection
# ---------------------------------------------------------------------------


def pg_connect(url: str) -> psycopg2.extensions.connection:
    parsed = urlparse(url)
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/"),
        user=parsed.username,
        password=parsed.password,
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate(sqlite_path: Path, pg_conn: psycopg2.extensions.connection) -> None:
    print(f"Opening SQLite database: {sqlite_path}")
    if not sqlite_path.exists():
        print(f"ERROR: SQLite file not found: {sqlite_path}", file=sys.stderr)
        sys.exit(1)

    sq_conn = sqlite3.connect(str(sqlite_path))
    sq_conn.row_factory = sqlite3.Row

    # Count rows
    total = sq_conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    print(f"Total rows in SQLite price_history: {total:,}")

    if total == 0:
        print("Nothing to migrate.")
        sq_conn.close()
        return

    cursor = sq_conn.execute(
        """
        SELECT store_name, product_name, description, price, unit_price,
               base_unit, business, valid_from, valid_until, fetched_at, raw_json
        FROM price_history
        ORDER BY id
        """
    )

    inserted = 0
    skipped = 0
    batch: list[tuple] = []

    def flush_batch() -> tuple[int, int]:
        nonlocal inserted, skipped
        if not batch:
            return 0, 0
        with pg_conn.cursor() as cur:
            # Use execute_values for efficient batch insert
            import psycopg2.extras

            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO price_history
                    (store_name, product_name, description, price, unit_price,
                     base_unit, business, valid_from, valid_until, fetched_at, raw_json)
                VALUES %s
                ON CONFLICT (store_name, product_name, price, valid_from, valid_until)
                DO NOTHING
                """,
                batch,
            )
            batch_inserted = cur.rowcount
        pg_conn.commit()
        batch_skipped = len(batch) - batch_inserted
        inserted += batch_inserted
        skipped += batch_skipped
        batch.clear()
        return batch_inserted, batch_skipped

    processed = 0
    for row in cursor:
        # Normalise raw_json: validate it is valid JSON if present, else store None
        raw_json = row["raw_json"]
        if raw_json:
            try:
                json.loads(raw_json)  # validate
            except (json.JSONDecodeError, TypeError):
                raw_json = None

        batch.append(
            (
                row["store_name"],
                row["product_name"],
                row["description"],
                row["price"],
                row["unit_price"],
                row["base_unit"],
                row["business"],
                normalise_timestamp(row["valid_from"]),
                normalise_timestamp(row["valid_until"]),
                normalise_timestamp(row["fetched_at"]) or row["fetched_at"],
                raw_json,
            )
        )

        processed += 1
        if len(batch) >= BATCH_SIZE:
            b_ins, b_skip = flush_batch()
            print(
                f"  Progress: {processed:,}/{total:,} rows processed "
                f"({inserted:,} inserted, {skipped:,} skipped)",
                end="\r",
            )

    # Final batch
    flush_batch()
    print()  # newline after \r progress

    sq_conn.close()
    print()
    print("=== Migration complete ===")
    print(f"Total rows processed : {processed:,}")
    print(f"Inserted             : {inserted:,}")
    print(f"Skipped (duplicates) : {skipped:,}")


def main() -> None:
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to PostgreSQL…")
    try:
        pg_conn = pg_connect(DATABASE_URL)
    except psycopg2.OperationalError as exc:
        print(f"ERROR: Could not connect to PostgreSQL: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        migrate(SQLITE_PATH, pg_conn)
    finally:
        pg_conn.close()


if __name__ == "__main__":
    main()
