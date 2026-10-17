"""Deterministic, evidence-linked investigation. Fault labels are not inputs."""

from copy import deepcopy

SLO_TARGET = 0.99
DEADLINE_S = 0.8


def baselines(long_window, short_window, host):
    """Comparators see the same observations. Windows are laboratory scale."""
    enough = (
        long_window["mature_requests"] >= 20 and short_window["mature_requests"] >= 5
    )
    burn_long = (long_window["bad_fraction"] or 0) / (1 - SLO_TARGET)
    burn_short = (short_window["bad_fraction"] or 0) / (1 - SLO_TARGET)
    return {
        "host_threshold": host.get("cpu_pct", 0) >= 90
        or host.get("memory_pct", 0) >= 92,
        "service_threshold": long_window["oldest_pending_s"] > DEADLINE_S
        or (enough and (short_window["bad_fraction"] or 0) > 0.1),
        "slo_burn": enough and burn_long >= 14.4 and burn_short >= 14.4,
        "burn_long": round(burn_long, 3),
        "burn_short": round(burn_short, 3),
        "slo_has_enough_requests": enough,
    }


def investigate(snapshot):
    """Evidence ranking is a hypothesis, not calibrated causal confidence."""
    components = snapshot["components"]
    db = components["database.pool"]
    auth = components["authorization.http"]
    backlog = snapshot["oldest_pending_s"] > DEADLINE_S
    hypotheses = []
    if db["count"] >= 3 and ((db["p95_ms"] or 0) > 100 or db["errors"] >= 3):
        hypotheses.append(
            {
                "id": "database_pool",
                "component": "Database connection pool",
                "evidence": [
                    f"Pool acquisition p95 {db['p95_ms']} ms",
                    f"{db['errors']} failed acquisitions in the last 3 seconds",
                ],
                "contrary": [f"Authorization HTTP p95 {auth['p95_ms']} ms"],
                "check": "Inspect leaked leases and pool demand before increasing connection limits.",
                "action": "release_lab_pool_leases",
            }
        )
    if auth["count"] >= 3 and ((auth["p95_ms"] or 0) > 250 or auth["errors"] >= 3):
        hypotheses.append(
            {
                "id": "authorization",
                "component": "Authorization dependency",
                "evidence": [
                    f"HTTP p95 {auth['p95_ms']} ms",
                    f"{auth['errors']} HTTP errors in the last 3 seconds",
                ],
                "contrary": [f"Database acquisition p95 {db['p95_ms']} ms"],
                "check": "Inspect downstream latency and retry volume. Preserve idempotency across retries.",
                "action": None,
            }
        )
    if backlog and not hypotheses:
        hypotheses.append(
            {
                "id": "worker_progress",
                "component": "Transaction worker progress",
                "evidence": [
                    f"Oldest unfinished transaction {snapshot['oldest_pending_s']:.2f} s",
                    f"Queue depth {snapshot['queue_depth']}",
                ],
                "contrary": [
                    "No recent slow dependency spans were observed; missing spans limit diagnosis."
                ],
                "check": "Check worker heartbeats, scheduling, and queue consumer health.",
                "action": None,
            }
        )
    return hypotheses


class Investigator:
    def __init__(self):
        self.since = None
        self.clear_since = None
        self.active = None
        self.incidents = []
        self.last_observation = None

    def observe(self, snapshot, comparison):
        now = snapshot["timestamp"]
        clock = snapshot.get("monotonic_s", now)
        if (
            self.last_observation is not None
            and not 0 <= clock - self.last_observation <= 3
        ):
            self.since = self.clear_since = None
        self.last_observation = clock
        hypotheses = investigate(snapshot)
        impacted = snapshot["oldest_pending_s"] > DEADLINE_S or comparison["slo_burn"]
        signal = impacted and bool(hypotheses)
        opened = closed = False
        if signal:
            self.clear_since = None
            self.since = clock if self.since is None else self.since
            if self.active is None and clock - self.since >= 2:
                self.active = {
                    "id": max((i["id"] for i in self.incidents), default=0) + 1,
                    "opened_at": now,
                    "closed_at": None,
                    "status": "open",
                    "hypotheses": hypotheses,
                    "acknowledged": False,
                    "impact": {
                        "oldest_pending_s": snapshot["oldest_pending_s"],
                        "bad_fraction": snapshot["bad_fraction"],
                    },
                    "opening_evidence": deepcopy(snapshot),
                }
                self.incidents.append(self.active)
                self.incidents = self.incidents[-100:]
                opened = True
            if self.active:
                self.active["hypotheses"] = hypotheses
        else:
            self.since = None
            # Recovery requires current backlog and recent deadline outcomes clear.
            cleared = (
                snapshot["queue_depth"] == 0
                and (snapshot["bad_fraction"] or 0) <= 1 - SLO_TARGET
            )
            if cleared:
                self.clear_since = (
                    clock if self.clear_since is None else self.clear_since
                )
                if self.active and clock - self.clear_since >= 3:
                    self.active.update(status="resolved", closed_at=now)
                    self.active = None
                    closed = True
            else:
                self.clear_since = None
        return {
            "candidate": signal,
            "confirmed": self.active is not None,
            "hypotheses": hypotheses,
            "opened": opened,
            "closed": closed,
        }
