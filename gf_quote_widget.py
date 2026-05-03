"""
Google Finance–style quote block for Streamlit (data via yfinance).
"""

from __future__ import annotations

import html as html_module
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
import yfinance as yf

GF_PERIOD_OPTIONS = ("1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX")


def _fi_as_dict(fi) -> dict:
    if fi is None:
        return {}
    if isinstance(fi, dict):
        return fi
    try:
        return {k: fi[k] for k in fi}  # type: ignore[union-attr]
    except Exception:
        return {}


def _fmt_compact_usd(n) -> str:
    if n is None or (isinstance(n, float) and pd.isna(n)):
        return "—"
    try:
        x = float(n)
    except (TypeError, ValueError):
        return "—"
    ax = abs(x)
    if ax >= 1e12:
        return f"{x / 1e12:.2f}T"
    if ax >= 1e9:
        return f"{x / 1e9:.2f}B"
    if ax >= 1e6:
        return f"{x / 1e6:.2f}M"
    if ax >= 1e3:
        return f"{x / 1e3:.2f}K"
    return f"{x:.2f}"


def _logo_url(website: str | None) -> str | None:
    if not website or not str(website).strip():
        return None
    w = str(website).strip()
    try:
        if not w.lower().startswith(("http://", "https://")):
            w = "https://" + w
        netloc = urlparse(w).netloc
        if not netloc:
            return None
        return f"https://logo.clearbit.com/{netloc}"
    except Exception:
        return None


def _exchange_label(info: dict, sym: str) -> str:
    ex = (info.get("fullExchangeName") or info.get("exchange") or "").strip()
    q = (info.get("quoteType") or "").upper()
    if ex and q and q not in ex.upper():
        return f"{ex}: {sym.upper()}"
    if ex:
        return f"{ex}: {sym.upper()}"
    return sym.upper()


@st.cache_data(ttl=3600, show_spinner=False)
def _yf_short_name(sym: str) -> str:
    try:
        inf = yf.Ticker(sym).info or {}
        return str(inf.get("shortName") or inf.get("longName") or sym)
    except Exception:
        return sym


@st.cache_data(ttl=90, show_spinner=False)
def _yf_snapshot(sym: str) -> dict:
    out: dict = {"ok": False, "sym": sym.upper()}
    try:
        t = yf.Ticker(sym)
        info = t.info or {}
        fi = _fi_as_dict(getattr(t, "fast_info", None))
        price = info.get("regularMarketPrice")
        if price is None:
            price = info.get("currentPrice")
        if price is None:
            price = fi.get("last_price")

        prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
        if prev is None:
            prev = fi.get("previous_close")

        chg = info.get("regularMarketChange")
        chgp = info.get("regularMarketChangePercent")
        if chgp is None and chg is not None and prev not in (None, 0):
            try:
                chgp = float(chg) / float(prev) * 100.0
            except Exception:
                chgp = None
        if chgp is None and price is not None and prev not in (None, 0):
            try:
                chgp = (float(price) - float(prev)) / float(prev) * 100.0
            except Exception:
                chgp = None

        currency = (info.get("currency") or fi.get("currency") or "USD").upper()

        ms = str(info.get("marketState") or "").upper()
        rmt = info.get("regularMarketTime")
        status_bits: list[str] = []
        if ms in ("CLOSED", "POSTPOST"):
            status_bits.append("Closed")
        elif ms == "PRE":
            status_bits.append("Pre-market")
        elif ms == "POST":
            status_bits.append("After hours")
        elif ms == "REGULAR":
            status_bits.append("Market open")
        tz = info.get("exchangeTimezoneName") or info.get("timeZoneFullName") or ""
        if isinstance(rmt, (int, float)) and rmt > 1e9:
            try:
                ts = pd.Timestamp(rmt, unit="s", tz="UTC")
                if tz:
                    ts = ts.tz_convert(tz)
                status_bits.append(ts.strftime("%d %b, %H:%M %Z"))
            except Exception:
                pass
        elif tz:
            status_bits.append(tz)

        pre_line = ""
        pm = info.get("preMarketPrice")
        if pm is not None and prev not in (None, 0):
            try:
                d = float(pm) - float(prev)
                dp = d / float(prev) * 100.0
                pre_line = f"Pre-market {float(pm):.2f} {d:+.2f} ({dp:+.2f}%)"
            except Exception:
                pre_line = ""

        open_ = info.get("regularMarketOpen") or info.get("open") or fi.get("open")
        high = info.get("regularMarketDayHigh") or info.get("dayHigh") or fi.get("day_high")
        low = info.get("regularMarketDayLow") or info.get("dayLow") or fi.get("day_low")
        mcap = info.get("marketCap") or fi.get("market_cap")
        pe = info.get("trailingPE") or info.get("forwardPE")
        div_y = info.get("dividendYield")
        if div_y is not None and isinstance(div_y, (int, float)) and div_y < 1:
            div_y = float(div_y) * 100.0
        w_hi = info.get("fiftyTwoWeekHigh")
        w_lo = info.get("fiftyTwoWeekLow")

        name = str(info.get("shortName") or info.get("longName") or sym.upper())
        website = info.get("website")

        out.update(
            ok=price is not None,
            name=name,
            exchange_label=_exchange_label(info, sym),
            currency=currency,
            price=float(price) if price is not None else None,
            chg=float(chg) if chg is not None else None,
            chgp=float(chgp) if chgp is not None else None,
            status_line=" · ".join(status_bits) if status_bits else "",
            pre_line=pre_line,
            open_=float(open_) if open_ is not None else None,
            high=float(high) if high is not None else None,
            low=float(low) if low is not None else None,
            mcap=mcap,
            pe=float(pe) if pe is not None else None,
            div_pct=float(div_y) if div_y is not None else None,
            w52_hi=float(w_hi) if w_hi is not None else None,
            w52_lo=float(w_lo) if w_lo is not None else None,
            website=website,
            market_state=ms,
        )
    except Exception:
        out["ok"] = False
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _yf_history(sym: str, range_key: str) -> pd.DataFrame:
    try:
        t = yf.Ticker(sym)
        rk = str(range_key).upper()
        if rk == "1D":
            return t.history(period="1d", interval="5m", auto_adjust=True)
        if rk == "5D":
            return t.history(period="5d", interval="15m", auto_adjust=True)
        if rk == "1M":
            return t.history(period="1mo", interval="1h", auto_adjust=True)
        if rk == "6M":
            return t.history(period="6mo", interval="1d", auto_adjust=True)
        if rk == "YTD":
            start = datetime(datetime.now().year, 1, 1)
            return t.history(start=start, interval="1d", auto_adjust=True)
        if rk == "1Y":
            return t.history(period="1y", interval="1d", auto_adjust=True)
        if rk == "5Y":
            return t.history(period="5y", interval="1wk", auto_adjust=True)
        return t.history(period="max", interval="1mo", auto_adjust=True)
    except Exception:
        return pd.DataFrame()


def _related_price_batch(symbols: list[str]) -> dict[str, tuple[float | None, float | None]]:
    out: dict[str, tuple[float | None, float | None]] = {
        s: (None, None) for s in symbols
    }
    if not symbols:
        return out
    try:
        raw = yf.download(
            " ".join(symbols),
            period="12d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception:
        return out
    if raw is None or raw.empty:
        return out
    closes = raw["Close"] if "Close" in raw.columns else None
    if closes is None:
        return out
    if isinstance(closes, pd.Series):
        sym = symbols[0]
        s = closes.dropna()
        if s.empty:
            return out
        last = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) > 1 else None
        pct = ((last - prev) / prev * 100.0) if prev not in (None, 0) else None
        out[sym] = (last, pct)
        return out
    for sym in symbols:
        if sym not in closes.columns:
            continue
        s = closes[sym].dropna()
        if s.empty:
            continue
        last = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) > 1 else None
        pct = ((last - prev) / prev * 100.0) if prev not in (None, 0) else None
        out[sym] = (last, pct)
    return out


def _quarterly_revenue_snippet(sym: str) -> tuple[str | None, str | None]:
    try:
        t = yf.Ticker(sym)
        inc = getattr(t, "quarterly_income_stmt", None)
        if inc is None or getattr(inc, "empty", True):
            return None, None
        rev_row = None
        for idx in inc.index:
            low = str(idx).lower()
            if "total" in low and "revenue" in low:
                rev_row = inc.loc[idx]
                break
        if rev_row is None:
            return None, None
        rev_row = rev_row.dropna()
        if rev_row.empty or len(rev_row) < 2:
            return None, None
        latest = float(rev_row.iloc[0])
        prior = float(rev_row.iloc[1])
        end = rev_row.index[0]
        try:
            if hasattr(end, "month"):
                q = (int(end.month) - 1) // 3 + 1
                label = f"{end.year} Q{q}"
            else:
                label = str(end)[:10]
        except Exception:
            label = "Latest quarter"
        yoy = (latest - prior) / prior * 100.0 if prior else None
        body = f"{_fmt_compact_usd(latest)}"
        if yoy is not None:
            col = "#137333" if yoy >= 0 else "#C5221F"
            body += f' <span style="color:{col};font-weight:500;">{yoy:+.2f}% Y/Y</span>'
        body += " Revenue"
        return f"Quarterly revenue ({label})", body
    except Exception:
        return None, None


def render_google_finance_style_widget(sym: str, g_fin_url: str, universe_syms: list[str]) -> None:
    snap = _yf_snapshot(sym)
    name_esc = html_module.escape(snap.get("name") or sym)
    ex_esc = html_module.escape(str(snap.get("exchange_label") or sym))
    cur = snap.get("currency") or "USD"
    price = snap.get("price")
    chgp = snap.get("chgp")
    up = chgp is not None and float(chgp) >= 0
    delta_col = "#137333" if up else "#C5221F"
    logo = _logo_url(snap.get("website"))

    st.markdown(
        """
        <style>
        .gf-shell {
          font-family: 'Google Sans','Roboto',Arial,Helvetica,sans-serif;
          background:#fff;
          border:1px solid #dadce0;
          border-radius:8px;
          padding:20px 20px 16px;
          margin-bottom:12px;
          color:#202124;
        }
        .gf-head { display:flex; align-items:center; gap:12px; margin-bottom:8px; }
        .gf-logo {
          width:40px; height:40px; border-radius:8px; object-fit:contain;
          background:#fff; border:1px solid #eceff1;
        }
        .gf-co { font-size:1.05rem; font-weight:400; color:#202124; margin:0; line-height:1.3; }
        .gf-ex { font-size:0.82rem; color:#5f6368; margin:2px 0 0 0; }
        .gf-big-price { font-size:1.65rem; font-weight:400; letter-spacing:-0.5px; }
        .gf-delta { font-size:0.95rem; font-weight:500; margin-left:8px; }
        .gf-sub { font-size:0.78rem; color:#5f6368; margin-top:6px; line-height:1.4; }
        .gf-stat-grid {
          display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
          gap:10px 18px; margin-top:4px; font-size:0.8rem;
        }
        .gf-stat-label { color:#5f6368; display:block; margin-bottom:2px; }
        .gf-stat-val { color:#202124; font-weight:500; }
        .gf-side-wrap {
          border:1px solid #dadce0; border-radius:8px; padding:12px 14px; background:#fafafa;
        }
        .gf-side-title { font-size:0.72rem; font-weight:600; color:#5f6368; text-transform:uppercase;
          letter-spacing:0.5px; margin:0 0 10px 0; }
        .gf-rel-item {
          display:flex; justify-content:space-between; align-items:baseline;
          padding:8px 0; border-bottom:1px solid #eceff1; font-size:0.85rem; gap:8px;
        }
        .gf-rel-item:last-child { border-bottom:none; }
        .gf-rel-name { color:#1a73e8; font-weight:500; text-decoration:none; word-break:break-word; }
        .gf-rel-name:hover { text-decoration:underline; }
        .gf-small-card {
          margin-top:12px; padding:10px 12px; border:1px solid #dadce0; border-radius:8px;
          background:#fff; font-size:0.78rem;
        }
        .gf-small-card h4 { margin:0 0 6px 0; font-size:0.72rem; font-weight:600; color:#5f6368;
          text-transform:uppercase; letter-spacing:0.4px; }
        .gf-more {
          display:block; text-align:center; margin-top:14px; padding:10px;
          background:#f1f3f4; border-radius:24px; font-size:0.82rem; color:#1a73e8;
          font-weight:500; text-decoration:none;
        }
        .gf-more:hover { background:#e8eaed; }
        @media (max-width:900px) {
          .gf-stat-grid { grid-template-columns:repeat(2, minmax(0,1fr)); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    img_html = ""
    if logo:
        img_html = (
            f'<img class="gf-logo" src="{html_module.escape(logo)}" alt="" '
            'onerror="this.style.display=\'none\'" />'
        )
    else:
        img_html = (
            '<div class="gf-logo" style="display:flex;align-items:center;justify-content:center;'
            f'font-weight:600;font-size:1.1rem;color:#5f6368;">{html_module.escape(sym[0])}</div>'
        )

    price_html = "—"
    if price is not None:
        price_html = f"{float(price):,.2f} {html_module.escape(cur)}"

    delta_html = ""
    if chgp is not None:
        arr = "▲" if up else "▼"
        chg_abs = snap.get("chg")
        if chg_abs is not None:
            delta_html = (
                f'<span class="gf-delta" style="color:{delta_col};">{arr} '
                f"{float(chg_abs):+.2f} ({float(chgp):+.2f}%) today</span>"
            )
        else:
            delta_html = (
                f'<span class="gf-delta" style="color:{delta_col};">{arr} '
                f"({float(chgp):+.2f}%) today</span>"
            )

    sub = html_module.escape(snap.get("status_line") or "")
    pre = snap.get("pre_line") or ""
    if pre:
        sub = f"{sub}<br/>{html_module.escape(pre)}" if sub else html_module.escape(pre)

    open_s = _fmt_compact_usd(snap.get("open_"))
    hi_s = _fmt_compact_usd(snap.get("high"))
    lo_s = _fmt_compact_usd(snap.get("low"))
    mc_s = _fmt_compact_usd(snap.get("mcap"))
    pe_s = f"{snap.get('pe'):.2f}" if snap.get("pe") is not None else "—"
    div_s = "—"
    if snap.get("div_pct") is not None:
        div_s = f"{float(snap['div_pct']):.2f}%"
    w_hi = f"{snap['w52_hi']:.2f}" if snap.get("w52_hi") is not None else "—"
    w_lo = f"{snap['w52_lo']:.2f}" if snap.get("w52_lo") is not None else "—"

    col_main, col_side = st.columns([2.15, 1.0], gap="large")

    with col_main:
        st.markdown(
            f"""
            <div class="gf-shell">
              <div class="gf-head">
                {img_html}
                <div>
                  <p class="gf-co">{name_esc}</p>
                  <p class="gf-ex">{ex_esc}</p>
                </div>
              </div>
              <div>
                <span class="gf-big-price">{price_html}</span>
                {delta_html}
              </div>
              <div class="gf-sub">{sub}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        try:
            period = st.segmented_control(
                "Range",
                list(GF_PERIOD_OPTIONS),
                default="1D",
                key=f"gf_period_{sym}",
                label_visibility="collapsed",
            )
        except Exception:
            period = st.radio(
                "Range",
                list(GF_PERIOD_OPTIONS),
                horizontal=True,
                index=0,
                key=f"gf_period_fallback_{sym}",
                label_visibility="collapsed",
            )
        if isinstance(period, (list, tuple)):
            period = str(period[0]) if period else "1D"
        if not period:
            period = "1D"

        hist = _yf_history(sym, str(period))
        if hist is not None and not hist.empty and "Close" in hist.columns:
            chart_df = hist[["Close"]].rename(columns={"Close": "Price"})
            st.area_chart(chart_df, height=320, color="#34a853")
        else:
            st.caption("Chart unavailable for this range (no data).")

        st.markdown(
            f"""
            <div class="gf-shell" style="padding-top:12px;">
              <div class="gf-stat-grid">
                <div><span class="gf-stat-label">Open</span><span class="gf-stat-val">{open_s}</span></div>
                <div><span class="gf-stat-label">Mkt cap</span><span class="gf-stat-val">{mc_s}</span></div>
                <div><span class="gf-stat-label">Dividend</span><span class="gf-stat-val">{div_s}</span></div>
                <div><span class="gf-stat-label">High</span><span class="gf-stat-val">{hi_s}</span></div>
                <div><span class="gf-stat-label">P/E ratio</span><span class="gf-stat-val">{pe_s}</span></div>
                <div><span class="gf-stat-label">Qtrly Div Amt</span><span class="gf-stat-val">—</span></div>
                <div><span class="gf-stat-label">Low</span><span class="gf-stat-val">{lo_s}</span></div>
                <div><span class="gf-stat-label">52-wk high</span><span class="gf-stat-val">{w_hi}</span></div>
                <div><span class="gf-stat-label">52-wk low</span><span class="gf-stat-val">{w_lo}</span></div>
              </div>
              <a class="gf-more" href="{html_module.escape(g_fin_url, quote=True)}"
                 target="_blank" rel="noopener noreferrer">More about {name_esc} ›</a>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_side:
        others = sorted([x for x in universe_syms if x != sym])[:4]
        rel_parts: list[str] = [
            '<div class="gf-side-wrap"><p class="gf-side-title">Related in app</p>'
        ]
        if not others:
            rel_parts.append('<p style="margin:0;color:#5f6368;font-size:0.85rem;">No other tracked tickers.</p>')
        else:
            px_map = _related_price_batch(others)
            for o in others:
                nm = _yf_short_name(o)
                nm_esc = html_module.escape(nm)
                o_esc = html_module.escape(o)
                last, pct = px_map.get(o, (None, None))
                px_s = f"{last:.2f} USD" if last is not None else "—"
                if pct is not None:
                    col = "#137333" if pct >= 0 else "#C5221F"
                    pct_s = f'<span style="color:{col};font-weight:500;">{pct:+.2f}%</span>'
                else:
                    pct_s = "<span>—</span>"
                rel_parts.append(
                    f'<div class="gf-rel-item">'
                    f'<a class="gf-rel-name" href="/Stock?ticker={o_esc}">{nm_esc}</a>'
                    f'<span style="white-space:nowrap;">{html_module.escape(px_s)} {pct_s}</span>'
                    f"</div>"
                )
        rel_parts.append("</div>")
        st.markdown("".join(rel_parts), unsafe_allow_html=True)

        q_title, q_body = _quarterly_revenue_snippet(sym)
        if q_title and q_body:
            st.markdown(
                f"""
                <div class="gf-small-card">
                  <h4>{html_module.escape(q_title)}</h4>
                  <div>{q_body}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.caption("Quote data via yfinance · delay may apply.")

    if not snap.get("ok"):
        st.info("Live quote unavailable — check the symbol or try again. Catalyst data below is unchanged.")
