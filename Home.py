"""Nifty 500 Research Desk - entry point and menu.   Run:  streamlit run Home.py

Clients see only published calls, their risk profile, the track record and market/education pages.
The research desk (scanner, screener, backtest, publishing) appears only after admin login.
"""
import streamlit as st

from core import state

st.set_page_config(page_title="Nifty 500 Research Desk", page_icon="📊", layout="wide")
admin = state.is_admin()


def page(path, title, icon, default=False):
    return st.Page(path, title=title, icon=icon, default=default)


menu = {}
if admin:
    menu["Research desk"] = [
        page("pages/0_Desk_Home.py", "Desk home", "📊", default=True),
        page("pages/1_Fundamental_Ideas.py", "Fundamental ideas", "📈"),
        page("pages/2_Stock_Analysis.py", "Stock analysis", "🔍"),
        page("pages/3_Trading_Ideas.py", "Trading & F&O ideas", "⚡"),
        page("pages/4_Backtest.py", "Backtest", "🧪"),
        page("pages/15_Paper_Trading.py", "Paper trading", "🎮"),
        page("pages/14_Client_Profiles.py", "Client profiles", "👥"),
        page("pages/11_Client_Access.py", "Client QR code", "📱"),
    ]
menu["For clients"] = [
    page("pages/00_Client_Home.py", "Welcome", "🏠", default=not admin),
    page("pages/12_My_Calls.py", "My calls", "🎯"),
    page("pages/13_Risk_Profile.py", "Risk profile", "🧭"),
    page("pages/8_Track_Record.py", "Track record", "📒"),
]
menu["Markets"] = [
    page("pages/5_Market_Breadth.py", "Market breadth", "🌊"),
    page("pages/6_FII_DII.py", "FII / DII flows", "💰"),
    page("pages/7_Macro.py", "Macro", "🌍"),
    page("pages/9_Q2_Results.py", "Q2 results", "🧾"),
]
menu["Tools"] = [page("pages/10_Personal_Finance.py", "Personal finance", "💼")]

st.navigation(menu).run()
