import plotly.express as px_
import streamlit as st

from core import backtest, data, state, technicals

st.set_page_config(page_title="Backtest", page_icon="🧪", layout="wide")
demo = state.sidebar()
state.require_admin()
state.hero("Backtest lab", "Prove a setup on history before it reaches a client.")
st.markdown("""
Entry at the **next day's open** (no look-ahead) · stop/target from ATR · if a bar hits both, the **stop counts first** ·
round-trip costs deducted · the last 30% of the period is held out as **out-of-sample**.
Grade **A** (publishable) needs ≥30 out-of-sample trades, expectancy > 0.2R and profit factor > 1.4.
""")

px = state.load_prices(demo)
if state.need_data(px):
    st.stop()

c = st.columns(5)
setups = c[0].multiselect("Setups", list(technicals.SETUPS), list(technicals.SETUPS))
hold = c[1].number_input("Max holding (sessions)", 3, 60, 20)
cost = c[2].number_input("Round-trip cost %", 0.0, 2.0, 0.20, 0.05) / 100
atr_m = c[3].number_input("Stop = ATR x", 0.5, 5.0, 2.0, 0.5)
rr = c[4].number_input("Target = R x", 0.5, 5.0, 2.0, 0.5)
short = st.checkbox("Include short signals (needs F&O)", True)

if st.button("Run backtest on Nifty 500", type="primary"):
    bar = st.progress(0.0)
    tr = backtest.run(px, setups, progress=bar.progress, max_hold=hold, cost_pct=cost, atr_mult=atr_m, rr=rr,
                      allow_short=short)
    st.session_state["bt"] = tr
    data.save_cache(f"backtest_{'demo' if demo else 'live'}", backtest.summary(tr) if len(tr) else tr)

tr = st.session_state.get("bt")
if tr is None:
    s, saved = data.load_cache(f"backtest_{'demo' if demo else 'live'}")
    if s is not None and len(s):
        st.caption(f"Last saved summary ({saved:%d %b %Y %H:%M}). Run again for trade-level detail.")
        st.dataframe(s, hide_index=True, width="stretch")
    st.stop()
if tr.empty:
    st.write("No trades.")
    st.stop()

s = backtest.summary(tr)
N = __import__("streamlit").column_config.NumberColumn
st.dataframe(s, hide_index=True, width="stretch",
             column_config={"win_rate": N("Win rate", format="percent"), "avg_ret": N("Avg ret", format="percent"),
                            "expectancy_r": N("Expectancy (R)", format="%.2f"), "profit_factor": N("PF", format="%.2f"),
                            "avg_win": N(format="percent"), "avg_loss": N(format="percent"),
                            "max_dd_r": N("Max DD (R)", format="%.1f"), "avg_bars": N("Avg bars", format="%.1f")})
curve = tr.sort_values("exit_date").assign(cum_r=lambda d: d.groupby("setup")["r"].cumsum())
fig = px_.line(curve, x="exit_date", y="cum_r", color="setup", title="Cumulative R by setup")
fig.add_vline(x=tr.loc[tr["sample"] == "out-of-sample", "entry_date"].min(), line_dash="dash",
              annotation_text="out-of-sample →")
st.plotly_chart(fig, width="stretch")
st.download_button("Download all trades", tr.to_csv(index=False), "backtest_trades.csv")
