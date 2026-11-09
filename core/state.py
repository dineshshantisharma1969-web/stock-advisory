"""Shared Streamlit state: data-mode switch, cached loaders, refresh, common chart helpers."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from . import config, data, market, technicals, valuation

PRICE_TTL_H, FUND_TTL_H = 18, 24 * 7


def sidebar() -> bool:
    """Draws the common sidebar and returns True when demo mode is on."""
    with st.sidebar:
        demo = st.toggle("Demo data (offline)", value=st.session_state.get("demo", False), key="demo",
                         help="Synthetic data for trying the app without internet. Never real prices.")
        _, saved = data.load_cache("prices_live")
        st.caption(f"Live prices cached: {saved:%d %b %Y %H:%M}" if saved else "No live data downloaded yet")
        if not demo and st.button("Refresh live data", use_container_width=True):
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
    try:
        px = yp.prices(list(u["symbol"]), progress=lambda f: bar.progress(f, text=f"Prices {f:.0%}"))
        data.save_cache("prices_live", px)
        if fundamentals:
            fu = yp.all_fundamentals(list(u["symbol"]),
                                     progress=lambda f: bar.progress(f, text=f"Fundamentals {f:.0%} (slow)"))
            data.save_cache("fund_live", fu)
        st.cache_data.clear()
        st.success(f"Updated {len(px)} price histories.")
    except Exception as e:
        st.error(f"Refresh failed: {e}")


@st.cache_data(show_spinner=False)
def load_universe() -> pd.DataFrame:
    return data.load_universe()


@st.cache_data(show_spinner="Loading prices...")
def load_prices(demo: bool) -> dict:
    if demo:
        return data.DemoProvider().prices(list(load_universe()["symbol"]))
    px, _ = data.load_cache("prices_live")
    return px or {}


@st.cache_data(show_spinner="Loading fundamentals...")
def load_fundamentals(demo: bool) -> dict:
    if demo:
        return data.DemoProvider().all_fundamentals(list(load_universe()["symbol"]))
    fu, _ = data.load_cache("fund_live")
    return fu or {}


@st.cache_data(show_spinner="Scoring Nifty 500...")
def load_screen(demo: bool) -> pd.DataFrame:
    return valuation.screen(load_fundamentals(demo), load_universe())


@st.cache_data(show_spinner="Computing market breadth...")
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


def pct(x, d=1):
    return "-" if pd.isna(x) else f"{x:.{d}%}"


def num(x, d=1):
    return "-" if pd.isna(x) else f"{x:,.{d}f}"


# ------------------------------------------------------------------ charts
def price_chart(df: pd.DataFrame, title: str, show=("dma", "bb"), height=760) -> go.Figure:
    x = technicals.add_indicators(df).tail(260)
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.55, 0.15, 0.15, 0.15],
                        vertical_spacing=0.02, subplot_titles=(title, "Volume", "MACD (12,26,9)", "RSI (14)"))
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
    fig.update_layout(height=height, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=1.04, x=0))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig
