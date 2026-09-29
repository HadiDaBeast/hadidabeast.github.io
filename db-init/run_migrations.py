#!/usr/bin/env python3

import os
import re
from pathlib import Path

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MIGRATIONS_DIR = Path(__file__).parent


def get_connection(url):
    return psycopg2.connect(url)


def ensure_schema_version_table(conn):
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


def get_applied_versions(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_version")
        rows = cur.fetchall()
    return {row[0] for row in rows}


def extract_version(filename):
    match = re.match(r"^(\d+)_", filename)
    if match:
        return int(match.group(1))
    return None


def discover_migrations():
    entries = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        version = extract_version(path.name)
        if version is not None:
            entries.append((version, path))
    entries.sort(key=lambda t: t[0])
    return entries


def apply_migration(conn, version, path):
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_version (version) VALUES (%s) ON CONFLICT DO NOTHING",
            (version,),
        )
    conn.commit()


def main():
    conn = get_connection(DATABASE_URL)
    ensure_schema_version_table(conn)
    applied = get_applied_versions(conn)
    migrations = discover_migrations()
    pending = [(v, p) for v, p in migrations if v not in applied]
    for version, path in pending:
        apply_migration(conn, version, path)
    conn.close()


if __name__ == "__main__":
    main()
