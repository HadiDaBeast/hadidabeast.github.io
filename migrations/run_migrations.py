#!/usr/bin/env python3
"""
Migration runner for Prispulsen.

Reads all *.sql files in the same directory, sorted lexicographically
(so 001_*, 002_*, ... ordering is preserved), and applies any that have
not yet been recorded in the schema_version table.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/dbname python run_migrations.py

The script is idempotent: re-running it after a partial failure will
skip already-applied versions and continue from where it left off.
"""

import os
import re
import sys
from pathlib import Path

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MIGRATIONS_DIR = Path(__file__).parent


def get_connection(url: str) -> psycopg2.extensions.connection:
    return psycopg2.connect(url)


def ensure_schema_version_table(conn: psycopg2.extensions.connection) -> None:
    """Create schema_version if it doesn't exist yet (bootstraps fresh DBs)."""
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


def get_applied_versions(conn: psycopg2.extensions.connection) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_version")
        rows = cur.fetchall()
    return {row[0] for row in rows}


def extract_version(filename: str) -> int | None:
    """
    Extract the leading integer version from a migration filename.

    Accepts filenames like '001_initial_schema.sql', '002_add_index.sql'.
    Returns None if the filename does not start with digits.
    """
    match = re.match(r"^(\d+)_", filename)
    if match:
        return int(match.group(1))
    return None


def discover_migrations() -> list[tuple[int, Path]]:
    """
    Return a sorted list of (version, path) tuples for all .sql files
    in MIGRATIONS_DIR whose names start with a numeric prefix.
    """
    entries = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        version = extract_version(path.name)
        if version is not None:
            entries.append((version, path))
    entries.sort(key=lambda t: t[0])
    return entries


def apply_migration(
    conn: psycopg2.extensions.connection,
    version: int,
    path: Path,
) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_version (version) VALUES (%s) ON CONFLICT DO NOTHING",
            (version,),
        )
    conn.commit()


def main() -> None:
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to database…")
    try:
        conn = get_connection(DATABASE_URL)
    except psycopg2.OperationalError as exc:
        print(f"ERROR: Could not connect to database: {exc}", file=sys.stderr)
        sys.exit(1)

    ensure_schema_version_table(conn)
    applied = get_applied_versions(conn)
    print(f"Already applied versions: {sorted(applied) or 'none'}")

    migrations = discover_migrations()
    if not migrations:
        print("No migration files found.")
        conn.close()
        return

    pending = [(v, p) for v, p in migrations if v not in applied]
    if not pending:
        print("All migrations already applied. Nothing to do.")
        conn.close()
        return

    print(f"Found {len(pending)} pending migration(s):")
    for version, path in pending:
        print(f"  {version:03d}: {path.name}")

    for version, path in pending:
        print(f"Applying {path.name}…", end=" ", flush=True)
        try:
            apply_migration(conn, version, path)
            print("OK")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"FAILED")
            print(f"ERROR applying version {version}: {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)

    print(f"\nMigration complete. Applied {len(pending)} migration(s).")
    conn.close()


if __name__ == "__main__":
    main()
