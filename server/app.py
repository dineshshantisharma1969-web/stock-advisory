"""FastAPI server: serves the phone web app and the snapshot data behind a single password.
Local:  .venv\\Scripts\\python.exe -m uvicorn server.app:app --host 0.0.0.0 --port 8000
Cloud:  uvicorn server.app:app --host 0.0.0.0 --port $PORT   with APP_PASSWORD set (required when deployed)."""
import gzip
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT / "data")
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
ALLOW_REFRESH = os.environ.get("ALLOW_REFRESH", "1") != "0"  # the cloud copy is fed by uploads from the PC
PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET = hashlib.sha256(("session:" + (os.environ.get("APP_SECRET") or PASSWORD or "local")).encode()).digest()
COOKIE = "sa_session"

app = FastAPI(title="Stock advisory (personal)", docs_url=None, redoc_url=None)
_state = {"snapshot": None, "mtime": None, "refreshing": False, "last_refresh_log": ""}


def _load():
    if not SNAPSHOT_PATH.exists():
        _state["snapshot"] = None
        return None
    m = SNAPSHOT_PATH.stat().st_mtime
    if _state["snapshot"] is None or m != _state["mtime"]:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            _state["snapshot"] = json.load(f)
        _state["mtime"] = m
    return _state["snapshot"]


def _token():
    return hmac.new(SECRET, b"ok", hashlib.sha256).hexdigest()


def _authed(request: Request) -> bool:
    if not PASSWORD:
        return True  # no password configured: local use on this PC
    if hmac.compare_digest(request.headers.get("X-App-Password", ""), PASSWORD):
        return True
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _token())


@app.middleware("http")
async def guard(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/") and p not in ("/api/login", "/api/health") and not _authed(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
def health():
    s = _load()
    return {"ok": True, "has_snapshot": s is not None, "data_as_of": s and s["data_as_of"], "password_set": bool(PASSWORD)}


@app.post("/api/login")
async def login(request: Request, response: Response):
    body = await request.json()
    if not PASSWORD or hmac.compare_digest(body.get("password", ""), PASSWORD):
        # Secure only over HTTPS (Render / Railway); on home Wi-Fi the app is plain HTTP and a Secure cookie would be dropped
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").startswith("https")
        response.set_cookie(COOKIE, _token(), httponly=True, samesite="lax", secure=https, max_age=30 * 86400)
        return {"ok": True}
    raise HTTPException(401, "wrong password")


@app.get("/api/market")
def market():
    s = _load()
    if not s:
        raise HTTPException(503, "no snapshot yet: run jobs/refresh.py or upload one")
    out = {k: s[k] for k in ("generated_at", "data_as_of", "source", "universe", "market", "limits")}
    vi = s.get("valuation_info") or {}
    out["valuation_info"] = {k: vi.get(k) for k in ("note", "counts", "method")}
    out["track_record"] = s.get("track_record")
    return out


@app.get("/api/stocks")
def stocks():
    s = _load()
    if not s:
        raise HTTPException(503, "no snapshot yet")
    return {"data_as_of": s["data_as_of"], "stocks": s["stocks"]}


@app.get("/api/stock/{symbol}")
def stock(symbol: str):
    s = _load()
    if not s:
        raise HTTPException(503, "no snapshot yet")
    sym = symbol.upper()
    row = next((r for r in s["stocks"] if r["symbol"] == sym), None)
    if not row:
        raise HTTPException(404, f"{sym} not in snapshot")
    vi = s.get("valuation_info") or {}
    return {"data_as_of": s["data_as_of"], "stock": row, "chart": s["charts"].get(sym), "limits": s["limits"],
            "valuation": (s.get("valuations") or {}).get(sym), "valuation_method": vi.get("method"), "valuation_note": vi.get("note")}


@app.get("/api/fno")
def fno_view():
    s = _load()
    if not s or not s.get("fno"):
        raise HTTPException(503, "no F&O view yet: run jobs/refresh.py")
    return s["fno"]


@app.get("/api/backtest")
def backtest():
    p = DATA_DIR / "backtest.json"
    s = _load()
    b = json.loads(p.read_text(encoding="utf-8")) if p.exists() else (s or {}).get("backtest")
    if not b:
        raise HTTPException(404, "no back-test yet: run jobs/backtest_ratings.py")
    rows = [dict(date=r["date"], counts=r["counts"], **({"h12": {k: r["h12"][k] for k in ("end", "universe_mean", "nifty500")} |
                 {"buy": r["h12"]["groups"]["BUY"]["mean"], "hold": r["h12"]["groups"]["HOLD"]["mean"], "sell": r["h12"]["groups"]["SELL"]["mean"]}}
                 if "h12" in r else {})) for r in b["results"]]
    log = DATA_DIR / "ratings_log.jsonl"
    if log.exists():
        days = sorted({json.loads(l)["as_of"] for l in log.read_text(encoding="utf-8").splitlines() if l.strip()})
    else:
        days = [r["as_of"] for r in (((s or {}).get("track_record") or {}).get("rows") or [])]
    return {"run_on": b["run_on"], "summary": b["summary"], "rows": rows, "live_log": {"days": len(days), "first": days[0] if days else None}}


@app.post("/api/snapshot")
async def upload_snapshot(request: Request):
    raw = await request.body()
    if request.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    if "stocks" not in data or "market" not in data:
        raise HTTPException(400, "not a snapshot")
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_bytes(raw)
    _state["snapshot"] = None
    return {"ok": True, "message": f"snapshot stored: {len(data['stocks'])} stocks, data as of {data.get('data_as_of')}"}


def _run_refresh():
    _state["refreshing"] = True
    try:
        r = subprocess.run([sys.executable, str(ROOT / "jobs" / "refresh.py")], capture_output=True, text=True, timeout=1800)
        _state["last_refresh_log"] = (r.stdout + r.stderr)[-2000:]
    except Exception as e:
        _state["last_refresh_log"] = f"refresh failed: {e!r}"
    finally:
        _state["refreshing"] = False


@app.post("/api/refresh")
def refresh():
    if not ALLOW_REFRESH:
        return {"ok": False, "message": "This online copy gets its data from your PC: the 6 pm scheduled refresh uploads it. "
                                        "To update now, run jobs\\run_daily.cmd on the PC."}
    if _state["refreshing"]:
        return {"ok": True, "message": "refresh already running"}
    threading.Thread(target=_run_refresh, daemon=True).start()
    return {"ok": True, "message": "refresh started; reload in a few minutes"}


@app.get("/api/refresh/status")
def refresh_status():
    return {"refreshing": _state["refreshing"], "log": _state["last_refresh_log"]}


@app.on_event("startup")
def startup():
    if os.environ.get("REFRESH_ON_START") == "1" and not SNAPSHOT_PATH.exists():
        threading.Thread(target=_run_refresh, daemon=True).start()


app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")
