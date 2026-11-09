import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import personal as pf
from core import state

st.set_page_config(page_title="Personal Finance", page_icon="💼", layout="wide")
demo = state.sidebar()
state.hero("Personal finance", "Plan SIPs, goals, retirement, loans and tax in one place.")


def rupees(x):
    if x >= 1e7:
        return f"₹{x / 1e7:,.2f} cr"
    if x >= 1e5:
        return f"₹{x / 1e5:,.2f} lakh"
    return f"₹{x:,.0f}"


t_sip, t_goal, t_ret, t_loan, t_tax, t_alloc = st.tabs(
    ["📈 SIP & lumpsum", "🎯 Goal planner", "🏖️ Retirement", "🏠 Loan EMI & prepayment", "🧮 Tax: new vs old", "🧭 Allocation & emergency fund"])

with t_sip:
    a, b, c, d = st.columns(4)
    sip = a.number_input("Monthly SIP (₹)", 500, 10_000_000, 25_000, 500)
    ret = b.slider("Expected return (% p.a.)", 4.0, 18.0, 12.0, 0.5) / 100
    yrs = c.slider("Years", 1, 40, 15)
    step = d.slider("Annual step-up (%)", 0, 25, 10) / 100
    lump = st.number_input("One-time lumpsum today (₹)", 0, 1_000_000_000, 0, 10_000)
    sch = pf.sip_schedule(sip, ret, yrs, step)
    total = sch["value"].iloc[-1] + pf.lumpsum_fv(lump, ret, yrs)
    invested = sch["invested"].iloc[-1] + lump
    k = st.columns(3)
    k[0].metric("Invested", rupees(invested))
    k[1].metric("Value at end", rupees(total))
    k[2].metric("Wealth gained", rupees(total - invested), f"{total / invested:.1f}x" if invested else None)
    fig = go.Figure([go.Bar(x=sch["year"], y=sch["invested"], name="Invested", marker_color="#38bdf8"),
                     go.Bar(x=sch["year"], y=sch["gain"], name="Growth", marker_color="#22c55e")])
    fig.update_layout(barmode="stack", height=360, xaxis_title="Year", yaxis_tickprefix="₹", margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")
    st.caption("Returns are assumptions, not promises. Equity funds can fall 30-50% in bad years.")

with t_goal:
    a, b, c = st.columns(3)
    goal = st.selectbox("Goal", ["Child's education", "House down payment", "Car", "World trip", "Other"])
    today_cost = a.number_input("Cost in today's money (₹)", 10_000, 1_000_000_000, 2_500_000, 50_000)
    yrs_g = b.slider("Years to goal", 1, 30, 10, key="gy")
    infl = c.slider("Inflation for this goal (%)", 3.0, 12.0, 7.0, 0.5) / 100
    a2, b2 = st.columns(2)
    have = a2.number_input("Already saved for it (₹)", 0, 1_000_000_000, 200_000, 10_000)
    r_g = b2.slider("Expected return (% p.a.)", 4.0, 15.0, 11.0, 0.5, key="gr") / 100
    fut = pf.inflate(today_cost, infl, yrs_g)
    need = pf.required_sip(fut, r_g, yrs_g, have)
    k = st.columns(3)
    k[0].metric(f"{goal} will cost", rupees(fut), f"in {yrs_g} years")
    k[1].metric("Your savings will grow to", rupees(pf.lumpsum_fv(have, r_g, yrs_g)))
    k[2].metric("Monthly SIP needed", rupees(need))

with t_ret:
    a, b, c = st.columns(3)
    age = a.number_input("Current age", 18, 70, 35)
    ret_age = b.number_input("Retirement age", age + 1, 80, 60)
    life = c.number_input("Plan till age", ret_age + 1, 100, 85)
    a2, b2, c2, d2 = st.columns(4)
    exp = a2.number_input("Monthly expenses today (₹)", 5_000, 10_000_000, 60_000, 5_000)
    infl_r = b2.slider("Inflation (%)", 3.0, 10.0, 6.0, 0.5) / 100
    pre = c2.slider("Return before retirement (%)", 5.0, 15.0, 11.0, 0.5) / 100
    post = d2.slider("Return after retirement (%)", 4.0, 10.0, 7.5, 0.5) / 100
    have_r = st.number_input("Retirement savings so far: EPF, PPF, NPS, MF (₹)", 0, 10_000_000_000, 1_500_000, 50_000)
    rc = pf.retirement_corpus(exp, ret_age - age, life - ret_age, infl_r, post)
    sip_r = pf.required_sip(rc["corpus"], pre, ret_age - age, have_r)
    k = st.columns(3)
    k[0].metric("Monthly expense at retirement", rupees(rc["annual_expense_at_retirement"] / 12))
    k[1].metric("Corpus needed at retirement", rupees(rc["corpus"]))
    k[2].metric("Monthly investment needed", rupees(sip_r))

with t_loan:
    a, b, c = st.columns(3)
    P = a.number_input("Loan amount (₹)", 10_000, 1_000_000_000, 5_000_000, 50_000)
    rate = b.slider("Interest rate (% p.a.)", 5.0, 18.0, 8.5, 0.05) / 100
    tenure = c.slider("Tenure (years)", 1, 30, 20)
    a2, b2, c2 = st.columns(3)
    extra = a2.number_input("Extra payment every month (₹)", 0, 10_000_000, 5_000, 1_000)
    lump_amt = b2.number_input("One-time prepayment (₹)", 0, 1_000_000_000, 0, 50_000)
    lump_m = c2.number_input("...in month", 1, tenure * 12, 12)
    res = pf.prepayment_benefit(P, rate, tenure, extra, {int(lump_m): lump_amt} if lump_amt else None)
    k = st.columns(4)
    k[0].metric("EMI", rupees(res["emi"]))
    k[1].metric("Total interest (no prepayment)", rupees(res["interest_base"]))
    k[2].metric("Interest saved", rupees(res["interest_saved"]))
    k[3].metric("Loan ends earlier by", f"{res['months_saved'] // 12} y {res['months_saved'] % 12} m")
    base = pf.amortise(P, rate, tenure)
    new = pf.amortise(P, rate, tenure, extra, {int(lump_m): lump_amt} if lump_amt else None)
    fig = go.Figure([go.Scatter(x=base["month"] / 12, y=base["balance"], name="Without prepayment"),
                     go.Scatter(x=new["month"] / 12, y=new["balance"], name="With prepayment", line=dict(color="#22c55e"))])
    fig.update_layout(height=340, xaxis_title="Years", yaxis_title="Outstanding ₹", margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

with t_tax:
    st.caption(f"{pf.TAX_REGIMES['new']['label']} · {pf.TAX_REGIMES['old']['label']}. Resident individual below 60, "
               "salary and other normal income only (no capital gains). Verify slabs for the year you are filing.")
    a, b, c = st.columns(3)
    sal = a.number_input("Gross salary (₹ p.a.)", 0, 1_000_000_000, 1_800_000, 50_000)
    oth = b.number_input("Other income: interest, rent etc. (₹)", 0, 1_000_000_000, 0, 10_000)
    with c:
        st.write("Old-regime deductions")
        d80c = st.number_input("80C (max 1.5 lakh)", 0, 150_000, 150_000, 10_000)
        d80d = st.number_input("80D health insurance", 0, 100_000, 25_000, 5_000)
        dhra = st.number_input("HRA exemption", 0, 10_000_000, 0, 10_000)
        dhl = st.number_input("Home-loan interest 24(b) (max 2 lakh)", 0, 200_000, 0, 10_000)
        dnps = st.number_input("80CCD(1B) NPS (max 50k)", 0, 50_000, 0, 5_000)
    cmp = pf.compare_regimes(sal, d80c + d80d + dhra + dhl + dnps, oth)
    k = st.columns(3)
    k[0].metric("New regime tax", rupees(cmp["new"]["total_tax"]), f"{cmp['new']['effective_rate']:.1%} effective", delta_color="off")
    k[1].metric("Old regime tax", rupees(cmp["old"]["total_tax"]), f"{cmp['old']['effective_rate']:.1%} effective", delta_color="off")
    k[2].metric("Better choice", f"{cmp['better'].title()} regime", f"saves {rupees(cmp['saving'])}")
    st.dataframe(pd.DataFrame({"New": cmp["new"], "Old": cmp["old"]}).drop(["regime"]).map(
        lambda v: f"₹{v:,.0f}" if isinstance(v, float) and v > 1 else f"{v:.1%}" if isinstance(v, float) else v),
        width="stretch")

with t_alloc:
    a, b = st.columns(2)
    age_a = a.number_input("Age", 18, 90, 35, key="aa")
    risk = b.select_slider("Risk appetite", ["conservative", "moderate", "aggressive"], "moderate")
    al = pf.asset_allocation(age_a, risk)
    fig = go.Figure(go.Pie(labels=list(al), values=list(al.values()), hole=0.55,
                           marker=dict(colors=["#22c55e", "#38bdf8", "#f59e0b"])))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
    c1, c2 = st.columns(2)
    c1.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("**Starting-point allocation** (rule of thumb: equity ≈ 100 − age, adjusted for risk).")
        m_exp = st.number_input("Monthly household expenses (₹)", 5_000, 10_000_000, 60_000, 5_000, key="me")
        dep = st.number_input("Dependents", 0, 10, 2)
        stable = st.checkbox("Stable salaried income", True)
        st.metric("Emergency fund to keep in liquid/FD", rupees(pf.emergency_fund(m_exp, dep, stable)))
    st.caption("General education only. Suitability depends on your full financial situation.")
