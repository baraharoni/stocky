"""
simulator.py — Catalyst Alpha v1.0

Historical back-test simulator for the Alpha pipeline.

For each trading day in the requested window the simulator:
  1. Reconstructs the universe of "Next Play" gainers (+2% to +6%) using
     historical OHLCV from yfinance — no live screener APIs are called.
  2. Pulls the historical earnings calendar from Nasdaq for that exact date.
  3. Builds a single-prompt that mirrors the PM and Analyst agents' rules
     (filters, rationale format, target_price guardrails, dedup).
  4. Calls the same `claude_llm` used by the live Crew.
  5. Saves the top-N picks to the `simulated_predictions` table tagged with
     a `run_id` so multiple back-tests can coexist.

The live `alpha_predictions` table is never touched.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

import database as db

load_dotenv()

# ─── Tunables ────────────────────────────────────────────────────────────────

# Price-action window mirrored from agents.fetch_premarket_gainers.
GAINER_MIN_PCT = 2.0
GAINER_MAX_PCT = 6.0

# PM filter mirrored from agents._create_morning_tasks (pm_task).
PM_MCAP_MIN = 2_000_000_000
PM_VOL_MIN  = 1_000_000
PM_PRICE_MIN = 5.0

# Earnings filter mirrored from agents.fetch_earnings_calendar.
EARN_MCAP_MIN = 300_000_000
EARN_MCAP_MAX = 10_000_000_000
EARN_VOL_MIN  = 500_000

DEFAULT_START = "2025-09-01"
DEFAULT_END   = "2026-03-31"

NASDAQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":  "application/json, text/plain, */*",
    "Origin":  "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


# ─── Universe ────────────────────────────────────────────────────────────────
#
# Full S&P 500 + NASDAQ-100 union as of late 2025. This is intentionally a
# static list so back-test runs are deterministic and never affected by
# real-time index reconstitutions. New additions/removals after this snapshot
# add a small amount of survivorship bias, which is acceptable for the
# rough-and-ready educational purpose of this back-test.
#
# To refresh, pull the constituents CSVs from iShares (IVV / QQQ) and
# regenerate this list — the loader is intentionally trivial so the source
# of truth is auditable in version control.

_UNIVERSE_TICKERS: tuple[str, ...] = (
    "A", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI", "ADM",
    "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG", "AKAM",
    "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN", "AMP",
    "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH", "APO",
    "APTV", "ARE", "ARM", "ASML", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON",
    "AXP", "AZN", "AZO", "BA", "BAC", "BALL", "BAX", "BBY", "BDX", "BEN",
    "BF-B", "BG", "BIIB", "BK", "BKNG", "BKR", "BLDR", "BLK", "BMY", "BR",
    "BRK-B", "BRO", "BSX", "BWA", "BX", "BXP", "C", "CAG", "CAH", "CARR",
    "CAT", "CB", "CBOE", "CBRE", "CCEP", "CCI", "CCL", "CDNS", "CDW", "CE",
    "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI", "CINF", "CL", "CLX",
    "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC", "CNP", "COF", "COIN", "COO",
    "COP", "COR", "COST", "CPAY", "CPB", "CPRT", "CPT", "CRL", "CRM", "CRWD",
    "CSCO", "CSGP", "CSX", "CTAS", "CTLT", "CTRA", "CTSH", "CTVA", "CVS", "CVX",
    "CZR", "D", "DAL", "DASH", "DAY", "DD", "DDOG", "DE", "DECK", "DFS",
    "DG", "DGX", "DHI", "DHR", "DIS", "DLR", "DLTR", "DOC", "DOV", "DOW",
    "DPZ", "DRI", "DTE", "DUK", "DVA", "DVN", "DXCM", "EA", "EBAY", "ECL",
    "ED", "EFX", "EG", "EIX", "EL", "ELV", "EMN", "EMR", "ENPH", "EOG",
    "EPAM", "EQIX", "EQR", "EQT", "ERIE", "ES", "ESS", "ETN", "ETR", "EVRG",
    "EW", "EXC", "EXE", "EXPD", "EXPE", "EXR", "F", "FANG", "FAST", "FCX",
    "FDS", "FDX", "FE", "FFIV", "FI", "FICO", "FIS", "FITB", "FOX", "FOXA",
    "FRT", "FSLR", "FTNT", "FTV", "GD", "GDDY", "GE", "GEHC", "GEN", "GEV",
    "GFS", "GILD", "GIS", "GL", "GLW", "GM", "GNRC", "GOOG", "GOOGL", "GPC",
    "GPN", "GRMN", "GS", "GWW", "HAL", "HAS", "HBAN", "HCA", "HD", "HES",
    "HIG", "HII", "HLT", "HOLX", "HON", "HOOD", "HPE", "HPQ", "HRL",
    "HSIC", "HST", "HSY", "HUBB", "HUM", "HWM", "IBM", "ICE", "IDXX", "IEX",
    "IFF", "INCY", "INTC", "INTU", "INVH", "IP", "IPG", "IQV", "IR", "IRM",
    "ISRG", "IT", "ITW", "IVZ", "J", "JBHT", "JBL", "JCI", "JKHY", "JNJ",
    "JNPR", "JPM", "K", "KDP", "KEY", "KEYS", "KHC", "KIM", "KKR", "KLAC",
    "KMB", "KMI", "KMX", "KO", "KR", "KVUE", "L", "LCID", "LDOS", "LEN",
    "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNT", "LOW", "LRCX", "LULU",
    "LUV", "LVS", "LW", "LYB", "LYV", "MA", "MAA", "MAR", "MAS", "MCD",
    "MCHP", "MCK", "MCO", "MDB", "MDLZ", "MDT", "MELI", "MET", "META", "MGM",
    "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST", "MO", "MOH", "MOS",
    "MPC", "MPWR", "MRK", "MRNA", "MRVL", "MS", "MSCI", "MSFT", "MSI", "MSTR",
    "MTB", "MTCH", "MTD", "MU", "NCLH", "NDAQ", "NDSN", "NEE", "NEM", "NET",
    "NFLX", "NI", "NIO", "NKE", "NOC", "NOW", "NRG", "NSC", "NTAP", "NTRS",
    "NUE", "NVDA", "NVR", "NWS", "NWSA", "NXPI", "O", "ODFL", "OKE", "OKTA",
    "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY", "PANW", "PARA", "PAYC", "PAYX",
    "PCAR", "PCG", "PEG", "PEP", "PFE", "PFG", "PG", "PGR", "PH", "PHM",
    "PINS", "PKG", "PLD", "PLTR", "PM", "PNC", "PNR", "PNW", "PODD", "POOL",
    "PPG", "PPL", "PRU", "PSA", "PSX", "PTC", "PWR", "PYPL", "QCOM", "RCL",
    "REG", "REGN", "RF", "RIVN", "RJF", "RL", "RMD", "ROK", "ROKU", "ROL",
    "ROP", "ROST", "RSG", "RTX", "RVTY", "SBAC", "SBUX", "SCHW", "SHOP", "SHW",
    "SJM", "SLB", "SMCI", "SMH", "SNA", "SNAP", "SNOW", "SNPS", "SO", "SOLV",
    "SPG", "SPGI", "SPOT", "SQ", "SRE", "STE", "STLD", "STT", "STX", "STZ",
    "SW", "SWK", "SWKS", "SYF", "SYK", "SYY", "T", "TAP", "TDG", "TDY",
    "TEAM", "TECH", "TEL", "TER", "TFC", "TFX", "TGT", "TJX", "TKO", "TMO",
    "TMUS", "TPL", "TPR", "TRGP", "TRMB", "TROW", "TRV", "TSCO", "TSLA", "TSN",
    "TT", "TTD", "TTWO", "TXN", "TXT", "TYL", "UAL", "UBER", "UDR", "UHS",
    "ULTA", "UNH", "UNP", "UPS", "URI", "USB", "V", "VEEV", "VICI", "VLO",
    "VLTO", "VMC", "VRSK", "VRSN", "VRTX", "VST", "VTR", "VTRS", "VZ", "WAB",
    "WAT", "WBA", "WBD", "WDAY", "WDC", "WEC", "WELL", "WFC", "WM", "WMB",
    "WMT", "WRB", "WST", "WTW", "WY", "WYNN", "XEL", "XOM", "XPEV", "XPO",
    "XYL", "YUM", "ZBH", "ZBRA", "ZM", "ZS", "ZTS",
)


def _load_universe() -> list[str]:
    """Return the deduplicated, sorted back-test universe."""
    return sorted({t.strip().upper() for t in _UNIVERSE_TICKERS if t})


# ─── Universe-wide history cache ─────────────────────────────────────────────
#
# yfinance's batch downloader is by far the cheapest way to scan ~500 tickers
# across the back-test window. We pull the entire window once, cache the
# result on disk as a Parquet file, and slice per day from memory.

_CACHE_DIR = os.path.join(
    os.path.dirname(__file__) or ".", ".sim_cache"
)


def _cache_path(start: str, end: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"ohlcv_{start}_{end}.parquet")


def _download_universe_history(
    start: str,
    end: str,
    tickers: list[str],
    *,
    batch_size: int = 60,
    progress: bool = True,
) -> pd.DataFrame:
    """
    Batch-download daily OHLCV for the universe across [start, end+slack].
    Returns a long-form DataFrame indexed by (Date, Ticker) with Open/High/
    Low/Close/Volume columns.

    Cached on disk as Parquet so re-runs are near-instant.
    """
    # +210 days slack so T+180 forward returns are computable for the last
    # picks in the window.
    fetch_end = (date.fromisoformat(end) + timedelta(days=210)).isoformat()
    cache_file = _cache_path(start, fetch_end)
    if os.path.exists(cache_file):
        if progress:
            print(f"  [cache] reusing {cache_file}")
        try:
            return pd.read_parquet(cache_file)
        except Exception:
            os.remove(cache_file)  # fall through to re-download

    frames: list[pd.DataFrame] = []
    n_batches = (len(tickers) + batch_size - 1) // batch_size

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        if progress:
            print(
                f"  [download] batch {i // batch_size + 1}/{n_batches}  "
                f"({len(batch)} tickers, {start} -> {fetch_end})"
            )
        try:
            raw = yf.download(
                tickers=" ".join(batch),
                start=start,
                end=fetch_end,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception as exc:
            print(f"  [warn ] batch failed: {exc}")
            continue

        if raw is None or raw.empty:
            continue

        # yf returns either a multi-index column frame (multiple tickers) or a
        # plain frame (single ticker). Normalise both into long form.
        if isinstance(raw.columns, pd.MultiIndex):
            for tkr in batch:
                if tkr not in raw.columns.get_level_values(0):
                    continue
                sub = raw[tkr][["Open", "High", "Low", "Close", "Volume"]].copy()
                sub = sub.dropna(how="all")
                if sub.empty:
                    continue
                sub["Ticker"] = tkr
                sub.index = pd.to_datetime(sub.index).tz_localize(None)
                sub.index.name = "Date"
                frames.append(sub)
        else:
            sub = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            sub = sub.dropna(how="all")
            if not sub.empty:
                sub["Ticker"] = batch[0]
                sub.index = pd.to_datetime(sub.index).tz_localize(None)
                sub.index.name = "Date"
                frames.append(sub)

        # Be polite to the Yahoo edge.
        time.sleep(0.6)

    if not frames:
        return pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume", "Ticker"]
        )

    out = pd.concat(frames).reset_index().set_index(["Date", "Ticker"]).sort_index()
    try:
        out.to_parquet(cache_file)
        if progress:
            print(f"  [cache] wrote {cache_file}  rows={len(out):,}")
    except Exception as exc:
        print(f"  [warn ] failed to write cache: {exc}")
    return out


# ─── Static market-cap snapshot ──────────────────────────────────────────────
#
# We need an MCap estimate for the PM filter (>$2B) and for the
# earnings-calendar mid-cap filter ($300M-$10B). Pulling fast_info per ticker
# inflates back-test runtime by an order of magnitude, so we cache it once
# and reuse it across the entire run. This is a known approximation: a stock
# whose float was, say, $1.8B in Sept-25 and is $2.5B today will show up as
# "qualified" even though the live PM would have rejected it. For an
# educational back-test this trade-off is acceptable.

_MCAP_CACHE_FILE = os.path.join(_CACHE_DIR, "mcap_volume.parquet")


def _build_mcap_snapshot(
    tickers: list[str],
    *,
    progress: bool = True,
) -> pd.DataFrame:
    """One-off pull of (market_cap, three_month_avg_volume) per ticker."""
    if os.path.exists(_MCAP_CACHE_FILE):
        try:
            return pd.read_parquet(_MCAP_CACHE_FILE)
        except Exception:
            os.remove(_MCAP_CACHE_FILE)

    rows: list[dict] = []
    for i, tkr in enumerate(tickers, 1):
        if progress and (i == 1 or i % 25 == 0 or i == len(tickers)):
            print(f"  [mcap ] {i}/{len(tickers)} {tkr}")
        try:
            fast = yf.Ticker(tkr).fast_info
            mcap = float(getattr(fast, "market_cap", 0) or 0)
            avgv = float(getattr(fast, "three_month_average_volume", 0) or 0)
        except Exception:
            mcap, avgv = 0.0, 0.0
        rows.append({"Ticker": tkr, "market_cap": mcap, "avg_volume": avgv})
        # keep yfinance happy
        if i % 30 == 0:
            time.sleep(0.4)

    df = pd.DataFrame(rows).set_index("Ticker")
    os.makedirs(_CACHE_DIR, exist_ok=True)
    try:
        df.to_parquet(_MCAP_CACHE_FILE)
        if progress:
            print(f"  [cache] wrote {_MCAP_CACHE_FILE}")
    except Exception as exc:
        print(f"  [warn ] failed to write mcap cache: {exc}")
    return df


# ─── Sympathy map (mirrored from agents.SYMPATHY_MAP) ───────────────────────

_SYMPATHY_REF = (
    "NVDA→SMCI,AMD,MRVL,AVGO,MU,KLAC,LRCX | AMD→NVDA,INTC,QCOM,MRVL,SMCI | "
    "MSFT→GOOGL,AMZN,CRM,NOW,TEAM,VEEV | AAPL→QCOM,AVGO,MU,AMAT | "
    "META→SNAP,PINS,GOOGL,UBER,LYFT | AMZN→SHOP,GOOGL,MSFT,ABNB | "
    "TSLA→RIVN,LCID,NIO,XPEV | NFLX→ROKU,SNAP,PINS | COIN→MSTR,HOOD | "
    "PLTR→DDOG,NET,ZS,SNOW,PANW | CRWD→PANW,ZS,NET,OKTA"
)


# ─── Step 1 — historical pre-market gainers reconstruction ──────────────────

def _reconstruct_gainers_for_date(
    target_d: date,
    history: pd.DataFrame,
    mcap_snap: pd.DataFrame,
) -> list[dict]:
    """
    For target_d, find every ticker whose prior-trading-close → target_d-close
    move falls inside the [+2%, +6%] "Next Play" window AND passes the live
    PM filter (MCap > $2B, AvgVol > 1M, Price > $5).

    Returns rows shaped like the entries the live `fetch_premarket_gainers`
    tool produces, ready to feed into the simulation prompt.
    """
    if history.empty:
        return []

    # Slice the history to the date AT or before target_d (covers holidays).
    try:
        day_slice = history.xs(pd.Timestamp(target_d), level="Date", drop_level=False)
    except KeyError:
        return []

    # Find the previous trading day in our cached window.
    all_dates = sorted(history.index.get_level_values("Date").unique())
    target_ts = pd.Timestamp(target_d)
    prior_dates = [d for d in all_dates if d < target_ts]
    if not prior_dates:
        return []
    prev_ts = prior_dates[-1]
    prev_slice = history.xs(prev_ts, level="Date", drop_level=False)

    out: list[dict] = []
    for (_, ticker), today_row in day_slice.iterrows():
        try:
            prev_close = float(prev_slice.loc[(prev_ts, ticker), "Close"])
            curr_close = float(today_row["Close"])
            curr_vol   = float(today_row["Volume"])
        except (KeyError, TypeError, ValueError):
            continue

        if prev_close <= 0 or curr_close <= 0:
            continue
        pct = (curr_close - prev_close) / prev_close * 100.0
        if pct < GAINER_MIN_PCT or pct > GAINER_MAX_PCT:
            continue
        if curr_close < PM_PRICE_MIN:
            continue
        if curr_vol < PM_VOL_MIN:
            continue

        try:
            mcap = float(mcap_snap.loc[ticker, "market_cap"])
            avgv = float(mcap_snap.loc[ticker, "avg_volume"])
        except (KeyError, TypeError, ValueError):
            mcap, avgv = 0.0, 0.0
        if mcap < PM_MCAP_MIN:
            continue

        out.append({
            "ticker":       ticker,
            "percent_change": round(pct, 2),
            "price":        round(curr_close, 2),
            "market_cap_b": round(mcap / 1e9, 2),
            "avg_volume_m": round((avgv or curr_vol) / 1e6, 2),
            "today_volume_m": round(curr_vol / 1e6, 2),
            "prev_close":   round(prev_close, 2),
        })

    out.sort(key=lambda r: r["percent_change"], reverse=True)
    return out[:40]


# ─── Step 2 — historical earnings calendar ──────────────────────────────────

def _parse_mcap_str(s: str) -> float:
    """Mirror of agents._parse_mcap_str."""
    if not s:
        return 0.0
    s = str(s).strip().upper().replace("$", "").replace(",", "")
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


def _fetch_earnings_for_date(target_d: date) -> list[dict]:
    """
    Pull the Nasdaq earnings calendar for `target_d` and apply the live
    mid-cap pre-filter ($300M-$10B based on the API-provided MCap string).
    Volume validation is deferred to the in-memory mcap snapshot (so we don't
    rate-limit ourselves with a yfinance call per ticker).
    """
    url = (
        "https://api.nasdaq.com/api/calendar/earnings"
        f"?date={target_d.isoformat()}"
    )
    try:
        resp = requests.get(url, headers=NASDAQ_HEADERS, timeout=12)
        resp.raise_for_status()
        rows = (resp.json().get("data") or {}).get("rows") or []
    except Exception as exc:
        print(f"  [warn ] earnings API failed for {target_d}: {exc}")
        return []

    out: list[dict] = []
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        mcap = _parse_mcap_str(r.get("marketCap", ""))
        # Keep tickers with unparseable MCap so the in-memory snapshot can
        # validate them (matches live behaviour).
        if mcap and not (EARN_MCAP_MIN <= mcap <= EARN_MCAP_MAX):
            continue

        rt_raw = str(r.get("time") or "").lower()
        if "pre" in rt_raw:
            report_label = "BMO"
        elif "after" in rt_raw or "post" in rt_raw:
            report_label = "AMC"
        else:
            report_label = "TBD"

        out.append({
            "ticker":       sym,
            "company":      str(r.get("name") or "").strip(),
            "report_time":  report_label,
            "eps_forecast": str(r.get("epsForecast") or "").strip() or "N/A",
            "mcap_raw":     str(r.get("marketCap") or "").strip(),
            "mcap_b":       round(mcap / 1e9, 2) if mcap else None,
        })

    return out[:40]


def _enrich_earnings_with_snapshot(
    earnings: list[dict],
    mcap_snap: pd.DataFrame,
    history: pd.DataFrame,
    target_d: date,
) -> list[dict]:
    """
    Apply the same MCap/Volume gates and add today's % move so the prompt
    matches what the live earnings tool produces.
    """
    enriched: list[dict] = []

    try:
        day_slice = history.xs(
            pd.Timestamp(target_d), level="Date", drop_level=False
        )
    except KeyError:
        day_slice = pd.DataFrame()

    all_dates = sorted(history.index.get_level_values("Date").unique()) \
        if not history.empty else []
    prev_ts = next(
        (d for d in reversed(all_dates) if d < pd.Timestamp(target_d)),
        None,
    )

    for e in earnings:
        tkr = e["ticker"]
        try:
            mcap = float(mcap_snap.loc[tkr, "market_cap"])
            avgv = float(mcap_snap.loc[tkr, "avg_volume"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (EARN_MCAP_MIN <= mcap <= EARN_MCAP_MAX):
            continue
        if avgv < EARN_VOL_MIN:
            continue

        pct_change = None
        if not day_slice.empty and prev_ts is not None:
            try:
                today_close = float(
                    day_slice.loc[(pd.Timestamp(target_d), tkr), "Close"]
                )
                prev_close  = float(
                    history.loc[(prev_ts, tkr), "Close"]
                )
                if prev_close > 0:
                    pct_change = round(
                        (today_close - prev_close) / prev_close * 100.0, 2
                    )
            except (KeyError, TypeError, ValueError):
                pct_change = None

        enriched.append({
            **e,
            "market_cap_b": round(mcap / 1e9, 2),
            "avg_volume_m": round(avgv / 1e6, 2),
            "pct_change_today": pct_change,
        })

    enriched.sort(
        key=lambda r: abs(r.get("pct_change_today") or 0), reverse=True
    )
    return enriched


# ─── Step 3 — single-prompt synthesis ───────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a back-simulated multi-agent quant desk reproducing the live "
    "Catalyst Alpha pipeline (DataAgent -> PM -> Analyst -> Reporter) in a "
    "single deterministic call. You apply the SAME institutional filters and "
    "SAME rationale style the live pipeline uses. You may NOT use any "
    "knowledge of price action AFTER the simulated date — this is a back "
    "test, future leakage invalidates the run. Output ONLY a JSON array, "
    "no prose, no markdown."
)


def _build_simulation_prompt(
    target_d: date,
    earnings: list[dict],
    gainers: list[dict],
    prior_feedback: list[dict],
    *,
    top_n: int = 3,
) -> str:
    """
    The single-prompt that mirrors the four-agent live pipeline. The agent
    rules below are copied near-verbatim from agents._create_morning_tasks
    so the back-test reasons the same way the live system does.
    """

    earnings_lines = []
    for e in earnings[:30]:
        chg = (
            f"{e['pct_change_today']:+.2f}%"
            if e.get("pct_change_today") is not None else "N/A"
        )
        earnings_lines.append(
            f"{e['ticker']:<6} | {chg:>7} | "
            f"${e.get('market_cap_b', '?')}B | "
            f"{e.get('avg_volume_m', '?')}M vol | "
            f"{e['report_time']} | EPS:{e['eps_forecast']}"
        )
    earnings_block = "\n".join(earnings_lines) or "(no earnings reporters)"

    gainer_lines = []
    for g in gainers[:30]:
        gainer_lines.append(
            f"{g['ticker']:<6} | ${g['price']:.2f} | {g['percent_change']:+.2f}% | "
            f"${g['market_cap_b']}B | {g['today_volume_m']}M vol"
        )
    gainers_block = "\n".join(gainer_lines) or "(no gainers in +2%-+6% window)"

    feedback_lines = []
    for f in prior_feedback[:6]:
        snippet = (f.get("manager_feedback") or "").strip()[:240].replace("\n", " ")
        feedback_lines.append(
            f"- {f.get('date')} {f.get('ticker'):<6} "
            f"conf={f.get('confidence_score')} eod={f.get('actual_eod_change')}%  "
            f"{snippet}"
        )
    feedback_block = "\n".join(feedback_lines) or "(no prior feedback yet)"

    return f"""Today is {target_d.isoformat()}. Run the FULL Alpha pipeline mentally:
DataAgent -> PM -> Analyst -> Reporter, then output the Reporter JSON.

CRITICAL — back-test integrity:
You see only data that existed on {target_d.isoformat()}. Do NOT use any
knowledge of how these stocks moved AFTER this date.

═══════════════════════════════════════════════════════════════════════════
SECTION A — Mid-Cap Earnings Catalysts (Nasdaq calendar for {target_d.isoformat()})
filter: MCap $300M-$10B  |  AvgVol > 500K  |  sorted by abs(%chg today)
TICKER | %CHG | MCap | AvgVol | BMO/AMC | EPS forecast
{earnings_block}

═══════════════════════════════════════════════════════════════════════════
SECTION B — Pre-Market Gainers (+2% to +6% "Next Play" window)
filter (PM rules already applied): MCap > $2B  |  AvgVol > 1M  |  Price > $5
TICKER | $PRICE | %CHG | MCap | TodayVol
{gainers_block}

═══════════════════════════════════════════════════════════════════════════
SYMPATHY MAP (when stock X moves, these peers often move with it):
{_SYMPATHY_REF}

═══════════════════════════════════════════════════════════════════════════
MANAGER FEEDBACK (ONLY rows from BEFORE {target_d.isoformat()} — long-term memory):
{feedback_block}

═══════════════════════════════════════════════════════════════════════════
PIPELINE RULES YOU MUST APPLY:

PM filter (REJECT if any fails):
  - Market Cap < $2,000,000,000
  - Avg Volume < 1,000,000 shares/day
  - Price < $5
  - Catalyst is meme/social-only, not earnings/upgrade/macro/sympathy
  - Already up > 6.0% today (extension limit — "do not buy at the top")

Analyst workflow:
  1. Dedup tickers across SECTION A and SECTION B; merge their catalysts.
  2. Triage candidates → keep the 6-8 strongest setups.
  3. Score each: Catalyst Strength + Sympathy Strength + Technical Setup +
     Liquidity Score (each 1-10, average / 10 = raw confidence).
  4. Apply Manager-feedback adjustments (+/- up to 0.10 per Hard Rule).
  5. Pick the top {top_n}, sorted by confidence_score DESCENDING.
     NEVER output the same ticker twice.

RATIONALE FORMAT — every pick must be 3 lines:
  Catalyst: <specific event/data point — dual catalyst if both A+B applied>
  Data:     <exact %chg, MCap, Volume from the data above>
  Context:  <why it moves today / sympathy peer / Manager Lesson applied>

  FORBIDDEN words in rationale: "attractive", "solid", "robust", "good",
  "potential". Be terse, numeric, Bloomberg-flash style.

TARGET_PRICE rules (every pick MUST have one):
  - Strictly above the pick price.
  - At most 5x the pick price.
  - Anchor to evidence (prior swing high, gap-fill, IV-implied move,
    1.5-2x implied daily vol). Typical range: +4% to +25% above pick.
  - Higher confidence -> more ambitious target.
  - Round to 2 decimals.

═══════════════════════════════════════════════════════════════════════════
OUTPUT — exactly this shape, nothing else (no markdown, no prose):

[
  {{
    "ticker":           "XXXX",
    "pm_rationale":     "Catalyst: ...\\nData: ...\\nContext: ...",
    "confidence_score": 0.XX,
    "target_price":     12.34,
    "price_at_pick":    11.40
  }},
  ... exactly {top_n} entries, sorted by confidence_score DESC, unique tickers ...
]

If SECTION A and SECTION B together contain fewer than {top_n} usable
setups, output as many as you can justify (still as a JSON array). Never
invent a ticker that does not appear in the data above.
"""


# ─── Step 4 — call the LLM ──────────────────────────────────────────────────

def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    return s.strip()


def _extract_json_array(raw: str) -> list[dict]:
    """Best-effort JSON-array extraction from a possibly chatty LLM reply."""
    if not raw:
        return []
    s = _strip_code_fence(raw)
    try:
        obj = json.loads(s)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("predictions"), list):
            return obj["predictions"]
    except Exception:
        pass
    # Fallback: grab the first bracketed array anywhere in the string.
    m = re.search(r"\[[\s\S]*\]", s)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, list):
                return obj
        except Exception:
            return []
    return []


def _call_llm_for_day(prompt: str) -> list[dict]:
    """Call the same `claude_llm` instance the live Crew uses."""
    from agents import claude_llm  # deferred so DB-only commands stay light

    try:
        raw = claude_llm.call(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ]
        )
    except Exception as exc:
        print(f"  [warn ] LLM call failed: {exc}")
        return []
    return _extract_json_array(str(raw or ""))


# ─── Step 5 — orchestration ─────────────────────────────────────────────────

def _validate_pick(
    pick: dict,
    *,
    candidate_tickers: set[str],
    pick_price_lookup: dict[str, float],
) -> tuple[dict | None, str | None]:
    """
    Sanity-check one pick. Returns (cleaned_pick_or_None, reason_dropped).
    Cleaned pick has guaranteed: ticker, confidence_score, pm_rationale,
    price_at_pick (>0), target_price (None or strictly above pick).
    """
    tkr = str(pick.get("ticker") or "").strip().upper()
    if not tkr:
        return None, "missing ticker"
    if tkr not in candidate_tickers:
        return None, f"ticker {tkr} not in today's candidate list"

    try:
        conf = float(pick.get("confidence_score", 0.0))
    except (TypeError, ValueError):
        return None, "non-numeric confidence_score"
    conf = max(0.0, min(1.0, conf))

    pick_price = pick_price_lookup.get(tkr)
    if pick_price is None or pick_price <= 0:
        # Allow LLM-supplied price as fallback so we don't drop valid picks
        # for rare tickers missing from cache.
        try:
            pick_price = float(pick.get("price_at_pick") or 0.0)
        except (TypeError, ValueError):
            pick_price = 0.0
    if pick_price <= 0:
        return None, "no usable pick price"

    target_price = None
    try:
        tp_raw = pick.get("target_price")
        if tp_raw is not None:
            tp = float(tp_raw)
            if pick_price < tp <= pick_price * 5.0:
                target_price = round(tp, 2)
    except (TypeError, ValueError):
        target_price = None

    return {
        "ticker":           tkr,
        "confidence_score": round(conf, 2),
        "pm_rationale":     str(pick.get("pm_rationale") or "").strip(),
        "price_at_pick":    round(float(pick_price), 4),
        "target_price":     target_price,
    }, None


def simulate_day(
    target_d: date,
    *,
    run_id: str,
    history: pd.DataFrame,
    mcap_snap: pd.DataFrame,
    top_n: int = 3,
    progress: bool = True,
) -> dict:
    """
    Run the full single-prompt simulation for one trading day. Returns a
    summary dict and writes top-N picks to `simulated_predictions`.
    """
    summary = {
        "date":             target_d.isoformat(),
        "earnings_count":   0,
        "gainers_count":    0,
        "picks_returned":   0,
        "picks_saved":      0,
        "skipped_reasons":  [],
        "status":           "ok",
        "note":             "",
    }

    # Step 1 — gather candidates.
    earnings_raw = _fetch_earnings_for_date(target_d)
    earnings = _enrich_earnings_with_snapshot(
        earnings_raw, mcap_snap, history, target_d
    )
    gainers = _reconstruct_gainers_for_date(target_d, history, mcap_snap)
    summary["earnings_count"] = len(earnings)
    summary["gainers_count"]  = len(gainers)

    if not earnings and not gainers:
        summary["status"] = "skipped"
        summary["note"]   = "no candidates (likely market holiday)"
        if progress:
            print(f"  [skip ] {target_d}: no candidates")
        return summary

    # Step 2 — prior Manager feedback (only dates strictly < target_d).
    prior_feedback = db.get_prior_manager_feedback(target_d.isoformat())

    # Step 3 — prompt + LLM.
    prompt = _build_simulation_prompt(
        target_d, earnings, gainers, prior_feedback, top_n=top_n
    )
    picks = _call_llm_for_day(prompt)
    summary["picks_returned"] = len(picks)
    if not picks:
        summary["status"] = "no_picks"
        summary["note"]   = "LLM returned no usable picks"
        if progress:
            print(f"  [skip ] {target_d}: LLM produced no picks")
        return summary

    # Step 4 — validate against the candidate list.
    candidate_tickers: set[str] = (
        {e["ticker"] for e in earnings} | {g["ticker"] for g in gainers}
    )
    pick_price_lookup: dict[str, float] = {}
    for g in gainers:
        pick_price_lookup[g["ticker"]] = g["price"]
    # Earnings rows don't carry price; pull from the day's history slice.
    if earnings:
        try:
            day_slice = history.xs(
                pd.Timestamp(target_d), level="Date", drop_level=False
            )
            for e in earnings:
                if e["ticker"] in pick_price_lookup:
                    continue
                try:
                    px = float(
                        day_slice.loc[
                            (pd.Timestamp(target_d), e["ticker"]), "Close"
                        ]
                    )
                    if px > 0:
                        pick_price_lookup[e["ticker"]] = px
                except (KeyError, TypeError, ValueError):
                    continue
        except KeyError:
            pass

    cleaned: list[dict] = []
    seen_tickers: set[str] = set()
    for pick in picks:
        clean, reason = _validate_pick(
            pick,
            candidate_tickers=candidate_tickers,
            pick_price_lookup=pick_price_lookup,
        )
        if clean is None:
            summary["skipped_reasons"].append(
                f"{pick.get('ticker', '?')}: {reason}"
            )
            continue
        if clean["ticker"] in seen_tickers:
            summary["skipped_reasons"].append(
                f"{clean['ticker']}: duplicate in LLM output"
            )
            continue
        seen_tickers.add(clean["ticker"])
        cleaned.append(clean)

    cleaned.sort(key=lambda p: p["confidence_score"], reverse=True)
    cleaned = cleaned[:top_n]

    # Step 5 — persist.
    for rank, p in enumerate(cleaned, 1):
        db.insert_simulated_prediction(
            run_id=run_id,
            date=target_d.isoformat(),
            ticker=p["ticker"],
            pick_rank=rank,
            pm_rationale=p["pm_rationale"],
            confidence_score=p["confidence_score"],
            metrics_dict={
                "Price":         f"${p['price_at_pick']:.2f}",
                "Source":        (
                    "earnings+gainers" if p["ticker"] in {
                        e["ticker"] for e in earnings
                    } and p["ticker"] in {g["ticker"] for g in gainers}
                    else ("earnings" if p["ticker"] in {
                        e["ticker"] for e in earnings
                    } else "gainers")
                ),
                "ConfidenceRank": rank,
            },
            price_at_pick=p["price_at_pick"],
            target_price=p["target_price"],
        )
        summary["picks_saved"] += 1

    if progress:
        ticks = ", ".join(
            f"{p['ticker']}({p['confidence_score']:.2f})" for p in cleaned
        )
        print(
            f"  [ok   ] {target_d}: "
            f"{summary['earnings_count']} earn + {summary['gainers_count']} gain "
            f"-> {summary['picks_saved']} picks  {ticks or '(none)'}"
        )

    return summary


def simulate_range(
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    *,
    run_id: str | None = None,
    top_n: int = 3,
    skip_existing: bool = True,
    progress: bool = True,
) -> dict:
    """
    Loop simulate_day over every business day in [start, end].
    Re-runnable: by default skips dates that already have rows for run_id.
    """
    db.init_db()

    if run_id is None:
        run_id = f"sim_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_" \
                 f"{uuid.uuid4().hex[:6]}"
    if progress:
        print(f"\n=== Historical simulation  run_id={run_id}  "
              f"window={start} -> {end}  top_n={top_n} ===\n")

    # Pre-flight: pull universe history + mcap snapshot once.
    universe = _load_universe()
    if progress:
        print(
            f"[universe] {len(universe)} tickers (S&P 500 + NASDAQ-100)"
        )
    history = _download_universe_history(start, end, universe, progress=progress)
    if history.empty:
        return {
            "run_id": run_id, "status": "fatal",
            "note": "yfinance returned no history for the universe",
        }
    mcap_snap = _build_mcap_snapshot(universe, progress=progress)

    done_dates = (
        db.get_simulated_dates_done(run_id) if skip_existing else set()
    )

    business_days = pd.bdate_range(start=start, end=end).date.tolist()
    overall = {
        "run_id":         run_id,
        "start":          start,
        "end":            end,
        "trading_days":   len(business_days),
        "days_run":       0,
        "days_skipped":   0,
        "days_no_data":   0,
        "picks_saved":    0,
        "errors":         [],
    }

    for i, d in enumerate(business_days, 1):
        if d.isoformat() in done_dates:
            overall["days_skipped"] += 1
            if progress:
                print(f"  [skip ] {d}: already simulated for this run")
            continue
        try:
            summary = simulate_day(
                d, run_id=run_id, history=history, mcap_snap=mcap_snap,
                top_n=top_n, progress=progress,
            )
            overall["days_run"] += 1
            overall["picks_saved"] += summary["picks_saved"]
            if summary["status"] in ("skipped", "no_picks"):
                overall["days_no_data"] += 1
        except Exception as exc:
            overall["errors"].append(f"{d}: {exc}")
            if progress:
                print(f"  [error] {d}: {exc}")
        if progress and i % 10 == 0:
            print(f"  ... {i}/{len(business_days)} days processed")

    if progress:
        print()
        print(f"[done ] run_id={run_id}")
        print(f"        days_run={overall['days_run']}  "
              f"skipped={overall['days_skipped']}  "
              f"no_data={overall['days_no_data']}  "
              f"picks={overall['picks_saved']}")
        if overall["errors"]:
            print(f"        errors={len(overall['errors'])} (first 3):")
            for e in overall["errors"][:3]:
                print(f"          - {e}")

    return overall


# ─── Returns backfill (price_today + extended horizons) ─────────────────────

def compute_simulated_returns(run_id: str | None = None) -> dict:
    """Thin wrapper around db.update_simulated_returns for symmetry."""
    db.init_db()
    return db.update_simulated_returns(run_id=run_id)
