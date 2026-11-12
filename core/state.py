"""Shared Streamlit state: data-mode switch, cached loaders, refresh, common chart helpers."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import plotly.io as pio
from plotly.subplots import make_subplots

from . import config, data, market, technicals, valuation

BRAND = "Nifty 500 Research Desk"
ACCENT, UP, DOWN = "#22c55e", "#22c55e", "#ef4444"
SERIES = ["#38bdf8", "#f59e0b", "#a78bfa", "#22c55e", "#f472b6", "#94a3b8"]

# One chart look across every page: transparent background on the app's navy, soft gridlines.
pio.templates["desk"] = go.layout.Template(pio.templates["plotly_dark"])
pio.templates["desk"].layout.update(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(17,26,46,0.55)", colorway=SERIES,
    font=dict(family="Inter, system-ui, sans-serif", color="#cbd5e1", size=12),
    xaxis=dict(gridcolor="rgba(148,163,184,0.10)", zeroline=False),
    yaxis=dict(gridcolor="rgba(148,163,184,0.10)", zeroline=False),
    hoverlabel=dict(bgcolor="#111a2e", bordercolor="#334155"))
pio.templates.default = "desk"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select {
  font-family: 'Inter', system-ui, sans-serif !important; }
.stApp { background: radial-gradient(1200px 600px at 10% -10%, #12224a 0%, #0b1220 55%) fixed; }
.block-container { padding-top: 1.6rem; max-width: 1400px; }
section[data-testid="stSidebar"] { background: linear-gradient(180deg, #0f1a33 0%, #0b1220 100%);
  border-right: 1px solid rgba(148,163,184,.12); }
.hero { border-radius: 18px; padding: 22px 26px; margin: 0 0 18px 0;
  background: linear-gradient(120deg, rgba(34,197,94,.16), rgba(56,189,248,.12) 55%, rgba(167,139,250,.10));
  border: 1px solid rgba(148,163,184,.18); box-shadow: 0 10px 30px rgba(0,0,0,.25); }
.hero .kicker { font-size: .78rem; letter-spacing: .14em; text-transform: uppercase; color: #86efac; font-weight: 700; }
.hero h1 { font-size: 2.0rem; font-weight: 800; margin: .15rem 0 .25rem 0; color: #f8fafc; padding: 0; }
.hero p { color: #cbd5e1; margin: 0; font-size: .98rem; }
div[data-testid="stMetric"] { background: rgba(17,26,46,.75); border: 1px solid rgba(148,163,184,.16);
  border-radius: 14px; padding: 14px 16px; box-shadow: 0 4px 14px rgba(0,0,0,.18); }
div[data-testid="stMetricLabel"] p { color: #94a3b8 !important; font-size: .8rem !important; font-weight: 600;
  text-transform: uppercase; letter-spacing: .04em; }
div[data-testid="stMetricValue"] { font-weight: 700; color: #f8fafc; }
div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid rgba(148,163,184,.14); }
div[data-testid="stExpander"], div[data-testid="stForm"] { border-radius: 14px !important;
  border: 1px solid rgba(148,163,184,.16) !important; background: rgba(17,26,46,.45); }
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] { background: rgba(17,26,46,.7); border-radius: 10px 10px 0 0; padding: 8px 16px; }
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button { border-radius: 10px; font-weight: 600;
  border: 1px solid rgba(34,197,94,.45); }
.stButton > button:hover { border-color: #22c55e; color: #22c55e; }
h2, h3 { font-weight: 700 !important; letter-spacing: -.01em; }
.pill { display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 700; }
.pill.up { background: rgba(34,197,94,.15); color: #4ade80; } .pill.down { background: rgba(239,68,68,.15); color: #f87171; }
.pill.mid { background: rgba(245,158,11,.15); color: #fbbf24; }
.tile { border-radius: 16px; padding: 18px; height: 100%; background: rgba(17,26,46,.8);
  border: 1px solid rgba(148,163,184,.16); }
.tile h4 { margin: 0 0 4px 0; color: #f8fafc; } .tile p { color: #94a3b8; margin: 0; font-size: .9rem; }
.foot { color: #64748b; font-size: .78rem; margin-top: 28px; border-top: 1px solid rgba(148,163,184,.12); padding-top: 10px; }
</style>
"""


def hero(title: str, subtitle: str = "", kicker: str = BRAND):
    st.markdown(f'<div class="hero"><div class="kicker">{kicker}</div><h1>{title}</h1><p>{subtitle}</p></div>',
                unsafe_allow_html=True)


def quick_links(links=None):
    """Big shortcut buttons shown on the home page."""
    links = links or [("pages/9_Q2_Results.py", "🧾  Q2 FY27 results"), ("pages/10_Personal_Finance.py", "💼  Personal finance"),
             ("pages/1_Fundamental_Ideas.py", "📈  Value ideas"), ("pages/3_Trading_Ideas.py", "⚡  Trading & F&O"),
             ("pages/11_Client_Access.py", "📱  Client QR code")]
    cols = st.columns(len(links))
    for c, (page, label) in zip(cols, links):
        if c.button(label, width="stretch", type="primary" if "Q2" in label else "secondary"):
            st.switch_page(page)


def pill(text: str, tone: str = "mid") -> str:
    return f'<span class="pill {tone}">{text}</span>'

PRICE_TTL_H, FUND_TTL_H = 18, 24 * 7


def _admin_password() -> str | None:
    import os
    try:
        pw = st.secrets.get("admin_password")
    except Exception:
        pw = None
    return pw or os.environ.get("ADMIN_PASSWORD")


def is_admin() -> bool:
    """With no password configured (local use) everyone is admin. Once deployed for clients, set
    admin_password in .streamlit/secrets.toml (or ADMIN_PASSWORD env var): clients then get a read-only view."""
    return _admin_password() is None or st.session_state.get("admin_ok", False)


def require_admin():
    """Stop the page for clients. Admin pages are also hidden from the client menu."""
    if not is_admin():
        st.info("This section is for the research desk. Please use **My calls** and the market pages.")
        st.stop()


def _login_box():
    if _admin_password() is None:
        return
    if st.session_state.get("admin_ok"):
        if st.button("Log out (admin)", width="stretch"):
            st.session_state["admin_ok"] = False
            st.rerun()
        return
    with st.expander("Admin login"):
        pw = st.text_input("Password", type="password", key="admin_pw")
        if st.button("Log in") and pw:
            import hmac
            st.session_state["admin_ok"] = hmac.compare_digest(pw, _admin_password())
            if not st.session_state["admin_ok"]:
                st.error("Wrong password")
            st.rerun()


def sidebar() -> bool:
    """Draws the common sidebar and returns True when demo mode is on."""
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown(f"### 📊 {BRAND}")
        import sys as _sys
        st.caption(f"Version {config.APP_VERSION} · code {getattr(_sys.modules.get('core'), '_stamp', '-')}")
        _login_box()
        import os
        demo_default = st.session_state.get("demo", os.environ.get("DESK_DEMO") == "1")
        demo = demo_default if not is_admin() else st.toggle("Demo data (offline)", value=demo_default,
                         key="demo",
                         help="Synthetic data for trying the app without internet. Never real prices.")
        from . import cloud
        st.caption("Storage: ☁️ Supabase (persistent)" if cloud.client() else "Storage: 💻 this computer only")
        if is_admin() and not demo and cloud.client() and st.button("Update snapshot (no download)", width="stretch",
                                                                    help="Write today's readable summary and paper-trade "
                                                                         "results to Supabase from the data already loaded"):
            from . import snapshot
            bt, _ = data.load_cache("backtest_live")
            with st.spinner("Writing snapshot..."):
                done = snapshot.publish(cloud.client(), load_prices(False), load_universe(), load_screen(False), bt,
                                        log=st.caption)
            st.success("Snapshot: " + ", ".join(f"{k} {v}" for k, v in done.items()))
        if is_admin() and not demo:
            st.caption("Prices: NSE bhavcopy daily (official) · Yahoo weekly full refresh")
        if is_admin() and not demo:
            from . import kite
            if not kite.configured():
                st.caption("Zerodha: optional, not set up")
            else:
                info = kite.session_info()
                if info:
                    until = datetime.fromisoformat(info["valid_until"]).astimezone(kite.IST)
                    st.caption(f"Zerodha: ✅ connected until {until:%d %b %H:%M} IST")
                else:
                    st.caption(f"Kite API key in use: {kite.masked_key()}")
                    st.link_button("🔑 Connect Zerodha (daily login)", kite.login_url(), width="stretch",
                                   help="Opens Zerodha's login page. After login you come back here automatically.")
        if is_admin() and st.button("Test Supabase connection", width="stretch"):
            ok, msg = cloud.diagnose()
            if ok:
                st.success(msg)
            else:
                st.error(msg)
        saved = data.cache_saved_at("prices_live")
        st.caption(f"Live prices cached: {saved:%d %b %Y %H:%M}" if saved else "No live data downloaded yet")
        if not demo and is_admin() and st.button("Refresh live data", width="stretch"):
            refresh_live()
        st.divider()
        st.caption(config.DISCLAIMER)
    if demo:
        st.warning("DEMO MODE - synthetic data, not real market prices or financials.", icon="⚠️")
    return demo


def refresh_live(fundamentals: bool = True):
    u = load_universe()
    yp = data.YahooProvider()
    bar = st.progress(0.0, text="Downloading prices for Nifty 500...")
    syms = list(u["symbol"])
    try:
        px, msg = data.refresh_prices(syms, progress=lambda f: bar.progress(f, text=f"Prices {f:.0%}"),
                                      log=lambda m: data_note("Refresh live data", m))
        data_note("Refresh result", msg)
        if px is None:
            # Never overwrite good data with a refresh that failed
            st.error(msg + " Try again later.")
            return
        data.save_cache("prices_live", px)
        fund_at = data.cache_saved_at("fund_live")
        fund_fresh = fund_at is not None and datetime.now() - fund_at < timedelta(days=7)
        if fundamentals and fund_fresh:
            msg += f" Fundamentals are fresh (updated {fund_at:%d %b}); next update after 7 days."
        if fundamentals and not fund_fresh:
            fu = yp.all_fundamentals(syms, prices=px,
                                     progress=lambda f: bar.progress(f, text=f"Fundamentals {f:.0%} (slow)"))
            old, _ = data.load_cache("fund_live")
            merged, n_ok = data.merge_refresh(old, fu, data.usable_fundamentals)
            if n_ok:
                data.save_cache("fund_live", merged)
            msg += f" Fundamentals updated for {n_ok} of {len(syms)}"
            msg += "." if n_ok == len(syms) else "; the rest keep their previous figures."
        st.cache_data.clear()
        from . import cloud as _cloud, snapshot
        if _cloud.client():
            bt, _ = data.load_cache("backtest_live")
            done = snapshot.publish(_cloud.client(), px, u, load_screen(False), bt, log=st.caption)
            msg += f" Snapshot saved ({done.get('market_snapshot', 0)} stocks, {done.get('signals', 0)} signals)."
        st.success(msg)
        from . import cloud
        if cloud.client() is None:
            st.warning("Saved on this server only - Supabase is not connected, so this data is lost when the "
                       "app restarts. Click 'Test Supabase connection' to see why.")
    except Exception as e:
        st.error(f"Refresh failed: {e}")


@st.cache_data(show_spinner=False)
def load_universe() -> pd.DataFrame:
    return data.load_universe()


@st.cache_data(show_spinner="Loading prices...", ttl=1800)
def load_prices(demo: bool) -> dict:
    if demo:
        return data.DemoProvider().prices(list(load_universe()["symbol"]))
    px, _ = data.load_cache("prices_live")
    return px or {}


@st.cache_data(show_spinner="Loading fundamentals...", ttl=1800)
def load_fundamentals(demo: bool) -> dict:
    if demo:
        return data.DemoProvider().all_fundamentals(list(load_universe()["symbol"]))
    fu, _ = data.load_cache("fund_live")
    return fu or {}


@st.cache_data(show_spinner="Scoring Nifty 500...", ttl=1800)
def load_screen(demo: bool) -> pd.DataFrame:
    return valuation.screen(load_fundamentals(demo), load_universe())


@st.cache_data(show_spinner="Computing market breadth...", ttl=1800)
def load_breadth(demo: bool) -> pd.DataFrame:
    px = load_prices(demo)
    return market.breadth(px) if px else pd.DataFrame()


def need_data(obj, what="prices") -> bool:
    if obj is None or len(obj) == 0:
        st.info(f"No {what} yet. Click **Refresh live data** in the sidebar (needs internet), "
                "or switch on **Demo data** to explore the app.")
        return True
    return False


def backtest_grades(demo: bool) -> dict:
    s, _ = data.load_cache(f"backtest_{'demo' if demo else 'live'}")
    if s is None or s.empty:
        return {}
    return s.drop_duplicates("setup").set_index("setup")["grade"].to_dict()


def publish_block_reason(kind: str, grade: str | None = None, verdict: str | None = None,
                         gate: str | None = None, demo: bool = False) -> str | None:
    """House rule: only A-graded setups (swing/F&O) and gate-passing Strong ideas (fundamental) get published."""
    if demo:
        return "Switch off demo mode to publish - demo ideas would pollute your track record."
    if kind in ("swing", "fno"):
        if not grade or not str(grade).startswith("A"):
            return (f"Not publishable: this setup's out-of-sample backtest grade is '{grade or 'not tested'}'. "
                    "Only grade A setups are published. Run the Backtest page on live data first.")
    elif kind == "fundamental":
        if verdict != "Strong idea" or gate != "pass":
            return f"Not publishable: needs verdict 'Strong idea' and a passed quality gate (got {verdict}, {gate})."
    return None


def data_note(step: str, message: str):
    """Show a data-source note and record it in app_errors (step 'Data note: ...') so it can be reviewed later."""
    st.caption(message)
    try:
        from . import cloud
        c = cloud.client()
        if c:
            c.insert("app_errors", [{"step": f"Data note: {step}", "error": str(message)[:2000], "traceback": "",
                                     "versions": config.APP_VERSION}])
    except Exception:
        pass


def report_error(step: str, exc: Exception):
    """Show the error and also save the full traceback to Supabase (table app_errors) for diagnosis."""
    import platform
    import traceback
    from . import cloud
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    st.error(f"{step} failed: {type(exc).__name__}: {exc}. The full report was sent to Claude.")
    try:
        import numpy, plotly
        versions = (f"python {platform.python_version()}, streamlit {st.__version__}, pandas {pd.__version__}, "
                    f"numpy {numpy.__version__}, plotly {plotly.__version__}")
        c = cloud.client()
        if c:
            c.insert("app_errors", [{"step": step, "error": f"{type(exc).__name__}: {exc}"[:2000],
                                     "traceback": tb[-8000:], "versions": versions}])
    except Exception:
        pass


def prepare_for_claude():
    """One button: backtest all strategies on the loaded data, save grades, write the Supabase snapshot.
    No re-download. Each step is protected separately and saved as soon as it finishes."""
    from . import backtest, cloud, snapshot
    c = cloud.client()
    if not c:
        st.error("Supabase is not connected.")
        return
    try:
        px = load_prices(False)
    except Exception as e:
        report_error("Loading prices", e)
        return
    if not px:
        st.error("No live prices loaded yet - tap Refresh live data first.")
        return
    years = max((len(df) for df in px.values()), default=0) / 250
    if years < config.HISTORY_YEARS - 0.5:
        with st.status(f"0/3 Price history is {years:.1f} years - downloading {config.HISTORY_YEARS} years "
                       "(1-3 minutes)...", expanded=True) as pre:
            try:
                bar0 = st.progress(0.0)
                new_px, msg = data.refresh_prices(list(load_universe()["symbol"]), progress=bar0.progress,
                                                  log=lambda m: data_note("Prepare: price history", m))
                if new_px is not None:
                    data.save_cache("prices_live", new_px)
                    st.cache_data.clear()
                    px = new_px
                st.write(msg)
                pre.update(state="complete")
            except Exception as e:
                report_error("Downloading 5-year history", e)
    summ = None
    with st.status("Preparing data for Claude - keep this screen open (3-6 minutes)", expanded=True) as box:
        st.write(f"1/3 Backtesting {len(technicals.SETUPS)} strategies on {len(px)} stocks...")
        try:
            bar = st.progress(0.0)
            tr = backtest.run(px, progress=bar.progress)
            summ = backtest.summary(tr) if len(tr) else tr
            data.save_cache("backtest_live", summ)
            rows = snapshot.grade_rows(summ)
            if rows:
                c.insert("backtest_grades", rows, upsert_on="setup,sample")
            n_a = sum(1 for r in rows if str(r["grade"]).startswith("A")) // 2
            st.write(f"   saved grades: {len(rows) // 2} strategies, {n_a} graded A")
        except Exception as e:
            report_error("Backtest", e)
        st.write("2/3 Writing market snapshot, signals and breadth...")
        try:
            screen = None
            try:
                screen = load_screen(False)
            except Exception as e:
                report_error("Fundamental screen (snapshot continues without it)", e)
            done = snapshot.publish(c, px, load_universe(), screen, summ,
                                    log=lambda m: (st.write("   " + m), report_error("Snapshot part", RuntimeError(m))))
            st.write("   " + ", ".join(f"{k}: {v}" for k, v in done.items()))
        except Exception as e:
            report_error("Snapshot", e)
        st.write("3/3 Forward-test book (Momentum leaders)...")
        try:
            from . import forward
            placed = forward.run(c, px)
            st.write(f"   {len(placed)} new trade(s): " + ", ".join(r["symbol"] for r in placed) if placed else
                     "   no new trades (no signal, breadth below 50%, or already holding)")
        except Exception as e:
            report_error("Forward-test book", e)
        st.write("Finished.")
        box.update(label="Finished - tell Claude \"done\"", state="complete", expanded=True)


def pct(x, d=1):
    return "-" if pd.isna(x) else f"{x:.{d}%}"


def num(x, d=1):
    return "-" if pd.isna(x) else f"{x:,.{d}f}"


# ------------------------------------------------------------------ charts
def price_chart(df: pd.DataFrame, title: str, show=("dma", "bb"), height=760) -> go.Figure:
    x = technicals.add_indicators(df).tail(260)
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.55, 0.15, 0.15, 0.15],
                        vertical_spacing=0.035, subplot_titles=("", "Volume", "MACD (12,26,9)", "RSI (14)"))
    fig.add_trace(go.Candlestick(x=x.index, open=x["Open"], high=x["High"], low=x["Low"], close=x["Close"],
                                 name="Price", showlegend=False), 1, 1)
    if "dma" in show:
        for n, col in ((20, "#9467bd"), (50, "#ff7f0e"), (200, "#1f77b4")):
            fig.add_trace(go.Scatter(x=x.index, y=x[f"dma{n}"], name=f"{n} DMA", line=dict(width=1.3, color=col)), 1, 1)
    if "bb" in show:
        fig.add_trace(go.Scatter(x=x.index, y=x["bb_up"], name="BB upper", line=dict(width=0.8, dash="dot", color="grey")), 1, 1)
        fig.add_trace(go.Scatter(x=x.index, y=x["bb_lo"], name="BB lower", line=dict(width=0.8, dash="dot", color="grey"),
                                 fill="tonexty", fillcolor="rgba(128,128,128,0.08)"), 1, 1)
    fig.add_trace(go.Bar(x=x.index, y=x["Volume"], name="Volume", marker_color="rgba(100,100,160,0.5)", showlegend=False), 2, 1)
    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in x["hist"]]
    fig.add_trace(go.Bar(x=x.index, y=x["hist"], marker_color=colors, showlegend=False), 3, 1)
    fig.add_trace(go.Scatter(x=x.index, y=x["macd"], name="MACD", line=dict(width=1.2, color="#1f77b4")), 3, 1)
    fig.add_trace(go.Scatter(x=x.index, y=x["signal"], name="Signal", line=dict(width=1.2, color="#ff7f0e")), 3, 1)
    fig.add_trace(go.Scatter(x=x.index, y=x["rsi"], name="RSI", line=dict(width=1.2, color="#9467bd"), showlegend=False), 4, 1)
    for lvl in (30, 70):
        fig.add_hline(y=lvl, line=dict(dash="dot", width=0.8, color="grey"), row=4, col=1)
    fig.update_layout(height=height, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=70, b=10),
                      title=dict(text=f"<b>{title}</b>", x=0, y=0.99, font=dict(size=18, color="#f8fafc")),
                      legend=dict(orientation="h", y=1.0, yanchor="bottom", x=1, xanchor="right", font=dict(size=11)))
    fig.update_traces(selector=dict(type="candlestick"), increasing_line_color=UP, decreasing_line_color=DOWN,
                      increasing_fillcolor=UP, decreasing_fillcolor=DOWN)
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig
