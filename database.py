"""
database.py — Catalyst Alpha v1.0
SQLite schema initialization and all CRUD helpers.
"""

import sqlite3
from datetime import datetime, date, timedelta

DB_PATH = "catalyst_alpha.db"


# ─── Connection ───────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Schema ───────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't already exist."""
    conn = get_connection()
    cur = conn.cursor()

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
            return_30d        REAL,
            strategy          TEXT    NOT NULL DEFAULT 'alpha',
            metrics           TEXT
        )
    """)

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
        ("return_30d", "REAL"),
        ("strategy",   "TEXT NOT NULL DEFAULT 'alpha'"),
        ("metrics",    "TEXT"),
    ]
    conn = get_connection()
    cur  = conn.cursor()
    existing = {row[1] for row in cur.execute("PRAGMA table_info(alpha_predictions)")}
    for col, typedef in new_cols:
        if col not in existing:
            cur.execute(f"ALTER TABLE alpha_predictions ADD COLUMN {col} {typedef}")
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
) -> None:
    import json as _json
    metrics_json = _json.dumps(metrics_dict) if metrics_dict else None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alpha_predictions
            (date, ticker, pm_rationale, confidence_score, strategy, metrics)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (date, ticker, pm_rationale, confidence_score, strategy, metrics_json),
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
            SELECT id, ticker, pm_rationale, confidence_score, metrics
            FROM alpha_predictions
            WHERE date = ? AND strategy = ?
            """,
            (date, strategy),
        )
    else:
        cur.execute(
            """
            SELECT id, ticker, pm_rationale, confidence_score, metrics
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
                   return_3d, return_7d, return_30d, metrics
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
                   return_3d, return_7d, return_30d, metrics
            FROM alpha_predictions
            ORDER BY date DESC
            """
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Alpha Decay: multi-horizon return tracking ───────────────────────────────

def update_historical_returns() -> dict:
    """
    For every prediction that still has a NULL in return_3d / return_7d / return_30d,
    check whether the corresponding calendar-day window has elapsed. If it has,
    fetch the closing price via yfinance (weekend/holiday safe — uses the nearest
    following trading session) and write the % return back to the database.

    Baseline: the trading-day close BEFORE the recommendation date.
    Return %:  (target_close - baseline_close) / baseline_close * 100

    Returns a summary dict: {records_scanned, records_updated, updates_written}.
    """
    import yfinance as yf  # local import keeps startup fast for --help

    WINDOWS = [(3, "return_3d"), (7, "return_7d"), (30, "return_30d")]

    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        """
        SELECT id, date, ticker
        FROM alpha_predictions
        WHERE return_3d IS NULL OR return_7d IS NULL OR return_30d IS NULL
        """
    )
    pending = [dict(r) for r in cur.fetchall()]
    conn.close()

    today          = date.today()
    records_updated = 0
    updates_written = 0

    for row in pending:
        rec_date = date.fromisoformat(row["date"])
        ticker   = row["ticker"]

        # Only fetch history once per prediction, covering the widest window
        fetch_start = (rec_date - timedelta(days=3)).isoformat()
        fetch_end   = (rec_date + timedelta(days=33)).isoformat()

        try:
            hist = yf.Ticker(ticker).history(start=fetch_start, end=fetch_end)
            if len(hist) < 2:
                continue

            # Strip timezone so .date comparisons work consistently
            hist.index = hist.index.tz_localize(None)

            # Baseline = last trading close BEFORE recommendation date
            pre_rec = hist[hist.index.date < rec_date]
            if pre_rec.empty:
                continue
            baseline = float(pre_rec["Close"].iloc[-1])

        except Exception:
            continue

        col_updates: dict[str, float] = {}

        for days, col in WINDOWS:
            target = rec_date + timedelta(days=days)
            if target > today:
                continue  # window not yet elapsed

            # Nearest trading session on or after the target date
            post = hist[hist.index.date >= target]
            if post.empty:
                continue

            target_price        = float(post["Close"].iloc[0])
            ret                 = round((target_price - baseline) / baseline * 100, 2)
            col_updates[col]    = ret

        if col_updates:
            set_clause = ", ".join(f"{k} = ?" for k in col_updates)
            vals       = list(col_updates.values()) + [row["id"]]
            conn = get_connection()
            conn.execute(
                f"UPDATE alpha_predictions SET {set_clause} WHERE id = ?", vals
            )
            conn.commit()
            conn.close()
            records_updated += 1
            updates_written  += len(col_updates)

    return {
        "records_scanned": len(pending),
        "records_updated": records_updated,
        "updates_written": updates_written,
    }


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
        },
        {
            "date": "2026-04-03",
            "ticker": "MRVL",
            "pm_rationale": "Custom AI silicon theme rerating. MRVL's Coherent DSP and custom ASIC business benefits directly from NVDA's AI infrastructure growth. $60B market cap, 15M avg vol.",
            "confidence_score": 0.74,
            "actual_eod_change": 8.3,
            "manager_feedback": "HIT ✅ — MRVL delivered +8.3%. Custom silicon thesis held. Lesson: MRVL has stronger fundamental basis than pure-sympathy plays since it has direct revenue exposure to hyperscaler AI capex. Future confidence: 0.78.",
        },
        {
            "date": "2026-04-03",
            "ticker": "PLTR",
            "pm_rationale": "AI government contracts angle — defense tech beneficiary of AI spending cycle. $80B market cap, 45M avg vol. Analyst day catalyst.",
            "confidence_score": 0.61,
            "actual_eod_change": 1.4,
            "manager_feedback": "MISS ⚠️ — PLTR only moved +1.4%. Lesson: PLTR is too idiosyncratic — it requires its own catalyst, not just sector tailwinds. The connection to NVDA earnings was too tenuous. Reduce confidence for 'narrative' plays without direct financial linkage. Reject similar setups below 0.70 confidence.",
        },
        {
            "date": "2026-04-02",
            "ticker": "CRM",
            "pm_rationale": "MSFT Azure beat drives enterprise SaaS spending expectations. CRM directly benefits from AI-integrated CRM demand (Einstein AI). $230B market cap, 8M avg vol.",
            "confidence_score": 0.79,
            "actual_eod_change": 6.5,
            "manager_feedback": "HIT ✅ — CRM delivered +6.5%. MSFT -> CRM sympathy thesis confirmed. Enterprise SaaS basket moves together during cloud earnings cycles. Lesson: SaaS sympathy plays have 2-3 day momentum windows — consider also flagging next-day plays.",
        },
        {
            "date": "2026-04-02",
            "ticker": "NOW",
            "pm_rationale": "ServiceNow benefits from same enterprise AI capex cycle. MSFT's Copilot success signals strong enterprise AI demand. $170B market cap, 3M avg vol — slightly lower liquidity.",
            "confidence_score": 0.71,
            "actual_eod_change": 5.8,
            "manager_feedback": "NEAR MISS ⚠️ — NOW moved +5.8% (target was 6%). Thesis was correct but gain was marginal. Lesson: NOW has lower beta than CRM in MSFT sympathy plays due to lower volume and longer sales cycles. Adjust confidence down ~0.05 for NOW in enterprise SaaS basket.",
        },
    ]
    conn = get_connection()
    cur = conn.cursor()
    for p in predictions:
        cur.execute(
            """
            INSERT INTO alpha_predictions
                (date, ticker, pm_rationale, confidence_score, actual_eod_change, manager_feedback)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                p["date"], p["ticker"], p["pm_rationale"],
                p["confidence_score"], p["actual_eod_change"], p["manager_feedback"],
            ),
        )
    conn.commit()
    conn.close()
    print("[OK] Demo data seeded successfully.")
