import streamlit as st

from core import ledger, profile, state

st.set_page_config(page_title="Risk Profile", page_icon="🧭", layout="wide")
demo = state.sidebar()
state.hero("Your risk profile", "Eight quick questions decide which kind of calls suit you.")

with st.form("risk"):
    answers = {}
    for key, question, opts in profile.QUESTIONS:
        answers[key] = st.radio(f"**{question}**", [o for o, _ in opts], index=None, key=f"q_{key}")
    st.divider()
    c1, c2 = st.columns(2)
    name = c1.text_input("Your name (optional)")
    contact = c2.text_input("Phone or email (optional)")
    consent = st.checkbox("Save my answers with the research desk so they can serve me suitable calls")
    done = st.form_submit_button("See my profile", type="primary")

if done:
    if any(v is None for v in answers.values()):
        st.error("Please answer all eight questions.")
        st.stop()
    res = profile.assess(answers)
    st.session_state["risk_profile"] = res
    c = st.columns(3)
    c[0].metric("Risk score", f"{res['score']} / {res['max']}")
    c[1].metric("Profile", res["profile"])
    c[2].metric("Suitable tracks", len(res["tracks"]))
    for t in res["tracks"]:
        st.markdown(f"- ✅ {ledger.TRACKS[t]}")
    for r in res["reasons"]:
        st.markdown(f"- ⛔ {r}")
    if consent:
        if demo:
            st.caption("Demo mode: answers not saved.")
        else:
            ledger.save_profile({"client_name": name or None, "contact": contact or None, "answers": answers,
                                 "score": res["score"], "tracks": res["tracks"], "consent": True})
            st.caption("Saved with the research desk.")
    st.page_link("pages/12_My_Calls.py", label="See calls for my profile →", icon="🎯")
st.caption("This is a general suitability check, not a full financial plan. Review it if your situation changes.")
