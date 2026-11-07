"""Upload data/snapshot.json from this PC to the online copy of the app (Render / Railway).
Reads the address from config/cloud_url.txt and the password from config/app_password.txt (both kept out of Git),
or takes them as arguments. Does nothing (exit 0) when no address is configured, so the scheduled job is safe
to run before the app is deployed.
Run: .venv\\Scripts\\python.exe jobs\\push_snapshot.py [https://your-app.onrender.com] [password]"""
import gzip
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    p = ROOT / "config" / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


url = (sys.argv[1] if len(sys.argv) > 1 else read("cloud_url.txt")).rstrip("/")
password = sys.argv[2] if len(sys.argv) > 2 else read("app_password.txt")
if not url:
    print("push skipped: no config/cloud_url.txt (app not deployed yet)")
    sys.exit(0)
if not password:
    print("push skipped: no config/app_password.txt")
    sys.exit(1)
body = gzip.compress((ROOT / "data" / "snapshot.json").read_bytes())
req = urllib.request.Request(f"{url}/api/snapshot", data=body, method="POST",
                             headers={"Content-Type": "application/json", "Content-Encoding": "gzip", "X-App-Password": password})
try:
    # free Render instances sleep when idle; the first request wakes them and can take about a minute
    with urllib.request.urlopen(req, timeout=180) as r:
        print("push:", r.status, json.loads(r.read().decode())["message"])
except urllib.error.HTTPError as e:
    print("push failed:", e.code, e.read().decode()[:200])
    sys.exit(1)
