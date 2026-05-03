"""
3_Simulation.py — Catalyst Alpha v1.0

Streamlit dashboard for the historical back-test (Sep 2025 → Mar 2026).
Reads from the `simulated_predictions` table populated by:

    python main.py --simulate-history
    python main.py --simulate-returns
"""

from __future__ import annotations

import html
import urllib.parse
from datetime import date as _date, datetime, timedelta

import pandas as pd
import streamlit as st

from database import (
    get_simulated_predictions,
    get_simulated_run_ids,
    init_db,
)


st.set_page_config(
    page_title="Historical Simulation – Catalyst Alpha",
    page_icon="🕰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# ─── Shared CSS (lighter copy of app.py) ─────────────────────────────────────

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
    :root {
        --bg:         #F8F9FA;
        --bg-card:    #FFFFFF;
        --bg-card2:   #F3F4F6;
        --accent:     #6D28D9;
        --accent-lt:  #F5F3FF;
        --green:      #00873C;
        --green-bg:   #F0FDF4;
        --red:        #EB0F29;
        --red-bg:     #FFF1F2;
        --amber:      #B45309;
        --amber-bg:   #FFFBEB;
        --text-main:  #111827;
        --text-sub:   #374151;
        --text-muted: #6B7280;
        --border:     #E0E3E7;
        --mono:       'IBM Plex Mono', 'Courier New', monospace;
    }
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background-color: var(--bg); }
    .block-container { padding-top: 1.5rem !important; max-width: 1400px; }
    .sim-header {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-left: 4px solid var(--accent);
        border-radius: 10px;
        padding: 18px 26px 14px;
        margin-bottom: 20px;
    }
    .sim-header h1 {
        color: var(--text-main);
        font-size: 1.5rem;
        font-weight: 700;
        margin: 0 0 2px 0;
    }
    .sim-header h1 span { color: var(--accent); }
    .sim-header p  { color: var(--text-muted); margin: 0; font-size: 0.82rem; }
    .sim-card {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 12px;
    }
    .sim-rank {
        display: inline-block;
        background: var(--accent-lt);
        color: var(--accent);
        border: 1px solid #DDD6FE;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.72rem;
        font-weight: 700;
        font-family: var(--mono);
        margin-right: 10px;
    }
    a.sim-ticker, a.sim-ticker:visited {
        font-family: var(--mono);
        font-size: 1.3rem;
        font-weight: 700;
        color: var(--accent);
        text-decoration: none;
        letter-spacing: -0.3px;
    }
    a.sim-ticker:hover { text-decoration: underline; }
    .sim-meta { color: var(--text-muted); font-size: 0.82rem; }
    .sim-section-label {
        color: var(--text-muted);
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        margin: 12px 0 6px 0;
    }
    .sim-rationale {
        color: var(--text-sub);
        font-size: 0.875rem;
        line-height: 1.65;
        white-space: pre-wrap;
    }
    .tag-hit, .tag-miss, .tag-near, .tag-pending {
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 0.75rem;
        font-weight: 600;
        font-family: var(--mono);
    }
    .tag-hit { background: var(--green-bg); color: var(--green); border: 1px solid #BBF7D0; }
    .tag-miss { background: var(--red-bg); color: var(--red); border: 1px solid #FECDD3; }
    .tag-near { background: var(--amber-bg); color: var(--amber); border: 1px solid #FDE68A; }
    .tag-pending { background: var(--bg-card2); color: var(--text-muted); border: 1px solid var(--border); }
    div[data-testid="metric-container"] {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 12px 14px;
    }
    div[data-testid="metric-container"] label {
        color: var(--text-muted) !important;
        font-size: 0.72rem !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.6px;
    }
    div[data-testid="stMetricValue"] {
        color: var(--text-main) !important;
        font-size: 1.25rem !important;
        font-weight: 700 !important;
        font-family: var(--mono) !important;
    }
    .empty-sim {
        background: var(--bg-card);
        border: 1px dashed var(--border);
        border-radius: 10px;
        padding: 48px 40px;
        text-align: center;
        color: var(--text-muted);
    }
    .empty-sim h3 { color: var(--text-sub); font-size: 1rem; font-weight: 600; }
    .empty-sim code {
        background: var(--bg-card2);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 1px 6px;
        font-family: var(--mono);
        font-size: 0.82rem;
        color: var(--accent);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Header ──────────────────────────────────────────────────────────────────

st.markdown(
    f"""
    <div class="sim-header">
        <h1>🕰️ <span>Historical</span> Simulation &nbsp;·&nbsp; Catalyst Alpha</h1>
        <p>
            What would the live Alpha pipeline have recommended every day from
            Sep 2025 through Mar 2026? Top 3 picks per trading day, scored by
            confidence, with returns at T+1 / T+3 / T+7 / T+30 / T+90 / T+180
            and the latest close.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _google_finance_url(ticker: str) -> str:
    sym = str(ticker).strip().upper()
    if not sym:
        return "https://www.google.com/finance?hl=he&gl=il"
    path = urllib.parse.quote(sym, safe="")
    return f"https://www.google.com/finance/quote/{path}?hl=he&gl=il"


def _fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{float(val):+.2f}%"


def _fmt_pct_with_pending(val, rec_d: _date, today_d: _date, window: int) -> str:
    if val is not None and not (isinstance(val, float) and pd.isna(val)):
        return f"{float(val):+.2f}%"
    days_elapsed = (today_d - rec_d).days
    if days_elapsed < window:
        return f"⏳ {window - days_elapsed}d left"
    return "—"


def _status_pill(eod_val) -> tuple[str, str]:
    if eod_val is None or (isinstance(eod_val, float) and pd.isna(eod_val)):
        return "⏳ Pending EOD", "tag-pending"
    v = float(eod_val)
    if v >= 6.0:
        return f"✅ HIT (+{v:.1f}%)", "tag-hit"
    if v >= 0:
        return f"⚠️ Near (+{v:.1f}%)", "tag-near"
    return f"❌ Miss ({v:.1f}%)", "tag-miss"


def _target_status_pill(
    target,
    pick,
    rec_date: _date,
    today_d: _date,
    hit_date_raw,
) -> dict:
    has_target = target is not None and pd.notna(target) and float(target) > 0
    has_pick   = pick   is not None and pd.notna(pick)   and float(pick)   > 0
    if not has_target:
        return {
            "target_str": "—", "upside_str": "",
            "pill_text": "No target", "pill_bg": "rgba(120,120,120,0.10)",
            "pill_fg": "#6B7280",
        }

    target_f = float(target)
    target_str = f"${target_f:.2f}"
    upside_str = ""
    if has_pick:
        ups = (target_f - float(pick)) / float(pick) * 100.0
        upside_str = f"+{ups:.1f}%" if ups >= 0 else f"{ups:.1f}%"

    hit_date_s = str(hit_date_raw or "").strip()
    window_end = rec_date + timedelta(days=30)

    if hit_date_s and hit_date_s.upper() != "MISSED":
        return {
            "target_str": target_str, "upside_str": upside_str,
            "pill_text": f"✓ Hit on {hit_date_s}",
            "pill_bg": "rgba(34,197,94,0.14)", "pill_fg": "#15803D",
        }
    if hit_date_s.upper() == "MISSED" or window_end < today_d:
        return {
            "target_str": target_str, "upside_str": upside_str,
            "pill_text": "✗ Missed (T+30)",
            "pill_bg": "rgba(239,68,68,0.14)", "pill_fg": "#B91C1C",
        }
    days_left = max(0, (window_end - today_d).days)
    return {
        "target_str": target_str, "upside_str": upside_str,
        "pill_text": f"Pending — {days_left}d left",
        "pill_bg": "rgba(109,40,217,0.12)", "pill_fg": "#6D28D9",
    }


# ─── Sidebar: run picker ─────────────────────────────────────────────────────

runs = get_simulated_run_ids()
if not runs:
    st.markdown(
        '<div class="empty-sim">'
        "<h3>No simulation runs yet</h3>"
        "<p>Generate the historical back-test by running:</p>"
        "<p><code>python main.py --simulate-history</code></p>"
        "<p>Then refresh this page. Each run is tagged with a unique "
        "<code>run_id</code> so you can compare different prompt or window "
        "variants side by side.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.stop()

with st.sidebar:
    st.markdown("### 🕰️ Simulation runs")
    st.caption(f"{len(runs)} run(s) in the database")
    run_options = [
        f"{r['run_id']}  ({r['picks']} picks · {r['first_date']} → {r['last_date']})"
        for r in runs
    ]
    sel_idx = st.selectbox(
        "Run", options=list(range(len(runs))),
        format_func=lambda i: run_options[i],
        index=0, key="sim_run_idx",
    )
    selected_run = runs[sel_idx]
    st.divider()
    st.caption("Backfill / refresh")
    st.code(
        f"python main.py --simulate-returns "
        f"--run-id {selected_run['run_id']}",
        language="bash",
    )
    st.caption(
        "Re-runs are safe: existing return cells aren't overwritten, "
        "but `price_today` is always refreshed."
    )

run_id = selected_run["run_id"]


# ─── Load picks ──────────────────────────────────────────────────────────────

raw = get_simulated_predictions(run_id=run_id)
if not raw:
    st.markdown(
        '<div class="empty-sim">'
        f"<h3>Run <code>{run_id}</code> has no picks</h3>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.stop()

df = pd.DataFrame(raw)
for col in (
    "confidence_score", "price_at_pick", "target_price",
    "return_session", "actual_eod_change",
    "return_3d", "return_7d", "return_14d", "return_30d",
    "return_90d", "return_180d", "price_today", "pick_rank",
):
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

# Derived columns: target upside %, total return-since-pick (using price_today).
_pick = pd.to_numeric(df["price_at_pick"], errors="coerce")
_tgt  = pd.to_numeric(df["target_price"],  errors="coerce")
df["target_upside_pct"] = (
    (_tgt - _pick) / _pick * 100.0
).where(_pick.gt(0))
_today_px = pd.to_numeric(df["price_today"], errors="coerce")
df["return_today_pct"] = (
    (_today_px - _pick) / _pick * 100.0
).where(_pick.gt(0))


# ─── Top-of-page metrics ─────────────────────────────────────────────────────

today_d = _date.today()
total = int(df.shape[0])
resolved_eod = int(df["actual_eod_change"].notna().sum())
hits         = int((df["actual_eod_change"] >= 6.0).sum())
hit_rate     = f"{hits/resolved_eod*100:.0f}%" if resolved_eod else "—"

avg_eod   = df["actual_eod_change"].dropna()
avg_t30   = df["return_30d"].dropna()
avg_t90   = df["return_90d"].dropna()
avg_t180  = df["return_180d"].dropna()
avg_today = df["return_today_pct"].dropna()


def _avg_str(s: pd.Series) -> str:
    return f"{s.mean():+.2f}%" if not s.empty else "—"


m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total picks", total)
m2.metric("Resolved EOD", resolved_eod)
m3.metric("Hit rate ≥ 6%", hit_rate)
m4.metric("Avg T+30", _avg_str(avg_t30))
m5.metric("Avg T+90", _avg_str(avg_t90))
m6.metric("Avg T+180", _avg_str(avg_t180))

m7, m8, m9, m10 = st.columns(4)
m7.metric("Avg EOD", _avg_str(avg_eod))
m8.metric("Avg vs today", _avg_str(avg_today))
m9.metric("Window start", str(df["date"].min()))
m10.metric("Window end",   str(df["date"].max()))

st.divider()


# ─── Filters (aligned with main dashboard / production) ─────────────────────

_rec_date = pd.to_datetime(df["date"], errors="coerce").dt.date
_valid_rd = _rec_date.dropna()
d_lo = _valid_rd.min() if not _valid_rd.empty else _date.today()
d_hi = _valid_rd.max() if not _valid_rd.empty else _date.today()

sort_by_labels = {
    "Recommendation date": "date",
    "Confidence":          "confidence_score",
    "Pick rank":           "pick_rank",
    "EOD":                 "actual_eod_change",
    "T+3":                 "return_3d",
    "T+7":                 "return_7d",
    "T+14":                "return_14d",
    "T+30":                "return_30d",
    "T+90":                "return_90d",
    "T+180":               "return_180d",
    "Vs today":            "return_today_pct",
    "Target upside %":     "target_upside_pct",
    "Ticker (A–Z)":        "ticker",
}
sort_keys = list(sort_by_labels.keys())

st.caption(
    "**Date range** · **Sort** · **Order** · **EOD status** · **Ticker** · "
    "**Min conf.** % · **T+3 / T+7 / T+14** (checkbox = has measured return)"
)
fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(
    [1.45, 1.05, 0.82, 1.05, 1.0, 0.72], gap="small"
)
with fc1:
    date_rng = st.date_input(
        "Date range",
        value=(d_lo, d_hi),
        min_value=d_lo,
        max_value=d_hi,
        key="sim_drng",
        label_visibility="collapsed",
        help="Only picks whose simulation date falls in this range (inclusive).",
    )
with fc2:
    sort_lbl = st.selectbox(
        "Sort",
        sort_keys,
        index=0,
        key="sim_sort",
        label_visibility="collapsed",
        help="Order rows by the chosen column. Pending values sort to the end.",
    )
with fc3:
    order = st.selectbox(
        "Ord",
        ["High to low", "Low to high"],
        key="sim_order",
        label_visibility="collapsed",
        help="For dates: High = newest first. For ticker: High = Z→A.",
    )
with fc4:
    status_f = st.selectbox(
        "Status",
        [
            "All",
            "Hit (≥6%)",
            "Near (0% to <6%)",
            "Miss (<0%)",
            "Pending EOD",
        ],
        key="sim_status",
        label_visibility="collapsed",
    )
with fc5:
    tick_q = st.text_input(
        "Tkr",
        "",
        key="sim_tickerq",
        label_visibility="collapsed",
        placeholder="Ticker",
    )
with fc6:
    min_conf_pct = st.number_input(
        "Min conf %",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=1.0,
        key="sim_minconf",
        label_visibility="collapsed",
        help="Show only picks with confidence ≥ this value (0 = no filter).",
    )

cx1, cx2, cx3 = st.columns([0.62, 0.62, 0.62], gap="small")
with cx1:
    req_data_3 = st.checkbox(
        "T+3",
        value=False,
        key="sim_has3",
        help="Only rows where T+3 return is filled (not pending).",
    )
with cx2:
    req_data_7 = st.checkbox(
        "T+7",
        value=False,
        key="sim_has7",
        help="Only rows where T+7 return is filled (not pending).",
    )
with cx3:
    req_data_14 = st.checkbox(
        "T+14",
        value=False,
        key="sim_has14",
        help="Only rows where T+14 return is filled (not pending).",
    )

if isinstance(date_rng, tuple) and len(date_rng) == 2:
    lo_d, hi_d = date_rng[0], date_rng[1]
    if lo_d is not None and hi_d is not None:
        if lo_d > hi_d:
            lo_d, hi_d = hi_d, lo_d
        view = df[
            _rec_date.notna() & (_rec_date >= lo_d) & (_rec_date <= hi_d)
        ]
    elif lo_d is not None:
        view = df[_rec_date.notna() & (_rec_date >= lo_d)]
    elif hi_d is not None:
        view = df[_rec_date.notna() & (_rec_date <= hi_d)]
    else:
        view = df.copy()
elif date_rng is not None and not isinstance(date_rng, tuple):
    view = df[_rec_date == date_rng].copy()
else:
    view = df.copy()

eod = view["actual_eod_change"]
if status_f == "Hit (≥6%)":
    view = view[eod.notna() & (eod >= 6.0)]
elif status_f == "Pending EOD":
    view = view[eod.isna()]
elif status_f == "Near (0% to <6%)":
    view = view[eod.notna() & (eod >= 0) & (eod < 6.0)]
elif status_f == "Miss (<0%)":
    view = view[eod.notna() & (eod < 0)]

tq = (tick_q or "").strip().upper()
if tq:
    view = view[
        view["ticker"].astype(str).str.upper().str.contains(tq, na=False)
    ]

if min_conf_pct and float(min_conf_pct) > 0:
    thr = float(min_conf_pct) / 100.0
    view = view[
        view["confidence_score"].notna() & (view["confidence_score"] >= thr)
    ]

if req_data_3:
    view = view[view["return_3d"].notna()]
if req_data_7:
    view = view[view["return_7d"].notna()]
if req_data_14:
    view = view[view["return_14d"].notna()]

sort_col = sort_by_labels[sort_lbl]
asc = order == "Low to high"
if sort_col == "ticker":
    view = view.sort_values(
        by="ticker", ascending=asc, kind="mergesort", key=lambda s: s.str.upper()
    )
else:
    view = view.sort_values(
        by=sort_col, ascending=asc, na_position="last", kind="mergesort"
    )

view = view.reset_index(drop=True)


# ─── $1,000 per pick simulation (right sidebar block) ────────────────────────

def _sim_thousand(series: pd.Series) -> tuple[str, str, int, int]:
    s_all = pd.to_numeric(series, errors="coerce")
    n_total = int(s_all.shape[0])
    s = s_all.dropna()
    n_resolved = int(s.shape[0])
    if n_resolved == 0:
        return ("—", "—", 0, n_total)
    pnl = (s / 100.0 * 1000.0).sum()
    invested = 1000.0 * n_resolved
    pct = pnl / invested * 100.0 if invested else 0.0
    sign = "+" if pnl >= 0 else ""
    return (f"{sign}${pnl:,.2f}", f"{sign}{pct:.2f}%", n_resolved, n_total)


with st.sidebar:
    st.divider()
    st.markdown("### 💵 If you'd put $1,000 on every pick")
    for label, col in [
        ("EOD (same day)",  "actual_eod_change"),
        ("Hold to T+30",    "return_30d"),
        ("Hold to T+90",    "return_90d"),
        ("Hold to T+180",   "return_180d"),
        ("Hold until today", "return_today_pct"),
    ]:
        pnl, pct, nres, ntot = _sim_thousand(view[col])
        with st.container(border=True):
            st.caption(label)
            color = (
                "var(--green)" if pnl.startswith("+") else
                ("var(--red)" if pnl.startswith("-") else "var(--text-muted)")
            )
            st.markdown(
                f"<div style='font-family:var(--mono);font-size:1.15rem;"
                f"font-weight:700;color:{color};'>{pnl}</div>"
                f"<div style='color:var(--text-muted);font-size:0.78rem;'>"
                f"{pct} on {nres}/{ntot} resolved</div>",
                unsafe_allow_html=True,
            )


# ─── Per-day cards ──────────────────────────────────────────────────────────

st.markdown(
    f"<p class='sim-meta'>Showing <b>{len(view)}</b> picks · run "
    f"<code>{html.escape(run_id)}</code></p>",
    unsafe_allow_html=True,
)

if view.empty:
    st.info("No picks match the current filters.")
else:
    # Group by date so each trading day is a clear visual block.
    for d_str, day_rows in view.groupby("date", sort=False):
        try:
            rec_date = _date.fromisoformat(str(d_str))
        except ValueError:
            continue
        day_rows = day_rows.sort_values("pick_rank", ascending=True)

        st.markdown(
            f"### 📅 {d_str} &nbsp;<span class='sim-meta'>"
            f"({len(day_rows)} pick(s) · day +{(today_d - rec_date).days})</span>",
            unsafe_allow_html=True,
        )

        for _, row in day_rows.iterrows():
            pill_text, pill_cls = _status_pill(row.get("actual_eod_change"))
            tkr = str(row["ticker"])
            conf = row.get("confidence_score")
            conf_s = f"{float(conf):.0%}" if pd.notna(conf) else "—"
            pick_px = row.get("price_at_pick")
            today_px = row.get("price_today")
            tgt_view = _target_status_pill(
                row.get("target_price"), pick_px, rec_date, today_d,
                row.get("target_hit_date"),
            )

            st.markdown('<div class="sim-card">', unsafe_allow_html=True)

            head_l, head_r = st.columns([0.45, 0.55])
            with head_l:
                st.markdown(
                    f"<span class='sim-rank'>#{int(row.get('pick_rank') or 0)}</span>"
                    f"<a class='sim-ticker' href='{_google_finance_url(tkr)}' "
                    f"target='_blank' rel='noopener noreferrer'>"
                    f"{html.escape(tkr)}</a>"
                    f"<div class='sim-meta'>"
                    f"Confidence: {conf_s}"
                    f"{' · Pick $' + f'{float(pick_px):.2f}' if pd.notna(pick_px) else ''}"
                    f"{' · Today $' + f'{float(today_px):.2f}' if pd.notna(today_px) else ''}"
                    "</div>",
                    unsafe_allow_html=True,
                )
            with head_r:
                ups = tgt_view["upside_str"]
                ups_html = (
                    f"<span class='sim-meta' style='margin-left:6px;'>{ups}</span>"
                    if ups else ""
                )
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:10px;"
                    f"flex-wrap:wrap;justify-content:flex-end;'>"
                    f"<span class='{pill_cls}'>{pill_text}</span>"
                    f"<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
                    f"background:{tgt_view['pill_bg']};color:{tgt_view['pill_fg']};"
                    f"font-weight:600;font-size:0.75rem;'>"
                    f"Target {tgt_view['target_str']} · {tgt_view['pill_text']}</span>"
                    f"{ups_html}"
                    "</div>",
                    unsafe_allow_html=True,
                )

            st.markdown(
                "<p class='sim-section-label'>Alpha decay timeline</p>",
                unsafe_allow_html=True,
            )
            c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
            c1.metric("Session", _fmt_pct(row.get("return_session")))
            c2.metric("EOD",     _fmt_pct(row.get("actual_eod_change")))
            c3.metric("T+3",   _fmt_pct_with_pending(
                row.get("return_3d"),  rec_date, today_d, 3))
            c4.metric("T+7",   _fmt_pct_with_pending(
                row.get("return_7d"),  rec_date, today_d, 7))
            c5.metric("T+14",  _fmt_pct_with_pending(
                row.get("return_14d"), rec_date, today_d, 14))
            c6.metric("T+30",  _fmt_pct_with_pending(
                row.get("return_30d"), rec_date, today_d, 30))
            c7.metric("T+90",  _fmt_pct_with_pending(
                row.get("return_90d"), rec_date, today_d, 90))
            c8.metric("T+180", _fmt_pct_with_pending(
                row.get("return_180d"), rec_date, today_d, 180))

            today_pct = row.get("return_today_pct")
            if pd.notna(today_pct):
                color = "var(--green)" if float(today_pct) >= 0 else "var(--red)"
                st.markdown(
                    f"<p class='sim-section-label' style='margin-top:6px;'>"
                    "Return since pick — using latest close</p>"
                    f"<div style='font-family:var(--mono);font-size:1.4rem;"
                    f"font-weight:700;color:{color};'>"
                    f"{float(today_pct):+.2f}%"
                    f"<span class='sim-meta' style='margin-left:8px;'>"
                    f"({(today_d - rec_date).days}d held)</span></div>",
                    unsafe_allow_html=True,
                )

            st.markdown(
                "<p class='sim-section-label'>Rationale (back-test)</p>"
                f"<p class='sim-rationale'>"
                f"{html.escape(row.get('pm_rationale') or '_(no rationale)_')}"
                "</p>",
                unsafe_allow_html=True,
            )

            st.markdown("</div>", unsafe_allow_html=True)


# ─── Raw table at bottom ────────────────────────────────────────────────────

with st.expander("📋 Raw simulated predictions table"):
    show = view.copy()
    show["confidence_score"] = show["confidence_score"].apply(
        lambda x: f"{x:.0%}" if pd.notna(x) else "—"
    )
    for col in ("price_at_pick", "target_price", "price_today"):
        show[col] = show[col].apply(
            lambda x: f"${float(x):.2f}" if pd.notna(x) else "—"
        )
    for col in (
        "actual_eod_change", "return_session",
        "return_3d", "return_7d", "return_14d", "return_30d",
        "return_90d", "return_180d", "return_today_pct", "target_upside_pct",
    ):
        show[col] = show[col].apply(
            lambda x: f"{x:+.2f}%" if pd.notna(x) else "⏳"
        )
    cols = [
        "date", "pick_rank", "ticker", "confidence_score",
        "price_at_pick", "target_price", "target_upside_pct", "target_hit_date",
        "return_session", "actual_eod_change",
        "return_3d", "return_7d", "return_14d", "return_30d",
        "return_90d", "return_180d", "price_today", "return_today_pct",
    ]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True, hide_index=True)

    csv_bytes = view.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name=f"simulated_{run_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )
