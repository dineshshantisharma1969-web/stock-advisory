import plotly.express as px_
import streamlit as st

from core import results, state

st.set_page_config(page_title="Quarterly Results", page_icon="🧾", layout="wide")
demo = state.sidebar()
state.hero("Results season", "Quarterly growth and margins across the Nifty 500, Q2 FY27 by default.")
st.caption("Indian FY quarters: Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar. Q2 FY27 = quarter ended "
           "30 Sep 2026. Growth is judged against the company's own year-ago quarter; no analyst consensus is used.")

fu, px = state.load_fundamentals(demo), state.load_prices(demo)
if state.need_data(fu, "fundamentals / quarterly results"):
    st.stop()
qs = results.available_quarters(fu)
if not qs:
    st.info("No quarterly data. Refresh live data (quarterly results are downloaded with fundamentals).")
    st.stop()
default = qs.index("Q2 FY27") if "Q2 FY27" in qs else 0
c1, c2, c3 = st.columns(3)
quarter = c1.selectbox("Quarter", qs, index=default)
df = results.results_table(fu, state.load_universe(), quarter, px)
if df.empty:
    st.info(f"No company has reported {quarter} in the downloaded data yet.")
    st.stop()
inds = c2.multiselect("Industry", sorted(df["industry"].unique()))
grades = c3.multiselect("Grade", ["Strong", "Turnaround", "Good", "Mixed", "Poor", "Insufficient data"])
v = df[df["industry"].isin(inds)] if inds else df
v = v[v["grade"].isin(grades)] if grades else v

n = len(df)
k = st.columns(5)
k[0].metric("Companies reported", n, help=f"out of {len(state.load_universe())} in the Nifty 500")
k[1].metric("Median revenue YoY", state.pct(df["rev_yoy"].median()))
k[2].metric("Median profit YoY", state.pct(df["np_yoy"].median()))
k[3].metric("Strong results", int((df["grade"] == "Strong").sum()))
k[4].metric("Poor results", int((df["grade"] == "Poor").sum()))

N = st.column_config.NumberColumn
st.dataframe(v, hide_index=True, use_container_width=True, height=480,
             column_config={"revenue_cr": N("Revenue ₹cr", format="%.0f"), "net_profit_cr": N("Net profit ₹cr", format="%.0f"),
                            "rev_yoy": N("Rev YoY", format="percent"), "rev_qoq": N("Rev QoQ", format="percent"),
                            "np_yoy": N("NP YoY", format="percent"), "np_qoq": N("NP QoQ", format="percent"),
                            "op_margin": N("Op margin", format="percent"), "margin_chg_yoy": N("Margin Δ YoY", format="percent"),
                            "price_1w": N("Price 1w", format="percent")})
st.download_button("Download CSV", v.to_csv(index=False), f"results_{quarter.replace(' ', '_')}.csv")

st.subheader("By industry")
ind = df.groupby("industry").agg(companies=("symbol", "count"), rev_yoy=("rev_yoy", "median"),
                                 np_yoy=("np_yoy", "median")).sort_values("np_yoy")
fig = px_.bar(ind.reset_index(), x="np_yoy", y="industry", orientation="h", hover_data=["companies", "rev_yoy"],
              labels={"np_yoy": "Median net profit YoY"})
fig.update_layout(height=600, xaxis_tickformat=".0%", margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, use_container_width=True)
st.caption("Yahoo Finance quarterly figures can lag company filings by a few days and may be consolidated or "
           "standalone depending on the company; check the exchange filing before quoting a number.")
