from contextlib import asynccontextmanager
import secrets
import re
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Literal
from transaction_lab.common import internal_auth
from transaction_lab.ledger import Conflict
from transaction_lab.service import LabService

service = None


@asynccontextmanager
async def lifespan(app):
    global service
    service = LabService()
    service.start()
    yield
    service.close()


app = FastAPI(
    title="ZWatch transaction laboratory",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    dependencies=[Depends(internal_auth)],
)


class Transaction(BaseModel):
    idempotency_key: str = Field(
        min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"
    )
    amount_minor: int = Field(ge=1, le=1000000, strict=True)


class Fault(BaseModel):
    kind: Literal["pool_exhaustion", "dependency_delay", "worker_pause"]
    duration_s: int = Field(default=20, ge=5, le=30)


class Load(BaseModel):
    rps: int = Field(default=12, ge=1, le=30)
    duration_s: int = Field(default=90, ge=5, le=180)


class Proposal(BaseModel):
    incident_id: int = Field(ge=1)


class Approval(BaseModel):
    proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def action(call):
    try:
        return call()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/transactions", status_code=202)
def accept(body: Transaction, traceparent: str | None = Header(default=None)):
    match = re.fullmatch(r"00-([0-9a-f]{32})-[0-9a-f]{16}-01", traceparent or "")
    trace_id = match[1] if match and int(match[1], 16) else secrets.token_hex(16)
    try:
        row, duplicate = service.ledger.accept(
            body.idempotency_key, body.amount_minor, trace_id
        )
    except Conflict as error:
        raise HTTPException(409, str(error)) from error
    except OverflowError as error:
        raise HTTPException(429, str(error)) from error
    return {
        "transaction_id": row["id"],
        "deduplicated": duplicate,
        "state": row["state"],
        "trace_id": row["trace_id"],
    }


@app.get("/transactions/{identifier}")
def transaction(identifier: str):
    row = service.ledger.get(identifier)
    if not row:
        raise HTTPException(404, "Transaction not found")
    return row


@app.get("/state")
def state():
    return service.snapshot()


@app.get("/traces/{trace_id}")
def trace_detail(trace_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        raise HTTPException(422, "Trace ID must be 32 hexadecimal characters")
    return service.ledger.trace_detail(trace_id)


@app.get("/healthz")
def health():
    state = service.snapshot()["freshness"]
    return Response(status_code=200 if state["status"] == "healthy" else 503)


@app.post("/control/load")
def load(body: Load):
    return action(
        lambda: service.start_load(body.rps, body.duration_s, "authenticated_operator")
    )


@app.post("/control/stop")
def stop_load():
    with service.lock:
        service.load["running"] = False
    return {"stopping": True}


@app.post("/control/fault")
def fault(body: Fault):
    return action(
        lambda: service.inject(body.kind, body.duration_s, "authenticated_operator")
    )


@app.post("/control/clear")
def clear():
    return service.clear_fault("authenticated_operator")


@app.post("/recovery/propose")
def propose(body: Proposal):
    return action(lambda: service.propose(body.incident_id, "authenticated_operator"))


@app.post("/recovery/approve")
def approve(body: Approval):
    return action(lambda: service.approve(body.proposal_id, "authenticated_operator"))
