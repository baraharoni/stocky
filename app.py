"""
app.py — Catalyst Alpha v1.0
Streamlit dashboard with two tabs:
  Tab 1 — Market Retrospective  (actual_market_movers)
  Tab 2 — Alpha Picks           (alpha_predictions vs EOD reality)
"""

import hashlib
import html
import io
import urllib.parse
import zipfile

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from database import init_db, get_all_predictions, get_all_market_movers
from datetime import date as _date
from pathlib import Path
import base64

_APP_DIR = Path(__file__).resolve().parent


def _resolve_static_logo() -> Path | None:
    for name in ("StockyInfo.png", "StockyIcon.png"):
        p = _APP_DIR / "static" / name
        if p.is_file():
            return p
    return None


_LOGO_PATH = _resolve_static_logo()


def _logo_data_uri() -> str | None:
    if _LOGO_PATH is None:
        return None
    b64 = base64.standard_b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="stocky",
    page_icon=str(_LOGO_PATH) if _LOGO_PATH is not None else "⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Global CSS ───────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;500;600&display=swap');

    /* ── Google Finance–inspired palette ── */
    :root {
        --bg:         #FAFAFA;
        --bg-card:    #FFFFFF;
        --bg-card2:   #F1F3F4;
        --accent:     #1A73E8;
        --accent-hov: #1557B0;
        --accent-lt:  #E8F0FE;
        --green:      #137333;
        --green-bg:   #E6F4EA;
        --red:        #D93025;
        --red-bg:     #FCE8E6;
        --amber:      #B06000;
        --amber-bg:   #FEF7E0;
        --text-main:  #202124;
        --text-sub:   #3C4043;
        --text-muted: #5F6368;
        --border:     #DADCE0;
        --border-acc: #AECBFA;
        --shadow-sm:  0 1px 2px rgba(60,64,67,0.08);
        --shadow-md:  0 1px 3px rgba(60,64,67,0.12), 0 1px 2px rgba(60,64,67,0.08);
        --mono:       'Roboto Mono', ui-monospace, monospace;
        --radius:     8px;
        --radius-lg:  12px;
    }

    html, body, [class*="css"] { font-family: 'Roboto', 'Segoe UI', sans-serif; }
    .stApp { background-color: var(--bg); }

    .block-container {
        padding-top: 1.25rem !important;
        padding-bottom: 2rem !important;
        max-width: 1280px;
    }

    /* ── Top bar (Finance-style) ── */
    .alpha-header {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 16px 22px;
        margin-bottom: 18px;
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
        box-shadow: var(--shadow-sm);
    }
    .alpha-header h1 {
        color: var(--text-main);
        font-size: 1.375rem;
        font-weight: 500;
        letter-spacing: -0.02em;
        margin: 0 0 4px 0;
        line-height: 1.25;
    }
    .alpha-header h1 span { color: var(--accent); font-weight: 700; }
    .alpha-header-brand {
        display: flex;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
    }
    .alpha-header-logo {
        height: 36px;
        width: auto;
        display: block;
    }
    .alpha-header-brand h1 { margin: 0; }
    .header-badge {
        background: var(--bg-card2);
        color: var(--text-sub);
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 6px 14px;
        font-size: 0.75rem;
        font-weight: 500;
        white-space: nowrap;
        align-self: center;
    }

    /* ── Metric tiles ── */
    div[data-testid="metric-container"] {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 12px 14px;
        box-shadow: var(--shadow-sm);
    }
    div[data-testid="metric-container"] label {
        color: var(--text-muted) !important;
        font-size: 0.6875rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    div[data-testid="stMetricValue"] {
        color: var(--text-main) !important;
        font-size: 1.375rem !important;
        font-weight: 500 !important;
        font-family: var(--mono) !important;
        letter-spacing: -0.02em;
    }
    div[data-testid="stMetricDelta"] {
        font-family: var(--mono);
        font-weight: 500 !important;
        font-size: 0.8125rem !important;
    }

    /* ── Tabs — underline nav (GF-style) ── */
    div[data-testid="stTabs"] { margin-top: 4px; }
    div[data-baseweb="tab-list"] {
        background: transparent !important;
        border: none !important;
        border-bottom: 1px solid var(--border) !important;
        border-radius: 0 !important;
        padding: 0 !important;
        gap: 4px !important;
    }
    button[data-baseweb="tab"] {
        color: var(--text-muted) !important;
        font-size: 0.875rem !important;
        font-weight: 500 !important;
        border-radius: 0 !important;
        padding: 10px 16px !important;
        margin-bottom: -1px !important;
        background: transparent !important;
        border: none !important;
        border-bottom: 2px solid transparent !important;
        transition: color 0.15s, border-color 0.15s;
    }
    button[data-baseweb="tab"]:hover {
        background: transparent !important;
        color: var(--text-main) !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: var(--accent) !important;
        background: transparent !important;
        border: none !important;
        border-bottom: 2px solid var(--accent) !important;
        font-weight: 500 !important;
        box-shadow: none !important;
    }
    div[data-baseweb="tab-highlight"] { display: none !important; }
    div[data-baseweb="tab-border"]    { display: none !important; }

    .section-label {
        color: var(--text-muted);
        font-size: 0.6875rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 8px;
    }

    div[data-testid="stSelectbox"] > div > div {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        color: var(--text-main) !important;
        font-size: 0.875rem !important;
    }

    div[data-testid="stNumberInput"] input {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        color: var(--text-main) !important;
        font-family: var(--mono) !important;
        font-size: 0.875rem !important;
    }

    div[data-testid="stTextInput"] input {
        border-radius: var(--radius) !important;
        border-color: var(--border) !important;
    }

    /* ── Quote cards ── */
    .pred-card {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 18px 20px;
        margin-bottom: 12px;
        transition: box-shadow 0.2s ease, border-color 0.2s ease;
        box-shadow: var(--shadow-sm);
    }
    .pred-card:hover {
        box-shadow: var(--shadow-md);
        border-color: #BDC1C6;
    }
    a.pred-ticker, a.pred-ticker:visited, span.pred-ticker {
        font-family: var(--mono);
        font-size: 1.25rem;
        font-weight: 600;
        color: var(--accent);
        letter-spacing: -0.02em;
    }
    a.pred-ticker:hover { color: var(--accent-hov) !important; text-decoration: none !important; border-bottom: 1px solid var(--accent-hov); }
    .pred-meta { color: var(--text-muted); font-size: 0.8125rem; margin-top: 2px; }

    /* ── Google Finance–style horizontal quote strip ── */
    .gf-strip-scroll {
        display: flex;
        flex-direction: row;
        flex-wrap: nowrap;
        gap: 10px;
        overflow-x: auto;
        overflow-y: hidden;
        padding: 4px 2px 14px 2px;
        scroll-snap-type: x proximity;
        -webkit-overflow-scrolling: touch;
    }
    .gf-mini-card {
        flex: 0 0 auto;
        scroll-snap-align: start;
        min-width: 156px;
        max-width: 188px;
        background: #F8F9FA;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 12px 14px;
        text-decoration: none !important;
        color: inherit;
        box-shadow: var(--shadow-sm);
        transition: box-shadow 0.15s ease, border-color 0.15s ease;
    }
    .gf-mini-card:hover {
        box-shadow: var(--shadow-md);
        border-color: #BDC1C6;
    }
    .gf-mini-name {
        font-weight: 700;
        font-size: 0.94rem;
        color: var(--text-main);
        letter-spacing: -0.02em;
        font-family: var(--mono);
    }
    .gf-mini-date {
        font-size: 0.68rem;
        color: var(--text-muted);
        margin-top: 3px;
    }
    .gf-mini-table {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 3px 10px;
        align-items: baseline;
        margin-top: 10px;
        font-family: var(--mono);
        font-size: 0.74rem;
    }
    .gf-mini-lbl {
        color: var(--text-muted);
        font-weight: 500;
        font-size: 0.62rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .gf-strip-pos { color: var(--green); font-weight: 600; }
    .gf-strip-neg { color: var(--red); font-weight: 600; }
    .gf-strip-zero { color: var(--text-sub); font-weight: 500; }
    .gf-strip-pend { color: var(--text-muted); font-weight: 400; font-size: 0.68rem; }

    .tag-hit {
        background: var(--green-bg);
        color: var(--green);
        border: 1px solid #CEEAD6;
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 0.75rem;
        font-weight: 500;
        font-family: var(--mono);
    }
    .tag-near {
        background: var(--amber-bg);
        color: var(--amber);
        border: 1px solid #FEEFC3;
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 0.75rem;
        font-weight: 500;
        font-family: var(--mono);
    }
    .tag-miss {
        background: var(--red-bg);
        color: var(--red);
        border: 1px solid #FAD2CF;
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 0.75rem;
        font-weight: 500;
        font-family: var(--mono);
    }
    .tag-pending {
        background: var(--bg-card2);
        color: var(--text-muted);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 0.75rem;
        font-weight: 500;
        font-family: var(--mono);
    }

    .divider { border: none; border-top: 1px solid var(--border); margin: 14px 0; }
    .rationale, .feedback {
        color: var(--text-sub);
        font-size: 0.875rem;
        line-height: 1.6;
    }
    .result-big {
        font-family: var(--mono);
        font-size: 1.75rem;
        font-weight: 600;
        letter-spacing: -0.02em;
        transition: color 0.3s;
    }
    .green { color: var(--green); }
    .red   { color: var(--red); }
    .amber { color: var(--amber); }
    .muted { color: var(--text-muted); }

    @keyframes price-up {
        0%   { background-color: #CEEAD6; }
        100% { background-color: transparent; }
    }
    @keyframes price-down {
        0%   { background-color: #FAD2CF; }
        100% { background-color: transparent; }
    }
    .flash-up   { animation: price-up   0.35s ease-out; }
    .flash-down { animation: price-down 0.35s ease-out; }

    .stDataFrame {
        border-radius: var(--radius);
        overflow: hidden;
        border: 1px solid var(--border);
        box-shadow: var(--shadow-sm);
    }
    .stDataFrame thead th {
        background: var(--bg-card2) !important;
        color: var(--text-muted) !important;
        font-size: 0.6875rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    .stDataFrame tbody tr:hover td { background: #F8F9FA !important; }
    .stDataFrame td, .stDataFrame th { padding: 8px 12px !important; }

    details[data-testid="stExpander"] summary {
        color: var(--accent) !important;
        font-size: 0.8125rem !important;
        font-weight: 500 !important;
    }
    details[data-testid="stExpander"] {
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        background: var(--bg-card) !important;
    }

    hr[data-testid="stDivider"] { border-color: var(--border) !important; }

    section[data-testid="stSidebar"] {
        background: var(--bg-card);
        border-right: 1px solid var(--border);
    }
    [data-testid="stSidebar"] [data-testid="stMetricValue"] {
        font-size: 1.0625rem !important;
    }
    [data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div { min-width: 0; }

    small[data-testid="stCaptionContainer"] {
        color: var(--text-muted) !important;
        font-size: 0.8125rem !important;
    }

    .empty-state {
        background: var(--bg-card);
        border: 1px dashed var(--border);
        border-radius: var(--radius-lg);
        padding: 44px 36px;
        text-align: center;
        color: var(--text-muted);
        box-shadow: var(--shadow-sm);
    }
    .empty-state h3 { color: var(--text-sub); font-size: 1rem; font-weight: 500; }
    .empty-state code {
        background: var(--bg-card2);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 2px 8px;
        font-family: var(--mono);
        font-size: 0.8125rem;
        color: var(--accent);
    }
    .group-summary-h {
        color: var(--accent);
        font-size: 0.75rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin: 0 0 4px 0;
    }
    .group-hint { font-size: 0.8125rem; color: var(--text-sub); line-height: 1.45; }
    .pick-row-wrap div[data-baseweb="checkbox"] { margin-top: 0.1rem; }
    .sel-placeholder {
        border: 1px dashed var(--border);
        border-radius: var(--radius-lg);
        padding: 14px 16px;
        background: var(--bg-card2);
        margin: 0 0 4px 0;
    }

    /* Primary buttons — Material blue */
    .stButton > button[kind="primary"] {
        background-color: var(--accent) !important;
        border-color: var(--accent) !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: var(--accent-hov) !important;
        border-color: var(--accent-hov) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─── Bootstrap DB ─────────────────────────────────────────────────────────────

init_db()


# ─── Export helpers ───────────────────────────────────────────────────────────

_EXPORT_COLS = [
    "date", "ticker", "strategy", "confidence_score",
    "price_at_pick", "target_price", "target_upside_pct", "target_hit_date",
    "actual_eod_change", "return_session",
    "return_3d", "return_7d", "return_14d", "return_30d",
    "pm_rationale", "manager_feedback", "metrics",
]


@st.cache_data(ttl=15, show_spinner=False)
def _load_export_frames() -> dict[str, pd.DataFrame]:
    """All three datasets as DataFrames, in a stable column order."""
    alpha   = pd.DataFrame(get_all_predictions(strategy="alpha")   or [])
    squeeze = pd.DataFrame(get_all_predictions(strategy="squeeze") or [])
    movers  = pd.DataFrame(get_all_market_movers()                 or [])

    def _shape(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=_EXPORT_COLS)
        # Derive target_upside_pct = (target − pick) / pick · 100  (NaN-safe).
        if "price_at_pick" in df.columns and "target_price" in df.columns:
            _pick   = pd.to_numeric(df["price_at_pick"], errors="coerce")
            _target = pd.to_numeric(df["target_price"],  errors="coerce")
            df = df.assign(
                target_upside_pct=((_target - _pick) / _pick * 100.0).where(_pick.gt(0))
            )
        else:
            df = df.assign(target_upside_pct=pd.Series(dtype=float))
        keep = [c for c in _EXPORT_COLS if c in df.columns]
        out  = df[keep].copy()
        for c in ("confidence_score", "price_at_pick", "target_price",
                  "target_upside_pct",
                  "actual_eod_change", "return_session",
                  "return_3d", "return_7d", "return_14d", "return_30d"):
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")
        return out.sort_values(["date", "ticker"], ascending=[False, True])

    return {
        "Alpha Picks"   : _shape(alpha),
        "Squeeze Picks" : _shape(squeeze),
        "Market Movers" : movers if not movers.empty else
                          pd.DataFrame(columns=["date", "ticker", "percent_change", "catalyst_reason"]),
    }


def _build_excel_bytes(frames: dict[str, pd.DataFrame]) -> bytes | None:
    """Returns a multi-sheet .xlsx as bytes, or None if openpyxl isn't installed."""
    try:
        import openpyxl  # noqa: F401  — required by pandas ExcelWriter
    except Exception:
        return None
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        wrote_any = False
        for sheet, df in frames.items():
            if df.empty:
                pd.DataFrame({"info": [f"No rows in '{sheet}' yet."]}).to_excel(
                    xw, sheet_name=sheet[:31], index=False
                )
            else:
                df.to_excel(xw, sheet_name=sheet[:31], index=False)
            wrote_any = True
        if not wrote_any:
            pd.DataFrame({"info": ["DB is empty."]}).to_excel(
                xw, sheet_name="empty", index=False
            )
    return buf.getvalue()


def _build_csv_zip_bytes(frames: dict[str, pd.DataFrame]) -> bytes:
    """Returns a ZIP containing one CSV per dataset (always available, no extra deps)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for sheet, df in frames.items():
            slug = sheet.lower().replace(" ", "_")
            zf.writestr(f"{slug}.csv", df.to_csv(index=False))
    return buf.getvalue()


def _render_export_section() -> None:
    """Sidebar block: pick a format and download every prediction + movers."""
    with st.sidebar:
        st.markdown("### Export data")
        st.caption("Download every prediction (Alpha + Squeeze) and the market movers.")

        frames    = _load_export_frames()
        n_alpha   = int(frames["Alpha Picks"].shape[0])
        n_squeeze = int(frames["Squeeze Picks"].shape[0])
        n_movers  = int(frames["Market Movers"].shape[0])
        st.caption(
            f"In DB now: **{n_alpha}** Alpha · **{n_squeeze}** Squeeze · **{n_movers}** Movers"
        )

        fmt = st.radio(
            "Format",
            ["Excel (.xlsx)", "CSV bundle (.zip)"],
            horizontal=True,
            key="export_fmt",
            label_visibility="collapsed",
        )

        ts = datetime.now().strftime("%Y%m%d_%H%M")

        if fmt.startswith("Excel"):
            data = _build_excel_bytes(frames)
            if data is None:
                st.warning(
                    "`openpyxl` isn't installed. Run `pip install openpyxl` "
                    "or use the CSV bundle option."
                )
            else:
                st.download_button(
                    label="Download all data",
                    data=data,
                    file_name=f"catalyst_alpha_export_{ts}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="dl_xlsx",
                )
        else:
            data = _build_csv_zip_bytes(frames)
            st.download_button(
                label="Download all data",
                data=data,
                file_name=f"catalyst_alpha_export_{ts}.zip",
                mime="application/zip",
                use_container_width=True,
                key="dl_zip",
            )

        st.divider()


_render_export_section()

# ─── Header ───────────────────────────────────────────────────────────────────

_logo_uri = _logo_data_uri()
_logo_html = (
    f'<img src="{_logo_uri}" alt="" class="alpha-header-logo" />' if _logo_uri else ""
)
st.markdown(
    f"""
    <div class="alpha-header">
        <div class="alpha-header-brand">
            {_logo_html}
            <h1><span>stocky</span></h1>
        </div>
        <span class="header-badge">{datetime.now().strftime('%a %d %b %Y · %H:%M')}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab1, tab2 = st.tabs([
    "Alpha Breakouts",
    "Short Squeeze Snipers",
])


# ── Prediction-card helpers ────────────────────────────────────────────────────

def _fmt_return(val, days_elapsed: int, window: int) -> tuple[str, float | None]:
    if days_elapsed < window:
        return f"Pending ({window - days_elapsed}d)", None
    if pd.isna(val):
        return "Pending", None
    return f"{val:+.2f}%", float(val)


def _fmt_return_session(
    val, rec_d: _date, today_d: _date, days_elapsed: int
) -> tuple[str, float | None]:
    """
    RTH open → close on pick day. Pending if pick is still today; missing data → em dash.
    """
    if val is not None and not (isinstance(val, float) and pd.isna(val)):
        return f"{float(val):+.2f}%", float(val)
    if rec_d == today_d or days_elapsed < 0:
        return "Pending ⏳", None
    return "—", None


def _metric_delta(val) -> tuple[str | None, str | None]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None, None
    # Streamlit: "normal" = green for positive delta, red for negative. "inverse" flips (bad for % returns).
    return f"{val:+.2f}%", "normal"


def _row_display_label(ticker, rec_date) -> str:
    return f"{ticker} · {rec_date}"


def _pick_cb_key(
    date_key, pick_fp: str, row_ix: int, ticker, rec_date
) -> str:
    """
    Session key for the per-card “include in group” checkbox. Includes row index so
    duplicate (ticker, date) rows in the same view do not collide.
    """
    t = str(ticker).strip().upper()
    d = str(rec_date).strip()
    return f"{date_key}__px__{pick_fp}__i{int(row_ix)}__{t}__{d}"


def _mean_display_pct(series) -> str:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return "—"
    return f"{s.mean():+.2f}%"


def _target_status(
    target,
    pick,
    rec_date: _date,
    today_d: _date,
    hit_date_raw,
) -> dict:
    """
    Build the per-card sell-target view-model.

    Returns a dict with:
      target_str  : "$12.34" or "—"
      upside_pct  : float or None
      upside_str  : "+8.7%" or ""
      pill_text   : badge label (e.g. "Hit on 2026-04-15")
      pill_bg     : background color CSS
      pill_fg     : foreground color CSS
    """
    has_target = target is not None and pd.notna(target) and float(target) > 0
    has_pick   = pick   is not None and pd.notna(pick)   and float(pick)   > 0

    if not has_target:
        return {
            "target_str": "—",
            "upside_pct": None,
            "upside_str": "",
            "pill_text" : "No target set",
            "pill_bg"   : "rgba(120,120,120,0.10)",
            "pill_fg"   : "#6B7280",
        }

    target_f = float(target)
    target_str = f"${target_f:.2f}"

    upside_pct = None
    upside_str = ""
    if has_pick:
        upside_pct = (target_f - float(pick)) / float(pick) * 100.0
        upside_str = f"+{upside_pct:.1f}%" if upside_pct >= 0 else f"{upside_pct:.1f}%"

    hit_date_s = str(hit_date_raw or "").strip()
    window_end = rec_date + timedelta(days=30)

    if hit_date_s and hit_date_s.upper() != "MISSED":
        return {
            "target_str": target_str,
            "upside_pct": upside_pct,
            "upside_str": upside_str,
            "pill_text" : f"✓ Hit on {hit_date_s}",
            "pill_bg"   : "rgba(34,197,94,0.14)",
            "pill_fg"   : "#15803D",
        }
    if hit_date_s.upper() == "MISSED" or window_end < today_d:
        return {
            "target_str": target_str,
            "upside_pct": upside_pct,
            "upside_str": upside_str,
            "pill_text" : "✗ Missed (T+30)",
            "pill_bg"   : "rgba(239,68,68,0.14)",
            "pill_fg"   : "#B91C1C",
        }
    days_left = (window_end - today_d).days
    return {
        "target_str": target_str,
        "upside_pct": upside_pct,
        "upside_str": upside_str,
        "pill_text" : f"Pending — {days_left}d left in window",
        "pill_bg"   : "rgba(26,115,232,0.12)",
        "pill_fg"   : "#174EA6",
    }


def _mean_display_conf(series) -> str:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return "—"
    return f"{s.mean():.0%}"


def _sim_one_thousand(series) -> tuple[str, str, int, int]:
    """
    Simulate investing $1,000 in every pick, where `series` is a % return column
    (e.g. EOD %). Returns:
      (P&L $ string, P&L % vs invested string, n picks resolved, n picks total).
    Picks with NaN return are skipped from the P&L (treated as “no data”).
    """
    s_all = pd.to_numeric(series, errors="coerce")
    n_total = int(s_all.shape[0])
    s = s_all.dropna()
    n_resolved = int(s.shape[0])
    if n_resolved == 0:
        return ("—", "—", 0, n_total)
    pnl_each = (s / 100.0) * 1000.0
    pnl_total = float(pnl_each.sum())
    invested = 1000.0 * n_resolved
    pnl_pct = (pnl_total / invested) * 100.0 if invested > 0 else 0.0
    sign = "+" if pnl_total >= 0 else ""
    return (
        f"{sign}${pnl_total:,.2f}",
        f"{sign}{pnl_pct:.2f}%",
        n_resolved,
        n_total,
    )


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


def _google_finance_url(ticker: str) -> str:
    """
    Open the symbol on Google Finance without forcing an exchange.
    This avoids falling back to generic search results for non-NASDAQ tickers.
    """
    sym = str(ticker).strip().upper()
    if not sym:
        return "https://www.google.com/finance?hl=he&gl=il"
    path = urllib.parse.quote(sym, safe="")
    return f"https://www.google.com/finance/quote/{path}?hl=he&gl=il"


def _prepare_strip_latest_per_ticker(df_p: pd.DataFrame) -> pd.DataFrame:
    """Collapse to one row per ticker — keep the most recent recommendation."""
    df = df_p.copy()
    df["_rd"] = pd.to_datetime(df["Date"], errors="coerce")
    df["_tuk"] = df["Ticker"].astype(str).str.strip().str.upper()
    df = df.sort_values(
        ["_rd", "_tuk"],
        ascending=[False, True],
        na_position="last",
        kind="mergesort",
    )
    df = df.drop_duplicates(subset=["_tuk"], keep="first").reset_index(drop=True)
    return df.drop(columns=["_tuk"])


def _strip_cell_pct_html(num, pend_text: str) -> str:
    """Format a return % for the strip, or pending label when `num` is None."""
    if num is None or (isinstance(num, float) and pd.isna(num)):
        return f'<span class="gf-strip-pend">{html.escape(pend_text)}</span>'
    v = float(num)
    cls = "gf-strip-pos" if v > 0 else ("gf-strip-neg" if v < 0 else "gf-strip-zero")
    return f'<span class="{cls}">{v:+.1f}%</span>'


def _strip_eod_html(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return '<span class="gf-strip-pend">⏳</span>'
    v = float(val)
    cls = "gf-strip-pos" if v > 0 else ("gf-strip-neg" if v < 0 else "gf-strip-zero")
    return f'<span class="{cls}">{v:+.1f}%</span>'


def _render_gf_quote_strip(df_latest: pd.DataFrame, date_key: str, today_d: _date) -> None:
    """Horizontal, Finance-style strip: ticker + EOD / T+3 / T+7 / T+14 / T+30."""
    st.markdown(
        '<p class="section-label">Quote strip · latest pick per symbol</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Scroll horizontally · Default = **most recently recommended** first · "
        "These controls apply only to this strip (main **Sort** row affects the list below)."
    )
    t_strip_a, t_strip_b = st.columns([1.15, 0.95], gap="small")
    with t_strip_a:
        strip_metric = st.selectbox(
            "Strip sort",
            [
                "Last recommended",
                "Recommendation date",
                "EOD %",
                "T+3",
                "T+7",
                "T+14",
                "T+30",
            ],
            index=0,
            key=f"{date_key}_strip_metric",
            label_visibility="collapsed",
            help=(
                "Order strip cards (one card per ticker = latest recommendation). "
                "\"Last recommended\" = newest pick date first."
            ),
        )
    with t_strip_b:
        strip_ord_disabled = strip_metric == "Last recommended"
        st.selectbox(
            "Strip order",
            ["High to low", "Low to high"],
            index=0,
            key=f"{date_key}_strip_order",
            label_visibility="collapsed",
            disabled=strip_ord_disabled,
            help="Dates: High = newest first. Returns: High = larger % first.",
        )

    strip_ord = st.session_state.get(f"{date_key}_strip_order", "High to low")
    df_sl = df_latest.copy()
    if "_rd" not in df_sl.columns:
        df_sl["_rd"] = pd.to_datetime(df_sl["Date"], errors="coerce")

    metric_to_col = {
        "Last recommended": "_rd",
        "Recommendation date": "_rd",
        "EOD %": "EOD Change %",
        "T+3": "Return 3D",
        "T+7": "Return 7D",
        "T+14": "Return 14D",
        "T+30": "Return 30D",
    }
    scol = metric_to_col[strip_metric]
    if strip_metric == "Last recommended":
        asc = False
    else:
        asc = strip_ord == "Low to high"
    df_sl = df_sl.sort_values(
        by=scol,
        ascending=asc,
        na_position="last",
        kind="mergesort",
    )

    cards: list[str] = ['<div class="gf-strip-scroll">']
    for _, row in df_sl.iterrows():
        try:
            rec_date = _date.fromisoformat(str(row["Date"]))
        except (TypeError, ValueError):
            rec_date = today_d
        days_elapsed = (today_d - rec_date).days
        tkr = str(row["Ticker"]).strip().upper()
        g_url = html.escape(_google_finance_url(tkr), quote=True)

        eod_h = _strip_eod_html(row["EOD Change %"])

        s3, n3 = _fmt_return(row["Return 3D"], days_elapsed, 3)
        s7, n7 = _fmt_return(row["Return 7D"], days_elapsed, 7)
        s14, n14 = _fmt_return(row["Return 14D"], days_elapsed, 14)
        s30, n30 = _fmt_return(row["Return 30D"], days_elapsed, 30)
        r3_h = _strip_cell_pct_html(n3, s3)
        r7_h = _strip_cell_pct_html(n7, s7)
        r14_h = _strip_cell_pct_html(n14, s14)
        r30_h = _strip_cell_pct_html(n30, s30)

        cards.append(
            f'<a class="gf-mini-card" href="{g_url}" target="_blank" rel="noopener noreferrer">'
            f'<div class="gf-mini-name">{html.escape(tkr)}</div>'
            f'<div class="gf-mini-date">{html.escape(str(row["Date"]))}</div>'
            '<div class="gf-mini-table">'
            '<span class="gf-mini-lbl">EOD</span>'
            f"{eod_h}"
            '<span class="gf-mini-lbl">+3</span>'
            f"{r3_h}"
            '<span class="gf-mini-lbl">+7</span>'
            f"{r7_h}"
            '<span class="gf-mini-lbl">+14</span>'
            f"{r14_h}"
            '<span class="gf-mini-lbl">+30</span>'
            f"{r30_h}"
            "</div>"
            "</a>"
        )
    cards.append("</div>")
    st.markdown("".join(cards), unsafe_allow_html=True)
    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)


def _render_predictions(
    strategy: str,
    label: str,
    caption: str,
    empty_cmd: str,
    date_key: str,
    show_metrics: bool = False,
) -> None:
    """Render a full prediction tab for the given strategy ('alpha' or 'squeeze')."""
    if label.strip():
        st.markdown(f'<p class="section-label">{label}</p>', unsafe_allow_html=True)
    if caption.strip():
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
        "Return 3D", "Return 7D", "Return 14D", "Return 30D",
        "Metrics", "Price at pick", "Return session",
        "Target $", "Target Hit Date",
    ]
    for col in (
        "Confidence", "EOD Change %", "Return 3D", "Return 7D", "Return 14D", "Return 30D",
        "Price at pick", "Return session", "Target $",
    ):
        df_p[col] = pd.to_numeric(df_p[col], errors="coerce")

    _today_strip = _date.today()
    df_strip_latest = _prepare_strip_latest_per_ticker(df_p)
    _render_gf_quote_strip(df_strip_latest, date_key, _today_strip)

    # Derived: target upside %  = (target − pick) / pick · 100  (NaN if either missing)
    _pick_num   = pd.to_numeric(df_p["Price at pick"], errors="coerce")
    _target_num = pd.to_numeric(df_p["Target $"],      errors="coerce")
    df_p["Target Upside %"] = (
        (_target_num - _pick_num) / _pick_num * 100.0
    ).where(_pick_num.gt(0))

    _rec_date = pd.to_datetime(df_p["Date"], errors="coerce").dt.date
    _valid_rd = _rec_date.dropna()
    d_lo = _valid_rd.min() if not _valid_rd.empty else _date.today()
    d_hi = _valid_rd.max() if not _valid_rd.empty else _date.today()

    sort_by_labels = {
        "EOD (same day)":   "EOD Change %",
        "Session (RTH day)": "Return session",
        "T+3 return":        "Return 3D",
        "T+7 return":        "Return 7D",
        "T+14 return":      "Return 14D",
        "T+30 return":      "Return 30D",
        "Target upside %":  "Target Upside %",
        "Recommendation date": "Date",
        "Confidence":       "Confidence",
        "Ticker (A–Z)":     "Ticker",
    }
    sort_keys = list(sort_by_labels.keys())

    st.caption(
        "**Date range** · **Sort** · **Order** · **EOD status** · **Ticker** · "
        "**Min conf.** % · **T+3 / T+7 / T+14** (checkbox = has measured return) · **Dedup**"
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
            key=f"{date_key}_drng",
            label_visibility="collapsed",
            help="Only picks whose recommendation date falls in this range (inclusive).",
        )
    with fc2:
        sort_label = st.selectbox(
            "Sort",
            sort_keys,
            index=0,
            key=f"{date_key}_sort",
            label_visibility="collapsed",
            help="Order cards by the chosen column. Pending or missing values sort to the end.",
        )
    with fc3:
        order = st.selectbox(
            "Ord",
            ["High to low", "Low to high"],
            key=f"{date_key}_order",
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
            key=f"{date_key}_status",
            label_visibility="collapsed",
        )
    with fc5:
        tick_q = st.text_input(
            "Tkr",
            "",
            key=f"{date_key}_ticker",
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
            key=f"{date_key}_minconf",
            label_visibility="collapsed",
            help="Show only picks with confidence ≥ this value (0 = no filter).",
        )

    cx1, cx2, cx3, ft2 = st.columns([0.62, 0.62, 0.62, 1.45], gap="small")
    with cx1:
        req_data_3 = st.checkbox(
            "T+3",
            value=False,
            key=f"{date_key}_has3",
            help="Only picks where the T+3 return is already filled in (not pending).",
        )
    with cx2:
        req_data_7 = st.checkbox(
            "T+7",
            value=False,
            key=f"{date_key}_has7",
            help="Only picks where the T+7 return is already filled in (not pending).",
        )
    with cx3:
        req_data_14 = st.checkbox(
            "T+14",
            value=False,
            key=f"{date_key}_has14",
            help="Only picks where the T+14 return is already filled in (not pending).",
        )
    with ft2:
        hide_dups = st.toggle(
            "Hide duplicate tickers (keep first after sort)",
            value=True,
            key=f"{date_key}_hide_dups",
            help=(
                "When ON: keep only the first row per ticker after applying the "
                "current sort. Toggle OFF to inspect every recommendation date "
                "for the same name."
            ),
        )

    if isinstance(date_rng, tuple) and len(date_rng) == 2:
        lo_d, hi_d = date_rng[0], date_rng[1]
        if lo_d is not None and hi_d is not None:
            if lo_d > hi_d:
                lo_d, hi_d = hi_d, lo_d
            df_base = df_p[
                _rec_date.notna() & (_rec_date >= lo_d) & (_rec_date <= hi_d)
            ]
        elif lo_d is not None:
            df_base = df_p[_rec_date.notna() & (_rec_date >= lo_d)]
        elif hi_d is not None:
            df_base = df_p[_rec_date.notna() & (_rec_date <= hi_d)]
        else:
            df_base = df_p
    elif date_rng is not None and not isinstance(date_rng, tuple):
        df_base = df_p[_rec_date == date_rng]
    else:
        df_base = df_p

    df_work = df_base.copy()
    eod = df_work["EOD Change %"]
    if status_f == "Hit (≥6%)":
        df_work = df_work[eod.notna() & (eod >= 6.0)]
    elif status_f == "Pending EOD":
        df_work = df_work[eod.isna()]
    elif status_f == "Near (0% to <6%)":
        df_work = df_work[eod.notna() & (eod >= 0) & (eod < 6.0)]
    elif status_f == "Miss (<0%)":
        df_work = df_work[eod.notna() & (eod < 0)]
    tq = (tick_q or "").strip().upper()
    if tq:
        df_work = df_work[
            df_work["Ticker"].astype(str).str.upper().str.contains(tq, na=False)
        ]

    if min_conf_pct and float(min_conf_pct) > 0:
        thr = float(min_conf_pct) / 100.0
        df_work = df_work[
            df_work["Confidence"].notna() & (df_work["Confidence"] >= thr)
        ]

    if req_data_3:
        df_work = df_work[df_work["Return 3D"].notna()]
    if req_data_7:
        df_work = df_work[df_work["Return 7D"].notna()]
    if req_data_14:
        df_work = df_work[df_work["Return 14D"].notna()]

    col_sort = sort_by_labels[sort_label]
    high_first = order == "High to low"
    if col_sort == "Ticker":
        sub = df_work["Ticker"].astype(str).str.upper()
        df_work = df_work.assign(_sort_t=sub).sort_values(
            by="_sort_t",
            ascending=not high_first,
            kind="mergesort",
        ).drop(columns="_sort_t")
    else:
        asc = not high_first
        df_work = df_work.sort_values(
            by=col_sort,
            ascending=asc,
            na_position="last",
            kind="mergesort",
        )

    df_pre_dedup_size = int(df_work.shape[0])

    if hide_dups and not df_work.empty:
        _u = df_work["Ticker"].astype(str).str.upper()
        df_work = df_work.assign(_uk=_u).drop_duplicates(
            subset=["_uk"], keep="first"
        ).drop(columns="_uk")

    df_view = df_work
    dup_hidden_n = max(0, df_pre_dedup_size - int(df_view.shape[0]))

    resolved = df_view[df_view["EOD Change %"].notna()]
    hits     = resolved[resolved["EOD Change %"] >= 6.0]
    hit_rate = f"{len(hits)/len(resolved)*100:.0f}%" if len(resolved) > 0 else "—"

    c1, c2, c3, c4 = st.columns(4)
    _picks_label = "Unique Tickers" if hide_dups else "Total Picks"
    _picks_delta = (
        f"-{dup_hidden_n} dup hidden"
        if (hide_dups and dup_hidden_n > 0)
        else None
    )
    c1.metric(_picks_label, len(df_view), delta=_picks_delta, delta_color="off")
    c2.metric("Resolved",    len(resolved))
    c3.metric("Hits ≥ 6%",  len(hits))
    c4.metric("Hit Rate",    hit_rate)

    st.divider()

    today_d = _date.today()
    _view = df_view.reset_index(drop=True)
    _pfp = ""
    _klist: list[str] = []
    if not _view.empty:
        _pfp = hashlib.md5(
            "|".join(
                _row_display_label(r["Ticker"], r["Date"])
                for _, r in _view.iterrows()
            ).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        _klist = [
            _pick_cb_key(date_key, _pfp, j, r["Ticker"], r["Date"])
            for j, (_, r) in enumerate(_view.iterrows())
        ]
        sa1, sa2, sa3 = st.columns([0.9, 0.9, 4.0], gap="small")
        with sa1:
            if st.button(
                f"☑ Select all ({len(_view)})",
                key=f"{date_key}_selall_{_pfp}",
                use_container_width=True,
                help="Tick every visible card. Re-running the button after a filter change will re-sync.",
            ):
                for _k in _klist:
                    st.session_state[_k] = True
                st.rerun()
        with sa2:
            if st.button(
                "☐ Clear",
                key=f"{date_key}_clrall_{_pfp}",
                use_container_width=True,
                help="Untick all visible cards.",
            ):
                for _k in _klist:
                    st.session_state[_k] = False
                st.rerun()
        with sa3:
            st.caption(
                "Tick the boxes on the cards, then see who is in the group and the averages in the **sidebar** →"
            )

    for _row_i, (_, row) in enumerate(_view.iterrows()):
        label_tag, css_cls = _status(row)
        conf_pct           = f"{row['Confidence']:.0%}" if pd.notna(row["Confidence"]) else "N/A"
        rec_date           = _date.fromisoformat(row["Date"])
        days_elapsed       = (today_d - rec_date).days

        # ── Card header (ticker = link to per-stock page) ─────────────────────
        tkr = str(row["Ticker"])
        p_pick = row.get("Price at pick")
        p_pick_s = ""
        if p_pick is not None and pd.notna(p_pick):
            p_pick_s = f' &nbsp;·&nbsp; <span class="pred-meta" title="Last price when the pick was saved">Pick ${float(p_pick):.2f}</span>'
        st.markdown(
            '<div class="pred-card pick-row-wrap">', unsafe_allow_html=True
        )
        cb, h1, h2 = st.columns([0.1, 0.28, 0.6], gap="small")
        with cb:
            st.checkbox(
                "in_group",
                key=_pick_cb_key(
                    date_key, _pfp, _row_i, row["Ticker"], row["Date"]
                ),
                label_visibility="collapsed",
                help="Add this card to the group (summary of averages is above the list).",
            )
        with h1:
            g_url = _google_finance_url(tkr)
            st.markdown(
                f'<a class="pred-ticker" style="text-decoration:none;" href="{g_url}" '
                f'target="_blank" rel="noopener noreferrer">{html.escape(tkr)}</a>',
                unsafe_allow_html=True,
            )
        with h2:
            st.markdown(
                f"""
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:0.1rem;">
                  <span class="{css_cls}">{label_tag}</span>
                  <span class="pred-meta">{row['Date']} &nbsp;·&nbsp; Confidence: {conf_pct}
                  &nbsp;·&nbsp; Day +{days_elapsed}{p_pick_s}
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown('<hr class="divider">', unsafe_allow_html=True)

        # ── Sell Target  +  Hit / Miss badge ─────────────────────────────────
        _tgt_view = _target_status(
            row.get("Target $"),
            row.get("Price at pick"),
            rec_date,
            today_d,
            row.get("Target Hit Date"),
        )
        tgt_left, tgt_right = st.columns([0.55, 0.45], gap="small")
        with tgt_left:
            _ups_html = (
                f'<span class="pred-meta" style="margin-left:8px;">{_tgt_view["upside_str"]}</span>'
                if _tgt_view["upside_str"] else ""
            )
            st.markdown(
                '<p class="section-label" style="margin:2px 0 4px 0;">Analyst Sell Target</p>'
                f'<div style="font-size:1.05rem;font-weight:600;font-family:var(--mono);'
                f'letter-spacing:-0.3px;">{_tgt_view["target_str"]}{_ups_html}</div>',
                unsafe_allow_html=True,
            )
        with tgt_right:
            st.markdown(
                '<p class="section-label" style="margin:2px 0 4px 0;">Target Status (within T+30 high)</p>'
                f'<span style="display:inline-block;padding:5px 12px;border-radius:999px;'
                f'background:{_tgt_view["pill_bg"]};color:{_tgt_view["pill_fg"]};'
                f'font-weight:500;font-size:0.78rem;letter-spacing:0.02em;">'
                f'{html.escape(_tgt_view["pill_text"])}</span>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<div style="margin-top:8px;"></div>', unsafe_allow_html=True
        )

        # ── Squeeze-only: full quantitative metrics grid ─────────────────────
        if show_metrics:
            metrics = _parse_metrics(row.get("Metrics"))
            if metrics:
                st.markdown(
                    '<p class="section-label" style="margin-bottom:6px; color:#174EA6;">'
                    'Quantitative screen · Python-verified metrics</p>',
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
                    'background:#E8F0FE; border-left:3px solid #1A73E8; '
                    'border-radius:4px; font-size:0.72rem; color:#174EA6;">'
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
        m_eod, m_ses, m_3d, m_7d, m_14d, m_30d = st.columns(6)

        eod_val = row["EOD Change %"]
        if pd.notna(eod_val):
            eod_label         = f"{eod_val:+.2f}%"
            eod_delta, eod_dc = _metric_delta(eod_val)
        else:
            eod_label, eod_delta, eod_dc = "Pending ⏳", None, None
        m_eod.metric("EOD (Same Day)", eod_label,
                     delta=eod_delta, delta_color=eod_dc or "off")

        rs_str, rs_num = _fmt_return_session(
            row.get("Return session"), rec_date, today_d, days_elapsed
        )
        rs_delta, rs_dc = _metric_delta(rs_num)
        m_ses.metric(
            "Session (open→close)",
            rs_str,
            help="On the pick date: % change from the regular session open to that day’s close (RTH).",
            delta=rs_delta,
            delta_color=rs_dc or "off",
        )

        r3_str,  r3_num  = _fmt_return(row["Return 3D"],  days_elapsed, 3)
        r3_delta, r3_dc  = _metric_delta(r3_num)
        m_3d.metric("T+3 Days", r3_str, delta=r3_delta, delta_color=r3_dc or "off")

        r7_str,  r7_num  = _fmt_return(row["Return 7D"],  days_elapsed, 7)
        r7_delta, r7_dc  = _metric_delta(r7_num)
        m_7d.metric("T+7 Days", r7_str, delta=r7_delta, delta_color=r7_dc or "off")

        r14_str, r14_num = _fmt_return(row["Return 14D"], days_elapsed, 14)
        r14_delta, r14_dc = _metric_delta(r14_num)
        m_14d.metric("T+14 Days", r14_str, delta=r14_delta, delta_color=r14_dc or "off")

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

    if not _view.empty and _pfp:
        _mask0 = [bool(st.session_state.get(k, False)) for k in _klist]
        _sname = "Alpha" if str(strategy) == "alpha" else "Squeeze"
        with st.sidebar:
            st.divider()
            st.caption("Group (this tab)")
            st.markdown(
                f"**Group selection** &nbsp;·&nbsp; *{_sname}*  \n"
                f"*{int(_view.shape[0])}* card(s) in the current filtered list."
            )
            if not any(_mask0):
                st.caption("No checkboxes on yet. Tick a card in the list to add it to the group here.")
            else:
                _sel0 = _view[_mask0]
                _n_sel = int(_sel0.shape[0])
                st.markdown(
                    '<p class="group-summary-h" style="margin:10px 0 6px 0;">In this group</p>',
                    unsafe_allow_html=True,
                )
                st.dataframe(
                    _sel0[["Ticker", "Date"]].copy(),
                    use_container_width=True,
                    hide_index=True,
                    height=min(200, 38 + 35 * _n_sel),
                )
                st.caption(
                    f"{_n_sel} of {len(_view)} from this list selected. "
                    "Each average uses only rows with data; pending is excluded for that line."
                )

                # ── $1,000-per-pick simulation ──────────────────────────────────
                _pnl_str, _pnl_pct_s, _n_res, _n_tot = _sim_one_thousand(
                    _sel0["EOD Change %"]
                )
                _invested = _n_tot * 1000
                _is_pos = _pnl_str not in ("—",) and not _pnl_str.startswith("-")
                _pnl_color = "var(--green)" if _is_pos and _pnl_str != "—" else (
                    "var(--red)" if _pnl_str.startswith("-") else "var(--text-muted)"
                )
                with st.container(border=True):
                    st.markdown(
                        '<p class="group-summary-h" style="margin:0 0 4px 0;">'
                        '$1,000 per pick &mdash; EOD simulation</p>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='font-size:1.55rem;font-weight:700;font-family:var(--mono);"
                        f"color:{_pnl_color};letter-spacing:-0.5px;'>{_pnl_str}</div>",
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        f"{_pnl_pct_s} on **${_invested:,}** invested"
                        + (
                            f" &nbsp;·&nbsp; {_n_res}/{_n_tot} resolved (rest excluded)"
                            if _n_res != _n_tot else f" &nbsp;·&nbsp; {_n_tot} pick(s)"
                        )
                    )
                    st.caption(
                        "Assumes you bought $1,000 per resolved pick at the open (or pick close) "
                        "and sold at the same trading day's EOD. Rows still pending EOD are not counted."
                    )

                with st.container(border=True):
                    st.caption("Averages")
                    r0, r1 = st.columns(2)
                    r0.metric("In group", f"{_n_sel}")
                    r1.metric("Avg conf.", _mean_display_conf(_sel0["Confidence"]))
                    a1, a2 = st.columns(2)
                    a1.metric("Avg EOD", _mean_display_pct(_sel0["EOD Change %"]))
                    a2.metric("Avg T+3", _mean_display_pct(_sel0["Return 3D"]))
                    a3, a4 = st.columns(2)
                    a3.metric("Avg T+7", _mean_display_pct(_sel0["Return 7D"]))
                    a4.metric("Avg T+14", _mean_display_pct(_sel0["Return 14D"]))
                    a5, a6 = st.columns(2)
                    a5.metric("Avg T+30", _mean_display_pct(_sel0["Return 30D"]))
                    a6.metric("Avg session (RTH)", _mean_display_pct(_sel0["Return session"]))

                    # Sell-target group stats
                    _ups = pd.to_numeric(_sel0["Target Upside %"], errors="coerce").dropna()
                    _hit_status = _sel0["Target Hit Date"].astype(str).str.strip()
                    _resolved = _hit_status[
                        (_hit_status != "") & (_hit_status.str.lower() != "none")
                    ]
                    if len(_resolved) > 0:
                        _hits = _resolved[_resolved.str.upper() != "MISSED"]
                        _hit_rate = f"{len(_hits) / len(_resolved) * 100:.0f}%"
                    else:
                        _hit_rate = "—"
                    t1, t2 = st.columns(2)
                    t1.metric(
                        "Avg target upside",
                        f"+{_ups.mean():.2f}%" if not _ups.empty else "—",
                        help="Average analyst upside ((target − pick) / pick) for picks in this group that have a target.",
                    )
                    t2.metric(
                        "Target hit rate",
                        _hit_rate,
                        help="Share of picks whose target was reached within the T+30 window. Pending picks are excluded from the denominator.",
                    )

    with st.expander("📋  Raw predictions table"):
        raw_cols     = ["Date", "Ticker", "Confidence", "Price at pick",
                        "Target $", "Target Upside %", "Target Hit",
                        "EOD Change %", "Return session",
                        "Return 3D", "Return 7D", "Return 14D", "Return 30D"]
        _today_d = _date.today()

        def _hit_label(row) -> str:
            tgt = row.get("Target $")
            if tgt is None or pd.isna(tgt):
                return "—"
            hit = str(row.get("Target Hit Date") or "").strip()
            if hit and hit.upper() != "MISSED":
                return f"✓ {hit}"
            try:
                rd = _date.fromisoformat(str(row["Date"]))
            except (ValueError, TypeError):
                return "Pending"
            if hit.upper() == "MISSED" or (rd + timedelta(days=30)) < _today_d:
                return "✗ Missed"
            return "Pending"

        df_view_raw = df_view.copy()
        df_view_raw["Target Hit"] = df_view_raw.apply(_hit_label, axis=1)
        display_cols = df_view_raw[raw_cols].copy()
        display_cols["Confidence"] = display_cols["Confidence"].apply(
            lambda x: f"{x:.0%}" if pd.notna(x) else "N/A")
        display_cols["Price at pick"] = display_cols["Price at pick"].apply(
            lambda x: f"${float(x):.2f}" if pd.notna(x) else "—"
        )
        display_cols["Target $"] = display_cols["Target $"].apply(
            lambda x: f"${float(x):.2f}" if pd.notna(x) else "—"
        )
        display_cols["Target Upside %"] = display_cols["Target Upside %"].apply(
            lambda x: f"+{x:.1f}%" if pd.notna(x) and x >= 0 else (
                f"{x:.1f}%" if pd.notna(x) else "—"
            )
        )
        for c in ("EOD Change %", "Return session",
                  "Return 3D", "Return 7D", "Return 14D", "Return 30D"):
            display_cols[c] = display_cols[c].apply(
                lambda x: f"{x:+.2f}%" if pd.notna(x) else "⏳")
        show_tbl = display_cols.copy()
        show_tbl.insert(
            0,
            "↗",
            show_tbl["Ticker"].apply(lambda t: _google_finance_url(str(t))),
        )
        st.dataframe(
            show_tbl,
            use_container_width=True,
            hide_index=True,
            column_config={
                "↗": st.column_config.LinkColumn(
                    "↗", help="Open in Google Finance (new tab)", display_text="●"
                ),
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
#  TAB 1 — Alpha Breakouts
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    _render_predictions(
        strategy="alpha",
        label="",
        caption="",
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
