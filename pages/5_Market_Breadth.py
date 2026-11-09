import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from core import market, state

st.set_page_config(page_title="Market Breadth", page_icon="🌊", layout="wide")
demo = state.sidebar()
state.hero("Market breadth", "How many stocks are really participating in the move.")

b = state.load_breadth(demo)
if state.need_data(b):
    st.stop()
regime, why = market.breadth_regime(b)
st.info(f"**{regime}** - {why}")
last = b.iloc[-1]
c = st.columns(6)
c[0].metric("Advances", int(last["advances"]))
c[1].metric("Declines", int(last["declines"]))
c[2].metric("A/D ratio", state.num(last["ad_ratio"], 2))
c[3].metric("% > 50 DMA", f"{last['pct_above_50dma']:.0f}%")
c[4].metric("% > 200 DMA", f"{last['pct_above_200dma']:.0f}%")
c[5].metric("McClellan", state.num(last["mcclellan"], 0), help="> 0 breadth improving, < 0 deteriorating")

win = st.select_slider("Window", ["3M", "6M", "1Y", "2Y"], "1Y")
b = b.tail({"3M": 63, "6M": 126, "1Y": 252, "2Y": 504}[win])
fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                    subplot_titles=("Equal-weight Nifty 500 vs A/D line", "% of stocks above 50 / 200 DMA",
                                    "New 52-week highs vs lows", "McClellan oscillator"))
fig.add_trace(go.Scatter(x=b.index, y=b["equal_weight_index"], name="EW index"), 1, 1)
fig.add_trace(go.Scatter(x=b.index, y=b["ad_line"] / b["ad_line"].abs().max() * b["equal_weight_index"].max(),
                         name="A/D line (scaled)", line=dict(dash="dot")), 1, 1)
fig.add_trace(go.Scatter(x=b.index, y=b["pct_above_50dma"], name="> 50 DMA"), 2, 1)
fig.add_trace(go.Scatter(x=b.index, y=b["pct_above_200dma"], name="> 200 DMA"), 2, 1)
fig.add_trace(go.Bar(x=b.index, y=b["new_52w_highs"], name="52w highs", marker_color="#2ca02c"), 3, 1)
fig.add_trace(go.Bar(x=b.index, y=-b["new_52w_lows"], name="52w lows", marker_color="#d62728"), 3, 1)
fig.add_trace(go.Bar(x=b.index, y=b["mcclellan"], name="McClellan",
                     marker_color=["#2ca02c" if v > 0 else "#d62728" for v in b["mcclellan"]]), 4, 1)
fig.update_layout(height=900, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h"))
st.plotly_chart(fig, use_container_width=True)

st.subheader("Sector rotation")
period = st.radio("Return period", [5, 21, 63, 126], index=1, horizontal=True, format_func=lambda d: f"{d} sessions")
sp = market.sector_performance(state.load_prices(demo), state.load_universe(), period)
if len(sp):
    f2 = go.Figure(go.Bar(x=sp["mean"] * 100, y=sp.index, orientation="h",
                          marker_color=["#2ca02c" if v > 0 else "#d62728" for v in sp["mean"]],
                          text=[f"{v:.1%}" for v in sp["mean"]]))
    f2.update_layout(height=600, yaxis=dict(autorange="reversed"), xaxis_title="Avg return %",
                     margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(f2, use_container_width=True)
