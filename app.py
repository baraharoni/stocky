"""
app.py — Catalyst Alpha v1.0
Streamlit dashboard with two tabs:
  Tab 1 — Market Retrospective  (actual_market_movers)
  Tab 2 — Alpha Picks           (alpha_predictions vs EOD reality)
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from database import init_db, get_all_predictions
from datetime import date as _date

# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Catalyst Alpha v1.0",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Global CSS ───────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

    /* ── Palette ── */
    :root {
        --bg:         #F8F9FA;
        --bg-card:    #FFFFFF;
        --bg-card2:   #F3F4F6;
        --accent:     #1D4ED8;
        --accent-lt:  #EFF6FF;
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
        --border-acc: #BFDBFE;
        --mono:       'IBM Plex Mono', 'Courier New', monospace;
    }

    /* ── Reset & base ── */
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background-color: var(--bg); }

    /* ── Remove Streamlit top padding ── */
    .block-container { padding-top: 1.5rem !important; max-width: 1400px; }

    /* ── Header banner ── */
    .alpha-header {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-left: 4px solid var(--accent);
        border-radius: 10px;
        padding: 18px 26px 14px;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 8px;
    }
    .alpha-header h1 {
        color: var(--text-main);
        font-size: 1.5rem;
        font-weight: 700;
        letter-spacing: -0.3px;
        margin: 0 0 2px 0;
    }
    .alpha-header h1 span { color: var(--accent); }
    .alpha-header p  { color: var(--text-muted); margin: 0; font-size: 0.82rem; }
    .header-badge {
        background: var(--accent-lt);
        color: var(--accent);
        border: 1px solid var(--border-acc);
        border-radius: 20px;
        padding: 4px 14px;
        font-size: 0.76rem;
        font-weight: 600;
        white-space: nowrap;
    }

    /* ── Metric cards ── */
    div[data-testid="metric-container"] {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 14px 18px;
    }
    div[data-testid="metric-container"] label {
        color: var(--text-muted) !important;
        font-size: 0.72rem !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.8px;
    }
    div[data-testid="stMetricValue"] {
        color: var(--text-main) !important;
        font-size: 1.55rem !important;
        font-weight: 700 !important;
        font-family: var(--mono) !important;
        letter-spacing: -0.5px;
    }
    div[data-testid="stMetricDelta"] { font-family: var(--mono); }

    /* ── Tabs — segmented control ── */
    div[data-baseweb="tab-list"] {
        background: var(--bg-card2);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 4px;
        gap: 2px;
    }
    button[data-baseweb="tab"] {
        color: var(--text-muted) !important;
        font-size: 0.875rem !important;
        font-weight: 500 !important;
        border-radius: 8px !important;
        padding: 7px 20px !important;
        background: transparent !important;
        border-bottom: none !important;
        transition: background 0.15s, color 0.15s;
    }
    button[data-baseweb="tab"]:hover {
        background: var(--bg-card) !important;
        color: var(--text-main) !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: var(--accent) !important;
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-bottom: 1px solid var(--border) !important;
        font-weight: 600 !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    div[data-baseweb="tab-highlight"] { display: none !important; }
    div[data-baseweb="tab-border"]    { display: none !important; }

    /* ── Section labels ── */
    .section-label {
        color: var(--text-muted);
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        margin-bottom: 6px;
    }

    /* ── Selectbox (Command-palette style) ── */
    div[data-testid="stSelectbox"] > div > div {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        color: var(--text-main) !important;
        font-size: 0.875rem !important;
    }

    /* ── Number input ── */
    div[data-testid="stNumberInput"] input {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        color: var(--text-main) !important;
        font-family: var(--mono) !important;
        font-size: 0.875rem !important;
    }

    /* ── Prediction cards ── */
    .pred-card {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 10px;
        transition: box-shadow 0.2s;
    }
    .pred-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.07); }
    .pred-ticker {
        font-family: var(--mono);
        font-size: 1.3rem;
        font-weight: 700;
        color: var(--accent);
        letter-spacing: -0.3px;
    }
    .pred-meta   { color: var(--text-muted); font-size: 0.8rem; margin-top: 2px; }

    /* Status tags */
    .tag-hit {
        background: var(--green-bg);
        color: var(--green);
        border: 1px solid #BBF7D0;
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 0.75rem;
        font-weight: 600;
        font-family: var(--mono);
    }
    .tag-near {
        background: var(--amber-bg);
        color: var(--amber);
        border: 1px solid #FDE68A;
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 0.75rem;
        font-weight: 600;
        font-family: var(--mono);
    }
    .tag-miss {
        background: var(--red-bg);
        color: var(--red);
        border: 1px solid #FECDD3;
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 0.75rem;
        font-weight: 600;
        font-family: var(--mono);
    }
    .tag-pending {
        background: var(--bg-card2);
        color: var(--text-muted);
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 0.75rem;
        font-weight: 600;
        font-family: var(--mono);
    }

    .divider     { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
    .rationale   { color: var(--text-sub); font-size: 0.875rem; line-height: 1.65; }
    .feedback    { color: var(--text-sub); font-size: 0.875rem; line-height: 1.65; }
    .result-big  {
        font-family: var(--mono);
        font-size: 1.85rem;
        font-weight: 700;
        letter-spacing: -0.5px;
        transition: color 0.3s;
    }
    .green { color: var(--green); }
    .red   { color: var(--red);   }
    .amber { color: var(--amber); }
    .muted { color: var(--text-muted); }

    /* ── Price flash transition ── */
    @keyframes price-up {
        0%   { background-color: #D1FAE5; }
        100% { background-color: transparent; }
    }
    @keyframes price-down {
        0%   { background-color: #FFE4E6; }
        100% { background-color: transparent; }
    }
    .flash-up   { animation: price-up   0.3s ease-out; }
    .flash-down { animation: price-down 0.3s ease-out; }

    /* ── Dataframe ── */
    .stDataFrame { border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }
    .stDataFrame thead th {
        background: var(--bg-card2) !important;
        color: var(--text-muted) !important;
        font-size: 0.72rem !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.8px;
    }
    .stDataFrame tbody tr:hover td { background: var(--bg-card2) !important; }
    .stDataFrame td, .stDataFrame th { padding: 7px 12px !important; }

    /* ── Expander ── */
    details[data-testid="stExpander"] summary {
        color: var(--text-muted) !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
    }
    details[data-testid="stExpander"] {
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        background: var(--bg-card) !important;
    }

    /* ── Divider ── */
    hr[data-testid="stDivider"] { border-color: var(--border) !important; }

    /* ── Sidebar ── */
    section[data-testid="stSidebar"] { background: var(--bg-card); }

    /* ── Captions ── */
    small[data-testid="stCaptionContainer"] { color: var(--text-muted) !important; font-size: 0.8rem !important; }

    /* ── Empty state ── */
    .empty-state {
        background: var(--bg-card);
        border: 1px dashed var(--border);
        border-radius: 10px;
        padding: 48px 40px;
        text-align: center;
        color: var(--text-muted);
    }
    .empty-state h3 { color: var(--text-sub); font-size: 1rem; font-weight: 600; }
    .empty-state code {
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

# ─── Bootstrap DB ─────────────────────────────────────────────────────────────

init_db()

# ─── Header ───────────────────────────────────────────────────────────────────

st.markdown(
    f"""
    <div class="alpha-header">
        <div>
            <h1>⚡ <span>Catalyst</span> Alpha&nbsp;v1.0</h1>
            <p>NASDAQ Breakout Prediction Engine &nbsp;·&nbsp; Multi-Agent AI System</p>
        </div>
        <span class="header-badge">🕐 {datetime.now().strftime('%a %b %d, %Y &nbsp;·&nbsp; %H:%M')}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab1, tab2 = st.tabs([
    "🚀  Alpha Breakouts",
    "🎯  Short Squeeze Snipers",
])


# ── Prediction-card helpers ────────────────────────────────────────────────────

def _fmt_return(val, days_elapsed: int, window: int) -> tuple[str, float | None]:
    if days_elapsed < window:
        return f"Pending ({window - days_elapsed}d)", None
    if pd.isna(val):
        return "Pending", None
    return f"{val:+.2f}%", float(val)


def _metric_delta(val) -> tuple[str | None, str | None]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None, None
    return f"{val:+.2f}%", "normal" if val >= 0 else "inverse"


def _status(row: pd.Series) -> tuple[str, str]:
    v = row["EOD Change %"]
    if pd.isna(v):
        return "⏳  Pending EOD",  "tag-pending"
    if v >= 6.0:
        return f"✅  HIT  (+{v:.1f}%)",  "tag-hit"
    if v >= 0:
        return f"⚠️  Near Miss  (+{v:.1f}%)", "tag-near"
    return f"❌  Miss  ({v:.1f}%)", "tag-miss"


import json as _json


def _parse_metrics(raw) -> dict:
    """Safely parse the metrics JSON string from the DB. Returns {} on any failure."""
    if not raw or (isinstance(raw, float) and pd.isna(raw)):
        return {}
    try:
        return _json.loads(str(raw)) if isinstance(raw, str) else {}
    except Exception:
        return {}


def _render_predictions(
    strategy: str,
    label: str,
    caption: str,
    empty_cmd: str,
    date_key: str,
    show_metrics: bool = False,
) -> None:
    """Render a full prediction tab for the given strategy ('alpha' or 'squeeze')."""
    st.markdown(f'<p class="section-label">{label}</p>', unsafe_allow_html=True)
    st.caption(caption)

    preds_raw = get_all_predictions(strategy=strategy)

    if not preds_raw:
        st.markdown(
            '<div class="empty-state">'
            f'<h3>No {strategy} predictions yet</h3>'
            f'<p>Run <code>{empty_cmd}</code> to generate picks.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    df_p = pd.DataFrame(preds_raw)
    df_p.columns = [
        "Date", "Ticker", "PM Rationale",
        "Confidence", "EOD Change %", "Manager Feedback",
        "Return 3D", "Return 7D", "Return 30D", "Metrics",
    ]
    for col in ("Confidence", "EOD Change %", "Return 3D", "Return 7D", "Return 30D"):
        df_p[col] = pd.to_numeric(df_p[col], errors="coerce")

    all_dates = sorted(df_p["Date"].unique().tolist(), reverse=True)
    date_sel  = st.selectbox("Filter by Date", ["All"] + all_dates, key=date_key)
    df_view   = df_p if date_sel == "All" else df_p[df_p["Date"] == date_sel]

    resolved = df_view[df_view["EOD Change %"].notna()]
    hits     = resolved[resolved["EOD Change %"] >= 6.0]
    hit_rate = f"{len(hits)/len(resolved)*100:.0f}%" if len(resolved) > 0 else "—"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Picks", len(df_view))
    c2.metric("Resolved",    len(resolved))
    c3.metric("Hits ≥ 6%",  len(hits))
    c4.metric("Hit Rate",    hit_rate)

    st.divider()

    today_d = _date.today()

    for _, row in df_view.iterrows():
        label_tag, css_cls = _status(row)
        conf_pct           = f"{row['Confidence']:.0%}" if pd.notna(row["Confidence"]) else "N/A"
        rec_date           = _date.fromisoformat(row["Date"])
        days_elapsed       = (today_d - rec_date).days

        # ── Card header ───────────────────────────────────────────────────────
        st.markdown(
            f"""
            <div class="pred-card">
              <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
                <span class="pred-ticker">{row['Ticker']}</span>
                <span class="{css_cls}">{label_tag}</span>
                <span class="pred-meta">{row['Date']} &nbsp;·&nbsp; Confidence: {conf_pct}
                &nbsp;·&nbsp; Day +{days_elapsed}</span>
              </div>
              <hr class="divider">
            """,
            unsafe_allow_html=True,
        )

        # ── Squeeze-only: full quantitative metrics grid ─────────────────────
        if show_metrics:
            metrics = _parse_metrics(row.get("Metrics"))
            if metrics:
                st.markdown(
                    '<p class="section-label" style="margin-bottom:6px; color:#7C3AED;">'
                    '📊 Quantitative Screen — Python-Verified Metrics</p>',
                    unsafe_allow_html=True,
                )

                # ── Row 1: market context ─────────────────────────────────────
                r1a, r1b, r1c, r1d = st.columns(4)
                r1a.metric("Price",        metrics.get("Price",        "N/A"))
                r1b.metric("5d % Change",  metrics.get("5d % Change",  "N/A"))
                r1c.metric("Volume",       metrics.get("Volume",       "N/A"))
                r1d.metric("Market Cap",   metrics.get("Market Cap",   "N/A"))

                # ── Row 2: squeeze structure ──────────────────────────────────
                r2a, r2b, r2c, r2d = st.columns(4)
                r2a.metric("Float",        metrics.get("Float",        "N/A"))
                r2b.metric("Short %",      metrics.get("Short %",      "N/A"))
                r2c.metric("RVOL",         metrics.get("RVOL",         "N/A"))
                r2d.metric("Turnover",     metrics.get("Turnover",     "N/A"))

                # ── Row 3: qualitative flags ──────────────────────────────────
                r3a, r3b, r3c = st.columns(3)
                r3a.metric("Above VWAP",   metrics.get("Above VWAP",   "N/A"))
                r3b.metric("Country",      metrics.get("Country",      "N/A"))

                news_val = metrics.get("News", "N/A")
                if news_val == "Verified":
                    r3c.metric("News Catalyst", "✅ Verified",
                               delta="Catalyst Confirmed", delta_color="normal")
                elif news_val in ("None Found", "None"):
                    r3c.metric("News Catalyst", "❌ None Found",
                               delta="Rejected", delta_color="inverse")
                else:
                    r3c.metric("News Catalyst", "⏳ Pending")

                # ── Filter-pass badge ─────────────────────────────────────────
                st.markdown(
                    '<div style="margin-bottom:10px; padding:6px 12px; '
                    'background:#F5F3FF; border-left:3px solid #7C3AED; '
                    'border-radius:4px; font-size:0.72rem; color:#6D28D9;">'
                    '✓ All 10 quantitative filters confirmed in Python &nbsp;|&nbsp; '
                    'RVOL &gt; 2× &nbsp;|&nbsp; Short &gt; 10% &nbsp;|&nbsp; '
                    'Float 5M–20M &nbsp;|&nbsp; Turnover 0.33–3.0 &nbsp;|&nbsp; '
                    '5d &gt; +10% &nbsp;|&nbsp; Above VWAP &nbsp;|&nbsp; US Only'
                    '</div>',
                    unsafe_allow_html=True,
                )

        # ── Alpha Decay Timeline ──────────────────────────────────────────────
        st.markdown(
            '<p class="section-label" style="margin-bottom:4px;">Alpha Decay Timeline</p>',
            unsafe_allow_html=True,
        )
        m_eod, m_3d, m_7d, m_30d = st.columns(4)

        eod_val = row["EOD Change %"]
        if pd.notna(eod_val):
            eod_label         = f"{eod_val:+.2f}%"
            eod_delta, eod_dc = _metric_delta(eod_val)
        else:
            eod_label, eod_delta, eod_dc = "Pending ⏳", None, None
        m_eod.metric("EOD (Same Day)", eod_label,
                     delta=eod_delta, delta_color=eod_dc or "off")

        r3_str,  r3_num  = _fmt_return(row["Return 3D"],  days_elapsed, 3)
        r3_delta, r3_dc  = _metric_delta(r3_num)
        m_3d.metric("T+3 Days", r3_str, delta=r3_delta, delta_color=r3_dc or "off")

        r7_str,  r7_num  = _fmt_return(row["Return 7D"],  days_elapsed, 7)
        r7_delta, r7_dc  = _metric_delta(r7_num)
        m_7d.metric("T+7 Days", r7_str, delta=r7_delta, delta_color=r7_dc or "off")

        r30_str, r30_num = _fmt_return(row["Return 30D"], days_elapsed, 30)
        r30_delta, r30_dc = _metric_delta(r30_num)
        m_30d.metric("T+30 Days", r30_str, delta=r30_delta, delta_color=r30_dc or "off")

        st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

        # ── Rationale / Feedback ──────────────────────────────────────────────
        col_r, col_f = st.columns([1, 1], gap="large")
        with col_r:
            st.markdown('<p class="section-label">Rationale (Agent Analysis)</p>',
                        unsafe_allow_html=True)
            rationale = row["PM Rationale"] or "_No rationale recorded._"
            st.markdown(f'<p class="rationale">{rationale}</p>', unsafe_allow_html=True)

        with col_f:
            st.markdown('<p class="section-label">EOD Feedback (Manager Agent)</p>',
                        unsafe_allow_html=True)
            feedback = row["Manager Feedback"]
            if feedback and str(feedback).lower() not in ("none", "nan", ""):
                st.markdown(f'<p class="feedback">{feedback}</p>', unsafe_allow_html=True)
            else:
                st.markdown('<p class="muted"><em>EOD review not yet run.</em></p>',
                            unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("📋  Raw predictions table"):
        raw_cols     = ["Date", "Ticker", "Confidence",
                        "EOD Change %", "Return 3D", "Return 7D", "Return 30D"]
        display_cols = df_view[raw_cols].copy()
        display_cols["Confidence"] = display_cols["Confidence"].apply(
            lambda x: f"{x:.0%}" if pd.notna(x) else "N/A")
        for c in ("EOD Change %", "Return 3D", "Return 7D", "Return 30D"):
            display_cols[c] = display_cols[c].apply(
                lambda x: f"{x:+.2f}%" if pd.notna(x) else "⏳")
        st.dataframe(display_cols, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TAB 1 — Alpha Breakouts
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    _render_predictions(
        strategy="alpha",
        label="Alpha Breakouts — Morning Pick vs. EOD Reality",
        caption=(
            "Each morning the Analyst Agent surfaces 3–5 high-probability breakout picks "
            "from the earnings calendar + momentum scan. "
            "The Manager reviews them after close and writes lessons-learned feedback."
        ),
        empty_cmd="python main.py --morning",
        date_key="alpha_date",
        show_metrics=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  TAB 2 — Short Squeeze Snipers
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    _render_predictions(
        strategy="squeeze",
        label="Short Squeeze Snipers — Float Rotation Picks",
        caption=(
            "The Squeeze Agent screens for low-float stocks with high short interest, "
            "RVOL > 2×, and a confirmed positive news catalyst. "
            "Each card shows the exact quantitative metrics that triggered the pick."
        ),
        empty_cmd="python main.py --squeeze",
        date_key="squeeze_date",
        show_metrics=True,
    )
