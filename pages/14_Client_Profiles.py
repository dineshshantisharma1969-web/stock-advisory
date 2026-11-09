import pandas as pd
import streamlit as st

from core import ledger, profile, state

st.set_page_config(page_title="Client Profiles", page_icon="👥", layout="wide")
demo = state.sidebar()
state.require_admin()
state.hero("Client profiles", "Risk questionnaires clients chose to save - your suitability record.")

df = ledger.profiles()
if df.empty:
    st.info("No saved profiles yet. Clients save theirs from the Risk profile page.")
    st.stop()
df["tracks"] = df["tracks"].map(lambda s: ", ".join(ledger.TRACKS.get(t, t) for t in str(s).split(",")))
k = st.columns(3)
k[0].metric("Profiles", len(df))
k[1].metric("Suitable for F&O", int(df["tracks"].str.contains("F&O").sum()))
k[2].metric("Average score", f"{df['score'].mean():.0f} / {profile.MAX_SCORE}")
st.dataframe(df[["created_utc", "client_name", "contact", "score", "tracks"]], hide_index=True, width="stretch")
pick = st.selectbox("Answers for", df.index, format_func=lambda i: f"#{df.loc[i, 'id']} {df.loc[i, 'client_name'] or '(no name)'}")
st.json(df.loc[pick, "answers"])
st.download_button("Export (CSV)", df.to_csv(index=False), "risk_profiles.csv")
st.caption("Contains personal data: share only as your privacy policy allows.")
