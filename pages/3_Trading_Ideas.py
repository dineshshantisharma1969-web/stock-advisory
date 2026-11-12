import numpy as np
import streamlit as st

from core import fno, ledger, market, state, technicals

st.set_page_config(page_title="Trading & F&O Ideas", page_icon="⚡", layout="wide")
demo = state.sidebar()
state.require_admin()
state.hero("Swing &amp; F&amp;O ideas", "Technical setups with defined entry, stop, target and position size.")
st.caption("Rule-based setups on MACD, 20/50/200 DMA, Bollinger Bands, RSI, ADX, Supertrend and volume. "
           "Every idea carries an ATR-based stop and a fixed reward:risk target.")

px = state.load_prices(demo)
if state.need_data(px):
    st.stop()

grades = state.backtest_grades(demo)
if not grades:
    st.warning("These setups have not been backtested on this data yet. Run the **Backtest** page first - "
               "only publish setups graded A out-of-sample.")

regime, why = market.breadth_regime(state.load_breadth(demo))
c1, c2, c3, c4 = st.columns(4)
setups = c1.multiselect("Setups", list(technicals.SETUPS), list(technicals.SETUPS))
days = c2.slider("Signals in last N sessions", 1, 10, 3)
sides = c3.multiselect("Direction", ["long", "short"], ["long", "short"])
only_good = c4.checkbox("Only grade A (publishable)", bool(grades))
st.info(f"Market regime **{regime}** - {why}")

sig = technicals.scan({k: v for k, v in px.items()}, setups, days)
if sig.empty:
    st.write("No signals.")
    st.stop()
sig = sig[sig["side"].isin(sides)]
sig["grade"] = sig["setup"].map(grades).fillna("not tested")
if only_good:
    sig = sig[sig["grade"].str.startswith("A")]
# Trade with the regime: penalise longs in risk-off and shorts in risk-on
sig["with_regime"] = np.where((regime == "Risk-off") & (sig["side"] == "long") |
                              (regime == "Risk-on") & (sig["side"] == "short"), "against", "ok")
lots = fno.load_lots()
sig["fno_lot"] = sig["symbol"].map(lots)
sig = sig.sort_values(["date", "grade"], ascending=[False, True])
st.write(f"**{len(sig)}** signals")
N = st.column_config.NumberColumn
st.dataframe(sig, hide_index=True, width="stretch", height=400,
             column_config={"entry": N(format="%.1f"), "stop": N(format="%.1f"), "target": N(format="%.1f"),
                            "rsi": N(format="%.0f"), "adx": N(format="%.0f"), "risk_pct": N("Risk %", format="percent"),
                            "fno_lot": N("F&O lot")})
if not lots:
    st.caption("F&O lot sizes not loaded: save NSE's current lot-size list as data/fo_lots.csv (symbol,lot_size).")

if not len(sig):
    st.stop()
st.divider()
pick = st.selectbox("Work an idea", sig.index, format_func=lambda i: f"{sig.loc[i,'symbol']} · {sig.loc[i,'setup']} · {sig.loc[i,'side']} · {sig.loc[i,'date']}")
r = sig.loc[pick]
left, right = st.columns([3, 2])
with left:
    fig = state.price_chart(px[r["symbol"]], r["symbol"], height=620)
    for lvl, col, name in ((r["entry"], "blue", "Entry"), (r["stop"], "red", "Stop"), (r["target"], "green", "Target")):
        fig.add_hline(y=lvl, line=dict(color=col, dash="dash", width=1), annotation_text=name, row=1, col=1)
    st.plotly_chart(fig, width="stretch")
with right:
    st.metric("Entry / Stop / Target", f"{r['entry']:.1f} / {r['stop']:.1f} / {r['target']:.1f}")
    st.write(f"Backtest grade: **{r['grade']}** · Regime: **{r['with_regime']}**")
    instrument = st.radio("Instrument", ["Stock future", "Cash (swing)"], horizontal=True,
                          help="Stock option spreads are no longer offered: on 5 years of real NSE option prices "
                               "both debit and credit spreads on stock signals lost money after costs.")
    lot = int(r["fno_lot"]) if r["fno_lot"] == r["fno_lot"] and r["fno_lot"] else 0
    if instrument != "Cash (swing)":
        lot = st.number_input("Lot size (check NSE)", 1, 100000, lot or 1)
    capital = st.number_input("Capital (₹)", 10000, 100_000_000, 500_000, step=50_000)
    risk_pct = st.slider("Risk per trade (% of capital)", 0.25, 3.0, 1.0, 0.25) / 100
    per_share = abs(r["entry"] - r["stop"])
    if instrument == "Cash (swing)":
        qty = int(capital * risk_pct / per_share)
        st.write(f"Position size: **{qty} shares** (₹{qty * r['entry']:,.0f}); risk ₹{qty * per_share:,.0f}, "
                 f"reward ₹{qty * abs(r['target'] - r['entry']):,.0f}")
        desc = "Equity (swing)"
    elif instrument == "Stock future":
        lots_n = max(int(capital * risk_pct / (per_share * lot)), 0)
        st.write(f"Lots: **{lots_n}** · risk per lot ₹{per_share * lot:,.0f}")
        if lots_n == 0:
            st.error("One lot risks more than your per-trade limit. Trade it in cash with fewer shares, or skip.")
        desc = f"Future x{lots_n} lot(s)"
    if not state.is_admin():
        st.stop()
    with st.form("pub_t"):
        horizon = st.number_input("Horizon (sessions)", 3, 60, 20)
        note = st.text_area("Rationale (stored permanently)", f"{r['setup']} on {r['date']}; regime {regime}")
        default_track = "swing" if instrument == "Cash (swing)" else "fno"
        tracks = st.multiselect("Publish to client tracks", list(ledger.TRACKS), [default_track],
                                format_func=ledger.TRACKS.get)
        block = state.publish_block_reason("swing" if instrument == "Cash (swing)" else "fno", r["grade"], demo=demo)
        if block:
            st.caption("🔒 " + block)
        if st.form_submit_button("Publish to idea book (permanent)", disabled=bool(block)):
            if block:
                st.error(block)
            else:
                i = ledger.publish_idea({"kind": "swing" if instrument == "Cash (swing)" else "fno",
                                         "symbol": r["symbol"], "setup": r["setup"], "side": r["side"],
                                         "entry": r["entry"], "stop": r["stop"], "target": r["target"],
                                         "horizon_days": horizon, "instrument": desc,
                                         "lot_size": lot if instrument != "Cash (swing)" else None, "rationale": note,
                                         "tracks": tracks})
                st.success(f"Published idea #{i}.")
