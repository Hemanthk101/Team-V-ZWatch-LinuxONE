from pathlib import Path
import json
import os
import secrets
import urllib.request

from fastapi import Header, HTTPException

LAB_DATA = Path(
    os.environ.get(
        "ZWATCH_LAB_DATA",
        Path(__file__).resolve().parents[1] / "data" / "transaction-lab",
    )
)
LAB_DATA.mkdir(parents=True, exist_ok=True)
KEY_PATH = Path(os.environ.get("ZWATCH_LAB_KEY_FILE", LAB_DATA / ".internal-key"))
if not KEY_PATH.exists():
    try:
        descriptor = os.open(KEY_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(secrets.token_urlsafe(36))
LAB_KEY = KEY_PATH.read_text().strip()
if len(LAB_KEY) < 24:
    raise RuntimeError("Laboratory internal key is missing or too short")


def internal_auth(authorization: str | None = Header(default=None)):
    supplied = (authorization or "").removeprefix("Bearer ")
    if not secrets.compare_digest(supplied.encode(), LAB_KEY.encode()):
        raise HTTPException(401, "Internal service authentication required")


def request_json(url, method="GET", body=None, timeout=3, headers=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + LAB_KEY,
            **(headers or {}),
        },
        method=method,
    )
    # Laboratory endpoints are fixed loopback URLs; never route through a proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)
