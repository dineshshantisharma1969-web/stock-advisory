import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import config, state, technicals, valuation

st.set_page_config(page_title="Stock Analysis", page_icon="🔍", layout="wide")
demo = state.sidebar()
state.hero("Stock analysis", "Charts, intrinsic value and financials for any Nifty 500 company.")

u = state.load_universe()
px, fu = state.load_prices(demo), state.load_fundamentals(demo)
if state.need_data(px):
    st.stop()
opts = [s for s in u["symbol"] if s in px]
label = dict(zip(u["symbol"], u["symbol"] + " - " + u["name"]))
sym = st.selectbox("Stock", opts, format_func=lambda s: label.get(s, s))
industry = u.set_index("symbol").loc[sym, "industry"]
st.caption(industry)

tab_t, tab_v, tab_f = st.tabs(["Technical chart", "Valuation (DCF / reverse DCF / multiples)", "Financials"])

with tab_t:
    show = st.multiselect("Overlays", ["dma", "bb"], ["dma", "bb"],
                          format_func={"dma": "20/50/200 DMA", "bb": "Bollinger Bands (20,2)"}.get)
    st.plotly_chart(state.price_chart(px[sym], sym, show), use_container_width=True)
    x = technicals.add_indicators(px[sym]).iloc[-1]
    c = st.columns(6)
    c[0].metric("Close", state.num(x["Close"]))
    c[1].metric("RSI 14", state.num(x["rsi"]))
    c[2].metric("ADX 14", state.num(x["adx"]), help=">25 = trending")
    c[3].metric("Supertrend", "Up" if x["supertrend"] > 0 else "Down")
    c[4].metric("vs 200 DMA", state.pct(x["Close"] / x["dma200"] - 1))
    c[5].metric("ATR 14", state.num(x["atr"]))

f = fu.get(sym)
with tab_v:
    if not f:
        st.info("No fundamentals for this stock.")
    else:
        m = valuation.analyse(f, industry)
        c = st.columns(6)
        c[0].metric("PE", state.num(m["pe"]))
        c[1].metric("PEG", state.num(m["peg"], 2))
        c[2].metric("EV/EBITDA", state.num(m["ev_ebitda"]))
        c[3].metric("P/B", state.num(m["pb"]))
        c[4].metric("ROCE", state.pct(m["roce"]))
        c[5].metric("ROE", state.pct(m["roe"]))
        if m["is_financial"]:
            st.warning("Financial company: free-cash-flow DCF and EV multiples are not meaningful. "
                       "Use P/B vs ROE and earnings growth instead.")
        else:
            st.subheader("DCF (2-stage, growth fades to terminal)")
            fcf0 = valuation.base_fcf(f)
            if not (pd.notna(fcf0) and fcf0 > 0):
                st.info("Free cash flow is negative or missing, so a DCF is not meaningful here.")
            else:
                a, b, cc, d = st.columns(4)
                g = a.slider("Starting growth", -0.05, 0.40, float(valuation.default_growth(f)), 0.01, format="%.2f")
                w = b.slider("Discount rate (WACC)", 0.08, 0.20, float(round(valuation.wacc(f), 3)), 0.005, format="%.3f")
                tg = cc.slider("Terminal growth", 0.02, 0.07, config.TERMINAL_GROWTH, 0.005, format="%.3f")
                yrs = d.slider("Years", 5, 15, config.DCF_YEARS)
                res = valuation.dcf_value_per_share(f, g, w, tg, yrs)
                k = st.columns(5)
                k[0].metric("Base FCF (₹ cr)", state.num(res["fcf0"] / 1e7, 0))
                k[1].metric("Intrinsic value / share", state.num(res["value"], 0))
                k[2].metric("Price", state.num(f["price"], 0))
                k[3].metric("Margin of safety", state.pct(res["value"] / f["price"] - 1))
                k[4].metric("Terminal value share", state.pct(res["terminal_share"]),
                            help="Above ~75% means the value depends mostly on the far future: treat with caution")
                ig = valuation.reverse_dcf_growth(f, w, tg, yrs)
                st.subheader("Reverse DCF")
                st.write(f"At today's price the market is pricing **{state.pct(ig)}** starting FCF growth "
                         f"(fading to {tg:.1%}). The company has delivered revenue CAGR "
                         f"**{state.pct(m['revenue_cagr'])}** and profit CAGR **{state.pct(m['np_cagr'])}**.")
                # sensitivity grid
                gs, ws = np.arange(g - 0.04, g + 0.041, 0.02), np.arange(w - 0.02, w + 0.021, 0.01)
                grid = pd.DataFrame([[valuation.dcf_value_per_share(f, gg, ww, tg, yrs)["value"] if ww > tg else np.nan
                                      for gg in gs] for ww in ws],
                                    index=[f"WACC {ww:.1%}" for ww in ws], columns=[f"g {gg:.0%}" for gg in gs])
                st.caption("Sensitivity: value per share")
                st.dataframe(grid.style.format("{:,.0f}"), use_container_width=True)

with tab_f:
    if f:
        n = len(f["revenue"])
        yrs = [f"Y-{n - 1 - i}" if i < n - 1 else "Latest" for i in range(n)]
        fin = pd.DataFrame({"Revenue": f["revenue"], "EBIT": f["ebit"][:n] + [np.nan] * (n - len(f["ebit"])),
                            "Net profit": f["net_income"][:n] + [np.nan] * (n - len(f["net_income"]))},
                           index=yrs) / 1e7
        fig = go.Figure([go.Bar(x=fin.index, y=fin[c], name=c) for c in fin.columns])
        fig.update_layout(barmode="group", height=360, yaxis_title="₹ crore", margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(fin.style.format("{:,.0f}"), use_container_width=True)
        st.caption(f"Source: {f['source']} · as of {f['as_of']}")
