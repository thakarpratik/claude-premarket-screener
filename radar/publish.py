"""Upload the latest results to the private Vercel Blob store that the online site reads.

The engine keeps running on this PC; only its output (one JSON bundle) goes online.
Needs BLOB_READ_WRITE_TOKEN in the environment or in private.env (never committed).
Without it, publishing is skipped and the local app works as before."""
import json
import os
import urllib.error
import urllib.request

from .config import ROOT

BLOB_PATH = "moveradar/data.json"
API = "https://vercel.com/api/blob/?pathname=" + BLOB_PATH


def load_token():
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if token:
        return token
    env = ROOT / "private.env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition("=")
        if key.strip() == "BLOB_READ_WRITE_TOKEN":
            return value.strip().strip('"').strip("'")
    return ""


def upload(bundle):
    """Returns a short status message."""
    token = load_token()
    if not token:
        return "skipped (no BLOB_READ_WRITE_TOKEN in private.env)"
    body = json.dumps(bundle, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(API, data=body, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "x-api-version": "12",
        "x-vercel-blob-access": "private",
        "x-content-type": "application/json",
        "x-allow-overwrite": "1",
        "x-add-random-suffix": "0",
        "x-cache-control-max-age": "60",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return f"uploaded {len(body) / 1024:.0f} KB"
    except urllib.error.HTTPError as e:
        return f"failed ({e.code}): {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:
        return f"failed: {e}"
