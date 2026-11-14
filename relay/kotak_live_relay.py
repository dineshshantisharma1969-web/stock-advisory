"""Kotak live-price relay - runs on an office PC in India during market hours.

Every minute it asks Kotak Neo for the latest prices of the app's stocks (Nifty 500, F&O stocks and every
paper-trade stock) and saves them to the app's database. The app on your phone then shows live P&L.

  * Uses ONLY your Kotak consumer key (read-only market data). It never logs in and never places orders.
  * Needs only Python 3.9+ - no extra packages.
  * Settings live in relay_settings.ini next to this file (keep that file private).

Run:
    python kotak_live_relay.py --test     one round now, prints a few prices (use this first)
    python kotak_live_relay.py            normal mode: 9:14 am - 3:32 pm IST on weekdays, then stops
"""
from __future__ import annotations

import configparser
import csv
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "relay_settings.ini"
LOG = HERE / "relay_log.txt"
KOTAK = "https://mis.kotaksecurities.com"
IST = timezone(timedelta(hours=5, minutes=30))
START, END = (9, 14), (15, 32)          # market window, IST
EVERY = 60                              # seconds between rounds
BATCH = 40                              # Kotak rejects a request of 50 or more instruments
INDICES = {"NIFTY 50": "Nifty 50", "NIFTY BANK": "Nifty Bank", "INDIA VIX": "India VIX"}
SECRETS: list[str] = []


# ------------------------------------------------------------------ small helpers
def mask(text: str) -> str:
    """Never print or log the consumer key or passphrase (Kotak echoes the key back in errors)."""
    for s in SECRETS:
        if s and len(s) > 8:
            text = text.replace(s, f"{s[:4]}...{s[-4:]}")
    return text


def log(msg: str) -> None:
    line = f"{datetime.now(IST):%d-%b %H:%M:%S}  {mask(str(msg))}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def http(url: str, headers: dict, data: bytes | None = None, timeout: int = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def settings() -> dict:
    if not SETTINGS.exists():
        sys.exit(f"Settings file not found: {SETTINGS}\nCopy relay_settings_TEMPLATE.ini to relay_settings.ini "
                 "and fill it in.")
    cp = configparser.ConfigParser(interpolation=None)   # passphrases may contain % and other symbols
    cp.read(SETTINGS, encoding="utf-8")
    s = {"consumer_key": cp.get("kotak", "consumer_key", fallback="").strip().strip('"'),
         "passphrase": cp.get("app", "passphrase", fallback="").strip().strip('"'),
         "url": cp.get("app", "supabase_url", fallback="").strip().rstrip("/"),
         "key": cp.get("app", "publishable_key", fallback="").strip()}
    bad = [k for k, v in s.items() if not v or v.upper().startswith("PASTE")]
    if bad:
        sys.exit("Please fill in relay_settings.ini: " + ", ".join(bad))
    SECRETS.extend([s["consumer_key"], s["passphrase"]])
    return s


# ------------------------------------------------------------------ the app's database (Supabase)
class App:
    def __init__(self, s: dict):
        self.s = s
        self.h = {"apikey": s["key"], "Authorization": f"Bearer {s['key']}", "Content-Type": "application/json"}

    def _rpc(self, fn: str, body: dict):
        code, raw = http(f"{self.s['url']}/rest/v1/rpc/{fn}", self.h, json.dumps(body).encode())
        if code >= 300:
            text = raw.decode("utf-8", "replace")
            if "passphrase" in text:
                sys.exit("The app did not accept the relay passphrase. Set the SAME passphrase in the app "
                         "(Desk home -> Kotak live prices) and in relay_settings.ini.")
            raise RuntimeError(f"app database {fn} -> {code}: {mask(text)[:200]}")
        return json.loads(raw or b"null")

    def symbols(self) -> list[str]:
        return [r if isinstance(r, str) else list(r.values())[0] for r in self._rpc("relay_symbols", {"p_token": self.s["passphrase"]})]

    def push(self, rows: list[dict], message: str) -> int:
        return self._rpc("relay_push", {"p_token": self.s["passphrase"], "p_rows": rows, "p_message": message})


# ------------------------------------------------------------------ Kotak
class Kotak:
    def __init__(self, key: str):
        self.h = {"Authorization": key, "Content-Type": "application/x-www-form-urlencoded",
                  "User-Agent": "NeoSDK-Python/3.0.13", "Accept": "application/json"}

    def _get(self, path: str) -> bytes:
        code, raw = http(path if path.startswith("http") else KOTAK + path, self.h)
        if code == 429:
            raise RuntimeError("Kotak rate limit (429)")
        if code >= 300:
            raise RuntimeError(f"Kotak {path.split('?')[0][-50:]} -> {code}: {mask(raw.decode('utf-8', 'replace'))[:200]}")
        return raw

    def tokens(self) -> dict:
        """NSE symbol -> Kotak instrument token (EQ series preferred over BE)."""
        paths = json.loads(self._get("/script-details/1.0/masterscrip/file-paths"))["data"]["filesPaths"]
        want = [p for p in paths if p.rsplit("/", 1)[-1].lower().startswith("nse_cm")
                and not p.rsplit("/", 1)[-1].lower().startswith("nse_com")]
        if not want:
            raise RuntimeError("Kotak scrip master has no nse_cm file")
        rows = csv.DictReader(io.StringIO(http(want[0], {"User-Agent": "Mozilla/5.0"})[1].decode("utf-8", "replace")))
        out = {}
        for r in rows:
            r = {k.strip().rstrip(";"): (v or "").strip() for k, v in r.items() if k}
            trd, tok = r.get("pTrdSymbol", ""), r.get("pSymbol", "")
            for series in ("BE", "EQ"):
                if trd.endswith("-" + series) and tok:
                    if series == "EQ" or trd[:-3] not in out:
                        out[trd[: -len(series) - 1]] = tok
        return out

    def quotes(self, pairs: list[tuple[str, str]]) -> list[dict]:
        """pairs: (segment, token). Returns Kotak's quote dicts."""
        sym = ",".join(f"{seg}|{tok}" for seg, tok in pairs)
        raw = self._get(f"/script-details/1.0/quotes/neosymbol/{urllib.parse.quote(sym, safe='|,')}/all")
        js = json.loads(raw)
        if isinstance(js, dict):
            js = js.get("data") or js.get("message") or []
        return js if isinstance(js, list) else []


def f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def to_row(symbol: str, q: dict) -> dict | None:
    ltp = f(q.get("ltp"))
    if not ltp or ltp <= 0:
        return None
    o = q.get("ohlc") or {}
    t = q.get("lstup_time")
    when = datetime.fromtimestamp(int(t), IST).isoformat() if str(t or "").isdigit() else None
    return {"symbol": symbol, "ltp": ltp, "open": f(o.get("open")), "high": f(o.get("high")), "low": f(o.get("low")),
            "prev_close": f(o.get("close")), "change_pct": f(q.get("per_change")), "volume": f(q.get("last_volume")),
            "quote_time": when}


# ------------------------------------------------------------------ one round
def one_round(kotak: Kotak, app: App, tok: dict, symbols: list[str], use_indices: dict) -> tuple[int, str]:
    by_tok = {tok[s]: s for s in symbols if s in tok}
    pairs = [("nse_cm", t) for t in by_tok]
    rows, errors = [], 0
    for i in range(0, len(pairs), BATCH):
        try:
            for q in kotak.quotes(pairs[i:i + BATCH]):
                s = by_tok.get(str(q.get("exchange_token", "")).strip())
                r = to_row(s, q) if s else None
                if r:
                    rows.append(r)
        except Exception as e:
            errors += 1
            if errors <= 2:
                log(f"quote batch failed: {e}")
        time.sleep(0.1)
    if use_indices.get("on", True):
        try:
            got = kotak.quotes([("nse_cm", name) for name in INDICES.values()])
            names = {v.lower(): k for k, v in INDICES.items()}
            for q in got:
                key = names.get(str(q.get("exchange_token", "")).strip().lower()) or \
                      names.get(str(q.get("display_symbol", "")).strip().lower())
                r = to_row(key, q) if key else None
                if r:
                    rows.append(r)
        except Exception as e:
            use_indices["on"] = False          # this account/API does not serve indices this way: stop asking
            log(f"index quotes not available ({e}); continuing with stocks only")
    msg = f"{len(rows)} prices from Kotak, {errors} failed batches, {len(symbols) - len(by_tok)} symbols without a Kotak token"
    n = app.push(rows, msg) if rows else 0
    return n, msg


def in_window(now: datetime) -> bool:
    return now.weekday() < 5 and START <= (now.hour, now.minute) <= END


def main():
    test = "--test" in sys.argv
    s = settings()
    kotak, app = Kotak(s["consumer_key"]), App(s)
    log("Starting Kotak live-price relay" + (" (test round)" if test else ""))
    try:
        symbols = app.symbols()
        log(f"App asked for {len(symbols)} stocks")
        tok = kotak.tokens()
        log(f"Kotak instrument list loaded: {len(tok)} NSE stocks")
    except SystemExit:
        raise
    except Exception as e:
        log(f"Could not start: {e}")
        if "consumer key" in str(e).lower():
            log("Kotak did not accept the consumer key. Copy the CURRENT key from Kotak Neo -> More -> Trade API "
                "into relay_settings.ini.")
        sys.exit(1)
    idx = {"on": True}
    if test:
        n, msg = one_round(kotak, app, tok, symbols, idx)
        log(f"Test round: saved {n} prices. {msg}")
        log("If the number above is in the hundreds, everything works. Open the app: Desk home -> Kotak live prices.")
        return
    last_symbols = time.time()
    while True:
        now = datetime.now(IST)
        if now.weekday() >= 5 or (now.hour, now.minute) > END:
            log("Market closed - relay stopping for today.")
            return
        if not in_window(now):
            time.sleep(30)
            continue
        t0 = time.time()
        try:
            if time.time() - last_symbols > 3600:            # pick up new paper trades every hour
                symbols, last_symbols = app.symbols(), time.time()
            n, msg = one_round(kotak, app, tok, symbols, idx)
            log(f"saved {n} prices")
        except SystemExit:
            raise
        except Exception as e:
            log(f"round failed, will retry: {e}")
        time.sleep(max(5, EVERY - (time.time() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user")
