"""
Per-ticker view: all Catalyst predictions for one symbol, linked from the dashboard.
"""

import json as _json
import urllib.parse
from datetime import date as _date
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from database import get_all_tickers, get_predictions_by_ticker, init_db

from gf_quote_widget import render_google_finance_style_widget


def _fmt_ret(val, days_elapsed: int, window: int) -> str:
    if days_elapsed < window:
        return f"Pending ({window - days_elapsed}d)"
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "⏳"
    return f"{val:+.2f}%"


def _fmt_session_rth(val, rec, today: _date) -> str:
    """% from RTH open to close on pick day; intraday or future = pending."""
    if rec == today or rec > today:
        return "Pending ⏳"
    if val is not None and not (isinstance(val, float) and pd.isna(val)):
        return f"{float(val):+.2f}%"
    return "—"


def _safe_pct(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{float(x):.0%}"


def _parse_ticker_param() -> str:
    t = st.query_params.get("ticker")
    if t is None:
        return ""
    if isinstance(t, (list, tuple)) and t:
        return str(t[0] or "").strip()
    return str(t).strip()


def _google_finance_url(ticker: str) -> str:
    sym = str(ticker).strip().upper()
    if not sym:
        return "https://www.google.com/finance?hl=he&gl=il"
    path = urllib.parse.quote(sym, safe="")
    return f"https://www.google.com/finance/quote/{path}?hl=he&gl=il"


def _google_stock_search_url(ticker: str) -> str:
    sym = str(ticker).strip().upper()
    if not sym:
        return "https://www.google.com/search?q=stock"
    q = urllib.parse.quote_plus(f"{sym} stock")
    return f"https://www.google.com/search?q={q}"


st.set_page_config(
    page_title="Stock – Catalyst Alpha",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_db()

st.markdown(
    """
    <style>
    :root { --accent: #1D4ED8; }
    a.back-to-dash, a.back-to-dash:visited { color: var(--accent) !important; font-weight: 600 !important;
      text-decoration: none !important; }
    a.back-to-dash:hover { text-decoration: underline !important; }
    .stock-page-title { font-size: 1.75rem; font-weight: 700; color: #111827; margin: 0; }
    .stock-sub { color: #6B7280; font-size: 0.9rem; margin: 0.5rem 0 1.25rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<p style="margin:0 0 2px 0;">'
    '<a class="back-to-dash" href="/">⚡ Back to dashboard</a></p>',
    unsafe_allow_html=True,
)
st.divider()

p_from_url = _parse_ticker_param().upper()
all_syms = get_all_tickers()

if not all_syms:
    st.info("No predictions in the database yet. Run a pipeline or `python main.py --demo`.")
    st.stop()

opts: list[str] = [""] + all_syms
default_i = 0
if p_from_url and p_from_url in all_syms:
    default_i = 1 + all_syms.index(p_from_url)

sel = st.selectbox(
    "Ticker",
    options=opts,
    index=default_i,
    format_func=lambda x: "— choose —" if x == "" else x,
    # Include URL in key so navigation from a ticker link does not restore a stale pick.
    key=f"stock_ticker_picker_{p_from_url or 'none'}",
)

# Sync URL when user changes dropdown (or pick first load from list only)
if sel and sel.upper() != p_from_url:
    st.query_params["ticker"] = sel.upper()
    st.rerun()

ticker = (sel or p_from_url or "").strip().upper()
if p_from_url and p_from_url not in all_syms and all_syms:
    st.warning("Unknown or obsolete ticker in URL — pick a symbol below.")
    ticker = ""

if not ticker:
    st.markdown('<p class="stock-page-title">Company stock page</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="stock-sub">Choose a symbol or open a link from the main dashboard.</p>',
        unsafe_allow_html=True,
    )
    st.stop()

rows = get_predictions_by_ticker(ticker)
g_fin_url = _google_finance_url(ticker)
g_search_url = _google_stock_search_url(ticker)

render_google_finance_style_widget(ticker, g_fin_url, all_syms)

n = len(rows)
st.markdown(
    f'<p class="stock-sub">Catalyst history — {n} prediction(s) · '
    f'<a href="{g_search_url}" target="_blank" rel="noopener noreferrer">'
    f"Google search ↗</a> · "
    f'<a href="{g_fin_url}" target="_blank" rel="noopener noreferrer">'
    f"Google Finance ↗</a></p>",
    unsafe_allow_html=True,
)

today = _date.today()
table_rows: list[dict] = []
for r in rows:
    rec = _date.fromisoformat(r["date"])
    days = (today - rec).days
    conf = r.get("confidence_score")
    eod = r.get("actual_eod_change")
    conf_s = f"{float(conf):.0%}" if conf is not None and pd.notna(conf) else "—"
    if eod is not None and pd.notna(eod):
        eod_s = f"{float(eod):+.2f}%"
    else:
        eod_s = "⏳"
    pp = r.get("price_at_pick")
    pp_s = f"${float(pp):.2f}" if pp is not None and pd.notna(pp) else "—"
    sess_str = _fmt_session_rth(r.get("return_session"), rec, today)

    tgt = r.get("target_price")
    if tgt is not None and pd.notna(tgt):
        try:
            tgt_f = float(tgt)
            tgt_s = f"${tgt_f:.2f}"
        except (TypeError, ValueError):
            tgt_f, tgt_s = None, "—"
    else:
        tgt_f, tgt_s = None, "—"

    if tgt_f is None:
        hit_s = "—"
    else:
        raw_hit = str(r.get("target_hit_date") or "").strip()
        if raw_hit and raw_hit.upper() != "MISSED":
            hit_s = f"✓ {raw_hit}"
        elif raw_hit.upper() == "MISSED" or (rec + timedelta(days=30)) < today:
            hit_s = "✗ Missed"
        else:
            hit_s = "Pending"

    table_rows.append(
        {
            "Date": r["date"],
            "Strategy": r.get("strategy") or "alpha",
            "Conf.": conf_s,
            "Pick $": pp_s,
            "Target $": tgt_s,
            "Hit": hit_s,
            "EOD %": eod_s,
            "Session": sess_str,
            "T+3": _fmt_ret(r.get("return_3d"), days, 3),
            "T+7": _fmt_ret(r.get("return_7d"), days, 7),
            "T+14": _fmt_ret(r.get("return_14d"), days, 14),
            "T+30": _fmt_ret(r.get("return_30d"), days, 30),
        }
    )

if table_rows:
    st.subheader("Performance snapshot")
    st.dataframe(
        pd.DataFrame(table_rows),
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Predictions (newest first)")
if not rows:
    st.caption("No records for this ticker (unexpected if DB is consistent).")
    st.stop()

for r in rows:
    strat = (r.get("strategy") or "alpha").lower()
    badge = "Squeeze" if strat == "squeeze" else "Alpha"
    pp = r.get("price_at_pick")
    pp_line = f" — Pick **${float(pp):.2f}**" if pp is not None and pd.notna(pp) else ""
    st.markdown(
        f"**{r['date']}** · `{badge}` — conf. **{_safe_pct(r.get('confidence_score'))}**{pp_line}"
    )

    tgt = r.get("target_price")
    if tgt is not None and pd.notna(tgt):
        try:
            tgt_f = float(tgt)
        except (TypeError, ValueError):
            tgt_f = None
        if tgt_f is not None:
            try:
                rec_d = _date.fromisoformat(r["date"])
            except (TypeError, ValueError):
                rec_d = today
            ups_html = ""
            if pp is not None and pd.notna(pp) and float(pp) > 0:
                ups_pct = (tgt_f - float(pp)) / float(pp) * 100
                ups_html = f" ({ups_pct:+.1f}%)"
            raw_hit = str(r.get("target_hit_date") or "").strip()
            if raw_hit and raw_hit.upper() != "MISSED":
                hit_html = f"<span style='color:#15803D;'>✓ Hit on {raw_hit}</span>"
            elif raw_hit.upper() == "MISSED" or (rec_d + timedelta(days=30)) < today:
                hit_html = "<span style='color:#B91C1C;'>✗ Missed (T+30)</span>"
            else:
                days_left = max(0, (rec_d + timedelta(days=30) - today).days)
                hit_html = f"<span style='color:#1D4ED8;'>Pending — {days_left}d left</span>"
            st.markdown(
                f"**Sell target:** ${tgt_f:.2f}{ups_html} &nbsp;·&nbsp; {hit_html}",
                unsafe_allow_html=True,
            )

    st.markdown("**Rationale**")
    st.write(r.get("pm_rationale") or "_—_")
    mfb = r.get("manager_feedback")
    if mfb and str(mfb).strip().lower() not in ("none", "nan", ""):
        st.markdown("**EOD feedback**")
        st.write(mfb)
    m_json = r.get("metrics")
    if m_json and str(m_json).strip() and str(m_json) != "nan":
        try:
            md = _json.loads(m_json) if isinstance(m_json, str) else m_json
            if isinstance(md, dict) and md:
                with st.expander("Screen metrics (JSON)"):
                    st.json(md)
        except Exception:
            pass
    st.divider()

now = datetime.now()
st.caption(
    f"Data from local DB · {now:%Y-%m-%d %H:%M} · Not financial advice."
)
