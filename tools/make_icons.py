"""Render the app icon at every size phones and browsers need, with numpy only (no image library).
Design: deep navy-to-royal-blue gradient with a soft teal glow, three rising teal bars, a white trend line
and a gold point at the peak. Shapes are drawn with signed-distance functions, so edges are anti-aliased.
Run: .venv\\Scripts\\python.exe tools\\make_icons.py      -> web/icons/*.png and web/icon.svg"""
import struct
import zlib
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "web" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

# geometry on a 512 x 512 canvas
BARS = [(170, 290), (256, 230), (342, 165)]          # (centre x, top y); bottoms at 382
BAR_W, BAR_BOTTOM, BAR_R = 58, 382, 14
LINE = [(116, 318), (196, 262), (268, 286), (392, 150)]
LINE_W = 24
DOT, DOT_R, RING = (392, 150), 25, 7


def hexc(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])


def over(dst, color, alpha):
    a = alpha[..., None]
    return dst * (1 - a) + color * a


def cov(d):  # coverage from signed distance (pixels): inside < 0
    return np.clip(0.5 - d, 0, 1)


def sd_round_rect(x, y, cx, cy, hw, hh, r):
    qx, qy = np.abs(x - cx) - (hw - r), np.abs(y - cy) - (hh - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r


def sd_segment(x, y, a, b):
    ax, ay = a
    bx, by = b
    px, py, dx, dy = x - ax, y - ay, bx - ax, by - ay
    t = np.clip((px * dx + py * dy) / (dx * dx + dy * dy), 0, 1)
    return np.hypot(px - t * dx, py - t * dy)


def render(size, rounded):
    s = size / 512
    yy, xx = np.mgrid[0:size, 0:size].astype(float) + 0.5
    x, y = xx / s, yy / s                                     # design coordinates
    px = 1 / s                                                # one output pixel in design units

    t = np.clip((x + y) / 1024, 0, 1)[..., None]              # diagonal gradient
    c0, c1, c2 = hexc("#0B1026"), hexc("#172554"), hexc("#1E40AF")
    img = np.where(t < 0.55, c0 + (c1 - c0) * (t / 0.55), c1 + (c2 - c1) * ((t - 0.55) / 0.45))
    glow = np.exp(-((x - 400) ** 2 + (y - 110) ** 2) / (2 * 150 ** 2)) * 0.35
    img = over(img, hexc("#2DD4BF"), glow)
    for gy in (190, 270, 350):                                # faint chart grid
        img = over(img, hexc("#FFFFFF"), cov((np.abs(y - gy) - 1.2) / px) * 0.06 * ((x > 96) & (x < 416)))

    for cx, top in BARS:                                      # rising bars, teal gradient top to bottom
        hh = (BAR_BOTTOM - top) / 2
        d = sd_round_rect(x, y, cx, top + hh, BAR_W / 2, hh, BAR_R) / px
        g = np.clip((y - top) / (BAR_BOTTOM - top), 0, 1)[..., None]
        col = hexc("#5EEAD4") * (1 - g) + hexc("#0F766E") * g
        img = over(img, col, cov(d) * 0.92)

    d_line = np.min([sd_segment(x, y, LINE[i], LINE[i + 1]) for i in range(len(LINE) - 1)], axis=0)
    img = over(img, hexc("#000000"), cov((d_line - LINE_W / 2 - 6) / px) * 0.18)   # soft shadow
    img = over(img, hexc("#F8FAFC"), cov((d_line - LINE_W / 2) / px))

    d_dot = np.hypot(x - DOT[0], y - DOT[1])
    img = over(img, hexc("#FDE68A"), cov((d_dot - DOT_R - RING) / px) * 0.35)        # halo
    img = over(img, hexc("#F59E0B"), cov((d_dot - DOT_R) / px))
    img = over(img, hexc("#FCD34D"), cov((d_dot - DOT_R * 0.55) / px))

    alpha = np.ones((size, size))
    if rounded:                                               # rounded-square silhouette with transparent corners
        alpha = cov(sd_round_rect(x, y, 256, 256, 256, 256, 112) / px)
    return np.dstack([img, alpha])


def write_png(path, rgba):
    h, w = rgba.shape[:2]
    data = (np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8)
    raw = b"".join(b"\x00" + data[r].tobytes() for r in range(h))
    chunk = lambda tag, body: struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


SIZES = {
    "icon-192.png": (192, True), "icon-512.png": (512, True),              # Android / browsers ("any")
    "icon-maskable-512.png": (512, False), "icon-maskable-192.png": (192, False),  # Android adaptive (full bleed)
    "apple-touch-icon.png": (180, False),                                   # iPhone (no transparency)
    "favicon-32.png": (32, True), "preview-1024.png": (1024, True),
}
for name, (size, rounded) in SIZES.items():
    write_png(OUT / name, render(size, rounded))
    print("wrote", name)

# matching SVG for crisp display inside the page
svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0B1026"/><stop offset=".55" stop-color="#172554"/><stop offset="1" stop-color="#1E40AF"/></linearGradient>
<radialGradient id="gl" cx="400" cy="110" r="300" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#2DD4BF" stop-opacity=".35"/><stop offset="1" stop-color="#2DD4BF" stop-opacity="0"/></radialGradient>
<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5EEAD4"/><stop offset="1" stop-color="#0F766E"/></linearGradient></defs>
<rect width="512" height="512" rx="112" fill="url(#bg)"/><rect width="512" height="512" rx="112" fill="url(#gl)"/>
{''.join(f'<rect x="{cx - BAR_W / 2}" y="{top}" width="{BAR_W}" height="{BAR_BOTTOM - top}" rx="{BAR_R}" fill="url(#bar)" opacity=".92"/>' for cx, top in BARS)}
<polyline points="{' '.join(f'{a},{b}' for a, b in LINE)}" fill="none" stroke="#F8FAFC" stroke-width="{LINE_W}" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="{DOT[0]}" cy="{DOT[1]}" r="{DOT_R + RING}" fill="#FDE68A" opacity=".35"/><circle cx="{DOT[0]}" cy="{DOT[1]}" r="{DOT_R}" fill="#F59E0B"/><circle cx="{DOT[0]}" cy="{DOT[1]}" r="{DOT_R * 0.55}" fill="#FCD34D"/>
</svg>
"""
(OUT.parent / "icon.svg").write_text(svg, encoding="utf-8")
print("wrote icon.svg")
