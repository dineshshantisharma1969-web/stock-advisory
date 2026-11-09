import pandas as pd
import streamlit as st

from core import ledger, state, valuation

st.set_page_config(page_title="Fundamental Ideas", page_icon="📈", layout="wide")
demo = state.sidebar()
st.title("📈 Fundamental ideas")
st.caption("Composite score from DCF margin of safety, reverse-DCF growth gap, PEG, PE vs industry, EV/EBITDA, "
           "profit growth, ROCE/ROE, margin trend, leverage and FCF yield. Each factor is percentile-ranked "
           "across the Nifty 500. 'Strong idea' also requires passing the quality gate.")

sc = state.load_screen(demo)
if state.need_data(sc, "fundamentals"):
    st.stop()

f1, f2, f3, f4 = st.columns(4)
inds = f1.multiselect("Industry", sorted(sc["industry"].unique()))
verdicts = f2.multiselect("Verdict", ["Strong idea", "Watch", "Neutral", "Avoid"], default=["Strong idea", "Watch"])
min_mcap = f3.number_input("Min market cap (₹ cr)", 0, 1_000_000, 5000, step=1000)
only_gate = f4.checkbox("Only quality-gate pass", True)

v = sc.copy()
if inds:
    v = v[v["industry"].isin(inds)]
if verdicts:
    v = v[v["verdict"].isin(verdicts)]
v = v[v["market_cap_cr"].fillna(0) >= min_mcap]
if only_gate:
    v = v[v["quality_gate"] == "pass"]
st.write(f"**{len(v)}** companies")

P = st.column_config.NumberColumn
st.dataframe(
    v[["symbol", "name", "industry", "score", "verdict", "price", "dcf_value", "dcf_upside", "implied_growth",
       "pe", "pe_vs_industry", "peg", "ev_ebitda", "pb", "np_cagr", "np_growth_yoy", "roce", "roe",
       "op_margin", "op_margin_change", "debt_equity", "interest_cover", "fcf_yield", "market_cap_cr", "quality_gate"]],
    hide_index=True, use_container_width=True, height=520,
    column_config={
        "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
        "price": P(format="%.1f"), "dcf_value": P("DCF value", format="%.0f"),
        "dcf_upside": P("DCF upside", format="percent"), "implied_growth": P("Implied growth", format="percent",
                                                                            help="Reverse DCF: FCF growth the price implies"),
        "pe": P("PE", format="%.1f"), "pe_vs_industry": P("PE vs industry", format="percent"),
        "peg": P("PEG", format="%.2f"), "ev_ebitda": P("EV/EBITDA", format="%.1f"), "pb": P("P/B", format="%.1f"),
        "np_cagr": P("NP CAGR", format="percent"), "np_growth_yoy": P("NP YoY", format="percent"),
        "roce": P("ROCE", format="percent"), "roe": P("ROE", format="percent"),
        "op_margin": P("Op margin", format="percent"), "op_margin_change": P("Margin Δ", format="percent"),
        "debt_equity": P("D/E", format="%.2f"), "interest_cover": P("Int. cover", format="%.1f"),
        "fcf_yield": P("FCF yield", format="percent"), "market_cap_cr": P("Mcap ₹cr", format="%.0f"),
    })
st.download_button("Download CSV", v.to_csv(index=False), "fundamental_ideas.csv", "text/csv")

st.divider()
st.subheader("Why this idea? / publish")
if len(v):
    pick = st.selectbox("Company", v["symbol"] + " - " + v["name"])
    r = v[v["symbol"] == pick.split(" - ")[0]].iloc[0]
    reasons = valuation.idea_reasons(r)
    st.markdown("\n".join(f"- {x}" for x in reasons) or "- No single factor stands out; score is broad-based.")
    if r["is_financial"]:
        st.caption("Financial company: DCF/EV not applied; judged on PE, P/B, ROE and growth.")
    st.page_link("pages/2_Stock_Analysis.py", label=f"Open full analysis of {r['symbol']} →")
    with st.form("pub_f"):
        horizon = st.number_input("Holding horizon (trading days)", 20, 1000, 250)
        target = st.number_input("Target price", value=float(r["dcf_value"]) if pd.notna(r["dcf_value"]) else float(r["price"]) * 1.25)
        stop = st.number_input("Review / stop level", value=float(r["price"]) * 0.8)
        note = st.text_area("Rationale (stored permanently)", "; ".join(reasons))
        if st.form_submit_button("Publish to idea book (permanent)"):
            if demo:
                st.error("Switch off demo mode to publish - demo ideas would pollute your track record.")
            else:
                i = ledger.publish_idea({"kind": "fundamental", "symbol": r["symbol"], "setup": "Fundamental score",
                                         "side": "long", "entry": r["price"], "stop": stop, "target": target,
                                         "horizon_days": horizon, "instrument": "Equity (delivery)",
                                         "lot_size": None, "rationale": note})
                st.success(f"Published idea #{i}.")
