# Implementation roadmap

Owner: Team V. Video production remains on hold until project finalization.

## Milestone 1: real transaction incident and recovery laboratory

Implemented:

- Durable transaction acceptance, repeat-key handling, conflict rejection, bounded backlog.
- Separate running authorization HTTP service and six queue consumers.
- Real database-connection-pool leases and measured dependency timings.
- Deadline accounting that includes unfinished requests; throughput, queue age, completed latency.
- Evidence rules for pool contention, slow authorization, and missing worker progress.
- Elapsed-time confirmation, data freshness, persistent incidents and proposals.
- Isolated faults with automatic expiry and bounded load sessions.
- Explicit proposal/approval, stale-evidence checks, and one narrowly permitted recovery action.
- Recent-window recovery verification, preserved historical misses, local ledger reconciliation.
- Loopback listeners, separate internal service key, constrained containers.
- Reproducible controlled-service benchmark and engineering checks.

Acceptance evidence is in `evidence/transaction-benchmark.json` after the measured
run completes. Treat observations and failed cases as evidence; feature presence
alone does not establish readiness.

## Milestone 2: telemetry integration and robust detection

- OTLP instrumentation/export and OpenTelemetry resource conventions.
- Service/trace dependency discovery and deployment/configuration change events.
- More representative workload history, workload-conditioned expectations, and
  separate training/calibration/evaluation periods.
- Unexpected throughput drops, drift review, model registry and rollback.
- Validated pressure-stall metrics where the VM permits them.
- Independent collector health, durable buffering, duplicate and late-data policies.
- Tests against additional fault combinations and normal scheduled workload changes.

The current transaction investigator is deliberately described as evidence
rules. The original host detector is Isolation Forest. No causal AI, predictive
accuracy, or learned diagnosis is claimed for the new transaction workflow.

## Milestone 3: organizational deployment

- OIDC login and named actor identities; viewer/investigator/operator/admin roles.
- Revocable collector identities, key rotation, HTTPS, payload/cardinality budgets.
- PostgreSQL incident/configuration store and existing telemetry backend adapters.
- Notification delivery with retries, deduplication, maintenance windows and ownership.
- Independent signed audit checkpoints, redaction and configurable retention.
- Signed release artifacts, SBOM and dependency/security checks in CI.
- Backup restoration and upgrade/rollback drills.

## Milestone 4: production and pilot evidence

- Multiple failure domains, measured failover, capacity and durability objectives.
- Extended representative load and fault-injection results.
- Actual operator pilot, reviewed false alerts, task completion and recovery evidence.
- Organizational security review, support ownership and incident procedures.
- Optional confidential-computing deployment only after required LinuxONE host,
  guest and hardware support is verified.

Pilot outcomes require an actual participating organization and cannot be
fabricated by extending this laboratory. No production certification or
first-place competition outcome is implied by this roadmap.
