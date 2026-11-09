import plotly.graph_objects as go
import streamlit as st

from core import market, state

st.set_page_config(page_title="Macro", page_icon="🌍", layout="wide")
demo = state.sidebar()
state.hero("Macro picture", "Currency, crude, yields, volatility and the economy.")


@st.cache_data(ttl=3600, show_spinner="Loading macro data...")
def load(demo):
    return market.macro_prices(demo), market.world_bank(demo)


try:
    m, wb = load(demo)
except Exception as e:
    st.error(f"Could not load macro data: {e}")
    st.stop()

st.subheader("Market dashboard")
cols = st.columns(len(m.columns) if len(m.columns) <= 5 else 5)
for i, name in enumerate(m.columns):
    s = m[name].dropna()
    if len(s) > 21:
        cols[i % len(cols)].metric(name, f"{s.iloc[-1]:,.2f}", f"{s.iloc[-1] / s.iloc[-22] - 1:+.1%} (1m)")

st.subheader("What it means")
for head, imp in market.macro_signals(m):
    st.markdown(f"- **{head}** → {imp}")

st.subheader("Relative performance (rebased to 100)")
pick = st.multiselect("Series", list(m.columns), [c for c in ("Nifty 50", "Nifty Bank", "S&P 500", "Gold") if c in m.columns])
fig = go.Figure([go.Scatter(x=m.index, y=m[c] / m[c].dropna().iloc[0] * 100, name=c) for c in pick])
fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, width="stretch")

st.subheader("India economy (World Bank, annual)")
st.dataframe(wb.iloc[::-1].style.format("{:.1f}"), width="stretch")
st.caption("For RBI repo rate, monthly CPI/IIP and GST collections, add official releases; the World Bank "
           "series are annual and lag by a year.")
