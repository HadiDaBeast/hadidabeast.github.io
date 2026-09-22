"""
api-service — PostgreSQL access layer.

Uses psycopg2 with a SimpleConnectionPool. The pool is a module-level
singleton initialised once at startup via init_pool().

All queries use parameterised arguments; no string interpolation is used
for user-supplied values.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any, Generator, Optional
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

    Supports: postgresql://user:password@host:port/dbname
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

    Retries for up to *retry_timeout* seconds to handle DB container startup.
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
    """Yield a connection from the pool, returning it on exit."""
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
# Read queries
# ---------------------------------------------------------------------------


def get_current_offers(
    store: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """
    Return a page of currently valid offers and the total matching count.

    Parameters
    ----------
    store:
        If given, filter to this exact store_name value.
    page:
        1-based page number.
    page_size:
        Number of results per page (max 200 enforced by the route).

    Returns
    -------
    (offers, total) — offers is a list of dicts, total is the unfiltered count.
    """
    offset = (page - 1) * page_size

    base_where = """
        WHERE (valid_until IS NULL OR valid_until >= now())
          AND (valid_from IS NULL OR valid_from <= now())
    """
    params_filter: list[Any] = []
    if store:
        base_where += " AND store_name = %s"
        params_filter.append(store)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Total count
            cur.execute(
                f"SELECT COUNT(*) FROM price_history {base_where}",
                params_filter,
            )
            total = cur.fetchone()["count"]

            # Paginated rows
            cur.execute(
                f"""
                SELECT id, store_name, product_name, description, price,
                       unit_price, base_unit, business, valid_from, valid_until, fetched_at
                FROM price_history
                {base_where}
                ORDER BY product_name, price
                LIMIT %s OFFSET %s
                """,
                params_filter + [page_size, offset],
            )
            rows = cur.fetchall()

    return [_row_to_dict(r) for r in rows], total


def get_product_history(
    product_name: str,
    store: Optional[str] = None,
) -> list[dict]:
    """
    Return all price history rows for the given product name, optionally
    filtered to a single store, ordered by valid_from ascending.
    """
    params: list[Any] = [product_name]
    store_clause = ""
    if store:
        store_clause = " AND store_name = %s"
        params.append(store)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, store_name, product_name, price, unit_price,
                       base_unit, valid_from, valid_until, fetched_at
                FROM price_history
                WHERE product_name = %s
                {store_clause}
                ORDER BY valid_from ASC, store_name
                """,
                params,
            )
            rows = cur.fetchall()

    return [_row_to_dict(r) for r in rows]


def get_stores() -> list[str]:
    """Return all distinct store names found in price_history, sorted."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT store_name FROM price_history ORDER BY store_name"
            )
            rows = cur.fetchall()
    return [r[0] for r in rows if r[0]]


def get_last_ingestion_run() -> dict:
    """Return the most recent ingestion_runs row as a dict."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, status, started_at, completed_at, offers_stored,
                       error_message, created_at
                FROM ingestion_runs
                ORDER BY id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    if row is None:
        return {"status": "no_runs"}
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def healthcheck() -> tuple[bool, str]:
    """
    Verify that the DB pool is functional by executing a trivial query.

    Returns (True, "") on success, (False, error_message) on failure.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> dict:
    """Convert a RealDictRow to a plain dict with ISO timestamps."""
    result = {}
    for k, v in dict(row).items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
