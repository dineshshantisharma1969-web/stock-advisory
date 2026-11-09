import io

import qrcode
import streamlit as st
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer

from core import config, state

st.set_page_config(page_title="Client Access", page_icon="📱", layout="wide")
demo = state.sidebar()
state.hero("Client access", "Share the app with clients: one scan opens it on their phone.")


def make_qr(url: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=12, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(image_factory=StyledPilImage, module_drawer=RoundedModuleDrawer())
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


try:
    saved = st.secrets.get("public_url", "")
except Exception:
    saved = ""
url = st.text_input("Public link to this app", saved or "https://",
                    help="After deploying (e.g. Streamlit Community Cloud), paste the app's address. "
                         "Set public_url in .streamlit/secrets.toml to pre-fill it.")
valid = url.startswith("https://") and len(url) > len("https://") + 3
left, right = st.columns([1, 2])
with left:
    if valid:
        png = make_qr(url)
        st.image(png, caption=url, use_container_width=True)
        st.download_button("Download QR (PNG)", png, "client_qr.png", "image/png", use_container_width=True)
    else:
        st.info("Enter the app's public https:// link to generate the QR code.")
with right:
    st.markdown("""
#### Before you share it
1. **Deploy** the app so it has a public https link (Streamlit Community Cloud is free; a VPS works too).
2. **Set an admin password** in `.streamlit/secrets.toml` (`admin_password = "..."`). Clients then get a
   read-only view; publishing ideas, refreshing data and the trade journal need your login.
3. **Print the QR** on your visiting card, brochure or WhatsApp status.
""")
    st.warning("**Regulatory:** giving buy/sell recommendations to clients, paid or free, needs SEBI Research "
               "Analyst (or Investment Adviser) registration. Show your registration number, disclaimers and "
               "risk disclosures in the app before sharing this code.", icon="⚖️")
    st.caption(config.DISCLAIMER)
