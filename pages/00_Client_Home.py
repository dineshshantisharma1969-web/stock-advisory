import streamlit as st

from core import ledger, market, state

st.set_page_config(page_title="Welcome", page_icon="🏠", layout="wide")
demo = state.sidebar()
state.hero("Research you can verify", "Calls matched to your risk profile, every one tracked openly - hits and misses.")

state.quick_links([("pages/13_Risk_Profile.py", "🧭  Find my risk profile"), ("pages/12_My_Calls.py", "🎯  My calls"),
                   ("pages/8_Track_Record.py", "📒  Track record"), ("pages/9_Q2_Results.py", "🧾  Q2 results"),
                   ("pages/10_Personal_Finance.py", "💼  Personal finance")])

prof = st.session_state.get("risk_profile")
if prof:
    st.success(f"Your profile: **{prof['profile']}** · suitable tracks: "
               + ", ".join(ledger.TRACKS[t] for t in prof["tracks"]))
else:
    st.info("New here? Take the 2-minute **risk profile** so we show you only the calls that suit you.")

px = state.load_prices(demo)
b = state.load_breadth(demo)
if len(b):
    regime, why = market.breadth_regime(b)
    st.markdown(f"#### Market today: {regime}")
    st.caption(why)

st.markdown("#### Our three tracks")
ib = ledger.ideas()
ev = ledger.evaluate_ideas(ib, px) if len(ib) and px else ib
desc = {"longterm": "Fundamentally strong businesses at sensible valuations. Holding 1-3 years.",
        "swing": "Short-term trades with a fixed stop-loss and target. Holding 2-6 weeks.",
        "fno": "Defined-risk option strategies for experienced traders. Holding up to expiry."}
cols = st.columns(3)
for c, (key, name) in zip(cols, ledger.TRACKS.items()):
    t = ledger.ideas_for_track(ev, key) if len(ev) else ev
    closed = t[t["status"].isin(["target", "stop", "expired"])] if len(t) and "status" in t else t.iloc[0:0] if len(t) else t
    open_n = int(t["status"].isin(["open", "holding"]).sum()) if len(t) and "status" in t else 0
    hit = f"{(closed['ret'] > 0).mean():.0%}" if len(closed) else "-"
    with c:
        st.markdown(f'<div class="tile"><h4>{name}</h4><p>{desc[key]}</p></div>', unsafe_allow_html=True)
        k1, k2, k3 = st.columns(3)
        k1.metric("Open", open_n)
        k2.metric("Closed", len(closed))
        k3.metric("Hit rate", hit)

st.markdown('<div class="foot">Investments in securities are subject to market risks. Read all related documents '
            'carefully before investing. Past performance does not guarantee future returns.</div>',
            unsafe_allow_html=True)
