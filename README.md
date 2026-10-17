# ZWatch - Transaction reliability on LinuxONE / Team V

**Observe transaction failures, investigate dependency evidence, and verify a controlled recovery.** Built for IBM Z Datathon 2026 under Real-Time AI for Critical Decisions / System Anomaly Detection.

ZWatch now includes an isolated transaction service, durable queue, separate HTTP authorization simulator, dependency timings, deadline accounting, evidence-ranked incident hypotheses, and an approval/verification workflow for one bounded test recovery action. Generated requests exercise real services; no real payments are processed. The original host monitor and its separate synthetic Isolation Forest replay remain available.

**Current implementation:** [transaction reliability design and reproduction](docs/transaction-reliability.md), [remaining implementation stages](docs/implementation-roadmap.md).

**Original prototype materials:** [v1 pitch deck](docs/Team-V-ZWatch-Pitch.pdf), [v1 architecture](docs/architecture.svg), [synthetic replay evaluation](evidence/evaluation.json), [v1 submission draft](docs/submission-answers.md). These describe the earlier host-monitor prototype; final submission materials will be refreshed after the project is finalized.

The previously recorded v1 video is historical. New video production is on hold until the owner finalizes the project.

## Transaction reliability milestone

- Separate processes for dashboard, transaction queue/investigator, and HTTP authorization dependency; each listener binds to VM loopback.
- Durable idempotent acceptance, conflicting-key rejection, bounded backlog, atomic posting/completion, and interrupted-work recovery.
- Actual pool-acquisition and HTTP timings with propagated trace IDs; local structured spans, not yet OTLP export.
- Mature request cohorts include unfinished work in deadline misses. Low request volume and stale data are explicit states.
- Host thresholds, service thresholds, accelerated SLO burn alerts, and evidence-plus-persistence investigation observe the same workload.
- Pool exhaustion, dependency delay, and paused consumers affect only the laboratory and expire automatically.
- One proposal/approval action releases only deliberately injected connection leases; current diagnosis and expiry are rechecked before execution.
- Recovery checks a mature recent cohort, unfinished-work age, and local posting consistency while preserving earlier misses.
- Incidents, proposals, action events, transactions, and postings persist across process restarts. Tests cover concurrency, atomic rollback, idempotency, restart recovery, authorization, and approval preconditions.

## What works

- Live CPU, memory, root filesystem, network rate, load per CPU, and I/O wait collection using psutil.
- Separate live and synthetic models. Live training uses 80 real observations and a separate 40-observation calibration set after a 120-sample warmup.
- Isolation Forest with 128 trees, a calibration threshold at the 99.5th percentile, positive robust baseline deviations, and explicit critical resource guardrails.
- Three consecutive abnormal observations open an incident. Three clean observations resolve it. This is incident hysteresis, not predictive forecasting.
- Incident acknowledgment, SQLite WAL storage, audit records, CSV export, evaluation JSON export, input validation, token authentication, and same-origin mutation protection.
- Responsive dashboard with no external front-end/CDN dependency.
- Six repeatable held-out synthetic scenarios; dataset seeds and runtime are included in the evidence export.

## Honest scope

This is a production-oriented single-VM prototype, not a production-certified monitoring platform. Synthetic replay accuracy and controlled laboratory failures do not establish real-world accuracy. The host monitor's live baseline is intentionally short and needs representative operational history. Transaction investigation uses reviewable evidence rules rather than a fitted ML model; hypotheses are not proven root causes. The only executable recovery action operates on the test injector's own pool leases after explicit operator approval.

The deployment runs Python AI inference on IBM LinuxONE / s390x CPU. It does not use or claim Telum acceleration, protected execution, high availability, or confidential computing. VM system counters are read from inside a container; the root filesystem metric refers to its root filesystem, not every host volume.

## Run locally

Requires Python 3.12+ on Linux for the live collector.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Initialize the shared internal key before starting the two lab processes.
python -c "import transaction_lab.common"
python -m uvicorn transaction_lab.authapp:app --host 127.0.0.1 --port 38902 &
python -m uvicorn transaction_lab.labapp:app --host 127.0.0.1 --port 38901 &
python -m uvicorn app:app --host 127.0.0.1 --port 38900 --workers 1
```

The first start creates `.access-token` (owner-only permissions on Linux). Read it locally and enter it in the dashboard at `http://127.0.0.1:38900`. Never commit this file. For an existing secret, set `ZWATCH_KEY_FILE` to its path. Set `ZWATCH_DATA_DIR` to a writable persistent data directory. The API requires `Authorization: Bearer <token>`. Use exactly one worker because collection state is local to the process.

## LinuxONE deployment

The Dockerfile pins the IBM sample environment image by digest and installs the exact versions in `requirements-lock.txt`. The deployment starts the dashboard and both laboratory dependencies with explicit resource limits.

```bash
mkdir -p data
bash deploy.sh
# From your workstation, open an encrypted tunnel:
ssh -N -L 38900:127.0.0.1:38900 -i YOUR_PRIVATE_KEY linux1@YOUR_VM_IP
# Open http://127.0.0.1:38900 and enter the token stored on the VM in data/.access-token.
```

The deployment binds to VM loopback and uses an encrypted SSH tunnel for workstation access. Open `/transactions` for the new console and `/` for the original monitor. `deploy.sh` preserves the data directory while replacing the named application and laboratory containers. Run it as a user authorized to use Docker. Multi-user deployment requires organizational SSO/RBAC and HTTPS. All inference and telemetry storage remain on LinuxONE. No cloud AI API or paid model is required.

## Demo in under three minutes

1. Open Live monitor. Show the real s390x runtime and real CPU/memory/network counters.
2. Open Failure replay and run Cascading service degradation. The simulated label is intentionally visible.
3. Observe the score cross the calibrated threshold, three-sample confirmation, driver deviations, runbook, and recovery.
4. Acknowledge an incident and export metrics.
5. Open Evidence & design. Show held-out synthetic results, measured local inference latency, architecture, and limitations.

## Reproduce engineering checks

```bash
python -m unittest discover -s tests -v
```

Checks cover invalid telemetry rejection, spike suppression, confirmation and recovery, separate incident episodes, healthy control, and unchanged training metadata during scoring. `data/evaluation.json` is generated on service startup with independent training/calibration/evaluation seeds.

For authenticated integration checks, set `ZWATCH_TEST_URL` to the running service URL and `ZWATCH_KEY_FILE` to its secret file, then run the same command. These checks also cover authentication, foreign-origin mutation rejection, invalid scenarios and exports, and source separation. CSV exports contain the most recent 240 observations for the selected source; SQLite retains the most recent 20,000 observations.

## Resources and data provenance

- Isolation Forest / scikit-learn: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
- NumPy: https://numpy.org/
- psutil: https://github.com/giampaolo/psutil
- FastAPI: https://github.com/fastapi/fastapi
- Uvicorn: https://github.com/encode/uvicorn
- Python / SQLite standard library: https://www.python.org/ and https://www.sqlite.org/
- IBM LinuxONE sample environment: https://github.com/linuxone-community-cloud/jupyter-lab-ml

Replay data is generated by this project's `normal_workload` and `replay` functions. No external real customer or patient dataset is used. Raw live metrics and access tokens are excluded from the public repository. AI assisted implementation and documentation. Team V is responsible for reviewing the solution and disclosing assistance as required by the organizers.
