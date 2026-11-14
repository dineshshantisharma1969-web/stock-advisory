import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import cloud, snapshot, state

st.set_page_config(page_title="Paper Trading", page_icon="🎮", layout="wide")
demo = state.sidebar()
state.require_admin()
state.hero("Paper trading", "₹20 lakh paper capital per book · fills and P&L computed from real prices.")

c = cloud.client()
if not c:
    st.info("Paper trading needs Supabase (the trades and results live there).")
    st.stop()

from core import forward, income
BOOKS = {"claude": ("Main book (A-grade setups only)", 5, 0.02),
         forward.BOOK: ("Forward test: Momentum leaders", forward.MAX_NEW, forward.RISK),
         forward.DIP_BOOK: ("Forward test: Pullbacks (F&O stocks)", forward.MAX_NEW, forward.RISK),
         income.BOOK: ("Forward test: Nifty weekly condor", 1, 0.02)}
book = st.radio("Book", list(BOOKS), format_func=lambda b: BOOKS[b][0], horizontal=True)
label, max_day, max_risk = BOOKS[book]

_runs = c.select("bot_runs", {"order": "id.desc", "limit": "1"})
if _runs:
    _r = _runs[0]
    _at = pd.Timestamp(_r["run_at"]).tz_convert("Asia/Kolkata")
    with st.expander(("✅" if _r.get("ok") else "⚠️") + f" Bot summary · ran {_at:%d %b %H:%M} · prices of {_r.get('session')}",
                     expanded=False):
        st.caption("WhatsApp-ready text - use the copy icon at the top right of the box.")
        st.code(_r.get("summary") or "", language=None)
        if not _r.get("ok"):
            st.warning(_r.get("log") or "The bot reported a problem.")
else:
    st.caption("🤖 The daily bot has not run yet (office PC, weekdays 8:30 am).")

if book == income.BOOK:
    st.caption("Rules: one hedged condor a week, one lot · sold strikes 0.75 expected moves from Nifty · closed if "
               "Nifty ends a day at or beyond a sold strike, else held to expiry · every price is NSE's closing "
               "price, 1 point cost per leg.")
    ct = pd.DataFrame(c.select("condor_trades", {"order": "expiry.asc"}))
    if ct.empty:
        st.info("No condor yet. The first one is opened by 'Prepare data for Claude' on the evening of the first "
                "session after a weekly expiry (normally Wednesday).")
        st.stop()
    done = ct[ct["status"] != "open"]
    live = ct[ct["status"] == "open"]
    realised = done["pnl_rupees"].sum() if len(done) else 0.0
    unreal = (live["open_pnl_points"].fillna(0) * live["lot_size"]).sum() if len(live) else 0.0
    k = st.columns(5)
    k[0].metric("Equity", f"₹{snapshot.CAPITAL + realised + unreal:,.0f}",
                f"{(realised + unreal) / snapshot.CAPITAL:+.2%}")
    k[1].metric("Realised P&L", f"₹{realised:,.0f}")
    k[2].metric("Open P&L", f"₹{unreal:,.0f}")
    k[3].metric("Weeks", f"{len(ct)} ({len(live)} open)")
    k[4].metric("Weeks in profit", f"{(done['pnl_points'] > 0).mean():.0%}" if len(done) else "-")
    N = st.column_config.NumberColumn
    st.dataframe(ct[["entry_date", "expiry", "spot", "vix", "sell_put", "buy_put", "sell_call", "buy_call", "credit",
                     "max_loss", "status", "open_pnl_points", "exit_date", "exit_spot", "pnl_points", "pnl_rupees",
                     "r_multiple"]].iloc[::-1], hide_index=True, width="stretch",
                 column_config={"credit": N("Credit (pts)", format="%.1f"), "max_loss": N("Max loss (pts)", format="%.1f"),
                                "pnl_rupees": N("P&L ₹", format="%.0f"), "r_multiple": N("R", format="%.2f"),
                                "open_pnl_points": N("Open P&L (pts)", format="%.1f")})
    st.caption("Research result this is testing (5 years of NSE prices): 64% of weeks profitable, profit factor "
               "about 1.4. Thin and partly luck - which is why it is a paper test and not a client call.")
    st.stop()
trades = pd.DataFrame(c.select("paper_trades", {"order": "id.asc"}))
if trades.empty:
    st.info("No paper trades yet. Claude places them in Supabase before the session; results appear after the "
            "next refresh or 'Update snapshot'.")
    st.stop()

st.caption(f"Rules: max {max_day} new trades a day · max {max_risk:.0%} risk per trade"
           + (" · mechanical: trades are opened automatically from the strategy's signals" if book != "claude" else ""))
trades = trades[trades["book"] == book] if "book" in trades else trades
if trades.empty:
    why = {forward.BOOK: " Momentum leaders stays in cash until at least 50% of stocks are above their 200 DMA.",
           forward.DIP_BOOK: " The pullback book opens a trade when an F&O stock above its 200 DMA has a sharp "
                             "2-3 day dip; it is checked each time you run 'Prepare data for Claude'."}
    st.info("No trades in this book yet." + why.get(book, ""))
    st.stop()

# Recompute live from the loaded prices so the page is current even between snapshots
res = pd.DataFrame(snapshot.evaluate_paper(trades.to_dict("records"), state.load_prices(False)))
df = trades.merge(res, left_on="id", right_on="trade_id", how="left")
df["risk"] = (df["entry"] - df["stop"]).abs() * df["qty"]
closed = df[df["status"].isin(["target", "stop", "trail stop", "expired", "exit signal"])]
opn = df[df["status"] == "open"]

# Live prices from the Kotak relay (office PC), when it is running
from core import live as _live
try:
    _lp = _live.live_pnl(df, _live.quotes(c))
except Exception:
    _lp = pd.DataFrame()
if len(_lp):
    df.loc[_lp.index, "net_pnl"] = _lp["live_pnl"]
    df.loc[_lp.index, "exit"] = _lp["live_exit"]
    _sq = _lp[_lp["live_status"] != "open"]
    df.loc[_sq.index, "status"] = _sq["live_status"]
    closed = df[df["status"].isin(["target", "stop", "trail stop", "expired", "exit signal",
                                   "stop (squared off)", "target (squared off)"])]
    opn = df[df["status"] == "open"]
    _t = _lp["live_time"].max().tz_convert(_live.IST)
    st.success(f"🟢 Open positions marked to Kotak live prices ({_t:%H:%M})."
               + (f" Squared off today: {', '.join(_sq['symbol'])}." if len(_sq) else ""))
realised, unreal = closed["net_pnl"].sum(), opn["net_pnl"].sum()
equity = snapshot.CAPITAL + realised + unreal
k = st.columns(6)
k[0].metric("Equity", f"₹{equity:,.0f}", f"{(equity / snapshot.CAPITAL - 1):+.2%}")
k[1].metric("Realised P&L", f"₹{realised:,.0f}")
k[2].metric("Open P&L", f"₹{unreal:,.0f}")
k[3].metric("Trades", f"{len(df)} ({len(opn)} open)")
k[4].metric("Win rate", f"{(closed['net_pnl'] > 0).mean():.0%}" if len(closed) else "-")
k[5].metric("Avg R (closed)", f"{closed['r_multiple'].mean():.2f}" if len(closed) else "-")

breaches = snapshot.rule_checks(df, max_per_day=max_day, max_risk=max_risk)
if breaches:
    st.error("Rule breaches: " + "; ".join(breaches))
else:
    st.success(f"All trades within the rules (≤{max_day} a day, ≤{max_risk:.0%} risk each).")

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
