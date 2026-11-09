"""Research desk home (admin): regime, breadth, raw ideas and signals."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import data, ledger, market, state, technicals

st.set_page_config(page_title="Nifty 500 Research Desk", page_icon="📊", layout="wide")
demo = state.sidebar()
state.require_admin()
state.hero("Market command centre", "Value ideas, swing &amp; F&amp;O setups, breadth, flows and macro, with an auditable track record.")

state.quick_links()

px = state.load_prices(demo)
if state.need_data(px):
    st.stop()

# ---- market regime
b = state.load_breadth(demo)
regime, why = market.breadth_regime(b)
last = b.iloc[-1]
c = st.columns(5)
c[0].metric("Market regime", regime, help=why)
c[1].metric("Advances / Declines", f"{int(last['advances'])} / {int(last['declines'])}")
c[2].metric("% above 200 DMA", f"{last['pct_above_200dma']:.0f}%",
            f"{last['pct_above_200dma'] - b['pct_above_200dma'].iloc[-6]:+.0f} pts (1w)")
c[3].metric("% above 50 DMA", f"{last['pct_above_50dma']:.0f}%",
            f"{last['pct_above_50dma'] - b['pct_above_50dma'].iloc[-6]:+.0f} pts (1w)")
c[4].metric("52w highs / lows", f"{int(last['new_52w_highs'])} / {int(last['new_52w_lows'])}")
st.info(f"**{regime}:** {why}")

left, right = st.columns(2)
with left:
    st.subheader("Top fundamental ideas")
    sc = state.load_screen(demo)
    if len(sc):
        top = sc[sc["verdict"] == "Strong idea"].head(10)
        st.dataframe(top[["symbol", "name", "industry", "score", "price", "dcf_upside", "pe", "peg", "roce"]],
                     hide_index=True, width="stretch",
                     column_config={"dcf_upside": st.column_config.NumberColumn("DCF upside", format="percent"),
                                    "roce": st.column_config.NumberColumn("ROCE", format="percent"),
                                    "price": st.column_config.NumberColumn(format="%.1f"),
                                    "pe": st.column_config.NumberColumn("PE", format="%.1f"),
                                    "peg": st.column_config.NumberColumn("PEG", format="%.2f")})
        st.page_link("pages/1_Fundamental_Ideas.py", label="All fundamental ideas →")
    else:
        st.write("No fundamentals loaded yet.")
with right:
    st.subheader("Today's technical signals")
    sig = technicals.scan(px, lookback_days=1)
    grades = state.backtest_grades(demo)
    if len(sig):
        sig["backtest grade"] = sig["setup"].map(grades).fillna("not tested")
        st.dataframe(sig[["symbol", "setup", "side", "entry", "stop", "target", "backtest grade"]].head(12),
                     hide_index=True, width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="%.1f") for k in ("entry", "stop", "target")})
    else:
        st.write("No setups triggered on the latest session.")
    st.page_link("pages/3_Trading_Ideas.py", label="Trading & F&O ideas →")

st.subheader("Breadth vs equal-weight Nifty 500")
fig = go.Figure()
fig.add_trace(go.Scatter(x=b.index, y=b["equal_weight_index"], name="Equal-weight N500", yaxis="y1"))
fig.add_trace(go.Scatter(x=b.index, y=b["pct_above_200dma"], name="% above 200 DMA", yaxis="y2",
                         line=dict(dash="dot")))
fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"),
                  yaxis2=dict(overlaying="y", side="right", range=[0, 100]))
st.plotly_chart(fig, width="stretch")

st.subheader("Track record snapshot")
ib = ledger.ideas()
if len(ib):
    ev = ledger.evaluate_ideas(ib, px)
    closed = ev[ev["status"].isin(["target", "stop", "expired"])]
    k = st.columns(4)
    k[0].metric("Ideas published", len(ib))
    k[1].metric("Closed", len(closed))
    k[2].metric("Hit rate", state.pct(closed["ret"].gt(0).mean()) if len(closed) else "-")
    k[3].metric("Avg return / idea", state.pct(closed["ret"].mean()) if len(closed) else "-")
    ok, msg = ledger.verify_chain()
    (st.success if ok else st.error)(f"Integrity check: {msg}")
else:
    st.write("No ideas published yet. Publish from the Fundamental or Trading pages to start a verifiable track record.")
