"""End-of-day P&L and the calls carried into the next session."""
import streamlit as st

from core import eod, state

st.set_page_config(page_title="End of day", page_icon="🌇", layout="wide")
demo = state.sidebar()
state.hero("End of day", "Whether the book made money today, and which calls are still open for the next session.")

px = state.load_prices(demo)
if state.need_data(px):
    st.stop()

try:
    summary = eod.load_summary(px, admin=state.is_admin(), demo=demo)
except Exception as e:
    state.report_error("End-of-day summary", e)
    st.stop()
eod.show(summary)
