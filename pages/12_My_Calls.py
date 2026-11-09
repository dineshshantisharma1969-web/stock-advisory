import pandas as pd
import streamlit as st

from core import ledger, state

st.set_page_config(page_title="My Calls", page_icon="🎯", layout="wide")
demo = state.sidebar()
state.hero("My calls", "Published calls for your track, with the reasoning, levels and live status.")

prof = st.session_state.get("risk_profile")
allowed = prof["tracks"] if prof else list(ledger.TRACKS)
if not prof:
    st.info("Take the **risk profile** first so we show only the tracks that suit you.")
    st.page_link("pages/13_Risk_Profile.py", label="Find my risk profile →", icon="🧭")
track = st.radio("Track", allowed, format_func=ledger.TRACKS.get, horizontal=True)
if track == "fno":
    st.warning("F&O can lose money quickly. Trade only the defined-risk structure shown, size every position so "
               "the maximum loss is no more than 1-2% of your capital, and place the stop with the entry.", icon="⚠️")

ib = ledger.ideas()
px = state.load_prices(demo)
calls = ledger.ideas_for_track(ledger.evaluate_ideas(ib, px) if len(ib) else ib, track)
if calls.empty:
    st.info("No calls published for this track yet.")
    st.stop()

live = calls[calls["status"].isin(["open", "holding", "no data"])].iloc[::-1]
done = calls[calls["status"].isin(["target", "stop", "expired"])].iloc[::-1]
k = st.columns(4)
k[0].metric("Active calls", len(live))
k[1].metric("Closed calls", len(done))
k[2].metric("Hit rate", f"{(done['ret'] > 0).mean():.0%}" if len(done) else "-")
k[3].metric("Avg return per call", f"{done['ret'].mean():.1%}" if len(done) else "-")

st.subheader("Active")
for _, r in live.iterrows():
    side = "BUY" if r["side"] == "long" else "SELL"
    tone = "up" if r["side"] == "long" else "down"
    now = f" · now {r['exit']:,.1f} ({r['ret']:+.1%})" if pd.notna(r.get("ret")) else ""
    with st.container(border=True):
        st.markdown(f"### {r['symbol']} {state.pill(side, tone)} {state.pill(r['instrument'] or '', 'mid')}",
                    unsafe_allow_html=True)
        c = st.columns(4)
        c[0].metric("Entry", f"{r['entry']:,.1f}")
        c[1].metric("Stop-loss", f"{r['stop']:,.1f}")
        c[2].metric("Target", f"{r['target']:,.1f}")
        c[3].metric("Horizon", f"{int(r['horizon_days'])} sessions" if pd.notna(r["horizon_days"]) else "-")
        st.caption(f"Published {r['created_utc'][:10]} · {r['setup']}{now}")
        if r["rationale"]:
            st.markdown(f"**Why:** {r['rationale']}")

if len(done):
    st.subheader("Closed")
    st.dataframe(done[["created_utc", "symbol", "side", "instrument", "entry", "stop", "target", "status", "exit", "ret"]],
                 hide_index=True, width="stretch",
                 column_config={"ret": st.column_config.NumberColumn("Return", format="percent"),
                                "exit": st.column_config.NumberColumn(format="%.1f")})
st.caption("Research for information only, not personal advice. Prices are delayed. Every call stays in the "
           "tamper-evident track record. Investments in securities are subject to market risks.")
