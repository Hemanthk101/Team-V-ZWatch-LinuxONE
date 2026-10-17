# ZWatch transaction reliability: implemented milestone

Team V. LinuxONE architecture: s390x. Video production remains on hold until the project is finalized.

## What is working

- Durable test transactions with idempotency, conflict detection, bounded acceptance, leased work, and atomic local postings.
- Independent transaction and authorization HTTP processes, measured pool/HTTP timings, and service deadline accounting.
- Evidence-ranked incidents with preserved opening observations and retrievable trace records.
- Bounded fault injection and load sessions; one scoped recovery proposal/approval/verification workflow.
- Persistent incidents, proposals, transaction accounting, explicit stale/unavailable states, and a working console.

## Measured controlled-service results

9 injected-fault trials and 3 healthy high-traffic controls, using real HTTP test requests. Fixed parameters, same-host deployment, 500 ms collection, short laboratory SLO windows. No customer/payment data.

| Method | Fault trials detected | Median delay, seconds | Alert observations in healthy surge controls |
|---|---:|---:|---:|
| host_threshold | 0 / 9 | — | 0 / 233 |
| service_threshold | 9 / 9 | 0.949 | 0 / 233 |
| slo_burn | 9 / 9 | 3.102 | 0 / 233 |
| zwatch | 9 / 9 | 2.949 | 0 / 233 |

The simple service threshold was faster in this laboratory. ZWatch adds component evidence, investigation history, approval checks, and recovery verification; no detection-speed superiority is claimed.

- Leading hypothesis matched the injected component in 9 / 9 controlled trials. This is not proof of root cause in unknown production incidents.
- Pool recovery verified in 3 / 3 attempts. Recent verification outcomes and all earlier post-action misses are preserved separately.
- 8089 generated unique submissions; 813 repeated requests verified as idempotent; 0 generator submission errors.
- Dashboard totals include earlier development runs; benchmark counts cover only these 12 trials. The benchmark exercised recovery approvals programmatically; the console exposes the same proposal and approval steps to the operator.
- Maximum sampled transaction-service memory: 164.54 MiB. Maximum trial p95 collector/investigation duration: 42.76 ms. These measurements cover the transaction process, not total VM overhead.
- 42 engineering/integration checks passed, 0 skipped. Restart check reclaimed 24 pending transactions and verified a drained, consistent retained ledger. Stopped-backend visibility returned HTTP 503.
- A five-second fault configuration cleared automatically, observed after 5.598 seconds including collector scheduling and polling.

## Access and source

- Private dashboard through the owner's SSH tunnel: http://127.0.0.1:38900/transactions
- Public source: https://github.com/Hemanthk101/Team-V-ZWatch-LinuxONE
- Source revision checked: `c07b63919f174843cfd89d0c97782ba6d16d2886`
- Benchmark source and deployed application hashes match the published Git bytes.

## Remaining stages

Next: OpenTelemetry export and dependency/change integration; representative baselines, calibrated detection and drift review; then individual identity and roles, durable distributed ingestion, release/security controls, backup/failover exercises, and an actual operator pilot. Current investigation uses explicit evidence rules. Current identity is a shared owner token. This is a tested laboratory milestone, not a production-certified monitoring or payment platform.

The older deck, submission draft, and video describe v1. Submission materials and a new video will be finalized after the project is finalized.
