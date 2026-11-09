import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import market, state

st.set_page_config(page_title="FII / DII", page_icon="💰", layout="wide")
demo = state.sidebar()
st.title("💰 FII / DII cash-market flows")
st.caption("NSE publishes provisional FII/DII cash figures (₹ crore) for the latest day only. The app stores "
           "each day in data/fii_dii_history.csv so history builds up; run `python refresh.py` daily after 7pm IST.")

if demo:
    hist = market.demo_fii_dii()
else:
    c1, c2 = st.columns(2)
    if c1.button("Fetch today's figures from NSE"):
        try:
            hist = market.merge_fii_dii(market.fetch_fii_dii_today())
            st.success("Updated.")
        except Exception as e:
            st.error(f"NSE fetch failed ({e}). NSE sometimes blocks scripts - retry, or upload a CSV.")
    up = c2.file_uploader("Or upload history CSV (date,category[FII/DII],buy,sell,net)", type="csv")
    if up is not None:
        market.merge_fii_dii(pd.read_csv(up, parse_dates=["date"]))
        st.success("History merged.")
    hist = market.load_fii_dii_history()

if state.need_data(hist, "FII/DII history"):
    st.stop()
p = market.fii_dii_pivot(hist)
last = p.iloc[-1]
c = st.columns(4)
c[0].metric(f"FII net ({p.index[-1]:%d %b})", f"₹{last['FII']:,.0f} cr")
c[1].metric("DII net", f"₹{last['DII']:,.0f} cr")
c[2].metric("FII 5-day", f"₹{last['FII_5d']:,.0f} cr")
c[3].metric("DII 5-day", f"₹{last['DII_5d']:,.0f} cr")
mtd = p[p.index >= p.index[-1].replace(day=1)][["FII", "DII"]].sum()
st.write(f"Month to date: FII **₹{mtd['FII']:,.0f} cr**, DII **₹{mtd['DII']:,.0f} cr**")

tail = p.tail(st.slider("Sessions", 20, len(p), min(120, len(p))))
fig = go.Figure()
fig.add_trace(go.Bar(x=tail.index, y=tail["FII"], name="FII net", marker_color="#1f77b4"))
fig.add_trace(go.Bar(x=tail.index, y=tail["DII"], name="DII net", marker_color="#ff7f0e"))
fig.update_layout(barmode="group", height=380, yaxis_title="₹ crore", margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, use_container_width=True)
f2 = go.Figure([go.Scatter(x=tail.index, y=tail["FII"].cumsum(), name="FII cumulative"),
                go.Scatter(x=tail.index, y=tail["DII"].cumsum(), name="DII cumulative")])
f2.update_layout(height=320, yaxis_title="₹ crore", margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(f2, use_container_width=True)
m = p[["FII", "DII"]].resample("ME").sum()
m.index = m.index.strftime("%b %Y")
st.subheader("Monthly net flows (₹ cr)")
st.dataframe(m.iloc[::-1].style.format("{:,.0f}"), use_container_width=True)
