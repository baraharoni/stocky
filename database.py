"""
database.py — Catalyst Alpha v1.0
Schema initialization and CRUD helpers.

Uses SQLite when DATABASE_URL is unset (local file DB_PATH).
Uses PostgreSQL when DATABASE_URL is set (e.g. Neon) — same code paths everywhere.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, date, timedelta
from typing import Any
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _normalize_database_url(raw: str) -> str:
    """Strip BOM/newlines/quotes — Fly secrets & shell pastes often add junk that breaks IDNA."""
    s = (raw or "").strip().strip("\ufeff")
    s = s.replace("\r\n", "").replace("\r", "").replace("\n", "")
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _validate_pg_url_for_dns(url: str) -> None:
    """Raise ValueError with a helpful message if host is missing/malformed (avoids cryptic idna errors)."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(
            "DATABASE_URL has no hostname — usually unescaped @ or : in the password "
            "(URL-encode them, e.g. @ → %40), or stray quotes/newlines in the Fly secret."
        )
    for label in host.split("."):
        if not label:
            raise ValueError(
                "DATABASE_URL hostname has an empty segment — check for typos or bad quoting."
            )
        if len(label) > 63:
            raise ValueError("DATABASE_URL hostname segment too long for DNS.")


DB_PATH = os.environ.get("DB_PATH", "catalyst_alpha.db")
DATABASE_URL = _normalize_database_url(os.environ.get("DATABASE_URL") or "")
USE_POSTGRES = bool(
    DATABASE_URL
    and DATABASE_URL.startswith(("postgresql://", "postgres://"))
)


class DbCursor:
    """Wraps backend cursor; translates SQLite ? placeholders to psycopg %s."""

    def __init__(self, raw: Any, pg: bool) -> None:
        self._raw = raw
        self._pg = pg

    def execute(self, sql: str, params: tuple | list | None = None) -> DbCursor:
        if self._pg:
            sql = sql.replace("?", "%s")
        if params is None:
            self._raw.execute(sql)
        else:
            self._raw.execute(sql, params)
        return self

    def executemany(self, sql: str, seq: list) -> None:
        if self._pg:
            sql = sql.replace("?", "%s")
        self._raw.executemany(sql, seq)

    def fetchall(self) -> list:
        return self._raw.fetchall()

    def fetchone(self):
        return self._raw.fetchone()

    @property
    def lastrowid(self) -> int | None:
        return getattr(self._raw, "lastrowid", None)

    def __iter__(self):
        return iter(self._raw)


class DbConn:
    def __init__(self, raw: Any, pg: bool) -> None:
        self._raw = raw
        self._pg = pg

    def cursor(self) -> DbCursor:
        return DbCursor(self._raw.cursor(), self._pg)

    def execute(self, sql: str, params: tuple | list | None = None) -> DbCursor:
        c = self.cursor()
        c.execute(sql, params)
        return c

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()


def get_connection() -> DbConn:
    if USE_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        _validate_pg_url_for_dns(DATABASE_URL)
        raw = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=int(os.environ.get("PG_CONNECT_TIMEOUT", "90")),
        )
        return DbConn(raw, pg=True)
    raw = sqlite3.connect(DB_PATH)
    raw.row_factory = sqlite3.Row
    return DbConn(raw, pg=False)


def _pg_column_names(cur: DbCursor, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {str(r["column_name"]).lower() for r in cur.fetchall()}


# ─── Schema ───────────────────────────────────────────────────────────────────

def _ddl_type(typedef: str) -> str:
    """Map SQLite-style REAL to PostgreSQL DOUBLE PRECISION when needed."""
    if USE_POSTGRES:
        return typedef.replace("REAL", "DOUBLE PRECISION")
    return typedef


def _init_schema_sqlite(cur: DbCursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS actual_market_movers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            percent_change  REAL    NOT NULL,
            catalyst_reason TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS alpha_predictions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            date              TEXT    NOT NULL,
            ticker            TEXT    NOT NULL,
            pm_rationale      TEXT,
            confidence_score  REAL,
            actual_eod_change REAL,
            manager_feedback  TEXT,
            return_3d         REAL,
            return_7d         REAL,
            return_14d        REAL,
            return_30d        REAL,
            strategy          TEXT    NOT NULL DEFAULT 'alpha',
            metrics           TEXT,
            target_price      REAL,
            target_hit_date   TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS simulated_predictions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id            TEXT    NOT NULL,
            date              TEXT    NOT NULL,
            ticker            TEXT    NOT NULL,
            pick_rank         INTEGER NOT NULL,
            pm_rationale      TEXT,
            confidence_score  REAL,
            metrics           TEXT,
            price_at_pick     REAL,
            target_price      REAL,
            target_hit_date   TEXT,
            return_session    REAL,
            actual_eod_change REAL,
            return_3d         REAL,
            return_7d         REAL,
            return_14d        REAL,
            return_30d        REAL,
            return_90d        REAL,
            return_180d       REAL,
            price_today       REAL,
            fetched_at        TEXT
        )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_run_date "
        "ON simulated_predictions(run_id, date)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_ticker "
        "ON simulated_predictions(ticker)"
    )


def _init_schema_postgres(cur: DbCursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS actual_market_movers (
            id              SERIAL PRIMARY KEY,
            date            TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            percent_change  DOUBLE PRECISION NOT NULL,
            catalyst_reason TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS alpha_predictions (
            id                SERIAL PRIMARY KEY,
            date              TEXT    NOT NULL,
            ticker            TEXT    NOT NULL,
            pm_rationale      TEXT,
            confidence_score  DOUBLE PRECISION,
            actual_eod_change DOUBLE PRECISION,
            manager_feedback  TEXT,
            return_3d         DOUBLE PRECISION,
            return_7d         DOUBLE PRECISION,
            return_14d        DOUBLE PRECISION,
            return_30d        DOUBLE PRECISION,
            strategy          TEXT NOT NULL DEFAULT 'alpha',
            metrics           TEXT,
            target_price      DOUBLE PRECISION,
            target_hit_date   TEXT,
            price_at_pick     DOUBLE PRECISION,
            return_session    DOUBLE PRECISION
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS simulated_predictions (
            id                SERIAL PRIMARY KEY,
            run_id            TEXT NOT NULL,
            date              TEXT NOT NULL,
            ticker            TEXT NOT NULL,
            pick_rank         INTEGER NOT NULL,
            pm_rationale      TEXT,
            confidence_score  DOUBLE PRECISION,
            metrics           TEXT,
            price_at_pick     DOUBLE PRECISION,
            target_price      DOUBLE PRECISION,
            target_hit_date   TEXT,
            return_session    DOUBLE PRECISION,
            actual_eod_change DOUBLE PRECISION,
            return_3d         DOUBLE PRECISION,
            return_7d         DOUBLE PRECISION,
            return_14d        DOUBLE PRECISION,
            return_30d        DOUBLE PRECISION,
            return_90d        DOUBLE PRECISION,
            return_180d       DOUBLE PRECISION,
            price_today       DOUBLE PRECISION,
            fetched_at        TEXT
        )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_run_date "
        "ON simulated_predictions(run_id, date)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_ticker "
        "ON simulated_predictions(ticker)"
    )


def init_db() -> None:
    """Create tables if they don't already exist."""
    conn = get_connection()
    cur = conn.cursor()
    if USE_POSTGRES:
        _init_schema_postgres(cur)
    else:
        _init_schema_sqlite(cur)

    conn.commit()
    conn.close()
    _migrate_schema()


def _migrate_schema() -> None:
    """
    Safe, idempotent migration: adds any columns that don't yet exist in
    alpha_predictions. Handles databases created before Alpha Decay tracking.
    """
    new_cols = [
        ("return_3d",  "REAL"),
        ("return_7d",  "REAL"),
        ("return_14d", "REAL"),
        ("return_30d", "REAL"),
        ("strategy",   "TEXT NOT NULL DEFAULT 'alpha'"),
        ("metrics",    "TEXT"),
        ("price_at_pick", "REAL"),
        ("return_session", "REAL"),
        ("target_price", "REAL"),
        ("target_hit_date", "TEXT"),
    ]
    conn = get_connection()
    cur = conn.cursor()

    if USE_POSTGRES:
        existing = _pg_column_names(cur, "alpha_predictions")
    else:
        existing = {
            str(row[1]).lower()
            for row in cur.execute("PRAGMA table_info(alpha_predictions)")
        }

    for col, typedef in new_cols:
        if col.lower() not in existing:
            cur.execute(
                f"ALTER TABLE alpha_predictions ADD COLUMN {col} {_ddl_type(typedef)}"
            )

    sim_new_cols = [
        ("run_id",            "TEXT"),
        ("pick_rank",         "INTEGER"),
        ("metrics",           "TEXT"),
        ("price_at_pick",     "REAL"),
        ("target_price",      "REAL"),
        ("target_hit_date",   "TEXT"),
        ("return_session",    "REAL"),
        ("actual_eod_change", "REAL"),
        ("return_3d",         "REAL"),
        ("return_7d",         "REAL"),
        ("return_14d",        "REAL"),
        ("return_30d",        "REAL"),
        ("return_90d",        "REAL"),
        ("return_180d",       "REAL"),
        ("price_today",       "REAL"),
        ("fetched_at",        "TEXT"),
    ]

    if USE_POSTGRES:
        sim_existing = _pg_column_names(cur, "simulated_predictions")
    else:
        sim_existing = {
            str(row[1]).lower()
            for row in cur.execute("PRAGMA table_info(simulated_predictions)")
        }

    if sim_existing:
        for col, typedef in sim_new_cols:
            if col.lower() not in sim_existing:
                cur.execute(
                    f"ALTER TABLE simulated_predictions ADD COLUMN {col} {_ddl_type(typedef)}"
                )

    conn.commit()
    conn.close()


# ─── actual_market_movers ─────────────────────────────────────────────────────

def insert_market_movers(movers: list[dict]) -> None:
    """
    Insert a list of market mover records.
    Each dict must have: ticker, percent_change.
    Optional keys: date, catalyst_reason.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    for m in movers:
        cur.execute(
            """
            INSERT INTO actual_market_movers (date, ticker, percent_change, catalyst_reason)
            VALUES (?, ?, ?, ?)
            """,
            (
                m.get("date", today),
                m["ticker"],
                m["percent_change"],
                m.get("catalyst_reason", ""),
            ),
        )
    conn.commit()
    conn.close()


def get_all_market_movers() -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date, ticker, percent_change, catalyst_reason
        FROM actual_market_movers
        ORDER BY date DESC, percent_change DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── alpha_predictions ────────────────────────────────────────────────────────

def insert_prediction(
    date: str,
    ticker: str,
    pm_rationale: str,
    confidence_score: float,
    strategy: str = "alpha",
    metrics_dict: dict | None = None,
    price_at_pick: float | None = None,
    target_price: float | None = None,
) -> None:
    import json as _json
    metrics_json = _json.dumps(metrics_dict) if metrics_dict else None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alpha_predictions
            (date, ticker, pm_rationale, confidence_score, strategy, metrics,
             price_at_pick, target_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, ticker, pm_rationale, confidence_score, strategy, metrics_json,
         price_at_pick, target_price),
    )
    conn.commit()
    conn.close()


def get_predictions_for_date(date: str, strategy: str | None = None) -> list[dict]:
    """Returns predictions for a given date. Pass strategy='alpha' or 'squeeze' to filter."""
    conn = get_connection()
    cur = conn.cursor()
    if strategy:
        cur.execute(
            """
            SELECT id, ticker, pm_rationale, confidence_score, metrics,
                   target_price, target_hit_date
            FROM alpha_predictions
            WHERE date = ? AND strategy = ?
            """,
            (date, strategy),
        )
    else:
        cur.execute(
            """
            SELECT id, ticker, pm_rationale, confidence_score, metrics,
                   target_price, target_hit_date
            FROM alpha_predictions
            WHERE date = ?
            """,
            (date,),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_eod_result(
    prediction_id: int,
    actual_eod_change: float,
    manager_feedback: str,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE alpha_predictions
        SET actual_eod_change = ?, manager_feedback = ?
        WHERE id = ?
        """,
        (actual_eod_change, manager_feedback, prediction_id),
    )
    conn.commit()
    conn.close()


def get_recent_manager_feedback(limit: int = 5) -> list[dict]:
    """Returns the last `limit` resolved predictions (with manager feedback)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date, ticker, confidence_score, actual_eod_change, manager_feedback
        FROM alpha_predictions
        WHERE manager_feedback IS NOT NULL
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_predictions_performance(
    days_back: int = 14,
    limit: int = 200,
    strategy: str | None = None,
) -> list[dict]:
    """
    Returns the agent's own recent picks with their realised performance, so the
    morning Analyst can self-review BEFORE making new recommendations.

    Unlike `get_recent_manager_feedback` / `search_feedback_by_keywords`, this
    is NOT filtered by `manager_feedback IS NOT NULL` — it surfaces every pick
    inside the window, including very recent ones that the EOD Manager has not
    reviewed yet. That matters because a stock recommended 3 days ago may
    already have moved +15% (or -8%) before any Manager note exists, and the
    morning Analyst should see that real outcome.

    Each row carries:
      date, ticker, strategy, confidence_score, pm_rationale,
      price_at_pick, target_price, target_hit_date,
      return_session, actual_eod_change,
      return_3d, return_7d, return_14d, return_30d,
      manager_feedback (NULL when EOD review is still pending).

    Ordered newest-first.
    """
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=max(1, int(days_back)))).isoformat()

    conn = get_connection()
    cur = conn.cursor()
    if strategy:
        cur.execute(
            """
            SELECT date, ticker, strategy, confidence_score, pm_rationale,
                   price_at_pick, target_price, target_hit_date,
                   return_session, actual_eod_change,
                   return_3d, return_7d, return_14d, return_30d,
                   manager_feedback
            FROM alpha_predictions
            WHERE date >= ? AND strategy = ?
            ORDER BY date DESC, ticker ASC
            LIMIT ?
            """,
            (cutoff, strategy, int(limit)),
        )
    else:
        cur.execute(
            """
            SELECT date, ticker, strategy, confidence_score, pm_rationale,
                   price_at_pick, target_price, target_hit_date,
                   return_session, actual_eod_change,
                   return_3d, return_7d, return_14d, return_30d,
                   manager_feedback
            FROM alpha_predictions
            WHERE date >= ?
            ORDER BY date DESC, ticker ASC
            LIMIT ?
            """,
            (cutoff, int(limit)),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_feedback_by_keywords(keywords: list[str], limit: int = 10) -> list[dict]:
    """
    Full-text keyword search across manager_feedback, ticker, and pm_rationale.
    Returns rows where ANY keyword appears in any of those columns.
    Ordered by date DESC so the most recent relevant lessons surface first.
    """
    if not keywords:
        return []

    conn = get_connection()
    cur = conn.cursor()

    # Build OR-chained LIKE clauses across three columns per keyword
    clauses: list[str] = []
    params:  list[str] = []
    for kw in keywords:
        pattern = f"%{kw.strip()}%"
        clauses.append(
            "(manager_feedback LIKE ? OR ticker LIKE ? OR pm_rationale LIKE ?)"
        )
        params.extend([pattern, pattern, pattern])

    where_sql = " OR ".join(clauses)
    sql = f"""
        SELECT date, ticker, confidence_score, actual_eod_change, manager_feedback
        FROM alpha_predictions
        WHERE manager_feedback IS NOT NULL
          AND ({where_sql})
        ORDER BY date DESC
        LIMIT ?
    """
    params.append(limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_predictions(strategy: str | None = None) -> list[dict]:
    """Returns all predictions. Pass strategy='alpha' or 'squeeze' to filter by pipeline."""
    conn = get_connection()
    cur = conn.cursor()
    if strategy:
        cur.execute(
            """
            SELECT date, ticker, pm_rationale, confidence_score,
                   actual_eod_change, manager_feedback,
                   return_3d, return_7d, return_14d, return_30d,
                   metrics, price_at_pick, return_session,
                   target_price, target_hit_date
            FROM alpha_predictions
            WHERE strategy = ?
            ORDER BY date DESC
            """,
            (strategy,),
        )
    else:
        cur.execute(
            """
            SELECT date, ticker, pm_rationale, confidence_score,
                   actual_eod_change, manager_feedback,
                   return_3d, return_7d, return_14d, return_30d,
                   metrics, price_at_pick, return_session,
                   target_price, target_hit_date
            FROM alpha_predictions
            ORDER BY date DESC
            """
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_predictions_by_ticker(ticker: str) -> list[dict]:
    """
    All predictions for a given ticker (all strategies), newest first.
    Ticker match is case-insensitive.
    """
    t = (ticker or "").strip()
    if not t:
        return []
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date, ticker, pm_rationale, confidence_score,
               actual_eod_change, manager_feedback,
               return_3d, return_7d, return_14d, return_30d,
               metrics, strategy, price_at_pick, return_session,
               target_price, target_hit_date
        FROM alpha_predictions
        WHERE UPPER(ticker) = UPPER(?)
        ORDER BY date DESC
        """,
        (t,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_tickers() -> list[str]:
    """Distinct tickers in alpha_predictions, sorted A–Z (uppercase for display)."""
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT UPPER(ticker) AS t
        FROM alpha_predictions
        GROUP BY UPPER(ticker)
        ORDER BY t
        """
    )
    rows = [dict(r)["t"] for r in cur.fetchall()]
    conn.close()
    return rows


# ─── Alpha Decay: multi-horizon return tracking ───────────────────────────────

RETURN_WINDOWS: list[tuple[int, str]] = [
    (3,  "return_3d"),
    (7,  "return_7d"),
    (14, "return_14d"),
    (30, "return_30d"),
]


def _fetch_history_safe(ticker: str, start: str, end: str):
    """yfinance fetch with timezone strip; returns DataFrame or None on any failure."""
    try:
        import yfinance as yf  # local import keeps startup fast
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        hist.index = hist.index.tz_localize(None)
        return hist
    except Exception:
        return None


def _compute_returns_for_row(
    row: dict,
    today: "date",
    *,
    overwrite: bool = False,
) -> dict:
    """
    Compute every return cell (session, EOD, T+3/7/14/30) for one prediction row.

    overwrite=False  → only fills cells that are currently NULL.
    overwrite=True   → recomputes every elapsed cell, used by validation.

    Baseline rules
    --------------
    return_session         : RTH open → close on rec_date  (intraday daily bar)
    actual_eod_change      : prev-trading-close → rec_date close (same yardstick as T+N)
    return_3d/7d/14d/30d   : prev-trading-close → first close on/after rec_date+N days

    Using the same baseline (prior close) for EOD as for T+N gives consistent,
    comparable percentages across the whole alpha-decay timeline.
    """
    rec_date = date.fromisoformat(row["date"])
    ticker   = (row.get("ticker") or "").strip()
    if not ticker:
        return {}

    # widest window we may need: 33 calendar days post + a few pre for baseline
    fetch_start = (rec_date - timedelta(days=10)).isoformat()
    fetch_end   = (rec_date + timedelta(days=40)).isoformat()
    hist = _fetch_history_safe(ticker, fetch_start, fetch_end)
    if hist is None:
        return {}

    updates: dict[str, float] = {}

    # ── session: RTH open → close on rec_date ────────────────────────────────
    if (overwrite or row.get("return_session") is None) and rec_date <= today:
        day_bar = hist[hist.index.date == rec_date]
        if not day_bar.empty:
            o = float(day_bar["Open"].iloc[0])
            c = float(day_bar["Close"].iloc[0])
            if o and o > 0:
                updates["return_session"] = round((c - o) / o * 100, 2)

    # baseline = last trading close strictly BEFORE rec_date
    pre_rec = hist[hist.index.date < rec_date]
    if pre_rec.empty:
        return updates  # cannot compute prev-close-based fields
    baseline = float(pre_rec["Close"].iloc[-1])
    if baseline <= 0:
        return updates

    # ── actual_eod_change: prev-close → rec_date close ───────────────────────
    if (overwrite or row.get("actual_eod_change") is None) and rec_date <= today:
        day_bar = hist[hist.index.date == rec_date]
        if not day_bar.empty:
            c = float(day_bar["Close"].iloc[0])
            updates["actual_eod_change"] = round((c - baseline) / baseline * 100, 2)

    # ── multi-day forward windows ────────────────────────────────────────────
    for days, col in RETURN_WINDOWS:
        if not overwrite and row.get(col) is not None:
            continue
        target = rec_date + timedelta(days=days)
        if target > today:
            continue  # window not yet elapsed
        post = hist[hist.index.date >= target]
        if post.empty:
            continue
        target_price = float(post["Close"].iloc[0])
        updates[col] = round((target_price - baseline) / baseline * 100, 2)

    return updates


def _apply_updates(row_id: int, updates: dict) -> int:
    if not updates:
        return 0
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [row_id]
    conn = get_connection()
    conn.execute(f"UPDATE alpha_predictions SET {set_clause} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return len(updates)


def update_historical_returns() -> dict:
    """
    Incremental backfill: for every prediction that still has a NULL in
    return_session / actual_eod_change / return_3d / return_7d / return_14d /
    return_30d, fill from yfinance. Existing values are NEVER overwritten.

    Also resolves target_hit_date for any pick whose target_price is set but
    the hit date is unknown — done in the same pass so dashboards stay
    consistent without an extra command.

    Returns: {records_scanned, records_updated, updates_written, target_hits_resolved}.
    """
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, date, ticker, actual_eod_change, return_session,
               return_3d, return_7d, return_14d, return_30d
        FROM alpha_predictions
        WHERE return_session    IS NULL
           OR actual_eod_change IS NULL
           OR return_3d         IS NULL
           OR return_7d         IS NULL
           OR return_14d        IS NULL
           OR return_30d        IS NULL
        """
    )
    pending = [dict(r) for r in cur.fetchall()]
    conn.close()

    today           = date.today()
    records_updated = 0
    updates_written = 0

    for row in pending:
        col_updates = _compute_returns_for_row(row, today, overwrite=False)
        n = _apply_updates(row["id"], col_updates)
        if n:
            records_updated += 1
            updates_written += n

    # Always also resolve any pending sell-target hits — same yfinance dependency.
    hit_summary = update_target_hit_dates()

    return {
        "records_scanned": len(pending),
        "records_updated": records_updated,
        "updates_written": updates_written,
        "target_hits_resolved": hit_summary.get("hits_written", 0),
    }


def update_target_hit_dates() -> dict:
    """
    For every prediction with target_price set and target_hit_date NULL, fetch
    yfinance daily highs in [rec_date, rec_date + 30 calendar days] and store
    the FIRST date where High >= target_price. If the window has fully elapsed
    and no day reached the target, store the sentinel string 'MISSED' so we
    don't keep re-checking that row (NULL means "still resolving").

    Re-run safe (skips rows that already have a value). Idempotent.

    Returns: {records_scanned, hits_written, missed_marked, unfetchable}.
    """
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, date, ticker, target_price
        FROM alpha_predictions
        WHERE target_price    IS NOT NULL
          AND target_hit_date IS NULL
        """
    )
    pending = [dict(r) for r in cur.fetchall()]
    conn.close()

    today          = date.today()
    hits_written   = 0
    missed_marked  = 0
    unfetchable    = []

    for row in pending:
        try:
            rec_date = date.fromisoformat(row["date"])
            target   = float(row["target_price"])
        except (ValueError, TypeError):
            continue

        window_end = rec_date + timedelta(days=30)
        # Fetch from rec_date through window_end (+1 to make end inclusive in yf).
        hist = _fetch_history_safe(
            row["ticker"],
            rec_date.isoformat(),
            (window_end + timedelta(days=1)).isoformat(),
        )
        if hist is None or hist.empty:
            unfetchable.append(f"{row['ticker']} ({row['date']})")
            continue

        in_window = hist[
            (hist.index.date >= rec_date) & (hist.index.date <= window_end)
        ]
        if in_window.empty:
            continue

        hits = in_window[in_window["High"] >= target]
        if not hits.empty:
            hit_date = hits.index[0].date().isoformat()
            conn = get_connection()
            conn.execute(
                "UPDATE alpha_predictions SET target_hit_date = ? WHERE id = ?",
                (hit_date, row["id"]),
            )
            conn.commit()
            conn.close()
            hits_written += 1
        elif window_end < today:
            # Window fully elapsed without a hit — mark as missed so we stop
            # re-querying yfinance for this row on every run.
            conn = get_connection()
            conn.execute(
                "UPDATE alpha_predictions SET target_hit_date = ? WHERE id = ?",
                ("MISSED", row["id"]),
            )
            conn.commit()
            conn.close()
            missed_marked += 1

    return {
        "records_scanned": len(pending),
        "hits_written"   : hits_written,
        "missed_marked"  : missed_marked,
        "unfetchable"    : unfetchable,
    }


def validate_and_backfill_all(
    *,
    overwrite: bool = False,
    tolerance: float = 0.5,
) -> dict:
    """
    Audit EVERY prediction row against yfinance and fix gaps.

    overwrite=False (default): only fills NULL cells (same as
        update_historical_returns) but ALSO returns a `mismatches` list of cells
        whose stored value diverges from yfinance by more than `tolerance` (%pts).
        Nothing is overwritten — the operator decides what to do.

    overwrite=True: recomputes every elapsed cell from yfinance and writes the
        canonical value, useful when you want a clean slate.

    Returns:
        {
          'records_scanned' : int,
          'records_updated' : int,   # rows that received any write
          'updates_written' : int,   # individual cell writes
          'cells_filled'    : dict,  # per-column count of filled NULLs
          'mismatches'      : list[dict],  # only when overwrite=False
          'unfetchable'     : list[str],   # tickers where yfinance returned nothing
        }
    """
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, date, ticker, actual_eod_change, return_session,
               return_3d, return_7d, return_14d, return_30d
        FROM alpha_predictions
        ORDER BY date DESC, ticker ASC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    today           = date.today()
    records_updated = 0
    updates_written = 0
    cells_filled: dict[str, int] = {
        "return_session": 0, "actual_eod_change": 0,
        "return_3d": 0, "return_7d": 0, "return_14d": 0, "return_30d": 0,
    }
    mismatches: list[dict] = []
    unfetchable: list[str] = []

    for row in rows:
        # canonical values from yfinance (recomputed regardless of stored value)
        canonical = _compute_returns_for_row(
            {**row,
             # zero out so _compute_returns_for_row treats every cell as missing
             "return_session": None, "actual_eod_change": None,
             "return_3d": None, "return_7d": None,
             "return_14d": None, "return_30d": None},
            today,
            overwrite=True,
        )
        if not canonical:
            unfetchable.append(f"{row['ticker']} ({row['date']})")
            continue

        if overwrite:
            to_write = canonical
        else:
            to_write = {}
            for col, fresh in canonical.items():
                stored = row.get(col)
                if stored is None:
                    to_write[col] = fresh
                    cells_filled[col] = cells_filled.get(col, 0) + 1
                else:
                    try:
                        if abs(float(stored) - float(fresh)) > tolerance:
                            mismatches.append({
                                "id"     : row["id"],
                                "date"   : row["date"],
                                "ticker" : row["ticker"],
                                "column" : col,
                                "stored" : float(stored),
                                "fetched": float(fresh),
                                "diff"   : round(float(fresh) - float(stored), 2),
                            })
                    except (TypeError, ValueError):
                        continue

        n = _apply_updates(row["id"], to_write)
        if n:
            records_updated += 1
            updates_written += n

    return {
        "records_scanned": len(rows),
        "records_updated": records_updated,
        "updates_written": updates_written,
        "cells_filled"   : cells_filled,
        "mismatches"     : mismatches,
        "unfetchable"    : unfetchable,
    }


# ─── Duplicate consolidation ──────────────────────────────────────────────────

def _coalesce(*vals):
    """First non-None / non-NaN value."""
    for v in vals:
        if v is None:
            continue
        if isinstance(v, float):
            try:
                import math
                if math.isnan(v):
                    continue
            except Exception:
                pass
        return v
    return None


def merge_duplicate_predictions(dry_run: bool = False) -> dict:
    """
    Consolidate rows that share the same (UPPER(ticker), strategy, date) into a
    single row, merging EVERY field instead of dropping data:

      - pm_rationale     : unique rationales joined with bullets
      - confidence_score : MAX of the duplicates
      - manager_feedback : latest non-empty
      - actual_eod_change / return_session / return_3d/7d/14d/30d : first non-NULL
      - metrics (JSON)   : shallow merge (later rows fill missing keys only)
      - price_at_pick    : first non-NULL

    Returns:
        { 'groups_scanned', 'groups_merged', 'rows_deleted', 'rows_updated' }.
    """
    import json as _json

    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, date, ticker, pm_rationale, confidence_score,
               actual_eod_change, manager_feedback,
               return_3d, return_7d, return_14d, return_30d,
               strategy, metrics, price_at_pick, return_session,
               target_price, target_hit_date
        FROM alpha_predictions
        ORDER BY date ASC, id ASC
        """
    )
    all_rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    groups: dict[tuple[str, str, str], list[dict]] = {}
    for r in all_rows:
        key = (
            (r.get("ticker") or "").strip().upper(),
            (r.get("strategy") or "alpha").strip().lower() or "alpha",
            (r.get("date") or "").strip(),
        )
        groups.setdefault(key, []).append(r)

    groups_scanned = len(groups)
    groups_merged  = 0
    rows_deleted   = 0
    rows_updated   = 0

    conn = get_connection()
    cur  = conn.cursor()

    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        groups_merged += 1
        keeper = rows[0]
        dups   = rows[1:]

        rationales: list[str] = []
        for r in rows:
            txt = (r.get("pm_rationale") or "").strip()
            if txt and txt not in rationales:
                rationales.append(txt)
        if len(rationales) > 1:
            merged_rationale = "Multi-reason pick:\n- " + "\n- ".join(rationales)
        elif rationales:
            merged_rationale = rationales[0]
        else:
            merged_rationale = None

        confs = [r.get("confidence_score") for r in rows
                 if r.get("confidence_score") is not None]
        merged_conf = max(confs) if confs else None

        feedbacks = [r.get("manager_feedback") for r in rows
                     if (r.get("manager_feedback") or "").strip()]
        merged_feedback = feedbacks[-1] if feedbacks else None

        merged_eod      = _coalesce(*[r.get("actual_eod_change") for r in rows])
        merged_session  = _coalesce(*[r.get("return_session")   for r in rows])
        merged_r3       = _coalesce(*[r.get("return_3d")        for r in rows])
        merged_r7       = _coalesce(*[r.get("return_7d")        for r in rows])
        merged_r14      = _coalesce(*[r.get("return_14d")       for r in rows])
        merged_r30      = _coalesce(*[r.get("return_30d")       for r in rows])
        merged_price    = _coalesce(*[r.get("price_at_pick")    for r in rows])

        # Sell target: most ambitious (max) target wins; earliest hit date wins.
        targets = [r.get("target_price") for r in rows
                   if r.get("target_price") is not None]
        merged_target = max(targets) if targets else None

        hit_dates = sorted(
            d for d in (r.get("target_hit_date") for r in rows)
            if d and str(d).strip()
        )
        merged_target_hit = hit_dates[0] if hit_dates else None

        merged_metrics: dict = {}
        for r in rows:
            blob = r.get("metrics")
            if not blob:
                continue
            try:
                obj = _json.loads(blob) if isinstance(blob, str) else blob
            except Exception:
                obj = None
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k not in merged_metrics or merged_metrics[k] in (None, "", []):
                        merged_metrics[k] = v
        merged_metrics_json = _json.dumps(merged_metrics) if merged_metrics else None

        if dry_run:
            continue

        cur.execute(
            """
            UPDATE alpha_predictions
            SET pm_rationale      = ?,
                confidence_score  = ?,
                manager_feedback  = ?,
                actual_eod_change = ?,
                return_session    = ?,
                return_3d         = ?,
                return_7d         = ?,
                return_14d        = ?,
                return_30d        = ?,
                price_at_pick     = ?,
                metrics           = ?,
                target_price      = ?,
                target_hit_date   = ?
            WHERE id = ?
            """,
            (
                merged_rationale, merged_conf, merged_feedback,
                merged_eod, merged_session,
                merged_r3, merged_r7, merged_r14, merged_r30,
                merged_price, merged_metrics_json,
                merged_target, merged_target_hit,
                keeper["id"],
            ),
        )
        rows_updated += 1

        cur.executemany(
            "DELETE FROM alpha_predictions WHERE id = ?",
            [(d["id"],) for d in dups],
        )
        rows_deleted += len(dups)

    conn.commit()
    conn.close()

    return {
        "groups_scanned": groups_scanned,
        "groups_merged" : groups_merged,
        "rows_updated"  : rows_updated,
        "rows_deleted"  : rows_deleted,
    }


# ─── Simulated predictions (historical back-test) ────────────────────────────
#
# `simulated_predictions` stores back-tested picks generated by simulator.py.
# It is fully isolated from `alpha_predictions` so the live dashboard, EOD
# pipeline, and Manager memory are NEVER influenced by simulation runs.
#
# Each row represents one of the top-N picks for a single back-tested day,
# tagged by `run_id` so multiple runs (e.g. different prompt variants or
# date windows) can coexist.

SIM_RETURN_WINDOWS: list[tuple[int, str]] = [
    (3,   "return_3d"),
    (7,   "return_7d"),
    (14,  "return_14d"),
    (30,  "return_30d"),
    (90,  "return_90d"),
    (180, "return_180d"),
]


def insert_simulated_prediction(
    *,
    run_id: str,
    date: str,
    ticker: str,
    pick_rank: int,
    pm_rationale: str,
    confidence_score: float,
    metrics_dict: dict | None = None,
    price_at_pick: float | None = None,
    target_price: float | None = None,
) -> int:
    """Insert one simulated pick. Returns the new row id."""
    import json as _json
    metrics_json = _json.dumps(metrics_dict) if metrics_dict else None
    fetched_at = datetime.utcnow().isoformat(timespec="seconds")
    conn = get_connection()
    cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute(
            """
            INSERT INTO simulated_predictions
                (run_id, date, ticker, pick_rank, pm_rationale, confidence_score,
                 metrics, price_at_pick, target_price, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                run_id, date, ticker.upper().strip(), int(pick_rank),
                pm_rationale, float(confidence_score),
                metrics_json, price_at_pick, target_price, fetched_at,
            ),
        )
        row = cur.fetchone()
        new_id = int(row["id"]) if row else 0
    else:
        cur.execute(
            """
            INSERT INTO simulated_predictions
                (run_id, date, ticker, pick_rank, pm_rationale, confidence_score,
                 metrics, price_at_pick, target_price, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, date, ticker.upper().strip(), int(pick_rank),
                pm_rationale, float(confidence_score),
                metrics_json, price_at_pick, target_price, fetched_at,
            ),
        )
        new_id = int(cur.lastrowid or 0)
    conn.commit()
    conn.close()
    return new_id


def get_simulated_predictions(run_id: str | None = None) -> list[dict]:
    """All simulated picks (optionally filtered by run_id), newest date first."""
    conn = get_connection()
    cur = conn.cursor()
    if run_id:
        cur.execute(
            """
            SELECT id, run_id, date, ticker, pick_rank, pm_rationale,
                   confidence_score, metrics, price_at_pick, target_price,
                   target_hit_date, return_session, actual_eod_change,
                   return_3d, return_7d, return_14d, return_30d,
                   return_90d, return_180d, price_today, fetched_at
            FROM simulated_predictions
            WHERE run_id = ?
            ORDER BY date DESC, pick_rank ASC
            """,
            (run_id,),
        )
    else:
        cur.execute(
            """
            SELECT id, run_id, date, ticker, pick_rank, pm_rationale,
                   confidence_score, metrics, price_at_pick, target_price,
                   target_hit_date, return_session, actual_eod_change,
                   return_3d, return_7d, return_14d, return_30d,
                   return_90d, return_180d, price_today, fetched_at
            FROM simulated_predictions
            ORDER BY date DESC, pick_rank ASC
            """
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_simulated_run_ids() -> list[dict]:
    """List every distinct run_id with row count, min date, max date."""
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT run_id, COUNT(*) AS picks,
               MIN(date) AS first_date, MAX(date) AS last_date,
               MIN(fetched_at) AS started_at
        FROM simulated_predictions
        GROUP BY run_id
        ORDER BY started_at DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_simulated_dates_done(run_id: str) -> set[str]:
    """Set of dates that already have at least one row for this run_id."""
    conn = get_connection()
    cur = conn.execute(
        "SELECT DISTINCT date FROM simulated_predictions WHERE run_id = ?",
        (run_id,),
    )
    out = {dict(r)["date"] for r in cur.fetchall()}
    conn.close()
    return out


def _compute_sim_returns_for_row(row: dict, today: "date") -> dict:
    """
    Compute every return cell for one simulated row, including the extended
    horizons T+90 / T+180 and a `price_today` snapshot.

    Mirrors `_compute_returns_for_row` but with extra windows and an extra
    `price_today` field that captures the most recent close.
    """
    rec_date = date.fromisoformat(row["date"])
    ticker   = (row.get("ticker") or "").strip()
    if not ticker:
        return {}

    fetch_start = (rec_date - timedelta(days=10)).isoformat()
    # +200 days covers T+180 with slack for non-trading days.
    fetch_end   = (rec_date + timedelta(days=210)).isoformat()
    hist = _fetch_history_safe(ticker, fetch_start, fetch_end)
    if hist is None:
        return {}

    updates: dict[str, float] = {}

    if row.get("return_session") is None and rec_date <= today:
        day_bar = hist[hist.index.date == rec_date]
        if not day_bar.empty:
            o = float(day_bar["Open"].iloc[0])
            c = float(day_bar["Close"].iloc[0])
            if o and o > 0:
                updates["return_session"] = round((c - o) / o * 100, 2)

    pre_rec = hist[hist.index.date < rec_date]
    if pre_rec.empty:
        return updates
    baseline = float(pre_rec["Close"].iloc[-1])
    if baseline <= 0:
        return updates

    if row.get("actual_eod_change") is None and rec_date <= today:
        day_bar = hist[hist.index.date == rec_date]
        if not day_bar.empty:
            c = float(day_bar["Close"].iloc[0])
            updates["actual_eod_change"] = round((c - baseline) / baseline * 100, 2)

    for days, col in SIM_RETURN_WINDOWS:
        if row.get(col) is not None:
            continue
        target_d = rec_date + timedelta(days=days)
        if target_d > today:
            continue
        post = hist[hist.index.date >= target_d]
        if post.empty:
            continue
        target_price = float(post["Close"].iloc[0])
        updates[col] = round((target_price - baseline) / baseline * 100, 2)

    last_close = float(hist["Close"].iloc[-1])
    if last_close > 0:
        updates["price_today"] = round(last_close, 4)

    return updates


def _apply_sim_updates(row_id: int, updates: dict) -> int:
    if not updates:
        return 0
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [row_id]
    conn = get_connection()
    conn.execute(
        f"UPDATE simulated_predictions SET {set_clause} WHERE id = ?", vals
    )
    conn.commit()
    conn.close()
    return len(updates)


def update_simulated_returns(run_id: str | None = None) -> dict:
    """
    Backfill every return cell (session, EOD, T+3/7/14/30/90/180, price_today)
    for simulated predictions whose price_at_pick is set.

    Always refreshes `price_today` (it changes every market day), but never
    overwrites already-stored historical return cells. Resolves target hit
    dates in the same pass.

    Returns: {records_scanned, records_updated, updates_written,
              target_hits_resolved}.
    """
    conn = get_connection()
    cur = conn.cursor()
    if run_id:
        cur.execute(
            """
            SELECT id, date, ticker, return_session, actual_eod_change,
                   return_3d, return_7d, return_14d, return_30d,
                   return_90d, return_180d, price_today
            FROM simulated_predictions
            WHERE run_id = ?
            """,
            (run_id,),
        )
    else:
        cur.execute(
            """
            SELECT id, date, ticker, return_session, actual_eod_change,
                   return_3d, return_7d, return_14d, return_30d,
                   return_90d, return_180d, price_today
            FROM simulated_predictions
            """
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    today           = date.today()
    records_updated = 0
    updates_written = 0
    for row in rows:
        # Always recompute price_today even if other cells are full.
        row_for_calc = {**row, "price_today": None}
        col_updates = _compute_sim_returns_for_row(row_for_calc, today)
        # Avoid pointless writes if price_today hasn't actually changed.
        if (
            "price_today" in col_updates
            and row.get("price_today") is not None
            and abs(float(row["price_today"]) - col_updates["price_today"]) < 1e-6
            and len(col_updates) == 1
        ):
            continue
        n = _apply_sim_updates(row["id"], col_updates)
        if n:
            records_updated += 1
            updates_written += n

    hit_summary = update_simulated_target_hits(run_id=run_id)

    return {
        "records_scanned":      len(rows),
        "records_updated":      records_updated,
        "updates_written":      updates_written,
        "target_hits_resolved": hit_summary.get("hits_written", 0),
    }


def update_simulated_target_hits(run_id: str | None = None) -> dict:
    """Resolve target_hit_date for simulated picks (same logic as live)."""
    conn = get_connection()
    cur = conn.cursor()
    if run_id:
        cur.execute(
            """
            SELECT id, date, ticker, target_price
            FROM simulated_predictions
            WHERE target_price IS NOT NULL
              AND target_hit_date IS NULL
              AND run_id = ?
            """,
            (run_id,),
        )
    else:
        cur.execute(
            """
            SELECT id, date, ticker, target_price
            FROM simulated_predictions
            WHERE target_price IS NOT NULL
              AND target_hit_date IS NULL
            """
        )
    pending = [dict(r) for r in cur.fetchall()]
    conn.close()

    today_d = date.today()
    hits_written = 0
    missed_marked = 0

    for row in pending:
        try:
            rec_date = date.fromisoformat(row["date"])
            target   = float(row["target_price"])
        except (ValueError, TypeError):
            continue

        window_end = rec_date + timedelta(days=30)
        hist = _fetch_history_safe(
            row["ticker"],
            rec_date.isoformat(),
            (window_end + timedelta(days=1)).isoformat(),
        )
        if hist is None or hist.empty:
            continue

        in_window = hist[
            (hist.index.date >= rec_date) & (hist.index.date <= window_end)
        ]
        if in_window.empty:
            continue

        hits = in_window[in_window["High"] >= target]
        if not hits.empty:
            hit_date = hits.index[0].date().isoformat()
            conn = get_connection()
            conn.execute(
                "UPDATE simulated_predictions SET target_hit_date = ? "
                "WHERE id = ?",
                (hit_date, row["id"]),
            )
            conn.commit()
            conn.close()
            hits_written += 1
        elif window_end < today_d:
            conn = get_connection()
            conn.execute(
                "UPDATE simulated_predictions SET target_hit_date = ? "
                "WHERE id = ?",
                ("MISSED", row["id"]),
            )
            conn.commit()
            conn.close()
            missed_marked += 1

    return {
        "records_scanned": len(pending),
        "hits_written":    hits_written,
        "missed_marked":   missed_marked,
    }


def get_prior_manager_feedback(before_date: str, limit: int = 8) -> list[dict]:
    """
    Manager feedback rows whose pick date is strictly before `before_date`.
    Used by the simulator so historical runs only see lessons that existed
    at the time of the back-tested pick.
    """
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT date, ticker, confidence_score, actual_eod_change, manager_feedback
        FROM alpha_predictions
        WHERE manager_feedback IS NOT NULL
          AND date < ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (before_date, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ─── Seed demo data (for first-run UI testing) ────────────────────────────────

def seed_demo_data() -> None:
    """Populates the DB with realistic sample data so the dashboard isn't empty.
    Always clears existing data first so re-running --demo is idempotent.
    """
    init_db()
    conn = get_connection()
    conn.execute("DELETE FROM actual_market_movers")
    conn.execute("DELETE FROM alpha_predictions")
    conn.commit()
    conn.close()

    movers = [
        {"date": "2026-04-03", "ticker": "NVDA", "percent_change": 11.2, "catalyst_reason": "Q1 earnings beat — EPS $6.12 vs $5.58 est; data-center revenue +120% YoY"},
        {"date": "2026-04-03", "ticker": "SMCI", "percent_change": 9.8,  "catalyst_reason": "Sympathy play — NVDA GPU demand lifts server maker"},
        {"date": "2026-04-03", "ticker": "AVGO", "percent_change": 7.4,  "catalyst_reason": "Sympathy play — AI networking chips in focus after NVDA beat"},
        {"date": "2026-04-03", "ticker": "AMD",  "percent_change": 6.1,  "catalyst_reason": "Sympathy play — sector rotation into semis"},
        {"date": "2026-04-03", "ticker": "MRVL", "percent_change": 8.3,  "catalyst_reason": "Sympathy play — custom silicon demand rerating"},
        {"date": "2026-04-03", "ticker": "META", "percent_change": 4.2,  "catalyst_reason": "Analyst upgrade — JPM raises PT to $720"},
        {"date": "2026-04-03", "ticker": "CRWD", "percent_change": 3.1,  "catalyst_reason": "Macro tailwind — government AI cybersecurity contract"},
        {"date": "2026-04-02", "ticker": "MSFT", "percent_change": 7.9,  "catalyst_reason": "Azure Q3 cloud revenue +35% YoY, beats consensus by 4pp"},
        {"date": "2026-04-02", "ticker": "CRM",  "percent_change": 6.5,  "catalyst_reason": "Sympathy play — enterprise SaaS spending re-rated with MSFT beat"},
        {"date": "2026-04-02", "ticker": "NOW",  "percent_change": 5.8,  "catalyst_reason": "Sympathy play — MSFT AI Copilot demand lifts workflow SaaS peers"},
    ]
    insert_market_movers(movers)

    predictions = [
        {
            "date": "2026-04-03",
            "ticker": "SMCI",
            "pm_rationale": "Strong sympathy candidate: NVDA blowout earnings drive direct GPU server demand for SMCI. Market cap $14B, avg volume 18M — passes all PM filters. High institutional catalyst.",
            "confidence_score": 0.82,
            "actual_eod_change": 9.8,
            "manager_feedback": "HIT ✅ — SMCI delivered +9.8%. Sympathy play thesis was correct. NVDA's data-center beat was the dominant factor. Lesson: When NVDA beats by >10%, SMCI sympathy is highly reliable (historically 78% hit rate). Increase base confidence for NVDA->SMCI plays to 0.85.",
            "price_at_pick": 38.5,
            "return_session": 1.2,
        },
        {
            "date": "2026-04-03",
            "ticker": "MRVL",
            "pm_rationale": "Custom AI silicon theme rerating. MRVL's Coherent DSP and custom ASIC business benefits directly from NVDA's AI infrastructure growth. $60B market cap, 15M avg vol.",
            "confidence_score": 0.74,
            "actual_eod_change": 8.3,
            "manager_feedback": "HIT ✅ — MRVL delivered +8.3%. Custom silicon thesis held. Lesson: MRVL has stronger fundamental basis than pure-sympathy plays since it has direct revenue exposure to hyperscaler AI capex. Future confidence: 0.78.",
            "price_at_pick": 72.1,
            "return_session": 0.6,
        },
        {
            "date": "2026-04-03",
            "ticker": "PLTR",
            "pm_rationale": "AI government contracts angle — defense tech beneficiary of AI spending cycle. $80B market cap, 45M avg vol. Analyst day catalyst.",
            "confidence_score": 0.61,
            "actual_eod_change": 1.4,
            "manager_feedback": "MISS ⚠️ — PLTR only moved +1.4%. Lesson: PLTR is too idiosyncratic — it requires its own catalyst, not just sector tailwinds. The connection to NVDA earnings was too tenuous. Reduce confidence for 'narrative' plays without direct financial linkage. Reject similar setups below 0.70 confidence.",
            "price_at_pick": 25.0,
            "return_session": -0.3,
        },
        {
            "date": "2026-04-02",
            "ticker": "CRM",
            "pm_rationale": "MSFT Azure beat drives enterprise SaaS spending expectations. CRM directly benefits from AI-integrated CRM demand (Einstein AI). $230B market cap, 8M avg vol.",
            "confidence_score": 0.79,
            "actual_eod_change": 6.5,
            "manager_feedback": "HIT ✅ — CRM delivered +6.5%. MSFT -> CRM sympathy thesis confirmed. Enterprise SaaS basket moves together during cloud earnings cycles. Lesson: SaaS sympathy plays have 2-3 day momentum windows — consider also flagging next-day plays.",
            "price_at_pick": 300.0,
            "return_session": 0.4,
        },
        {
            "date": "2026-04-02",
            "ticker": "NOW",
            "pm_rationale": "ServiceNow benefits from same enterprise AI capex cycle. MSFT's Copilot success signals strong enterprise AI demand. $170B market cap, 3M avg vol — slightly lower liquidity.",
            "confidence_score": 0.71,
            "actual_eod_change": 5.8,
            "manager_feedback": "NEAR MISS ⚠️ — NOW moved +5.8% (target was 6%). Thesis was correct but gain was marginal. Lesson: NOW has lower beta than CRM in MSFT sympathy plays due to lower volume and longer sales cycles. Adjust confidence down ~0.05 for NOW in enterprise SaaS basket.",
            "price_at_pick": 110.0,
            "return_session": 0.2,
        },
    ]
    conn = get_connection()
    cur = conn.cursor()
    for p in predictions:
        cur.execute(
            """
            INSERT INTO alpha_predictions
                (date, ticker, pm_rationale, confidence_score, actual_eod_change, manager_feedback, price_at_pick, return_session)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p["date"], p["ticker"], p["pm_rationale"],
                p["confidence_score"], p["actual_eod_change"], p["manager_feedback"],
                p["price_at_pick"], p["return_session"],
            ),
        )
    conn.commit()
    conn.close()
    print("[OK] Demo data seeded successfully.")
