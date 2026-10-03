"""Upload the dashboard to the private Vercel app.

The phone reads this copy. The file stays in a private Blob store, and the
Vercel app only serves it after the password check. Without
BLOB_READ_WRITE_TOKEN in the environment or in private.env, the local HTML
file is still written and this step is skipped.
"""
import os
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / "private.env"
BLOB_PATH = "latest.html"
API = "https://vercel.com/api/blob/"


def load_token() -> str:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if token:
        return token
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "BLOB_READ_WRITE_TOKEN":
            return value.strip().strip('"').strip("'")
    return ""


def upload_dashboard(html: str) -> bool:
    """Upload html. Return True when the private app has the new copy."""
    token = load_token()
    if not token:
        print("  private app: skipped (no BLOB_READ_WRITE_TOKEN in private.env)")
        return False
    response = requests.put(
        API,
        params={"pathname": BLOB_PATH},
        data=html.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-version": "12",
            "x-vercel-blob-access": "private",
            "x-content-type": "text/html",
            "x-allow-overwrite": "1",
            "x-add-random-suffix": "0",
            "x-cache-control-max-age": "60",
        },
        timeout=90,
    )
    if response.ok:
        print("  private app: uploaded latest.html")
        return True
    detail = response.text.replace("\n", " ")[:300]
    print(f"  private app upload failed ({response.status_code}): {detail}")
    return False
