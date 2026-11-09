import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import cloud, snapshot, state

st.set_page_config(page_title="Paper Trading", page_icon="🎮", layout="wide")
demo = state.sidebar()
state.require_admin()
state.hero("Paper trading: Claude's book", "₹20 lakh paper capital · max 5 trades a day · max 2% risk per trade · "
           "fills and P&L computed from real prices.")

c = cloud.client()
if not c:
    st.info("Paper trading needs Supabase (the trades and results live there).")
    st.stop()

trades = pd.DataFrame(c.select("paper_trades", {"order": "id.asc"}))
if trades.empty:
    st.info("No paper trades yet. Claude places them in Supabase before the session; results appear after the "
            "next refresh or 'Update snapshot'.")
    st.stop()

# Recompute live from the loaded prices so the page is current even between snapshots
res = pd.DataFrame(snapshot.evaluate_paper(trades.to_dict("records"), state.load_prices(False)))
df = trades.merge(res, left_on="id", right_on="trade_id", how="left")
df["risk"] = (df["entry"] - df["stop"]).abs() * df["qty"]
closed = df[df["status"].isin(["target", "stop", "expired"])]
opn = df[df["status"] == "open"]

realised, unreal = closed["net_pnl"].sum(), opn["net_pnl"].sum()
equity = snapshot.CAPITAL + realised + unreal
k = st.columns(6)
k[0].metric("Equity", f"₹{equity:,.0f}", f"{(equity / snapshot.CAPITAL - 1):+.2%}")
k[1].metric("Realised P&L", f"₹{realised:,.0f}")
k[2].metric("Open P&L", f"₹{unreal:,.0f}")
k[3].metric("Trades", f"{len(df)} ({len(opn)} open)")
k[4].metric("Win rate", f"{(closed['net_pnl'] > 0).mean():.0%}" if len(closed) else "-")
k[5].metric("Avg R (closed)", f"{closed['r_multiple'].mean():.2f}" if len(closed) else "-")

breaches = snapshot.rule_checks(df)
(st.error("Rule breaches: " + "; ".join(breaches)) if breaches else st.success("All trades within the rules "
                                                                                "(≤5 a day, ≤2% risk each)."))

if len(closed):
    closed = closed.assign(exit_date=pd.to_datetime(closed["exit_date"]))
    daily = closed.groupby("exit_date")["net_pnl"].sum()
    t1, t2, t3 = st.tabs(["Daily", "Weekly", "Monthly"])
    for tab, rule, label in ((t1, "D", "%d %b"), (t2, "W-FRI", "w/e %d %b"), (t3, "ME", "%b %Y")):
        with tab:
            agg = daily.resample(rule).sum()
            agg = agg[agg != 0] if rule == "D" else agg
            fig = go.Figure(go.Bar(x=agg.index.strftime(label), y=agg.values,
                                   marker_color=[state.UP if v >= 0 else state.DOWN for v in agg.values]))
            fig.update_layout(height=280, yaxis_tickprefix="₹", margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch")
    curve = snapshot.CAPITAL + daily.cumsum()
    fig = go.Figure(go.Scatter(x=curve.index, y=curve.values, mode="lines+markers", line=dict(color=state.ACCENT)))
    fig.update_layout(height=280, title="Realised equity curve", yaxis_tickprefix="₹",
                      margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, width="stretch")

N = st.column_config.NumberColumn
st.subheader("All trades")
st.dataframe(df[["id", "trade_date", "symbol", "segment", "side", "entry_type", "entry", "stop", "target", "qty",
                 "risk", "status", "fill", "exit", "exit_date", "net_pnl", "r_multiple", "setup", "grade", "rationale"]]
             .iloc[::-1], hide_index=True, width="stretch",
             column_config={"risk": N("Risk ₹", format="%.0f"), "net_pnl": N("Net P&L ₹", format="%.0f"),
                            "r_multiple": N("R", format="%.2f"), "fill": N(format="%.2f"), "exit": N(format="%.2f")})
st.caption("Fills: market orders at the session open; limit orders only if the session trades through the limit. "
           "If a bar touches stop and target, the stop counts. Costs 0.1% per side. F&O trades are booked at "
           "futures exposure on the underlying (no option prices in free data). Paper results only.")
