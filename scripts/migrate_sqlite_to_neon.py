"""
One-shot copy: local SQLite (catalyst_alpha.db) -> Neon Postgres (DATABASE_URL).

Usage (from repo root):
  python scripts/migrate_sqlite_to_neon.py
  python scripts/migrate_sqlite_to_neon.py --sqlite path/to/old.db

Requires: DATABASE_URL in .env, psycopg installed.
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from pathlib import Path

# Repo root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _norm(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _sqlite_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [str(row[1]) for row in cur.fetchall()]


def _pg_columns(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    rows = cur.fetchall()
    return [str(r["column_name"]) for r in rows]


def _copy_table(
    *,
    sq: sqlite3.Connection,
    pg,
    table: str,
) -> int:
    sq_cur = sq.cursor()
    sq_cur.row_factory = sqlite3.Row
    pg_cur = pg.cursor()

    scols = _sqlite_columns(sq_cur, table)
    pcols = {c.lower(): c for c in _pg_columns(pg_cur, table)}
    cols = [pcols[c.lower()] for c in scols if c.lower() in pcols]
    if not cols:
        return 0

    sq_cur.execute(f'SELECT {", ".join(scols)} FROM {table}')
    rows = sq_cur.fetchall()
    if not rows:
        return 0

    # Build insert using PG column names (cols aligned with scols subset)
    sqlite_to_pg = []
    for c in scols:
        if c.lower() in pcols:
            sqlite_to_pg.append((c, pcols[c.lower()]))

    col_names = [pgc for _, pgc in sqlite_to_pg]
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = (
        f'INSERT INTO {table} ({", ".join(col_names)}) VALUES ({placeholders})'
    )

    n = 0
    for row in rows:
        vals = tuple(_norm(row[sqc]) for sqc, _ in sqlite_to_pg)
        pg_cur.execute(insert_sql, vals)
        n += 1
    pg.commit()
    return n


def _reset_sequences(pg, tables: list[str]) -> None:
    cur = pg.cursor()
    for t in tables:
        cur.execute(f"SELECT COALESCE(MAX(id), 0) AS mx FROM {t}")
        m = cur.fetchone()["mx"]
        if m and int(m) > 0:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, true)",
                (t, int(m)),
            )
    pg.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite -> Neon Postgres copy")
    parser.add_argument(
        "--sqlite",
        default=str(ROOT / "catalyst_alpha.db"),
        help="Path to SQLite file (default: ./catalyst_alpha.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print counts, do not write to Postgres",
    )
    args = parser.parse_args()

    os.chdir(ROOT)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    pg_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not pg_url.startswith(("postgresql://", "postgres://")):
        print("ERROR: DATABASE_URL must be set to a postgresql:// connection string.")
        sys.exit(1)

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.is_file():
        print(f"ERROR: SQLite file not found: {sqlite_path}")
        sys.exit(1)

    import psycopg
    from psycopg.rows import dict_row

    # Ensure Neon schema exists
    from database import init_db

    init_db()

    sq = sqlite3.connect(str(sqlite_path))
    sq.row_factory = sqlite3.Row

    tables = ["actual_market_movers", "alpha_predictions", "simulated_predictions"]

    sq_cur = sq.cursor()
    print("SQLite row counts:")
    for t in tables:
        sq_cur.execute(f"SELECT COUNT(*) AS n FROM {t}")
        print(f"  {t}: {sq_cur.fetchone()[0]}")

    if args.dry_run:
        sq.close()
        print("Dry run — no changes to Postgres.")
        return

    pg = psycopg.connect(pg_url, row_factory=dict_row)
    pg_cur = pg.cursor()
    # Clear destination (respect FK-free order)
    pg_cur.execute(
        "TRUNCATE actual_market_movers, alpha_predictions, simulated_predictions "
        "RESTART IDENTITY CASCADE"
    )
    pg.commit()
    print("Neon: truncated three tables.")

    total = 0
    for t in tables:
        n = _copy_table(sq=sq, pg=pg, table=t)
        print(f"Copied {t}: {n} rows")
        total += n

    _reset_sequences(pg, tables)
    sq.close()
    pg.close()
    print(f"Done. Total rows inserted: {total}")


if __name__ == "__main__":
    main()
