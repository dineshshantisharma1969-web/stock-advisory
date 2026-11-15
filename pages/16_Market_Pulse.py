import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from core import cloud, pulse, state

st.set_page_config(page_title="Market Pulse", page_icon="🫀", layout="wide")
demo = state.sidebar()
state.require_admin()
state.hero("Market pulse", "Oil · FII / DII cash · FII futures & options positions · market breadth - in one place.")

c = cloud.client()
if not c:
    st.info("The market pulse needs Supabase (the daily bot saves the data there).")
    st.stop()


@st.cache_data(ttl=900, show_spinner=False)
def _macro():
    try:
        return pulse.fetch_macro()
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _tables():
    since = (pd.Timestamp.today() - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    return (c.select("participant_oi", {"date": f"gte.{since}", "order": "date.asc"}),
            c.select("fii_dii", {"date": f"gte.{since}", "order": "date.asc"}),
            c.select("breadth_daily", {"order": "date.desc", "limit": "60"}),
            c.select("desk_journal", {"order": "date.desc", "limit": "250"}))


macro = _macro()
oi_rows, cash_rows, breadth_rows, journal = _tables()
p = pulse.build(oi_rows, cash_rows, breadth_rows, macro)
N = st.column_config.NumberColumn

# ---------------------------------------------------------------- headline
k = st.columns(4)
if "nifty" in p:
    k[0].metric("Nifty 50", f"{p['nifty']['last']:,.0f}", f"{p['nifty']['chg_pct']:+.2f}%")
if "vix" in p:
    k[1].metric("India VIX", f"{p['vix']['last']:.1f}", f"{p['vix']['chg_pct']:+.1f}%", delta_color="inverse")
if "brent" in p:
    k[2].metric("Brent crude", f"${p['brent']['last']:.1f}", f"{p['brent']['chg_pct']:+.1f}% day · {p['brent']['chg_5d_pct']:+.1f}% 5d",
                delta_color="inverse")
if "wti" in p:
    k[3].metric("WTI crude", f"${p['wti']['last']:.1f}", f"{p['wti']['chg_pct']:+.1f}% day · {p['wti']['chg_5d_pct']:+.1f}% 5d",
                delta_color="inverse")
if macro is None:
    st.caption("Oil / Nifty / VIX prices are not available right now (Yahoo Finance did not answer).")

lines = pulse.text(p)
if lines:
    with st.expander("WhatsApp-ready text", expanded=False):
        st.code("\n".join(lines), language=None)

# ---------------------------------------------------------------- cash
st.subheader("FII / DII - cash market (₹ crore, provisional)")
if p.get("cash"):
    k = st.columns(4)
    k[0].metric(f"FII net · {p['cash_date']:%d %b}", f"₹{p['cash'].get('fii', 0):,.0f} cr")
    k[1].metric(f"DII net · {p['cash_date']:%d %b}", f"₹{p['cash'].get('dii', 0):,.0f} cr")
    k[2].metric("FII net · last 5 days", f"₹{p['cash_5d'].get('fii', 0):,.0f} cr")
    k[3].metric("DII net · last 5 days", f"₹{p['cash_5d'].get('dii', 0):,.0f} cr")
    cd = pd.DataFrame(cash_rows)
    cd["date"] = pd.to_datetime(cd["date"])
    piv = cd.pivot_table(index="date", columns="category", values="net", aggfunc="last").tail(40)
    fig = go.Figure()
    for col, colour in (("FII", state.DOWN), ("DII", state.UP)):
        if col in piv:
            fig.add_bar(x=piv.index, y=piv[col], name=f"{col} net", marker_color=colour)
    fig.update_layout(height=280, barmode="group", margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"),
                      yaxis_title="₹ crore")
    st.plotly_chart(fig, width="stretch")
    st.caption(f"{p['cash_days']} day(s) saved. NSE publishes only the latest day; the daily bot adds one each morning. "
               "Buy and sell values are on the FII / DII page.")
else:
    st.info("No FII / DII cash figures saved yet - the daily bot adds them each morning (or upload on the FII / DII page).")

# ---------------------------------------------------------------- derivatives
st.subheader("FII positions in futures & options (contracts)")
fii = pulse.positions(oi_rows, "FII")
if len(fii):
    f = fii.iloc[-1]
    k = st.columns(4)
    k[0].metric(f"Index futures · {fii.index[-1]:%d %b}", f"{f['fut_long_pct']:.0f}% long",
                f"net {f['fut_net']:+,.0f}", delta_color="off")
    k[1].metric("Today's OI build-up", f["fut_action"],
                f"longs {f['fut_long_chg']:+,.0f} · shorts {f['fut_short_chg']:+,.0f}" if pd.notna(f["fut_long_chg"]) else None,
                delta_color="off")
    k[2].metric("Index options (net bullish)", f"{f['opt_net']:+,.0f}",
                f"{f['opt_net_chg']:+,.0f} today" if pd.notna(f["opt_net_chg"]) else None)
    k[3].metric("Stock futures (net)", f"{f['stk_fut_net']:+,.0f}",
                f"{f['stk_fut_net_chg']:+,.0f} today" if pd.notna(f["stk_fut_net_chg"]) else None)
    win = fii.tail(60)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        subplot_titles=("FII index futures: % of positions that are long",
                                        "FII index futures: daily change in net position (OI build-up)",
                                        "FII index options: daily change in net bullish position"))
    fig.add_trace(go.Scatter(x=win.index, y=win["fut_long_pct"], name="% long", line=dict(color=state.ACCENT)), 1, 1)
    for row, col in ((2, "fut_net_chg"), (3, "opt_net_chg")):
        fig.add_trace(go.Bar(x=win.index, y=win[col], name=col,
                             marker_color=[state.UP if v >= 0 else state.DOWN for v in win[col].fillna(0)]), row, 1)
    fig.update_layout(height=640, showlegend=False, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, width="stretch")
    with st.expander("All four participants - latest day"):
        latest = max(r["date"] for r in oi_rows)
        t = pd.DataFrame([r for r in oi_rows if r["date"] == latest]).set_index("participant")
        show = pd.DataFrame({
            "Index fut long": t["fut_idx_long"], "Index fut short": t["fut_idx_short"],
            "% long": t["fut_idx_long"] / (t["fut_idx_long"] + t["fut_idx_short"]) * 100,
            "Index calls (net)": t["opt_idx_call_long"] - t["opt_idx_call_short"],
            "Index puts (net)": t["opt_idx_put_long"] - t["opt_idx_put_short"],
            "Stock fut (net)": t["fut_stk_long"] - t["fut_stk_short"]})
        st.dataframe(show, width="stretch", column_config={"% long": N(format="%.0f%%")})
        st.caption("Client = retail and HNI traders · Pro = brokers' own books. Long build-up: longs added. "
                   "Short build-up: shorts added. Short covering: shorts closed. Long unwinding: longs closed.")
else:
    st.info("No FII positions saved yet. The daily bot downloads NSE's participant-wise open interest each "
            "morning (the first run fills in about six months).")

# ---------------------------------------------------------------- breadth
st.subheader("Market breadth (Nifty 500)")
if p.get("breadth"):
    b = p["breadth"]
    k = st.columns(5)
    k[0].metric(f"Advances / declines · {p['breadth_date']:%d %b}", f"{b['advances']} / {b['declines']}")
    k[1].metric("% above 50 DMA", f"{float(b['pct_above_50dma']):.0f}%")
    k[2].metric("% above 200 DMA", f"{float(b['pct_above_200dma']):.0f}%")
    k[3].metric("52-week highs / lows", f"{b['new_52w_highs']} / {b['new_52w_lows']}")
    k[4].metric("Regime", b["regime"])
    st.page_link("pages/5_Market_Breadth.py", label="Full breadth charts →")
else:
    st.info("Breadth appears after the next data refresh.")

# ---------------------------------------------------------------- honesty box
st.subheader("How much weight to give these")
st.caption("Tested on 5 years of history (Nov 2021 - Nov 2026, 1,243 sessions) against what Nifty did next.")
tone = {"context": "🟡 Context only", "weak": "🟡 Weak", "no": "🔴 No value", "filter": "🟢 Used as a filter",
        "untested": "⚪ Not tested yet"}
st.dataframe(pd.DataFrame([{"Indicator": a, "What history showed": b, "Verdict": tone[v]} for a, b, v in pulse.EVIDENCE]),
             hide_index=True, width="stretch")
st.info("Bottom line: these numbers describe the mood of the market; none of them reliably says which way Nifty "
        "goes tomorrow. Use them to decide how careful to be, not what to buy. The daily journal below keeps "
        "recording them so the test can be repeated on fresh data.")

# ---------------------------------------------------------------- journal
st.subheader("Daily journal")
if journal:
    j = pd.DataFrame(journal).sort_values("date")
    j["next_day_nifty_pct"] = j["nifty_chg_pct"].shift(-1)
    cols = ["date", "nifty_close", "nifty_chg_pct", "next_day_nifty_pct", "brent", "brent_chg_pct", "vix", "fii_cash_net",
            "dii_cash_net", "fii_fut_long_pct", "fii_fut_net_chg", "fii_fut_action", "fii_opt_net_chg", "advances",
            "declines", "pct_above_50dma", "pct_above_200dma", "regime", "note"]
    st.dataframe(j[[x for x in cols if x in j]].iloc[::-1], hide_index=True, width="stretch")
    st.caption(f"{len(j)} session(s) recorded. One row per session, written by the daily bot: what the market "
               "looked like, and (next column) what Nifty did the day after.")
else:
    st.info("The journal starts with the daily bot's next run.")
