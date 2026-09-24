import os

import psycopg2
import psycopg2.extras

conn = psycopg2.connect(os.environ["DATABASE_URL"])

BLOCKED_STORES = (
    "Trixie Together",
    "ÖoB",
)

STORE_NAME_MAP = {
    "ICA Nära": "ICA Nära Nättran",
}


def get_current_offers(store=None, page=1, page_size=50):
    offset = (page - 1) * page_size

    base_where = """
        WHERE (valid_until IS NULL OR valid_until >= now())
          AND (valid_from IS NULL OR valid_from <= now())
          AND store_name != ALL(%s)
    """
    params_filter = [list(BLOCKED_STORES)]
    if store:
        base_where += " AND store_name = %s"
        params_filter.append(store)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM price_history {base_where}",
            params_filter,
        )
        total = cur.fetchone()["count"]

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


def get_product_history(product_name, store=None):
    params = [product_name, list(BLOCKED_STORES)]
    store_clause = ""
    if store:
        store_clause = " AND store_name = %s"
        params.append(store)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, store_name, product_name, price, unit_price,
                   base_unit, valid_from, valid_until, fetched_at
            FROM price_history
            WHERE product_name = %s
              AND store_name != ALL(%s)
            {store_clause}
            ORDER BY valid_from ASC, store_name
            """,
            params,
        )
        rows = cur.fetchall()

    return [_row_to_dict(r) for r in rows]


def get_categories():
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT category FROM price_history
            WHERE category IS NOT NULL
              AND store_name != ALL(%s)
              AND (valid_until IS NULL OR valid_until >= now())
              AND (valid_from IS NULL OR valid_from <= now())
            ORDER BY category
            """,
            (list(BLOCKED_STORES),),
        )
        rows = cur.fetchall()
    return [r[0] for r in rows if r[0]]


def get_offers_by_category(category):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, store_name, product_name, description, price,
                   unit_price, base_unit, valid_from, valid_until, fetched_at
            FROM price_history
            WHERE category = %s
              AND store_name != ALL(%s)
              AND (valid_until IS NULL OR valid_until >= now())
              AND (valid_from IS NULL OR valid_from <= now())
            ORDER BY unit_price ASC NULLS LAST, price ASC
            """,
            (category, list(BLOCKED_STORES)),
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_stores():
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT store_name FROM price_history WHERE store_name != ALL(%s) ORDER BY store_name",
            (list(BLOCKED_STORES),),
        )
        rows = cur.fetchall()
    return [STORE_NAME_MAP.get(r[0], r[0]) for r in rows if r[0]]


def _row_to_dict(row):
    result = {}
    for k, v in dict(row).items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    if "store_name" in result and result["store_name"] in STORE_NAME_MAP:
        result["store_name"] = STORE_NAME_MAP[result["store_name"]]
    return result
