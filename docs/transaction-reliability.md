# Transaction reliability laboratory

ZWatch 2 adds an isolated transaction workload to the original host monitor.
Generated HTTP requests pass through running services; no real payments,
customer data, card credentials, or external payment provider are involved.

## Processes and resource limits

| Process | Loopback listener | Container CPU limit | Container RAM limit |
|---|---|---|---|
| ZWatch dashboard and original host monitor | 38900 | 1.5 CPUs | 2 GiB |
| Durable transaction service and investigator | 38901 | 0.8 CPU | 384 MiB |
| Authorization HTTP simulator | 38902 | 0.3 CPU | 256 MiB |

These are independent processes on one LinuxONE VM. They do not provide high
availability across machines. Code mounts are read-only, capabilities are
dropped, and no-new-privileges is enabled. The service key is separate from the
dashboard access token. Never publish either key or the live data directory.

## Transaction semantics

Acceptance persists a unique idempotency key, request fingerprint, amount in
minor test units, trace ID, timestamp, and queued state in SQLite WAL mode.
Repeating a key with identical content returns the existing transaction.
Reusing it for another amount returns HTTP 409. Capacity is bounded at 3,000
pending transactions; new requests receive HTTP 429 when full.

Six workers claim durable work and lease it before processing. They acquire a
connection from an actual four-connection pool, read the record, and call the
separate HTTP authorization simulator. Failed attempts return to the durable
queue with a short delay. HTTP authorization is side-effect-free in this lab.

The posting insert and completion-state update commit together. A primary key
prevents two postings for the same transaction. Startup reclaims interrupted
work in this single-process queue implementation. A real payment provider needs
its own idempotency and reconciliation protocol: a local database commit alone
cannot guarantee exactly-once effects in another system.

## Observations and diagnosis

The collector samples every 500 ms. Component spans measure actual pool waits,
database reads, and outbound HTTP calls. It propagates a W3C-style trace ID into
the authorization request. Records are local structured spans; this release
does not implement an OTLP exporter or claim a complete distributed trace.

The service objective is an explicitly laboratory assumption: 99% of accepted
transactions should complete within 800 ms. Deadline accounting includes
unfinished mature requests, preventing survivor bias from counting only
completed work. Latency p95 is reported separately for completed work.

The SLO comparator uses 3-second and 15-second windows, 14.4x error-budget burn,
and minimum mature request counts. These accelerated windows make the lab
observable; they are not a production 30-day SLO configuration.

The investigation engine uses fixed, reviewable evidence rules. It ranks pool
contention, authorization degradation, and absent worker progress from component
timings and service outcomes. It requires two seconds of sustained service
impact and available diagnostic evidence. Missing observations reset the
confirmation timer. Fault labels, injected schedules, and control state are not
inputs to diagnosis. The rankings are hypotheses, not causal proof or calibrated
probabilities. The original host view retains its separate Isolation Forest.

Each new incident preserves its opening observation and associated trace IDs.
The console can retrieve those local traces and their transaction state after
the current hypothesis changes. Incident confirmation and fault expiry use
monotonic time; wall-clock timestamps remain available for the timeline.

## Bounded faults

All experiments operate only on laboratory resources and expire in at most 30
seconds. Pool exhaustion borrows actual connections without returning them;
the expiry or approved recovery returns those exact leases. Dependency delay
adds an asynchronous delay only in the test authorization server. Worker pause
stops only these six laboratory consumers. None changes VM networking, host
processes, system files, or production services.

Load sessions are limited to 30 requests/second and 180 seconds. The browser
defaults to 12 requests/second for 120 seconds. Every tenth generated request
is repeated to verify idempotent acceptance. The generator records rejected
and failed submissions separately from accepted work.

## Recovery approval and verification

Only one executable recovery action is supported: releasing the fault injector's
own pool leases. There is no arbitrary command execution or production actuator.
The proposal names the active incident, evidence preconditions, expected result,
scope, expiry, and failure policy. Approval rechecks the same incident and
diagnosis, the proposal expiry, and the actual presence of injected leases.
Repeated approval cannot execute the action twice.

Verification examines at least 20 mature transactions in a rolling five-second
cohort accepted after the action, deadline compliance, the age of unfinished work,
and ledger reconciliation. Cumulative post-action misses are also retained in
the verification record, including misses before the service stabilized.
Historical misses remain in the incident and SLO history. No new transactions
means recovery cannot be verified. Verification has a 35-second deadline and
explicit failure/interruption states. A restart during verification requires
review. Re-acquiring released idle leases would recreate the test outage, so
the declared failure policy is to stop further actions and escalate rather than
perform that harmful reversal.

## Persistence, identity, and retention

Incidents, proposals, acceptance records, postings, spans, and action events are
persisted. Series charts retain a bounded in-memory window. Completed records,
spans, and events have 24-hour laboratory retention; pending transactions are
not deleted. Aggregate counts describe retained records, not lifetime totals.

The current UI uses the deployment owner's shared access token. Internal HTTP
calls use a separate owner-readable key and loopback listeners. Cross-origin
browser mutations are rejected. Individual SSO, role authorization, distributed
collector identity, and independently retained signed audit checkpoints are
subsequent work. This release must not be represented as a multi-tenant or
production-certified payment monitoring platform.

## Reproduce

Run `bash deploy.sh` in the project directory on LinuxONE. Access the dashboard
through the existing SSH tunnel and open `/transactions`.

Run all engineering and authenticated integration checks:

```bash
docker exec -e ZWATCH_TEST_URL=http://127.0.0.1:38900 \
  -e ZWATCH_KEY_FILE=/workspace/data/.access-token \
  zwatch python -m unittest discover -s tests -v
```

When no load or fault is active, run controlled benchmarking:

```bash
docker exec zwatch-lab python -m transaction_lab.benchmark --repeats 3 \
  --output data/transaction-lab/benchmark.json
```

The report includes observations, timing, comparator outcomes, hypotheses,
correctness, architecture, methodology, and source hashes. The benchmark runner
may know injected labels for evaluation; the investigator does not consume them.
Evaluate this small benchmark as a laboratory result, including failures. It
does not estimate real-world accuracy, production alert volume, or avoided loss.

## Sources informing the design

- OpenTelemetry conventions: https://opentelemetry.io/docs/concepts/semantic-conventions/
- SLO alerting: https://sre.google/workbook/alerting-on-slos/
- Linux resource pressure: https://cdn.kernel.org/doc/html/latest/accounting/psi.html
- Existing probable-cause capability: https://www.ibm.com/docs/en/instana-observability?topic=capabilities-root-cause-analysis
