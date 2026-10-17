"""Independent HTTP dependency for test authorizations; no payment network."""

import asyncio
import threading
import time
from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from transaction_lab.common import internal_auth

app = FastAPI(
    title="ZWatch test authorization dependency",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
lock = threading.Lock()
slow_until = 0.0


class Delay(BaseModel):
    duration_s: int = Field(default=15, ge=0, le=30)


@app.post("/control/delay", dependencies=[Depends(internal_auth)])
def delay(body: Delay):
    global slow_until
    with lock:
        slow_until = time.monotonic() + body.duration_s if body.duration_s else 0
    return {"duration_s": body.duration_s}


@app.post("/authorize", dependencies=[Depends(internal_auth)])
async def authorize():
    with lock:
        delayed = time.monotonic() < slow_until
    await asyncio.sleep(0.65 if delayed else 0.008)
    # No external side effects: retries may safely repeat this simulation.
    return {"approved": True, "scope": "simulated authorization; no money moved"}


@app.get("/healthz")
def health():
    return {"status": "ok", "service": "test-authorization"}
