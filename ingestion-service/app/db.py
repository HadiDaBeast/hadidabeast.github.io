"""
ingestion-service — PostgreSQL access layer.

Uses psycopg2 with a SimpleConnectionPool. The pool is a module-level
singleton initialised once at startup via init_pool().

All queries use parameterised arguments; no string interpolation is used
for user-supplied values.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any, Generator
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

logger = logging.getLogger(__name__)

_pool: SimpleConnectionPool | None = None

# ---------------------------------------------------------------------------
# Pool initialisation
# ---------------------------------------------------------------------------


def parse_database_url(url: str) -> dict[str, Any]:
    """
    Parse a DATABASE_URL string into psycopg2 connection keyword arguments.

    Supports the standard format:
        postgresql://user:password@host:port/dbname
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

    kwargs: dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "dbname": parsed.path.lstrip("/"),
    }
    if parsed.username:
        kwargs["user"] = parsed.username
    if parsed.password:
        kwargs["password"] = parsed.password
    return kwargs


def init_pool(
    database_url: str,
    min_conn: int = 1,
    max_conn: int = 10,
    retry_timeout: int = 30,
) -> None:
    """
    Initialise the module-level connection pool.

    Retries for up to *retry_timeout* seconds (1 s sleep between attempts)
    to handle the case where the database container is still starting up.
    """
    global _pool  # noqa: PLW0603
    conn_kwargs = parse_database_url(database_url)
    deadline = time.monotonic() + retry_timeout
    last_exc: Exception | None = None

    while time.monotonic() < deadline:
        try:
            _pool = SimpleConnectionPool(min_conn, max_conn, **conn_kwargs)
            logger.info(
                "DB pool ready (host=%s port=%s dbname=%s)",
                conn_kwargs.get("host"),
                conn_kwargs.get("port"),
                conn_kwargs.get("dbname"),
            )
            return
        except psycopg2.OperationalError as exc:
            last_exc = exc
            logger.warning("DB not ready yet, retrying in 1 s… (%s)", exc)
            time.sleep(1)

    raise RuntimeError(
        f"Could not connect to database after {retry_timeout}s: {last_exc}"
    )


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager that yields a connection from the pool and returns it
    automatically on exit — even on exception.
    """
    if _pool is None:
        raise RuntimeError("DB pool is not initialised. Call init_pool() first.")
    conn = _pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------


def init_schema() -> None:
    """
    Ensure the schema_version table exists so that run_migrations.py can
    track applied versions. The full schema is managed via SQL migration
    files in migrations/; this is just a lightweight safety net.
    """
    with get_conn() as conn:
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


def create_ingestion_run() -> int:
    """Insert a new queued ingestion run and return its ID."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingestion_runs (status) VALUES ('queued') RETURNING id"
            )
            row = cur.fetchone()
        conn.commit()
    return row[0]


def mark_ingestion_run_started(run_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingestion_runs SET status='running', started_at=now() WHERE id=%s",
                (run_id,),
            )
        conn.commit()


def mark_ingestion_run_succeeded(run_id: int, offers_stored: int) -> None:
    with get_conn() as conn:
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


def mark_ingestion_run_failed(run_id: int, error_message: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_runs
                SET status='failed', completed_at=now(), error_message=%s
                WHERE id=%s
                """,
                (error_message[:2000], run_id),
            )
        conn.commit()


def get_last_ingestion_run_status() -> dict:
    """Return the most recent ingestion_runs row as a dict."""
    with get_conn() as conn:
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
# Offer persistence
# ---------------------------------------------------------------------------


def insert_offer(
    store_name: str,
    parsed: dict,
    raw_json: str,
    fetched_at: str,
) -> None:
    """
    Insert a single offer row, skipping on conflict.

    The UNIQUE constraint on (store_name, product_name, price, valid_from,
    valid_until) means re-running the ingestion mid-week is idempotent.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO price_history
                    (store_name, product_name, description, price, unit_price,
                     base_unit, business, valid_from, valid_until, fetched_at, raw_json)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                ),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------


def get_current_offers() -> list[dict]:
    """
    Return all offers whose validity window covers today (UTC).
    Used by the ingestion-service /status endpoint and tests.
    """
    with get_conn() as conn:
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


def get_all_offers() -> list[dict]:
    """Return full price history, unfiltered."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM price_history ORDER BY product_name, valid_from"
            )
            rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_last_fetch_time() -> str | None:
    """Return the most recent fetched_at timestamp as an ISO string, or None."""
    with get_conn() as conn:
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


def _row_to_dict(row: Any) -> dict:
    """Convert a RealDictRow (or plain dict) to a plain dict with ISO timestamps."""
    result = {}
    for k, v in dict(row).items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
