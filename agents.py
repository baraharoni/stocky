"""
agents.py — Catalyst Alpha v1.0
Defines all 5 CrewAI agents, their tools, tasks, and assembled Crews.
"""

import os
import re
import json
import requests
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv
from yahoo_fin import stock_info as si

from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

import database as db

load_dotenv()

# ─── LLM ──────────────────────────────────────────────────────────────────────

claude_llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5"),
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
)

# ─── Domain Knowledge ─────────────────────────────────────────────────────────

# When stock X has a catalyst, these tickers often move in sympathy
SYMPATHY_MAP: dict[str, list[str]] = {
    "NVDA": ["SMCI", "AMD", "MRVL", "AVGO", "MU",  "KLAC", "LRCX"],
    "AMD":  ["NVDA", "INTC", "QCOM", "MRVL", "SMCI"],
    "MSFT": ["GOOGL", "AMZN", "CRM", "NOW",  "TEAM", "VEEV"],
    "AAPL": ["QCOM", "AVGO", "MU",  "AMAT"],
    "META": ["SNAP", "PINS", "GOOGL", "UBER", "LYFT"],
    "AMZN": ["SHOP", "GOOGL", "MSFT", "ABNB"],
    "TSLA": ["RIVN", "LCID", "NIO",  "XPEV"],
    "NFLX": ["ROKU", "SNAP", "PINS"],
    "COIN": ["MSTR", "HOOD"],
    "PLTR": ["DDOG", "NET",  "ZS",  "SNOW", "PANW"],
    "CRWD": ["PANW", "ZS",   "NET", "OKTA"],
}


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_mcap_str(s: str) -> float:
    """
    Parse Nasdaq-API market cap strings to a float in USD.
    Handles full numbers ('$1,430,652,218,220'), abbreviations ('$1.4B', '$450M'),
    and invalid values ('N/A', '', '-').
    """
    if not s:
        return 0.0
    s = s.strip().upper().replace("$", "").replace(",", "")
    if s in ("N/A", "NA", "", "-", "--"):
        return 0.0
    try:
        if s.endswith("T"):
            return float(s[:-1]) * 1e12
        if s.endswith("B"):
            return float(s[:-1]) * 1e9
        if s.endswith("M"):
            return float(s[:-1]) * 1e6
        return float(s)
    except (ValueError, AttributeError):
        return 0.0


def _fetch_gainers_primary() -> list[str]:
    """Primary source: yahoo_fin si.get_day_gainers(). May break if Yahoo changes HTML."""
    df = si.get_day_gainers()
    return df["Symbol"].dropna().tolist()[:100]


def _fetch_gainers_fallback() -> list[str]:
    """
    Fallback: Yahoo Finance JSON screener API.
    More stable than HTML scraping — returns structured JSON regardless of page layout.
    """
    url = (
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        "?scrIds=day_gainers&count=100&corsDomain=finance.yahoo.com"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=12)
    resp.raise_for_status()
    quotes = resp.json()["finance"]["result"][0]["quotes"]
    return [q["symbol"] for q in quotes]


# Compact SYMPATHY_MAP reference string — included in tool output to save tokens
# versus embedding the full dict (saves ~300 tokens per call).
_SYMPATHY_REF = (
    "NVDA→SMCI,AMD,MRVL,AVGO,MU,KLAC,LRCX | AMD→NVDA,INTC,QCOM,MRVL,SMCI | "
    "MSFT→GOOGL,AMZN,CRM,NOW,TEAM,VEEV | AAPL→QCOM,AVGO,MU,AMAT | "
    "META→SNAP,PINS,GOOGL,UBER,LYFT | AMZN→SHOP,GOOGL,MSFT,ABNB | "
    "TSLA→RIVN,LCID,NIO,XPEV | NFLX→ROKU,SNAP,PINS | COIN→MSTR,HOOD | "
    "PLTR→DDOG,NET,ZS,SNOW,PANW | CRWD→PANW,ZS,NET,OKTA"
)


@tool("Fetch Pre-Market Top Gainers")
def fetch_premarket_gainers(sector_focus: str) -> str:
    """
    Deep Scan: fetches up to 100 raw day-gainers and filters to the "Next Play"
    window before enriching each survivor with price, market cap, volume, and a
    one-line news headline. Returns up to 40 results as compact condensed strings.

    STRATEGY FILTER — "Next Play" Window:
      KEEP : +2.0% <= percent_change <= +6.0%  (early momentum / sympathy laggards)
      DROP  : percent_change < +2.0%            (not moving yet, no catalyst signal)
      DROP  : percent_change > +6.0%            (exhausted runner — move is extended)

    Each entry uses the format:
      TICKER | $PRICE | +X.XX% | $X.XB | X.XM vol | Short headline

    sector_focus can be 'all', 'semis', 'software', etc. — used as metadata only.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    results: list[dict] = []
    source_used = "unknown"

    # ── Step 1: pull live day-gainers (primary then fallback) ─────────────────
    raw_tickers: list[str] = []

    try:
        raw_tickers = _fetch_gainers_primary()
        source_used = "yahoo_fin/si.get_day_gainers"
    except Exception:
        pass  # primary failed — try fallback silently

    if not raw_tickers:
        try:
            raw_tickers = _fetch_gainers_fallback()
            source_used = "yahoo_finance_json_api"
        except Exception as exc:
            return json.dumps(
                {"error": f"All screener sources failed: {exc}", "condensed_scan": []}
            )

    # ── Step 2: enrich each ticker via yfinance ────────────────────────────────
    for ticker in raw_tickers:
        try:
            stock = yf.Ticker(ticker)
            hist  = stock.history(period="2d")

            if len(hist) < 2:
                continue

            prev_close = float(hist["Close"].iloc[-2])
            curr_price = float(hist["Close"].iloc[-1])
            pct_change = round((curr_price - prev_close) / prev_close * 100, 2)

            # "Next Play" window: drop flat stocks AND exhausted runners
            if pct_change < 2.0 or pct_change > 6.0:
                continue

            fast       = stock.fast_info
            market_cap = float(getattr(fast, "market_cap", 0) or 0)
            avg_volume = float(getattr(fast, "three_month_average_volume", 0) or 0)

            # Grab first news headline (same Ticker object — no extra HTTP call)
            headline = ""
            try:
                news_list = stock.news or []
                if news_list:
                    h = news_list[0]
                    raw_title = h.get("content", {}).get("title", h.get("title", ""))
                    headline  = raw_title[:65] + "…" if len(raw_title) > 65 else raw_title
            except Exception:
                pass

            results.append(
                {
                    "ticker":         ticker,
                    "percent_change": pct_change,
                    "current_price":  round(curr_price, 2),
                    "market_cap_b":   round(market_cap / 1e9, 1),
                    "avg_volume_m":   round(avg_volume / 1e6, 1),
                    "headline":       headline,
                }
            )
        except Exception:
            # One bad ticker must never stop the whole scan
            continue

    results.sort(key=lambda x: x["percent_change"], reverse=True)

    # ── Step 3: build condensed strings (token-efficient format) ──────────────
    condensed_scan = [
        (
            f"{r['ticker']} | ${r['current_price']:.2f} | {r['percent_change']:+.2f}% | "
            f"${r['market_cap_b']}B | {r['avg_volume_m']}M vol | {r['headline']}"
        )
        for r in results[:40]
    ]

    return json.dumps(
        {
            "date":              today,
            "sector_focus":      sector_focus,
            "source":            source_used,
            "tickers_scanned":   len(raw_tickers),
            "tickers_qualified": len(results),
            "condensed_scan":    condensed_scan,
            "sympathy_map":      _SYMPATHY_REF,
        }
    )


@tool("Fetch Earnings News for Tickers")
def fetch_earnings_news(tickers: str) -> str:
    """
    Fetches the 3 most recent news headlines for each ticker in the
    comma-separated list.  Returns JSON keyed by ticker with headline + publisher.
    Example input: 'NVDA,AMD,SMCI'
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",")][:20]
    news_data: dict[str, list] = {}

    for ticker in ticker_list:
        try:
            stock = yf.Ticker(ticker)
            raw_news = stock.news or []
            news_data[ticker] = [
                {
                    "title":     n.get("content", {}).get("title", n.get("title", "")),
                    "publisher": n.get("content", {}).get("provider", {}).get("displayName", n.get("publisher", "")),
                }
                for n in raw_news[:3]
            ]
        except Exception:
            news_data[ticker] = []

    return json.dumps(news_data)


@tool("Fetch Today's Earnings Calendar")
def fetch_earnings_calendar(date_str: str) -> str:
    """
    Deep Scan: fetches the FULL earnings calendar, programmatically pre-filters to
    mid-cap stocks ($300M–$10B, volume > 500K), and returns up to 40 candidates as
    compact condensed strings — keeping context lean while maximising coverage.

    Filter criteria (applied in Python — zero LLM cost):
      • Market Cap : $300M – $10B  (mid-caps with room to run; avoids mega-caps)
      • Avg Volume : > 500,000 shares  (liquidity floor)

    Each entry uses the format:
      TICKER | ±X.XX% | $X.XB | X.XM vol | SECTOR | BMO/AMC | EPS:$X.XX | peers:A,B

    Pass 'today', a YYYY-MM-DD date string, or leave blank for today.
    """
    from datetime import date as _date, timedelta as _td

    MCAP_MIN = 300e6    # $300 M
    MCAP_MAX = 10e9     # $10 B
    VOL_MIN  = 500_000

    # ── Resolve target date ───────────────────────────────────────────────────
    if not date_str or date_str.lower() in ("today", ""):
        target = _date.today()
    else:
        try:
            target = _date.fromisoformat(date_str.strip())
        except ValueError:
            target = _date.today()
    target_str = target.isoformat()

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 1 — Fetch full earnings list from Nasdaq API
    # ─────────────────────────────────────────────────────────────────────────
    raw:    list[dict] = []
    source: str        = "unknown"

    try:
        nasdaq_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept":  "application/json, text/plain, */*",
            "Origin":  "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        }
        url  = f"https://api.nasdaq.com/api/calendar/earnings?date={target_str}"
        resp = requests.get(url, headers=nasdaq_headers, timeout=12)
        resp.raise_for_status()

        rows = resp.json().get("data", {}).get("rows") or []
        for row in rows:
            sym = str(row.get("symbol", "")).strip().upper()
            if not sym:
                continue
            raw.append(
                {
                    "symbol":      sym,
                    "company":     row.get("name", "").strip(),
                    "report_time": row.get("time", ""),
                    "eps_est":     row.get("epsForecast", ""),
                    "mcap_str":    row.get("marketCap", ""),
                }
            )
        source = "nasdaq.com/api/calendar/earnings"

    except Exception:
        pass  # fall through to SYMPATHY_MAP fallback

    # ─────────────────────────────────────────────────────────────────────────
    #  Fallback: SYMPATHY_MAP parents via yfinance calendar
    # ─────────────────────────────────────────────────────────────────────────
    if not raw:
        window = {target - _td(days=1), target, target + _td(days=1)}
        for ticker in SYMPATHY_MAP:
            try:
                cal       = yf.Ticker(ticker).calendar
                raw_dates = cal.get("Earnings Date", []) if cal else []
                if isinstance(raw_dates, _date):
                    raw_dates = [raw_dates]
                if any(d in window for d in raw_dates):
                    raw.append(
                        {
                            "symbol":      ticker,
                            "company":     "",
                            "report_time": "time-not-supplied",
                            "eps_est":     str(cal.get("Earnings Average", "")),
                            "mcap_str":    "",
                        }
                    )
            except Exception:
                continue
        source = "yfinance/SYMPATHY_MAP-fallback"

    if not raw:
        return json.dumps(
            {
                "date": target_str, "source": source,
                "total_reporters": 0, "passed_filter": 0,
                "mid_cap_earnings": [],
                "note": (
                    "No earnings reporters found for this date. "
                    "Markets may be closed or the Nasdaq API returned no data. "
                    "Proceed with the gainers scan to find other catalysts."
                ),
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 2 — Cheap pre-filter using Nasdaq's market-cap string (zero API calls)
    #  Tickers with unparseable cap (0.0) are kept for yfinance validation.
    # ─────────────────────────────────────────────────────────────────────────
    candidates: list[dict] = []
    for item in raw:
        mcap = _parse_mcap_str(item["mcap_str"])
        if mcap == 0.0 or (MCAP_MIN <= mcap <= MCAP_MAX):
            candidates.append(item)

    # Safety cap: never hammer yfinance with more than 70 calls
    candidates = candidates[:70]

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 3 — yfinance enrichment + exact filter
    # ─────────────────────────────────────────────────────────────────────────
    enriched: list[dict] = []

    for item in candidates:
        ticker = item["symbol"]
        try:
            stock = yf.Ticker(ticker)
            fast  = stock.fast_info

            market_cap = float(getattr(fast, "market_cap",                       0) or 0)
            avg_volume = float(getattr(fast, "three_month_average_volume", 0) or 0)

            # Exact numeric filter
            if not (MCAP_MIN <= market_cap <= MCAP_MAX):
                continue
            if avg_volume < VOL_MIN:
                continue

            # 1-day price change
            hist       = stock.history(period="2d")
            pct_change = None
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
                curr_price = float(hist["Close"].iloc[-1])
                pct_change = round((curr_price - prev_close) / prev_close * 100, 2)

            # Sector — only fetched for survivors (acceptable extra call)
            sector = "Unknown"
            try:
                sector = stock.info.get("sector") or "Unknown"
            except Exception:
                pass

            # Human-readable report time
            rt = item["report_time"].lower()
            if "pre" in rt:
                report_label = "BMO"   # Before Market Open
            elif "after" in rt or "post" in rt:
                report_label = "AMC"   # After Market Close
            else:
                report_label = "TBD"

            enriched.append(
                {
                    "ticker":          ticker,
                    "company":         item["company"],
                    "report_time":     report_label,
                    "market_cap_b":    round(market_cap / 1e9, 2),
                    "avg_volume_m":    round(avg_volume / 1e6, 2),
                    "sector":          sector,
                    "pct_change_today": pct_change,
                    "eps_forecast":    item["eps_est"],
                    "sympathy_peers":  SYMPATHY_MAP.get(ticker, []),
                }
            )

        except Exception:
            continue

    # Sort by absolute price activity — most active reporters first
    enriched.sort(key=lambda x: abs(x.get("pct_change_today") or 0), reverse=True)

    # ── Build condensed strings (token-efficient format) ──────────────────────
    condensed_calendar: list[str] = []
    for e in enriched[:40]:
        chg_str   = f"{e['pct_change_today']:+.2f}%" if e["pct_change_today"] is not None else "N/A"
        peers_str = ",".join(e["sympathy_peers"])[:35] if e["sympathy_peers"] else "none"
        condensed_calendar.append(
            f"{e['ticker']} | {chg_str} | ${e['market_cap_b']}B | {e['avg_volume_m']}M vol | "
            f"{e['sector'][:12]} | {e['report_time']} | EPS:{e['eps_forecast'] or 'N/A'} | "
            f"peers:{peers_str}"
        )

    return json.dumps(
        {
            "date":               target_str,
            "source":             source,
            "total_reporters":    len(raw),
            "passed_filter":      len(enriched),
            "filter_criteria":    "Market Cap $300M–$10B, Avg Volume > 500K",
            "condensed_calendar": condensed_calendar,
            "note": (
                "Sorted by absolute price activity. "
                "BMO = Before Market Open (catalyst in play today). "
                "AMC = After Market Close (tomorrow's setup). "
                "peers = SYMPATHY_MAP peers that may move in sympathy."
            ),
        },
        default=str,
    )


@tool("Read Manager Feedback from Database")
def read_manager_feedback(keywords: str) -> str:
    """
    Searches the database for past Manager EOD feedback related to specific tickers,
    sectors, or catalysts. Input should be a comma-separated list of keywords
    (e.g., 'NVDA, semiconductor, earnings, sympathy'). The search matches keywords
    against the feedback text, ticker symbol, and pm_rationale columns, returning
    the most historically relevant lessons learned for today's setup.
    Returns JSON with date, ticker, confidence_score, actual_eod_change, and feedback text.
    """
    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]

    if keyword_list:
        feedback = db.search_feedback_by_keywords(keyword_list, limit=10)
    else:
        feedback = db.get_recent_manager_feedback(limit=5)

    if not feedback:
        return json.dumps(
            {
                "message": "No relevant historical feedback found. This may be the first run "
                           "or no past trades match these keywords.",
                "keywords_searched": keyword_list,
                "feedback": [],
            }
        )
    return json.dumps({"keywords_searched": keyword_list, "feedback": feedback})


@tool("Read Recent Picks Performance")
def read_recent_picks_performance(days_back: str) -> str:
    """
    Returns the agent's OWN recent stock picks together with their realised
    performance, so the morning Analyst can self-review BEFORE recommending
    today's picks. This is the self-improvement memory: every pick made in the
    last `days_back` days is shown with what actually happened — not just the
    ones the EOD Manager has already reviewed.

    For each pick the tool returns:
      date | ticker | strategy | conf | session | EOD | T+3 | T+7 | T+14 |
      T+30 | target | target_hit_date | rationale | manager_feedback

    It also returns a 'summary' object with:
      • total_picks, with_eod_data
      • n_hits_eod_>=6%, n_misses_eod_<=-3%
      • n_target_hit, n_target_missed_30d
      • avg_eod_change, avg_return_7d
      • top_winners / top_losers (up to 5 each)
      • confidence_calibration: avg return per confidence band — tells you
        whether your >=0.80 picks actually outperformed your <0.60 picks
      • instruction: how to apply these insights to today's picks

    Input:
      days_back : string-encoded int. Use '5' to inspect the last 5 trading
                  days, '14' (default) for two weeks, '30' for a month.
                  Clamped to [1, 60].
    """
    try:
        n = int((days_back or "14").strip())
    except (ValueError, AttributeError):
        n = 14
    n = max(1, min(60, n))

    rows = db.get_recent_predictions_performance(days_back=n, limit=200)
    if not rows:
        return json.dumps({
            "window": f"last {n} days",
            "total_picks": 0,
            "summary": (
                "No prior picks in this window — first run, or DB is empty. "
                "Proceed without historical bias and rely on Manager feedback "
                "and external signals only."
            ),
            "picks": [],
        })

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _fmt_pct(v):
        x = _f(v)
        return f"{x:+.1f}%" if x is not None else "—"

    picks: list[dict] = []
    for r in rows:
        rationale = (r.get("pm_rationale") or "").strip().replace("\n", " ")
        if len(rationale) > 110:
            rationale = rationale[:108] + "…"
        feedback = (r.get("manager_feedback") or "").strip().replace("\n", " ")
        if len(feedback) > 110:
            feedback = feedback[:108] + "…"
        target = _f(r.get("target_price"))
        conf = _f(r.get("confidence_score"))

        picks.append({
            "date":       r.get("date"),
            "ticker":     r.get("ticker"),
            "strategy":   r.get("strategy") or "alpha",
            "conf":       f"{conf:.2f}" if conf is not None else "—",
            "session":    _fmt_pct(r.get("return_session")),
            "eod":        _fmt_pct(r.get("actual_eod_change")),
            "T+3":        _fmt_pct(r.get("return_3d")),
            "T+7":        _fmt_pct(r.get("return_7d")),
            "T+14":       _fmt_pct(r.get("return_14d")),
            "T+30":       _fmt_pct(r.get("return_30d")),
            "target":     f"${target:.2f}" if target is not None else "—",
            "target_hit": r.get("target_hit_date") or "—",
            "rationale":  rationale,
            "feedback":   feedback or "(no EOD review yet)",
        })

    eod_vals = [v for v in (_f(r.get("actual_eod_change")) for r in rows) if v is not None]
    r7_vals  = [v for v in (_f(r.get("return_7d"))         for r in rows) if v is not None]

    n_hits_eod      = sum(1 for v in eod_vals if v >= 6.0)
    n_misses_eod    = sum(1 for v in eod_vals if v <= -3.0)
    n_target_hit    = sum(
        1 for r in rows
        if r.get("target_hit_date") and r.get("target_hit_date") not in ("MISSED", "")
    )
    n_target_missed = sum(1 for r in rows if r.get("target_hit_date") == "MISSED")

    avg_eod = round(sum(eod_vals) / len(eod_vals), 2) if eod_vals else None
    avg_r7  = round(sum(r7_vals)  / len(r7_vals),  2) if r7_vals  else None

    def _best_known(r):
        for k in ("return_7d", "return_3d", "actual_eod_change"):
            v = _f(r.get(k))
            if v is not None:
                return v
        return None

    scored = [(r, _best_known(r)) for r in rows]
    scored = [(r, v) for r, v in scored if v is not None]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_winners = [
        f"{r['date']} {r['ticker']} ({r.get('strategy', 'alpha')}): {v:+.1f}%"
        for r, v in scored[:5] if v > 0
    ]
    top_losers = [
        f"{r['date']} {r['ticker']} ({r.get('strategy', 'alpha')}): {v:+.1f}%"
        for r, v in reversed(scored[-5:]) if v < 0
    ]

    bands: dict[str, list[float]] = {
        "high (>=0.80)":   [],
        "med (0.60-0.79)": [],
        "low (<0.60)":     [],
    }
    for r, v in scored:
        c = _f(r.get("confidence_score"))
        if c is None:
            continue
        if c >= 0.80:
            bands["high (>=0.80)"].append(v)
        elif c >= 0.60:
            bands["med (0.60-0.79)"].append(v)
        else:
            bands["low (<0.60)"].append(v)
    calibration = {
        band: {
            "n":          len(vals),
            "avg_return": round(sum(vals) / len(vals), 2) if vals else None,
        }
        for band, vals in bands.items()
    }

    summary = {
        "window":               f"last {n} days",
        "total_picks":          len(rows),
        "with_eod_data":        len(eod_vals),
        "n_hits_eod_>=6%":      n_hits_eod,
        "n_misses_eod_<=-3%":   n_misses_eod,
        "n_target_hit":         n_target_hit,
        "n_target_missed_30d":  n_target_missed,
        "avg_eod_change":       avg_eod,
        "avg_return_7d":        avg_r7,
        "top_winners":          top_winners,
        "top_losers":           top_losers,
        "confidence_calibration": calibration,
        "instruction": (
            "Use this self-analysis to BIAS today's picks: "
            "(1) If a sector / catalyst type / sympathy chain shows up in "
            "    'top_winners' multiple times, look for analogous setups today "
            "    and start with a +0.05 confidence bonus. "
            "(2) If a pattern shows up in 'top_losers', either skip it or "
            "    require a much stronger thesis. "
            "(3) Compare the 'high' vs 'low' confidence bands — if your high-"
            "    confidence picks underperformed, tighten today's confidence "
            "    ceiling (do not exceed 0.78 unless evidence is overwhelming). "
            "(4) For each ticker you are about to recommend AGAIN, check if it "
            "    appears in 'picks' — if you already recommended it within the "
            "    last 5 days and it failed to move, demand a NEW catalyst "
            "    before re-listing. If it ALREADY hit its target, do not "
            "    re-recommend (chasing tops is a hard rule)."
        ),
    }

    return json.dumps({"summary": summary, "picks": picks}, default=str)


def _price_at_pick_from_metrics(metrics: dict | None) -> float | None:
    """Parse numeric price from the optional metrics dict (e.g. '$4.20' or 4.2)."""
    if not metrics:
        return None
    raw = metrics.get("Price")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).replace(",", "")
    m = re.search(r"[-+]?\d*\.?\d+", s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _fetch_last_price(ticker: str) -> float | None:
    """Last regular close/print from a short yfinance history (at save time)."""
    try:
        h = yf.Ticker(ticker).history(period="5d")
        if h.empty:
            return None
        return float(h["Close"].iloc[-1])
    except Exception:
        return None


@tool("Save Alpha Predictions to Database")
def save_predictions_to_db(predictions_json: str) -> str:
    """
    Persists the Analyst's top stock predictions to the alpha_predictions table.
    predictions_json must be a JSON array where each object contains:
      - ticker         (string)  : stock symbol
      - pm_rationale   (string)  : PM agent's approval rationale
      - confidence_score (float) : probability 0.0 – 1.0
      - target_price   (float, optional) : analyst's sell target in USD.
                       Must be strictly above pick price and at most 5x pick.
                       Out-of-range or non-numeric values are silently dropped.
    Returns a confirmation string.
    """
    try:
        data = json.loads(predictions_json)
        preds: list[dict] = data if isinstance(data, list) else data.get("predictions", [])
        today = datetime.now().strftime("%Y-%m-%d")

        # ── Deduplicate within this batch ───────────────────────────────────────
        # Group by (UPPER ticker, strategy). If the same name shows up multiple
        # times, merge rationales as bullet points, keep the MAX confidence,
        # take the MAX target_price (most ambitious sell target wins), and
        # shallow-merge every metrics key (later picks fill missing keys).
        merged: dict[tuple[str, str], dict] = {}
        for p in preds:
            tkr = str(p.get("ticker", "")).strip().upper()
            if not tkr:
                continue
            strat = str(p.get("strategy", "alpha")).strip().lower() or "alpha"
            key = (tkr, strat)

            raw_metrics = p.get("metrics")
            metrics_dict = raw_metrics if isinstance(raw_metrics, dict) else None
            try:
                conf = float(p.get("confidence_score", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            rationale = str(p.get("pm_rationale", "") or "").strip()

            # Sell target (analyst's "I'd sell here" price) — optional, must be > 0.
            target_price: float | None = None
            tp_raw = p.get("target_price")
            if tp_raw is not None:
                try:
                    tp_val = float(tp_raw)
                    if tp_val > 0:
                        target_price = tp_val
                except (TypeError, ValueError):
                    target_price = None

            cur = merged.get(key)
            if cur is None:
                merged[key] = {
                    "ticker": tkr,
                    "strategy": strat,
                    "confidence_score": conf,
                    "rationales": [rationale] if rationale else [],
                    "metrics": dict(metrics_dict) if metrics_dict else {},
                    "target_price": target_price,
                }
            else:
                cur["confidence_score"] = max(cur["confidence_score"], conf)
                if rationale and rationale not in cur["rationales"]:
                    cur["rationales"].append(rationale)
                if metrics_dict:
                    for mk, mv in metrics_dict.items():
                        if mk not in cur["metrics"] or cur["metrics"][mk] in (None, "", []):
                            cur["metrics"][mk] = mv
                if target_price is not None:
                    cur["target_price"] = (
                        target_price if cur["target_price"] is None
                        else max(cur["target_price"], target_price)
                    )

        # ── Merge against any rows already in DB for the same day ────────────
        # Prevents inserting a second row when an earlier batch (or a re-run)
        # already wrote this ticker today. We mutate the existing row instead
        # of inserting another, so the dashboard shows a single consolidated
        # entry that retains every historical data point (returns, manager
        # feedback, EOD, session, metrics).
        existing_today: dict[tuple[str, str], dict] = {}
        try:
            _conn = db.get_connection()
            _cur = _conn.execute(
                """
                SELECT id, ticker, strategy, pm_rationale, confidence_score, metrics,
                       target_price
                FROM alpha_predictions
                WHERE date = ?
                """,
                (today,),
            )
            for r in _cur.fetchall():
                row = dict(r)
                t = (row.get("ticker") or "").strip().upper()
                s = (row.get("strategy") or "alpha").strip().lower() or "alpha"
                if t:
                    existing_today.setdefault((t, s), row)
            _conn.close()
        except Exception:
            existing_today = {}

        saved: list[str] = []
        for (_tkr, _strat), m in merged.items():
            rats = m["rationales"]
            if len(rats) > 1:
                pm_rationale = "Multi-reason pick:\n- " + "\n- ".join(rats)
            elif rats:
                pm_rationale = rats[0]
            else:
                pm_rationale = ""

            metrics_dict = m["metrics"] or None
            price_at = _price_at_pick_from_metrics(metrics_dict)
            if price_at is None:
                price_at = _fetch_last_price(m["ticker"])

            target_price = m.get("target_price")
            # Reject obviously-broken targets (must sit strictly above pick price
            # and at most 5x — anything else is a hallucination, not a target).
            if target_price is not None and price_at is not None:
                try:
                    pp = float(price_at)
                    if not (pp > 0 and pp < target_price <= pp * 5.0):
                        target_price = None
                except (TypeError, ValueError):
                    target_price = None

            # If a row already exists for (ticker, strategy, today), update it
            # in-place and merge metrics + rationale + max confidence — never
            # create a duplicate that the user will see on the dashboard.
            existing = existing_today.get((m["ticker"], m["strategy"]))
            if existing is not None:
                existing_metrics = {}
                blob = existing.get("metrics")
                if blob:
                    try:
                        parsed = json.loads(blob) if isinstance(blob, str) else blob
                        if isinstance(parsed, dict):
                            existing_metrics = parsed
                    except Exception:
                        existing_metrics = {}
                if metrics_dict:
                    for mk, mv in metrics_dict.items():
                        if mk not in existing_metrics or existing_metrics[mk] in (None, "", []):
                            existing_metrics[mk] = mv

                existing_rationale = (existing.get("pm_rationale") or "").strip()
                if existing_rationale and pm_rationale and existing_rationale != pm_rationale:
                    if existing_rationale not in pm_rationale:
                        pm_rationale = (
                            "Multi-reason pick:\n- "
                            + "\n- ".join([existing_rationale, *rats])
                        )
                elif existing_rationale and not pm_rationale:
                    pm_rationale = existing_rationale

                merged_conf = max(
                    float(existing.get("confidence_score") or 0.0),
                    float(m["confidence_score"]),
                )

                # Existing target wins unless the new one is more ambitious.
                existing_target = existing.get("target_price")
                if existing_target is None:
                    final_target = target_price
                elif target_price is None:
                    final_target = existing_target
                else:
                    final_target = max(float(existing_target), float(target_price))

                conn = db.get_connection()
                conn.execute(
                    """
                    UPDATE alpha_predictions
                    SET pm_rationale     = ?,
                        confidence_score = ?,
                        metrics          = ?,
                        price_at_pick    = COALESCE(price_at_pick, ?),
                        target_price     = ?
                    WHERE id = ?
                    """,
                    (
                        pm_rationale,
                        merged_conf,
                        json.dumps(existing_metrics) if existing_metrics else None,
                        price_at,
                        final_target,
                        existing["id"],
                    ),
                )
                conn.commit()
                conn.close()
            else:
                db.insert_prediction(
                    date=today,
                    ticker=m["ticker"],
                    pm_rationale=pm_rationale,
                    confidence_score=m["confidence_score"],
                    strategy=m["strategy"],
                    metrics_dict=metrics_dict,
                    price_at_pick=price_at,
                    target_price=target_price,
                )
            saved.append(m["ticker"])
        return f"Saved {len(saved)} unique predictions for {today}: {', '.join(saved)}"
    except Exception as exc:
        return f"ERROR saving predictions: {exc}"


@tool("Save Market Movers to Database")
def save_market_movers_to_db(movers_json: str) -> str:
    """
    Persists today's actual market movers to the actual_market_movers table.
    movers_json must be a JSON array where each object contains:
      - ticker          (string) : stock symbol
      - percent_change  (float)  : actual % move
      - catalyst_reason (string) : brief explanation of the catalyst
    Returns a confirmation string.
    """
    try:
        data = json.loads(movers_json)
        movers: list[dict] = data if isinstance(data, list) else data.get("movers", [])
        today = datetime.now().strftime("%Y-%m-%d")
        for m in movers:
            m.setdefault("date", today)
        db.insert_market_movers(movers)
        return f"Saved {len(movers)} market movers to the database."
    except Exception as exc:
        return f"ERROR saving market movers: {exc}"


@tool("Fetch EOD Prices and Calculate Returns")
def fetch_eod_prices(date_str: str) -> str:
    """
    Retrieves today's end-of-day closing price for each predicted ticker,
    calculates the actual % change vs. the prior close, and returns a JSON
    summary so the Manager can write its feedback.
    date_str format: YYYY-MM-DD (e.g., '2026-04-04')
    """
    try:
        predictions = db.get_predictions_for_date(date_str)
        if not predictions:
            return json.dumps({"error": f"No predictions found for {date_str}."})

        results: list[dict] = []
        for pred in predictions:
            ticker = pred["ticker"]
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="2d")
                if len(hist) < 2:
                    actual_change = 0.0
                else:
                    prev_close = float(hist["Close"].iloc[-2])
                    eod_close   = float(hist["Close"].iloc[-1])
                    actual_change = round((eod_close - prev_close) / prev_close * 100, 2)
            except Exception:
                actual_change = 0.0

            results.append(
                {
                    "id":                  pred["id"],
                    "ticker":              ticker,
                    "predicted_confidence": pred["confidence_score"],
                    "actual_eod_change":   actual_change,
                    "hit_target":          actual_change >= 6.0,
                }
            )

        return json.dumps({"date": date_str, "eod_results": results})
    except Exception as exc:
        return f"ERROR fetching EOD data: {exc}"


@tool("Write Manager Feedback to Database")
def write_manager_feedback(feedback_json: str) -> str:
    """
    Persists the Manager's EOD feedback for each prediction to the database.
    feedback_json must be a JSON array where each object contains:
      - id                (int)    : the prediction's database id
      - actual_eod_change (float)  : the real % change at market close
      - manager_feedback  (string) : detailed lessons-learned narrative
    Returns a confirmation string listing which IDs were updated and which were not found.
    IMPORTANT: Only IDs that actually exist in the database will be updated.
    If an ID is not found, it is reported as an error — never silently ignored.
    """
    try:
        data = json.loads(feedback_json)
        feedbacks: list[dict] = data if isinstance(data, list) else data.get("feedbacks", [])

        updated: list[int] = []
        not_found: list[int] = []

        conn = db.get_connection()
        cur  = conn.cursor()

        for f in feedbacks:
            pred_id = int(f["id"])
            # Verify the ID actually exists before updating
            cur.execute("SELECT id FROM alpha_predictions WHERE id = ?", (pred_id,))
            if cur.fetchone() is None:
                not_found.append(pred_id)
                continue
            cur.execute(
                "UPDATE alpha_predictions SET actual_eod_change = ?, manager_feedback = ? WHERE id = ?",
                (float(f.get("actual_eod_change", 0.0)), str(f.get("manager_feedback", "")), pred_id),
            )
            updated.append(pred_id)

        conn.commit()
        conn.close()

        msg = f"Updated {len(updated)} predictions (IDs: {updated})."
        if not_found:
            msg += (
                f" WARNING: {len(not_found)} IDs not found in DB and were skipped: {not_found}. "
                "These are likely hallucinated IDs — check that you used the exact IDs "
                "returned by 'Fetch EOD Prices and Calculate Returns'."
            )
        return msg

    except Exception as exc:
        return f"ERROR writing feedback: {exc}"


# ═══════════════════════════════════════════════════════════════════════════════
#  AGENTS
# ═══════════════════════════════════════════════════════════════════════════════

data_agent = Agent(
    role="Pre-Market Data Intelligence Specialist",
    goal=(
        "Your priority is to identify mid-cap companies ($300M–$10B market cap) reporting "
        "earnings today that show unusual pre-market activity or belong to a hot sector. "
        "Combine the earnings calendar with the live gainers scan to surface 'under the radar' "
        "setups — stocks with real catalysts, sufficient liquidity, and room to run. "
        "Deliver clean, structured market intelligence for PM review."
    ),
    backstory=(
        "You are 'The Eyes' — a quantitative data specialist who scours pre-market activity "
        "before the opening bell every single day. You have deep expertise in reading price-action "
        "signals, spotting volume anomalies, and correlating news catalysts with price momentum. "
        "You rely on yfinance for real-time data and surface the top movers with full context "
        "so the PM can apply rigorous institutional filters."
    ),
    tools=[fetch_earnings_calendar, fetch_premarket_gainers, fetch_earnings_news],
    llm=claude_llm,
    verbose=True,
    allow_delegation=False,
)

pm_agent = Agent(
    role="Fintech Risk Manager & Portfolio Supervisor",
    goal=(
        "Filter the DataAgent's raw mover list to only pass institutionally-valid candidates: "
        "Market Cap > $2B, average daily volume > 1M shares, price > $5 (no penny stocks), "
        "and catalyst must be earnings/upgrade/macro — zero tolerance for meme-stock hype."
    ),
    backstory=(
        "You are 'The Supervisor' — a seasoned Fintech PM and risk manager who spent 15 years "
        "at Two Sigma and Citadel. You are obsessive about risk guardrails and institutional-grade "
        "filtering. You reject anything that smells like Reddit hype, pump-and-dump, or lacks "
        "sufficient liquidity for meaningful institutional participation. Your filters protect "
        "capital and prevent the system from chasing low-quality setups."
    ),
    tools=[],
    llm=claude_llm,
    verbose=True,
    allow_delegation=False,
)

analyst_agent = Agent(
    role="Lead Quantitative Strategist",
    goal=(
        "You are now in Deep Scan mode. You will receive a list of up to 40 PM-approved "
        "candidates. Your workflow: (1) Rapid Initial Filter — mentally score all 40 and cut "
        "to the top 8 based on catalyst/data alignment. (2) High-conviction deep analysis on "
        "those top 8. (3) Final output: 3–5 picks in JSON. Apply Manager lessons throughout."
    ),
    backstory=(
        "You are a ruthless, data-driven hedge fund analyst operating in Deep Scan mode. "
        "You receive large candidate lists and are trained to triage them instantly. "
        "Before EVERY morning's run you first review your OWN recent track record — "
        "every pick from the last two weeks, with realised session/EOD/T+3/T+7/T+14/"
        "T+30 returns and target-hit dates. You treat your own performance as the "
        "single most reliable feedback loop: patterns that just won 3 days ago get a "
        "small confidence bonus, patterns that just lost get a penalty, and tickers "
        "you already recommended in the last 5 days are NEVER chased without a fresh "
        "catalyst (re-listing a stock that already hit your target = chasing the top, "
        "which is forbidden). "
        "Stage 1 — Rapid Triage (40→8): In under 30 seconds of mental processing, eliminate "
        "anything with a weak or indirect catalyst, marginal volume, or a pattern flagged as "
        "unreliable by the Manager OR by your own recent self-review. You despise fluff "
        "and generic summaries. "
        "Stage 2 — High-Conviction Analysis (8→3-5): For your shortlist, you speak exclusively "
        "in numbers, catalysts, and risk-reward probabilities. You always quote specific market "
        "caps, volume figures, and news headlines. Your outputs read like terse Bloomberg "
        "terminal flash notes. "
        "You NEVER recommend chasing a stock that has already jumped double digits. You specialize "
        "in finding 'Sympathy Laggards' (Stock A reported great earnings and jumped 15%; you find "
        "its competitor Stock B, which is only up 3%, and pitch it as the catch-up trade) or "
        "'Early Momentum' (stocks up 3-5% on massive relative volume indicating institutional "
        "accumulation before a major breakout)."
    ),
    tools=[read_recent_picks_performance, read_manager_feedback],
    llm=claude_llm,
    verbose=True,
    allow_delegation=False,
)

reporter_agent = Agent(
    role="Alpha Report Generator & Database Writer",
    goal=(
        "Format the Analyst's 3 final picks into clean, structured, database-ready records "
        "and persist them — along with today's top market movers — to the SQLite database."
    ),
    backstory=(
        "You are 'The Mouth' — a precision-focused reporting agent who converts raw analytical "
        "output into perfectly structured database records. You never lose data, always validate "
        "JSON formatting, and ensure every prediction is logged with full rationale and confidence "
        "scores so the Manager can run its EOD evaluation cleanly."
    ),
    tools=[save_predictions_to_db, save_market_movers_to_db],
    llm=claude_llm,
    verbose=True,
    allow_delegation=False,
)

manager_agent = Agent(
    role="Post-Market Quant Evaluator",
    goal=(
        "After market close, fetch actual EOD prices for today's predictions, calculate real "
        "returns, and write precise lessons-learned feedback to the database — the self-improvement "
        "engine that makes The Brain smarter every morning."
    ),
    backstory=(
        "You are the system's ruthless performance accountability officer. You do not offer comfort "
        "for bad trades. You analyze End-of-Day (EOD) data mathematically. If a trade failed, you "
        "pinpoint the exact quantitative or macro reason. Your feedback must be terse, data-heavy, "
        "and structured as a strict lesson for the Analyst."
    ),
    tools=[fetch_eod_prices, write_manager_feedback],
    llm=claude_llm,
    verbose=True,
    allow_delegation=False,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  TASKS
# ═══════════════════════════════════════════════════════════════════════════════

def _create_morning_tasks() -> list[Task]:
    today = datetime.now().strftime("%Y-%m-%d")

    data_task = Task(
        description=(
            f"Today is {today}. Deep Scan mode: build a combined candidate list of up to 40 "
            "unique tickers for the PM by running three tools in sequence.\n\n"
            "STEP 0 — Mid-Cap Earnings Calendar:\n"
            "  Call 'Fetch Today's Earnings Calendar' with date_str='today'.\n"
            "  The tool returns pre-filtered, condensed strings (one line per ticker).\n"
            "  Each line: TICKER | ±%chg | $McapB | VolM | Sector | BMO/AMC | EPS | peers\n"
            "  Note which tickers are BMO (live catalyst today) vs AMC (tomorrow's setup).\n\n"
            "STEP 1 — Gainers Scan:\n"
            "  Call 'Fetch Pre-Market Top Gainers' with sector_focus='all'.\n"
            "  The tool returns condensed strings (one line per ticker).\n"
            "  Each line: TICKER | $Price | +%chg | $McapB | VolM | Headline\n"
            "  The tool also provides sympathy_map — use it to flag any gainer that is a\n"
            "  sympathy peer of a BMO earnings reporter from STEP 0.\n\n"
            "STEP 2 — Targeted News Enrichment:\n"
            "  Merge and deduplicate the STEP 0 and STEP 1 candidate lists.\n"
            "  Identify the top 20 most interesting tickers (highest price activity + "
            "  strongest catalyst signal) as a comma-separated string.\n"
            "  Call 'Fetch Earnings News for Tickers' ONCE with those 20 tickers.\n"
            "  Do NOT call it for all 40 — headline data for 3 headlines × 20 tickers is\n"
            "  sufficient to inform the PM without exhausting the context window.\n\n"
            "STEP 3 — Compile Combined Report (target: ~40 unique candidates):\n"
            "  Produce a structured report with two sections:\n"
            "  SECTION A — Earnings Movers: all mid-cap earners from STEP 0 (condensed format).\n"
            "  SECTION B — Momentum Movers: all gainers from STEP 1 (condensed format), with a\n"
            "  flag if a ticker overlaps SECTION A or is a sympathy peer of a BMO reporter.\n"
            "  Append news headlines (from STEP 2) next to each ticker where available.\n"
            "  The total unique tickers across both sections should approach 40."
        ),
        expected_output=(
            "A structured pre-market intelligence report:\n"
            "SECTION A — Mid-Cap Earnings Catalysts (up to 40 lines): "
            "condensed one-line entries for mid-cap stocks reporting today, "
            "with BMO/AMC flag, sector, EPS, sympathy peers, and news headline where available.\n"
            "SECTION B — Momentum Gainers (up to 40 lines): "
            "condensed one-line entries for stocks in the +2%–+6% Next Play window, "
            "with overlap flags for earnings reporters and their sympathy peers.\n"
            "Total unique candidates passed to the PM should be ~40."
        ),
        agent=data_agent,
    )

    pm_task = Task(
        description=(
            "Deep Scan mode: the DataAgent has surfaced up to 40 candidates. "
            "You MUST evaluate EVERY candidate — do not skip any. "
            "Apply institutional risk filters to each ticker:\n\n"
            "  ✅ APPROVE if ALL pass:\n"
            "    • Market Cap  > $2,000,000,000 (two billion USD)\n"
            "    • Avg Volume  > 1,000,000 shares per day\n"
            "    • Price       > $5.00  (eliminates penny stocks)\n"
            "    • Catalyst    = earnings beat | analyst upgrade | macro tailwind | "
            "      clear sympathy from large-cap earnings\n\n"
            "  ❌ REJECT if ANY fail:\n"
            "    • Market Cap below threshold (micro-cap risk)\n"
            "    • Volume below threshold (illiquid, no institutional participation)\n"
            "    • Social-media-driven hype with no fundamental catalyst\n"
            "    • Biotech binary event / penny-stock pump\n"
            "    • Extension Limit: MUST REJECT any stock already up > 6.0% from yesterday's "
            "      close. We do not buy at the top.\n\n"
            "Process the FULL list before outputting. The Analyst needs a rich APPROVED list "
            "to run its own 40→8→3 deep-scan triage — so be thorough, not stingy with approvals."
        ),
        expected_output=(
            "Two sections:\n"
            "APPROVED (up to 12 stocks): one line per ticker — ticker | market_cap | "
            "avg_volume | catalyst_type | one-sentence approval rationale.\n"
            "REJECTED: ticker + specific guardrail violated (one line each)."
        ),
        agent=pm_agent,
        context=[data_task],
    )

    analyst_task = Task(
        description=(
            f"Today is {today}. You are The Brain operating in Deep Scan mode.\n"
            "The PM has handed you a list of up to 40 approved candidates.\n"
            "Follow this exact workflow — STEP 0 is MANDATORY before anything else:\n\n"
            "STEP 0 — Self-Review (your own recent track record):\n"
            "  Call 'Read Recent Picks Performance' with days_back='14'.\n"
            "  This returns EVERY pick you made in the last 14 days, with their\n"
            "  realised performance: session, EOD, T+3, T+7, T+14, T+30, target_hit_date,\n"
            "  plus a self-analysis summary (hit rate, avg return, top winners, top\n"
            "  losers, confidence calibration per band).\n\n"
            "  You MUST extract the following before proceeding:\n"
            "  (a) WINNING PATTERNS — list 1–3 catalyst types / sectors / sympathy chains\n"
            "      that produced your top winners in the last 5–14 days.\n"
            "  (b) LOSING PATTERNS — list 1–3 patterns that consistently misfired.\n"
            "  (c) CONFIDENCE CALIBRATION VERDICT — compare 'high (>=0.80)' vs\n"
            "      'low (<0.60)' avg returns:\n"
            "      • If high-band UNDERPERFORMED low-band → today's confidence ceiling\n"
            "        is 0.78. Do NOT exceed it without overwhelming evidence.\n"
            "      • If high-band beat low-band by >3pp → calibration is healthy,\n"
            "        proceed normally.\n"
            "  (d) RE-PICK GUARD — for every PM-approved ticker, check if it appears in\n"
            "      the last 5 days of your picks. If yes:\n"
            "        • If it already HIT its target → REJECT. Do not chase a runner.\n"
            "        • If its T+3/T+7 is positive but no fresh catalyst → REJECT.\n"
            "        • If it FAILED (return_7d <= -3%) → only re-pick with a NEW,\n"
            "          materially different catalyst, and apply -0.10 confidence penalty.\n"
            "  Output a brief 'Self-Review Findings' block (4 short bullets) before\n"
            "  moving on. These findings drive every later step.\n\n"
            "STEP 1 — Query Long-Term Memory (Manager feedback):\n"
            "  Call 'Read Manager Feedback from Database' with a comma-separated string of "
            "  keywords drawn from the PM's APPROVED tickers, their sectors (e.g. "
            "  'semiconductor', 'software', 'EV'), and catalyst types (e.g. 'earnings', "
            "  'sympathy', 'macro', 'upgrade'). Study every returned feedback row for Hard "
            "  Rules, recurring failures, and reliable sympathy pairs.\n"
            "  Combine these older Hard Rules with the FRESH self-review findings from\n"
            "  STEP 0 — recent self-review wins on conflict (it reflects current regime).\n\n"
            "STEP 2 — Deduplication & Catalyst Merge (MANDATORY):\n"
            "  Before any triage, scan the full candidate list for duplicate tickers.\n"
            "  A ticker is a duplicate if it appears in BOTH the Earnings Calendar section\n"
            "  AND the Pre-market Gainers section of the DataAgent's report.\n"
            "  For every duplicate:\n"
            "    a) MERGE into a single entry — do NOT keep two separate rows.\n"
            "    b) COMBINE the catalysts in the merged rationale. Example: if CRDO is up\n"
            "       +4.2% in the gainers scan AND has an earnings report today, the rationale\n"
            "       must mention BOTH: 'Pre-market momentum +4.2% on earnings day — dual\n"
            "       catalyst: price action confirms earnings beat expectation.'\n"
            "    c) Use the HIGHER of the two confidence signals as the starting score.\n"
            "  After merging, NEVER output the same ticker more than once in any output.\n\n"
            "STEP 3 — Rapid Initial Filter (deduplicated list → 8):\n"
            "  Mentally triage ALL candidates (post-merge). Instantly eliminate any that have:\n"
            "  • A weak, indirect, or narrative-only catalyst (no hard data backing).\n"
            "  • Marginal volume relative to float (low institutional participation signal).\n"
            "  • A sector or pattern explicitly flagged as unreliable in Manager feedback.\n"
            "  • No clear entry thesis (e.g., 'just moving with the market').\n"
            "  Output a brief triage table: ticker | keep/cut | one-line reason.\n\n"
            "STEP 4 — High-Conviction Scoring (8 candidates):\n"
            "  For each of your 8 survivors, score on four dimensions (1–10):\n"
            "  • Catalyst Strength  : how powerful and credible is the catalyst?\n"
            "  • Sympathy Strength  : is this a direct beneficiary in the SYMPATHY_MAP?\n"
            "  • Technical Setup    : is the price action constructive (gap-up, volume surge)?\n"
            "  • Liquidity Score    : does volume support institutional participation?\n"
            "  Average the four scores, divide by 10 → raw confidence_score (0.0–1.0).\n"
            "  Then apply adjustments IN THIS ORDER:\n"
            "    1. Self-Review bonus/penalty (from STEP 0 findings):\n"
            "         + up to 0.05 if the setup matches a recent WINNING pattern.\n"
            "         − up to 0.10 if it matches a recent LOSING pattern.\n"
            "         − 0.10 if RE-PICK GUARD flagged it as a recent failed re-pick.\n"
            "    2. Manager Hard Rules (STEP 1): ± up to 0.10 per rule.\n"
            "    3. CAP at the calibration ceiling from STEP 0 (typically 0.78 if the\n"
            "       high band underperformed; otherwise no extra cap).\n\n"
            "STEP 5 — Final Deduplication Check + Output:\n"
            "  Before writing the JSON, do a final ticker uniqueness scan on your shortlist.\n"
            "  If the same ticker appears more than once (e.g., inherited from two candidate\n"
            "  lists that were not fully merged in STEP 2), keep the entry with the HIGHER\n"
            "  confidence_score and discard the other. NEVER output the same ticker twice.\n"
            "  Then output EXACTLY this JSON (3–5 items, all unique tickers):\n"
            '  [{"ticker": "XXXX", "pm_rationale": "Flash note...", '
            '"confidence_score": 0.XX, "target_price": 12.34}, ...]\n\n'
            "RATIONALE RULES (CRITICAL — applies to every pick):\n"
            "FORBIDDEN WORDS: 'attractive', 'solid', 'robust', 'good', 'potential'.\n"
            "MANDATORY FORMAT for pm_rationale:\n"
            "  - Catalyst: [Specific news event or data point — if dual catalyst, list both].\n"
            "  - Data: [Quote exact pre-market % change, Market Cap, and Volume].\n"
            "  - Context: [Why it moves today / Sympathy setup / Manager Lesson applied].\n"
            "EXAMPLE (dual catalyst): 'Catalyst: Earnings day (BMO) + pre-market +4.2% surge. "
            "Data: Up 4.2% pre-market on 5.1M volume ($2.3B MktCap). "
            "Setup: Price action pre-confirms beat; dual catalyst adds conviction. "
            "Manager Note: Applied +0.05 bonus — earnings+momentum combo has 82% hit rate.'\n\n"
            "TARGET_PRICE RULES (CRITICAL — every pick MUST have one):\n"
            "  - target_price is an absolute USD price where you would sell.\n"
            "  - It MUST be strictly above the current/pre-market price visible in the metrics.\n"
            "  - It MUST be at most 5x the current price (anything higher is rejected as a hallucination).\n"
            "  - Anchor it to evidence: prior swing high, gap-fill level, IV-implied move, "
            "    or 1.5–2x the implied daily volatility for swing trades. Typical range is "
            "    +4% to +25% above pick depending on conviction and float.\n"
            "  - Higher conviction (>0.80) → more ambitious target; lower conviction → tighter.\n"
            "  - Round to 2 decimals."
        ),
        expected_output=(
            "Part 0 — Self-Review Findings (4 short bullets): "
            "winning patterns (recent), losing patterns (recent), confidence-calibration "
            "verdict + today's confidence ceiling, and any tickers blocked by the "
            "RE-PICK GUARD.\n"
            "Part 1 — Dedup Log: list any tickers found in both sections, "
            "showing how their catalysts were merged into one entry.\n"
            "Part 2 — Triage Table: all candidates with keep/cut decisions and one-line reasons.\n"
            "Part 3 — Final JSON array of 3–5 picks (UNIQUE tickers only), each with keys: "
            "ticker (string), pm_rationale (terse flash note, Catalyst/Data/Context format, "
            "dual catalysts noted where applicable — and explicitly cite the Self-Review "
            "pattern that justified the bonus or the lesson that capped the score), "
            "confidence_score (float, 2 decimal places, 0.0–1.0, never above the calibration "
            "ceiling derived in Part 0), "
            "target_price (float, USD with 2 decimals — strictly above pick price, "
            "at most 5x pick price). "
            "Output the raw JSON array last so the Reporter can parse it directly."
        ),
        agent=analyst_agent,
        context=[pm_task],
    )

    reporter_task = Task(
        description=(
            "Execute the final morning save operations:\n\n"
            "OPERATION 0 — Sanity Check (run FIRST, before any DB writes):\n"
            "  Parse the Analyst's final JSON array. Build a set of tickers seen so far.\n"
            "  For each entry in order:\n"
            "    • If the ticker has NOT been seen yet → keep it, add to the seen set.\n"
            "    • If the ticker HAS been seen already → this is a duplicate. Compare the\n"
            "      two confidence_scores and keep the HIGHER one; discard the lower.\n"
            "  The result must be a list of UNIQUE tickers only.\n"
            "  If any duplicates were found and removed, note them in the briefing.\n\n"
            "OPERATION 1 — Save predictions:\n"
            "  Call 'Save Alpha Predictions to Database' with the deduplicated JSON array.\n\n"
            "OPERATION 2 — Save market movers:\n"
            "  From the DataAgent's pre-market data, construct a JSON array of the top 10 "
            "  movers in this format:\n"
            '  [{"ticker":"X","percent_change":N.N,"catalyst_reason":"..."},  ...]\n'
            "  Ensure no duplicate tickers in this array either.\n"
            "  Call 'Save Market Movers to Database' with this array.\n\n"
            "OPERATION 3 — Print morning briefing:\n"
            "  Output a clean, formatted morning briefing showing today's unique alpha picks\n"
            "  with ticker, confidence score, and 1-sentence rationale each.\n"
            "  If any duplicates were removed in OPERATION 0, append a line:\n"
            "  'Dedup: removed [TICKER] (lower confidence copy discarded).'"
        ),
        expected_output=(
            "Sanity check result (duplicates found/removed or 'all unique'), "
            "confirmation that both DB save operations succeeded, "
            "and a clean morning briefing of today's unique 3–5 alpha picks."
        ),
        agent=reporter_agent,
        context=[data_task, analyst_task],
    )

    return [data_task, pm_task, analyst_task, reporter_task]


def _create_eod_tasks() -> list[Task]:
    today = datetime.now().strftime("%Y-%m-%d")

    eod_task = Task(
        description=(
            f"Markets have closed for {today}. Run the full EOD performance review:\n\n"
            "⚠️  CRITICAL HALT RULE — read before doing anything else:\n"
            "  If STEP 1 returns a JSON object containing an 'error' key (e.g.\n"
            "  {\"error\": \"No predictions found for ...\"}), you MUST immediately\n"
            "  stop. Output ONLY: 'EOD HALT: <the exact error message from the tool>'.\n"
            "  Do NOT proceed to STEP 2 or STEP 3. Do NOT invent tickers, IDs,\n"
            "  feedback, or any data. Writing fake data to the database is a\n"
            "  critical compliance violation.\n\n"
            f"STEP 1 — Fetch actuals:\n"
            f"  Call 'Fetch EOD Prices and Calculate Returns' with date_str='{today}'.\n"
            f"  The tool returns the real prediction IDs, tickers, and actual % changes\n"
            f"  from the database. Check the response — if it has an 'error' key, HALT.\n\n"
            "STEP 2 — Analyze each result (only if STEP 1 succeeded):\n"
            "  For each prediction in the tool's response, determine:\n"
            "  • HIT (actual_eod_change ≥ 6%) — what worked in the thesis?\n"
            "  • NEAR MISS (0% ≤ actual_eod_change < 6%) — why did it underperform?\n"
            "  • MISS / NEGATIVE — what was wrong with the setup or catalyst?\n"
            "  Use ONLY the ids and tickers returned by the tool — never substitute your own.\n\n"
            "STEP 3 — Write feedback (only if STEP 1 succeeded):\n"
            "  Build a JSON array using the EXACT ids from STEP 1 and call\n"
            "  'Write Manager Feedback to Database':\n"
            '  [{"id": <exact int from tool>, "actual_eod_change": <exact float from tool>,\n'
            '    "manager_feedback": "<narrative>"}, ...]\n\n'
            "  The `manager_feedback` MUST follow this template:\n"
            "  - Outcome: [HIT / MISS / NEAR MISS] (X.XX%)\n"
            "  - Post-Mortem: [Terse, 1-sentence data-backed reason for the price action]\n"
            "  - Hard Rule: [One strict, actionable directive for the Analyst to use tomorrow]\n"
            "  EXAMPLE: 'Outcome: MISS (-2.1%). "
            "Post-Mortem: Initial earnings gap-up faded due to sector-wide semiconductor selloff at 11 AM. "
            "Hard Rule: Do not trust sympathy gap-ups on days where the QQQ is down >1% in pre-market.'"
        ),
        expected_output=(
            "If STEP 1 returned an error: output only 'EOD HALT: <error message>'.\n"
            "Otherwise: a comprehensive EOD performance report showing each prediction's "
            "result (ticker, confidence, actual %, hit/miss), root cause analysis, and "
            "confirmation that feedback was written to the database using the real IDs."
        ),
        agent=manager_agent,
    )

    return [eod_task]


# ═══════════════════════════════════════════════════════════════════════════════
#  CREW BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_morning_crew() -> Crew:
    """Sequential crew: DataAgent → PM → Analyst → Reporter."""
    return Crew(
        agents=[data_agent, pm_agent, analyst_agent, reporter_agent],
        tasks=_create_morning_tasks(),
        process=Process.sequential,
        verbose=True,
    )


def build_eod_crew() -> Crew:
    """Single-agent EOD review crew."""
    return Crew(
        agents=[manager_agent],
        tasks=_create_eod_tasks(),
        process=Process.sequential,
        verbose=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  SHORT SQUEEZE & FLOAT ROTATION PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

@tool("Fetch Squeeze Candidates")
def fetch_squeeze_candidates(scan_mode: str) -> str:
    """
    Scans the market for Short Squeeze & Float Rotation candidates.
    Applies strict quantitative filters using yfinance data before any LLM token is spent.

    Filters (ALL must pass):
      • Country          : United States
      • Market Cap       : $300M – $10B
      • Stock Price      : $1 – $50
      • Today's Volume   : > 1,000,000
      • Float            : 5M – 20M shares
      • Short Float %    : > 10%
      • RVOL             : > 2.0  (today's volume / 10-day avg volume)
      • Turnover Rate    : 0.33 – 3.0  (today's volume / float)
      • 5-Day % Change   : > +10%
      • Above VWAP       : current price > (High + Low + Close) / 3

    scan_mode: 'gainers' | 'active' | 'both' (recommended)
    Returns condensed strings for every ticker passing ALL filters.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # ── Step 1: build candidate universe ─────────────────────────────────────
    candidates: set[str] = set()

    if scan_mode in ("gainers", "both"):
        try:
            candidates.update(si.get_day_gainers()["Symbol"].dropna().tolist()[:60])
        except Exception:
            pass

    if scan_mode in ("active", "both"):
        try:
            candidates.update(si.get_day_most_active()["Symbol"].dropna().tolist()[:60])
        except Exception:
            pass

    # Fallback to Yahoo Finance JSON screener for both lists
    if not candidates:
        for scr_id in ("day_gainers", "most_actives"):
            try:
                url = (
                    "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
                    f"?scrIds={scr_id}&count=60&corsDomain=finance.yahoo.com"
                )
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                    )
                }
                resp   = requests.get(url, headers=headers, timeout=12)
                quotes = resp.json()["finance"]["result"][0]["quotes"]
                candidates.update(q["symbol"] for q in quotes)
            except Exception:
                continue

    if not candidates:
        return json.dumps({
            "date": today_str, "passed_filter": 0, "squeeze_candidates": [],
            "note": "All screener sources failed. Try again later.",
        })

    # ── Step 2: apply quantitative filters ───────────────────────────────────
    passed: list[dict] = []

    for ticker in list(candidates)[:100]:
        try:
            stock = yf.Ticker(ticker)
            fast  = stock.fast_info

            # Quick pre-checks using fast_info (no HTTP round-trip)
            market_cap = float(getattr(fast, "market_cap",       0) or 0)
            curr_price = float(getattr(fast, "last_price",       0) or 0)

            if not (300e6 <= market_cap <= 10e9):
                continue
            if not (1.0 <= curr_price <= 50.0):
                continue

            # Historical data for RVOL, VWAP, 5-day change
            hist = stock.history(period="11d")
            if len(hist) < 6:
                continue

            today_vol   = float(hist["Volume"].iloc[-1])
            if today_vol < 1_000_000:
                continue

            avg_10d_vol = float(hist["Volume"].iloc[:-1].mean())
            rvol        = today_vol / avg_10d_vol if avg_10d_vol > 0 else 0.0
            if rvol < 2.0:
                continue

            five_day_chg = (
                (hist["Close"].iloc[-1] / hist["Close"].iloc[-6] - 1) * 100
            )
            if five_day_chg < 10.0:
                continue

            vwap = (
                hist["High"].iloc[-1] + hist["Low"].iloc[-1] + hist["Close"].iloc[-1]
            ) / 3
            if curr_price <= vwap:
                continue

            # Fundamentals from info (slower — called only for survivors)
            info = stock.info

            if info.get("country") != "United States":
                continue

            float_shares = info.get("floatShares") or 0
            if not (5_000_000 <= float_shares <= 20_000_000):
                continue

            short_pct = info.get("shortPercentOfFloat") or 0.0
            if short_pct < 0.10:
                continue

            turnover = today_vol / float_shares if float_shares > 0 else 0.0
            if not (0.33 <= turnover <= 3.0):
                continue

            # All 10 filters passed — store raw numbers AND pre-formatted display strings.
            # Pre-formatting here means the LLM can never "recalculate" a value; it only
            # copies the strings that Python has already validated and formatted.
            float_m      = round(float_shares / 1e6, 1)
            short_pct_v  = round(short_pct * 100, 1)
            rvol_v       = round(rvol, 1)
            turnover_v   = round(turnover, 2)
            five_day_v   = round(five_day_chg, 1)
            today_vol_m  = round(today_vol / 1e6, 1)
            vwap_v       = round(vwap, 2)
            price_v      = round(curr_price, 2)
            mcap_b       = round(market_cap / 1e9, 2)

            # Country is already validated above (non-US is filtered out); store it.
            country = info.get("country", "N/A") or "N/A"

            passed.append({
                # Raw numeric kept for sorting — stripped before serialising
                "_rvol_raw": rvol_v,
                "ticker":    ticker,
                # python_metrics: ALL 11 fields are computed and formatted in Python.
                # The LLM MUST copy this object verbatim.
                # Exception: "News" starts as "N/A" — the LLM sets it to
                # "Verified" after a successful news-catalyst check, or "None Found"
                # if no qualifying catalyst exists. Every other field is immutable.
                "python_metrics": {
                    "Market Cap":  f"${mcap_b}B",
                    "Price":       f"${price_v:.2f}",
                    "5d % Change": f"{five_day_v:+.1f}%",
                    "Volume":      f"{today_vol_m}M",
                    "RVOL":        f"{rvol_v}x",
                    "Float":       f"{float_m}M",
                    "Short %":     f"{short_pct_v}%",
                    "Above VWAP":  "Yes",   # filter guarantees curr_price > vwap
                    "Turnover":    f"{turnover_v}",
                    "Country":     country,
                    "News":        "N/A",   # LLM fills this after catalyst check
                },
                # Human-readable condensed line — for LLM scanning only, never parsed
                "display": (
                    f"{ticker} | ${price_v:.2f} | MCap:${mcap_b}B | "
                    f"Float:{float_m}M | Short:{short_pct_v}% | RVOL:{rvol_v}x | "
                    f"Turnover:{turnover_v} | 5d:{five_day_v:+.1f}% | "
                    f"Vol:{today_vol_m}M | VWAP:${vwap_v:.2f} | {country}"
                ),
            })

        except Exception:
            continue

    # Sort: highest RVOL first (most aggressive float rotation at the top)
    passed.sort(key=lambda x: x["_rvol_raw"], reverse=True)

    # Strip internal sort key before serialising
    candidates_out = [
        {k: v for k, v in r.items() if k != "_rvol_raw"}
        for r in passed
    ]

    return json.dumps(
        {
            "date":            today_str,
            "source":          "yahoo_fin + yfinance",
            "tickers_scanned": len(candidates),
            "passed_filter":   len(candidates_out),
            # Each element has: ticker, python_metrics (authoritative), display (human-readable)
            "squeeze_candidates": candidates_out,
            "CRITICAL_INSTRUCTION": (
                "python_metrics contains 11 fields computed and validated by Python. "
                "10 fields (Market Cap, Price, 5d % Change, Volume, RVOL, Float, "
                "Short %, Above VWAP, Turnover, Country) are IMMUTABLE — copy them "
                "verbatim. The only field you may set is 'News': after running the "
                "news check, change it to 'Verified' (positive catalyst found) or "
                "'None Found' (no qualifying catalyst). Any ticker whose 'News' "
                "remains 'N/A' or is set to 'None Found' must be REJECTED."
            ),
            "filter_summary": (
                "ALL 10 passed: US only | MCap $300M-$10B | Price $1-$50 | "
                "Vol>1M | Float 5M-20M | Short>10% | RVOL>2x | "
                "Turnover 0.33-3.0 | 5d>+10% | Above VWAP"
            ),
        }
    )


# ── Squeeze Agent ─────────────────────────────────────────────────────────────

squeeze_agent = Agent(
    role="Float Rotation & Short Squeeze Sniper",
    goal=(
        "Receive mathematically pre-filtered squeeze candidates and validate that each "
        "one has a real, active, highly positive catalyst driving the unusual volume and "
        "float turnover. Reject anything moving on hype alone. Output only confirmed, "
        "news-backed squeeze setups."
    ),
    backstory=(
        "You specialize in explosive low-float setups. You understand that a true short "
        "squeeze requires THREE elements: (1) structural setup — high short interest and "
        "low float creating fuel, (2) technical trigger — RVOL > 2x and above VWAP "
        "confirming institutional buying, (3) news catalyst — a specific, verifiable "
        "positive event driving the turnover. "
        "CRITICAL DATA RULE: You are a read-only consumer of quantitative data. Every "
        "metric (Float, Short%, RVOL, Turnover, 5d_change, VWAP, Price, MCap, Vol) is "
        "pre-computed by Python and delivered in the 'python_metrics' field of each "
        "candidate. You are constitutionally incapable of calculating or altering these "
        "values — doing so would be a catastrophic compliance violation. Your analytical "
        "contribution is limited exclusively to judging whether a verifiable news catalyst "
        "exists. If the news is missing, negative, or ambiguous, you REJECT the candidate. "
        "You never recommend stocks moving purely on social media hype, Reddit mentions, "
        "or unnamed 'market speculation'. "
        "Your output reads like a terse risk desk memo: python_metrics copied verbatim "
        "first, verified news catalyst second, one-sentence thesis last."
    ),
    tools=[
        fetch_squeeze_candidates,
        fetch_earnings_news,
        read_recent_picks_performance,
        save_predictions_to_db,
    ],
    llm=claude_llm,
    verbose=True,
    allow_delegation=False,
)


# ── Squeeze Tasks ─────────────────────────────────────────────────────────────

def _create_squeeze_tasks() -> list[Task]:
    today = datetime.now().strftime("%Y-%m-%d")

    squeeze_task = Task(
        description=(
            f"Today is {today}. Run the Short Squeeze & Float Rotation pipeline.\n\n"
            "╔══════════════════════════════════════════════════════════════════════════╗\n"
            "║  DATA INTEGRITY CONTRACT — READ THIS FIRST, NEVER VIOLATE IT           ║\n"
            "║                                                                          ║\n"
            "║  Each ticker from 'Fetch Squeeze Candidates' has a 'python_metrics'     ║\n"
            "║  object with 11 fields. 10 of them are IMMUTABLE — computed and         ║\n"
            "║  validated by Python and may NOT be altered, rounded differently,        ║\n"
            "║  omitted, or invented:                                                   ║\n"
            "║    Market Cap | Price | 5d % Change | Volume | RVOL                     ║\n"
            "║    Float | Short % | Above VWAP | Turnover | Country                    ║\n"
            "║                                                                          ║\n"
            "║  The ONLY field you may set is 'News':                                  ║\n"
            "║    → 'Verified'   : a qualifying positive catalyst was found.           ║\n"
            "║    → 'None Found' : no qualifying catalyst; REJECT this ticker.         ║\n"
            "║                                                                          ║\n"
            "║  You MUST copy the ENTIRE metrics object (all 11 fields) verbatim       ║\n"
            "║  into 'metrics' in your output JSON. Do not reconstruct it from         ║\n"
            "║  memory or the display string — use the python_metrics object directly. ║\n"
            "╚══════════════════════════════════════════════════════════════════════════╝\n\n"
            "STEP 0 — Self-Review (your own recent squeeze track record):\n"
            "  Call 'Read Recent Picks Performance' with days_back='14'.\n"
            "  Focus on rows where strategy='squeeze'. Identify:\n"
            "    • Which past squeeze setups (high RVOL + high short %) actually\n"
            "      delivered the move — note the catalyst type (earnings beat,\n"
            "      product launch, FDA approval, etc.).\n"
            "    • Which faded fast (positive session but T+3 < 0) — these are\n"
            "      classic 'fake squeezes'; avoid analogous setups today.\n"
            "    • Whether your high-confidence squeeze picks (>=0.80) actually\n"
            "      outperformed your low-confidence ones. If not, cap today's\n"
            "      confidence at 0.78.\n"
            "    • Any ticker you re-list today that you ALREADY recommended in\n"
            "      the last 5 days — only repeat if there is a NEW, materially\n"
            "      different catalyst, otherwise REJECT.\n"
            "  Output a brief 'Squeeze Self-Review' block (3 bullets) before STEP 1.\n\n"
            "STEP 1 — Quantitative Screen (read-only):\n"
            "  Call 'Fetch Squeeze Candidates' with scan_mode='both'.\n"
            "  The tool returns a JSON object with 'squeeze_candidates' — an array where\n"
            "  each element has:\n"
            "    • ticker          — the symbol\n"
            "    • python_metrics  — the authoritative 11-field dict (ground truth)\n"
            "    • display         — condensed one-liner (for human scanning only)\n"
            "  All 10 quantitative filters were applied in Python. Every ticker here\n"
            "  passed EVERY rule. Do not question or re-derive any number.\n\n"
            "STEP 2 — Catalyst Validation (your ONLY analytical contribution):\n"
            "  Extract all tickers as a comma-separated string.\n"
            "  Call 'Fetch Earnings News for Tickers' with that string.\n"
            "  For each ticker, evaluate ONLY the news headlines:\n"
            "    ACCEPT → set News='Verified' if:\n"
            "      earnings beat | product launch | partnership | FDA approval |\n"
            "      analyst upgrade | contract win | short squeeze article.\n"
            "    REJECT → set News='None Found' if:\n"
            "      no news found | vague rumor | negative news | SEC filing only |\n"
            "      news is >5 days old | purely social-media driven.\n"
            "  Only ACCEPTED tickers proceed to STEP 3.\n\n"
            "STEP 3 — Rank & Select Top 3–5:\n"
            "  Rank accepted tickers by: RVOL × Short % × catalyst_quality.\n"
            "  Read RVOL and Short % directly from python_metrics — do not re-derive.\n"
            "  Prefer BMO catalysts (live today) over AMC (tomorrow).\n"
            "  DEDUPLICATION: never output the same ticker twice.\n\n"
            "STEP 4 — Save & Output:\n"
            "  Call 'Save Alpha Predictions to Database' with a JSON array.\n"
            "  For each pick, build the object by:\n"
            "    1. Start with python_metrics for that ticker (the full 11-field dict).\n"
            "    2. Set the 'News' field to 'Verified'.\n"
            "    3. Use that completed dict as the value for 'metrics'.\n"
            "  The exact required format:\n"
            "  [\n"
            "    {\n"
            '      "ticker": "XXX",\n'
            '      "pm_rationale": "<3-line format below>",\n'
            '      "confidence_score": 0.XX,\n'
            '      "target_price": 12.34,\n'
            '      "strategy": "squeeze",\n'
            '      "metrics": {\n'
            '        "Market Cap":  "<from python_metrics>",\n'
            '        "Price":       "<from python_metrics>",\n'
            '        "5d % Change": "<from python_metrics>",\n'
            '        "Volume":      "<from python_metrics>",\n'
            '        "RVOL":        "<from python_metrics>",\n'
            '        "Float":       "<from python_metrics>",\n'
            '        "Short %":     "<from python_metrics>",\n'
            '        "Above VWAP":  "<from python_metrics>",\n'
            '        "Turnover":    "<from python_metrics>",\n'
            '        "Country":     "<from python_metrics>",\n'
            '        "News":        "Verified"\n'
            "      }\n"
            "    }\n"
            "  ]\n\n"
            "  RATIONALE FORMAT (mandatory, 3 lines):\n"
            "  - Setup: [paste the display string from the tool output for this ticker]\n"
            "  - Catalyst: [exact news headline + date from 'Fetch Earnings News']\n"
            "  - Thesis: [one sentence on why the squeeze is active today]\n\n"
            "  TARGET_PRICE RULES (mandatory):\n"
            "  - target_price is a USD price where you would close the squeeze position.\n"
            "  - Read the current Price from python_metrics and set target STRICTLY above it.\n"
            "  - Cap at 5x current price (anything higher is rejected as hallucination).\n"
            "  - Squeeze targets are typically aggressive: +10% to +40% on the catalyst day,\n"
            "    +25% to +80% on multi-day RVOL-driven rotations. Anchor to recent swing highs,\n"
            "    short-interest levels, or 1.5–2x the 5d range, NOT to thin air.\n"
            "  - Round to 2 decimals."
        ),
        expected_output=(
            "Part 0 — Squeeze Self-Review: 3 bullets covering winning patterns, fading "
            "patterns, and the confidence ceiling derived from past squeeze calibration.\n"
            "Part 1 — Catalyst validation table: ticker | Verified/None Found | headline.\n"
            "Part 2 — JSON array of 3–5 unique confirmed squeeze picks, each with: "
            "ticker, pm_rationale (Setup/Catalyst/Thesis — and explicitly cite the Self-"
            "Review winner pattern this setup mirrors when applicable), confidence_score "
            "(0.0–1.0, capped by the Self-Review ceiling), target_price (USD float, "
            "strictly above current Price, at most 5x current Price), strategy='squeeze', "
            "metrics (all 11 fields — 10 verbatim from python_metrics plus News='Verified'). "
            "Output the raw JSON array last for direct parsing."
        ),
        agent=squeeze_agent,
    )

    return [squeeze_task]


def build_squeeze_crew() -> Crew:
    """Single-agent Short Squeeze pipeline."""
    return Crew(
        agents=[squeeze_agent],
        tasks=_create_squeeze_tasks(),
        process=Process.sequential,
        verbose=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  HISTORICAL TARGET SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

_TARGET_SIM_SYSTEM = (
    "You are a hedge-fund analyst back-simulating sell targets for previously "
    "made stock picks. You ONLY know the information available at pick time: "
    "the rationale, the screen metrics and the entry price. You DO NOT and CANNOT "
    "use any later price action — this is a back-test, future leakage would "
    "invalidate the simulation. Output ONLY a single JSON object with the key "
    '"target_price" — nothing else, no prose, no markdown fences.'
)


def _extract_target_from_llm_text(text: str) -> float | None:
    """Pull a single positive float out of an LLM JSON response, defensively."""
    if not text:
        return None
    s = text.strip()
    # Strip a leading code fence if the model added one.
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    # First try strict JSON parse.
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            tp = obj.get("target_price")
            if tp is not None:
                val = float(tp)
                if val > 0:
                    return val
    except Exception:
        pass
    # Fallback: pull the first number that looks like a target.
    import re
    m = re.search(r'"target_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)', s)
    if m:
        try:
            val = float(m.group(1))
            if val > 0:
                return val
        except ValueError:
            pass
    m = re.search(r"([0-9]+\.[0-9]+|[0-9]+)", s)
    if m:
        try:
            val = float(m.group(1))
            if val > 0:
                return val
        except ValueError:
            pass
    return None


def simulate_historical_targets(
    *,
    only_missing: bool = True,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """
    Back-simulate analyst sell targets for historical predictions.

    For every prediction (default: only those with target_price IS NULL), call
    claude_llm with ONLY the context that was visible at pick time and ask for
    a single target_price. Validate that pick < target <= 5x pick, then UPDATE
    the row. After all writes, call update_target_hit_dates() so the dashboard
    immediately knows which targets were ever reached.

    Args:
        only_missing : if True (default), only rows with NULL target_price.
        dry_run      : if True, simulate everything but skip DB writes.
        progress     : if True, print a per-row progress line to stdout.

    Returns:
        {
          'scanned'                   : int,   # rows considered
          'asked'                     : int,   # LLM calls actually issued
          'written'                   : int,   # rows updated in the DB
          'rejected_invalid'          : int,   # LLM returned out-of-range value
          'rejected_no_pick_price'    : int,   # cannot validate without pick price
          'llm_errors'                : int,   # network / parse failures
          'hits_resolved'             : int,   # from update_target_hit_dates()
        }
    """
    where = "WHERE price_at_pick IS NOT NULL"
    if only_missing:
        where += " AND target_price IS NULL"

    conn = db.get_connection()
    cur  = conn.execute(
        f"""
        SELECT id, date, ticker, strategy, pm_rationale, confidence_score,
               metrics, price_at_pick, target_price
        FROM alpha_predictions
        {where}
        ORDER BY date ASC, ticker ASC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    # Also count rows that were skipped because they have no pick price.
    if only_missing:
        skip_cur = conn.execute(
            "SELECT COUNT(*) AS n FROM alpha_predictions "
            "WHERE target_price IS NULL AND price_at_pick IS NULL"
        )
    else:
        skip_cur = conn.execute(
            "SELECT COUNT(*) AS n FROM alpha_predictions WHERE price_at_pick IS NULL"
        )
    rejected_no_pick_price = int(skip_cur.fetchone()["n"])
    conn.close()

    asked            = 0
    written          = 0
    rejected_invalid = 0
    llm_errors       = 0

    for i, row in enumerate(rows, 1):
        try:
            pick = float(row["price_at_pick"])
        except (TypeError, ValueError):
            rejected_no_pick_price += 1
            continue
        if pick <= 0:
            rejected_no_pick_price += 1
            continue

        prompt = (
            f"Today is {row['date']} (this is a back-simulation — DO NOT use any "
            f"information after this date).\n\n"
            f"Pick: {row['ticker']} @ ${pick:.2f}\n"
            f"Strategy : {row.get('strategy') or 'alpha'}\n"
            f"Confidence: {row.get('confidence_score')}\n"
            f"Rationale: {row.get('pm_rationale') or '(none)'}\n"
            f"Screen metrics (JSON): {row.get('metrics') or '{}'}\n\n"
            "Set a SELL TARGET — the absolute USD price where you would close "
            "this long position for a profit.\n"
            "Hard rules:\n"
            f"  1. target_price MUST be strictly above the pick price (${pick:.2f}).\n"
            f"  2. target_price MUST be at most 5x the pick price (${pick * 5:.2f}).\n"
            "  3. Anchor the number to evidence in the rationale/metrics: prior swing "
            "     highs, gap-fill levels, RVOL/short-interest implied moves, or 1.5–2x "
            "     the implied daily volatility. Typical swing targets are +4% to +25% "
            "     above pick; squeeze setups can be +10% to +60%.\n"
            "  4. Higher confidence → more ambitious target.\n"
            "  5. Round to 2 decimals.\n\n"
            'Output EXACTLY this JSON (and NOTHING else): {"target_price": <number>}'
        )

        asked += 1
        try:
            raw = claude_llm.call(
                [{"role": "system", "content": _TARGET_SIM_SYSTEM},
                 {"role": "user",   "content": prompt}]
            )
        except Exception as exc:
            llm_errors += 1
            if progress:
                print(f"  [{i}/{len(rows)}] {row['ticker']} ({row['date']}) "
                      f"-> LLM error: {exc}")
            continue

        target = _extract_target_from_llm_text(str(raw or ""))
        if target is None or not (pick < target <= pick * 5.0):
            rejected_invalid += 1
            if progress:
                snippet = str(raw or "").strip().replace("\n", " ")[:80]
                print(f"  [{i}/{len(rows)}] {row['ticker']} ({row['date']}) "
                      f"-> rejected (pick=${pick:.2f}, raw='{snippet}')")
            continue

        target = round(target, 2)
        upside = (target - pick) / pick * 100
        if progress:
            print(f"  [{i}/{len(rows)}] {row['ticker']:<6} ({row['date']}) "
                  f"pick=${pick:>7.2f}  target=${target:>7.2f}  (+{upside:5.1f}%)")

        if not dry_run:
            conn = db.get_connection()
            conn.execute(
                "UPDATE alpha_predictions SET target_price = ?, target_hit_date = NULL "
                "WHERE id = ?",
                (target, row["id"]),
            )
            conn.commit()
            conn.close()
            written += 1

    # Always resolve hits after writing — keeps the dashboard consistent.
    hits = {} if dry_run else db.update_target_hit_dates()

    return {
        "scanned"                : len(rows),
        "asked"                  : asked,
        "written"                : written,
        "rejected_invalid"       : rejected_invalid,
        "rejected_no_pick_price" : rejected_no_pick_price,
        "llm_errors"             : llm_errors,
        "hits_resolved"          : int(hits.get("hits_written", 0)),
    }
