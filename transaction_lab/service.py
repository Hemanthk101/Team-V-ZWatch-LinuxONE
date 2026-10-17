"""Bounded laboratory runtime: real requests, durable queue, independent HTTP.

Injected faults only affect this laboratory's resources and expire automatically.
Fault labels and schedules are excluded from investigation inputs.
"""

from collections import deque
from copy import deepcopy
from pathlib import Path
import json
import os
import platform
import queue
import secrets
import threading
import time
import uuid
import urllib.error

import psutil

from transaction_lab.analysis import DEADLINE_S, SLO_TARGET, Investigator, baselines
from transaction_lab.common import LAB_DATA, request_json
from transaction_lab.ledger import Ledger

AUTH_URL = "http://127.0.0.1:38902"
LAB_URL = "http://127.0.0.1:38901"


class LabService:
    def __init__(self, data=LAB_DATA):
        self.ledger = Ledger(Path(data) / "transactions.sqlite")
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.investigator = Investigator()
        self.series = deque(maxlen=900)
        self.threads = []
        self.fault = None
        self.fault_deadline = 0
        self.load_thread = None
        self.load_starting = False
        self.load = {
            "running": False,
            "rps": 0,
            "sent": 0,
            "errors": 0,
            "duplicates_verified": 0,
        }
        self.proposal = None
        self.last_sample = None
        self.last_error = None
        self.process = psutil.Process()
        self.started = time.time()
        self.state_path = Path(data) / "investigation.json"
        if self.state_path.exists():
            restored = json.loads(self.state_path.read_text())
            self.investigator.incidents = restored.get("incidents", [])[-50:]
            self.investigator.active = next(
                (
                    i
                    for i in reversed(self.investigator.incidents)
                    if i["status"] == "open"
                ),
                None,
            )
            saved_proposal = restored.get("proposal")
            if saved_proposal and saved_proposal["status"] == "verifying":
                saved_proposal.update(
                    status="verification_interrupted",
                    detail="Service restarted; inspect current outcomes before another action.",
                )
            self.proposal = saved_proposal
        self.ledger.event(
            "laboratory_started",
            {"architecture": platform.machine(), "pid": os.getpid()},
        )

    def persist(self):
        payload = {
            "incidents": self.investigator.incidents[-50:],
            "proposal": self.proposal,
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload))
        temporary.replace(self.state_path)

    def start(self):
        for target in [self.collect] + [self.worker] * 6:
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=3)
        self.ledger.pool.restore()
        if not any(thread.is_alive() for thread in self.threads):
            self.ledger.close()

    def worker(self):
        while not self.stop.is_set():
            with self.lock:
                paused = (
                    self.fault
                    and self.fault["kind"] == "worker_pause"
                    and self.fault_deadline > time.monotonic()
                )
            if paused:
                self.stop.wait(0.03)
                continue
            row = self.ledger.claim()
            if row is None:
                self.stop.wait(0.015)
                continue
            try:
                started = time.perf_counter()
                acquired = False
                try:
                    with self.ledger.pool.connection() as connection:
                        acquired = True
                        self.ledger.trace(
                            row["trace_id"],
                            "database.pool",
                            (time.perf_counter() - started) * 1000,
                            "ok",
                        )
                        query_start = time.perf_counter()
                        connection.execute(
                            "SELECT amount_minor FROM transactions WHERE id=?",
                            (row["id"],),
                        ).fetchone()
                        self.ledger.trace(
                            row["trace_id"],
                            "database.query",
                            (time.perf_counter() - query_start) * 1000,
                            "ok",
                        )
                except queue.Empty:
                    self.ledger.trace(
                        row["trace_id"],
                        "database.pool",
                        (time.perf_counter() - started) * 1000,
                        "timeout",
                    )
                    raise TimeoutError("Database connection acquisition timed out")
                started = time.perf_counter()
                try:
                    result = request_json(
                        AUTH_URL + "/authorize",
                        "POST",
                        {},
                        timeout=0.4,
                        headers={
                            "traceparent": f"00-{row['trace_id']}-{secrets.token_hex(8)}-01"
                        },
                    )
                    if not result.get("approved"):
                        raise ValueError("Authorization simulation declined")
                    self.ledger.trace(
                        row["trace_id"],
                        "authorization.http",
                        (time.perf_counter() - started) * 1000,
                        "ok",
                    )
                except Exception:
                    self.ledger.trace(
                        row["trace_id"],
                        "authorization.http",
                        (time.perf_counter() - started) * 1000,
                        "timeout_or_error",
                    )
                    raise
                self.ledger.complete(row)
            except Exception as error:
                self.ledger.retry(row["id"], type(error).__name__)

    def collect(self):
        psutil.cpu_percent(None)
        step = 0
        while not self.stop.is_set():
            tick = time.perf_counter()
            try:
                with self.lock:
                    if self.fault and self.fault_deadline <= time.monotonic():
                        self.clear_fault("automatic_expiry")
                    # A borrowed connection may have been in use during injection.
                    if self.fault and self.fault["kind"] == "pool_exhaustion":
                        self.ledger.pool.exhaust()
                long = self.ledger.snapshot(15, DEADLINE_S)
                long["monotonic_s"] = time.monotonic()
                short = self.ledger.snapshot(3, DEADLINE_S)
                host = {
                    "cpu_pct": psutil.cpu_percent(None),
                    "memory_pct": psutil.virtual_memory().percent,
                }
                comparisons = baselines(long, short, host)
                with self.lock:
                    analysis = self.investigator.observe(long, comparisons)
                    item = {
                        **long,
                        "short_bad_fraction": short["bad_fraction"],
                        "comparators": comparisons,
                        "analysis": analysis,
                        "host": host,
                    }
                    item["analysis_ms"] = round((time.perf_counter() - tick) * 1000, 3)
                    self.series.append(item)
                    self.last_sample = time.monotonic()
                    self.last_error = None
                    if analysis["opened"] or analysis["closed"]:
                        self.ledger.event(
                            (
                                "incident_opened"
                                if analysis["opened"]
                                else "incident_resolved"
                            ),
                            {"incident": self.investigator.incidents[-1]},
                        )
                        self.persist()
                    self.check_recovery(long)
                if step % 60 == 0:
                    self.ledger.prune()
                step += 1
            except Exception as error:
                self.last_error = type(error).__name__
            self.stop.wait(max(0.02, 0.5 - (time.perf_counter() - tick)))

    def inject(self, kind, duration_s, actor):
        with self.lock:
            if self.fault:
                raise ValueError("Clear the current fault before starting another")
            if kind not in ("pool_exhaustion", "dependency_delay", "worker_pause"):
                raise ValueError("Unsupported laboratory fault")
            if kind == "dependency_delay":
                request_json(
                    AUTH_URL + "/control/delay", "POST", {"duration_s": duration_s}
                )
            elif kind == "pool_exhaustion":
                self.ledger.pool.exhaust()
            self.fault = {
                "kind": kind,
                "started_at": time.time(),
                "expires_at": time.time() + duration_s,
            }
            self.fault_deadline = time.monotonic() + duration_s
            self.ledger.event(
                "fault_injected",
                {**self.fault, "actor": actor, "scope": "isolated test service"},
            )
            return self.fault.copy()

    def clear_fault(self, actor):
        with self.lock:
            previous = self.fault
            self.ledger.pool.restore()
            if previous and previous["kind"] == "dependency_delay":
                request_json(AUTH_URL + "/control/delay", "POST", {"duration_s": 0})
            self.fault = None
            if previous:
                self.ledger.event(
                    "fault_cleared", {"kind": previous["kind"], "actor": actor}
                )
            return {"cleared": previous is not None}

    def start_load(self, rps, duration_s, actor):
        with self.lock:
            if (
                self.load["running"]
                or self.load_starting
                or (self.load_thread and self.load_thread.is_alive())
            ):
                raise ValueError("A bounded load session is already running")
            self.load_starting = True
            session = uuid.uuid4().hex[:12]
            self.load = {
                "running": True,
                "rps": rps,
                "sent": 0,
                "errors": 0,
                "duplicates_verified": 0,
                "session": session,
                "expires_at": time.time() + duration_s,
            }
            self.ledger.event("load_started", {**self.load, "actor": actor})

        def produce():
            started = time.monotonic()
            index = 0
            try:
                while (
                    not self.stop.is_set() and time.monotonic() - started < duration_s
                ):
                    with self.lock:
                        if (
                            not self.load["running"]
                            or self.load.get("session") != session
                        ):
                            break
                    body = {
                        "idempotency_key": f"{session}-{index}",
                        "amount_minor": 100 + (index % 5000),
                    }
                    trace_id = secrets.token_hex(16)
                    try:
                        result = request_json(
                            LAB_URL + "/transactions",
                            "POST",
                            body,
                            headers={
                                "traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"
                            },
                        )
                        if index % 10 == 0:
                            duplicate = request_json(
                                LAB_URL + "/transactions", "POST", body
                            )
                            if (
                                duplicate["transaction_id"] != result["transaction_id"]
                                or not duplicate["deduplicated"]
                            ):
                                raise ValueError(
                                    "Duplicate request was not deduplicated"
                                )
                            with self.lock:
                                self.load["duplicates_verified"] += 1
                        with self.lock:
                            self.load["sent"] += 1
                    except Exception:
                        with self.lock:
                            self.load["errors"] += 1
                    index += 1
                    self.stop.wait(max(0, started + index / rps - time.monotonic()))
            finally:
                with self.lock:
                    self.load["running"] = False
                    self.ledger.event("load_stopped", self.load.copy())

        thread = threading.Thread(target=produce, daemon=True)
        with self.lock:
            self.load_thread = thread
            self.threads = [t for t in self.threads if t.is_alive()]
            self.threads.append(thread)
            thread.start()
            self.load_starting = False
        return self.load.copy()

    def propose(self, incident_id, actor):
        with self.lock:
            incident = self.investigator.active
            if not incident or incident["id"] != incident_id:
                raise ValueError("An active incident is required")
            if self.proposal and self.proposal["status"] == "verifying":
                raise ValueError("Recovery verification is already running")
            hypotheses = incident["hypotheses"]
            if not hypotheses or hypotheses[0]["action"] != "release_lab_pool_leases":
                raise ValueError("No permitted action for this hypothesis")
            identifier = uuid.uuid4().hex
            self.proposal = {
                "id": identifier,
                "incident_id": incident_id,
                "action": "release_lab_pool_leases",
                "created_at": time.time(),
                "expires_at": time.time() + 60,
                "status": "awaiting_approval",
                "scope": "Release only connections deliberately leased by the ZWatch fault injector",
                "preconditions": "Active database-pool hypothesis; injected pool leases still present; same incident",
                "expected": "Queue drains and new transactions meet the 800 ms laboratory deadline",
                "rollback": "Released idle pool leases are not re-acquired: doing so would recreate the test outage. On failure, stop further actions and escalate.",
                "requested_by": actor,
            }
            self.ledger.event("recovery_proposed", self.proposal)
            self.persist()
            return self.proposal.copy()

    def approve(self, proposal_id, actor):
        with self.lock:
            proposal = self.proposal
            if (
                not proposal
                or proposal["id"] != proposal_id
                or proposal["status"] != "awaiting_approval"
            ):
                raise ValueError("Proposal is not awaiting approval")
            if time.time() > proposal["expires_at"]:
                raise ValueError(
                    "Proposal expired; inspect current evidence and propose again"
                )
            incident = self.investigator.active
            if (
                not incident
                or incident["id"] != proposal["incident_id"]
                or not incident["hypotheses"]
                or incident["hypotheses"][0]["id"] != "database_pool"
            ):
                raise ValueError("Incident evidence changed; action rejected")
            if (
                not self.fault
                or self.fault["kind"] != "pool_exhaustion"
                or not self.ledger.pool.held
            ):
                raise ValueError("Test-only pool leases are no longer present")
            count = len(self.ledger.pool.held)
            self.clear_fault("approved_recovery:" + actor)
            proposal.update(
                status="verifying",
                executed_at=time.time(),
                verify_deadline=time.time() + 35,
                approved_by=actor,
                released_connections=count,
            )
            self.ledger.event("recovery_executed", proposal)
            self.persist()
            return proposal.copy()

    def check_recovery(self, snapshot):
        proposal = self.proposal
        if not proposal or proposal["status"] != "verifying":
            return
        # A mature rolling five-second cohort measures current recovery. Preserve
        # all post-action misses separately rather than erasing the transition.
        now = time.time()
        with self.ledger.lock:
            all_after = self.ledger.db.execute(
                "SELECT accepted_at,completed_at FROM transactions WHERE accepted_at>=? AND accepted_at<=?",
                (proposal["executed_at"], now - DEADLINE_S),
            ).fetchall()
        cohort = [row for row in all_after if row[0] >= now - 5 - DEADLINE_S]
        correctness = self.ledger.correctness()
        consistent = not any(
            correctness[k]
            for k in (
                "completed_without_posting",
                "posting_without_completion",
                "amount_mismatches",
                "duplicate_postings",
            )
        )
        bad = sum(r[1] is None or r[1] - r[0] > DEADLINE_S for r in cohort)
        good = (
            now - proposal["executed_at"] >= 5 + DEADLINE_S
            and len(cohort) >= 20
            and bad / len(cohort) <= 1 - SLO_TARGET
            and snapshot["oldest_pending_s"] <= DEADLINE_S
            and consistent
        )
        if good or time.time() > proposal["verify_deadline"]:
            proposal.update(
                status="verified" if good else "verification_failed",
                verified_at=time.time(),
                post_action_requests=len(cohort),
                post_action_bad_requests=bad,
                verification_window_s=5,
                total_post_action_requests=len(all_after),
                total_post_action_bad_requests=sum(
                    r[1] is None or r[1] - r[0] > DEADLINE_S for r in all_after
                ),
                correctness=correctness,
            )
            self.ledger.event("recovery_verification", proposal)
            self.persist()

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.last_sample if self.last_sample else None
            return {
                "scope": "Real HTTP test transactions on isolated services; no real payments",
                "freshness": {
                    "age_s": round(age, 2) if age is not None else None,
                    "status": (
                        "healthy"
                        if age is not None and age < 3 and not self.last_error
                        else "unknown"
                    ),
                    "last_error": self.last_error,
                },
                "latest": self.series[-1] if self.series else None,
                "series": list(self.series)[-120:],
                "incidents": deepcopy(self.investigator.incidents[-20:]),
                "proposal": deepcopy(self.proposal),
                "load": self.load.copy(),
                "laboratory_fault": self.fault,
                "correctness": self.ledger.correctness(),
                "traces": self.ledger.recent("traces", 35),
                "events": self.ledger.recent("events", 35),
                "runtime": {
                    "architecture": platform.machine(),
                    "pid": os.getpid(),
                    "rss_mib": round(self.process.memory_info().rss / 1024**2, 2),
                    "uptime_s": round(time.time() - self.started),
                },
                "policy": {
                    "target": SLO_TARGET,
                    "deadline_s": DEADLINE_S,
                    "long_window_s": 15,
                    "short_window_s": 3,
                    "persistence_s": 2,
                    "note": "Short laboratory windows; not a production SLO assessment",
                },
            }
