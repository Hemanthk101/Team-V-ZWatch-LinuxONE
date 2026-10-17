"""ZWatch detection engine. No network access or automated remediation."""

from __future__ import annotations
from dataclasses import dataclass, field
import time
import numpy as np
from sklearn.ensemble import IsolationForest

FEATURES = (
    "cpu_pct",
    "memory_pct",
    "disk_pct",
    "network_kbps",
    "load_per_cpu",
    "io_wait_pct",
)
LABELS = (
    "CPU utilization",
    "Memory utilization",
    "Disk utilization",
    "Network throughput",
    "Load per CPU",
    "I/O wait",
)
UNITS = ("%", "%", "%", "KB/s", "", "%")
FLOORS = np.array([3.0, 2.0, 0.5, 20.0, 0.12, 0.5])
SCENARIOS = {
    "compound": "Cascading service degradation",
    "cpu": "CPU saturation",
    "memory": "Memory pressure",
    "network": "Network surge",
    "disk": "Storage and I/O pressure",
    "normal": "Healthy workload control",
}
RUNBOOKS = {
    "cpu_pct": (
        "Inspect CPU pressure",
        "Check the top CPU consumers, recent deploys, and worker concurrency. Confirm service latency before scaling or restarting.",
    ),
    "memory_pct": (
        "Inspect memory pressure",
        "Compare RSS and cgroup limits with normal usage. Look for a leak or growing queue; preserve diagnostics before restarting.",
    ),
    "disk_pct": (
        "Inspect storage capacity",
        "Check free space and growing log paths. Review retention and archive safely; do not delete operational data automatically.",
    ),
    "network_kbps": (
        "Inspect network surge",
        "Compare traffic with request volume and expected peers. Review connection counts and deployment changes before blocking traffic.",
    ),
    "load_per_cpu": (
        "Inspect queueing",
        "Compare run queue, CPU, and blocked tasks. Check concurrency and downstream dependencies.",
    ),
    "io_wait_pct": (
        "Inspect storage latency",
        "Check I/O latency, throughput, and blocked processes. Review recent batch jobs and storage health.",
    ),
}


def clean_row(row):
    values = np.asarray([row[k] for k in FEATURES], dtype=float)
    if not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError("Metrics must be finite non-negative numbers")
    if np.any(values[[0, 1, 2, 5]] > 100):
        raise ValueError("Percentage metrics must be in [0, 100]")
    return values


def matrix(rows):
    result = np.asarray([clean_row(r) for r in rows])
    result[:, 3] = np.log1p(result[:, 3])
    return result


class Detector:
    """Train and calibrate separately; the evaluation stream never updates the model."""

    def fit(self, training, calibration, provenance):
        if len(training) < 30 or len(calibration) < 20:
            raise ValueError("Need at least 30 training and 20 calibration samples")
        self.model = IsolationForest(
            n_estimators=128,
            max_samples=min(256, len(training)),
            random_state=17,
            n_jobs=1,
        )
        self.model.fit(matrix(training))
        scores = -self.model.score_samples(matrix(calibration))
        self.threshold = float(np.quantile(scores, 0.995))
        raw = np.asarray([clean_row(r) for r in training])
        self.center = np.median(raw, axis=0)
        self.scale = np.maximum(
            1.4826 * np.median(np.abs(raw - self.center), axis=0), FLOORS
        )
        self.info = {
            "training_samples": len(training),
            "calibration_samples": len(calibration),
            "provenance": provenance,
            "threshold_quantile": 0.995,
            "threshold": self.threshold,
            "algorithm": "Isolation Forest, 128 trees",
            "version": "zwatch-1.0",
            "baseline": {k: round(float(v), 3) for k, v in zip(FEATURES, self.center)},
        }
        return self

    def score(self, row):
        started = time.perf_counter()
        raw = clean_row(row)
        value = float(-self.model.score_samples(matrix([row]))[0])
        z = np.maximum(0.0, (raw - self.center) / self.scale)
        rules = []
        for k, limit in [
            ("cpu_pct", 90),
            ("memory_pct", 92),
            ("disk_pct", 95),
            ("io_wait_pct", 30),
        ]:
            if row[k] >= limit:
                rules.append(k)
        if row["network_kbps"] > max(
            500.0, self.center[3] * 5, self.center[3] + 10 * self.scale[3]
        ):
            rules.append("network_kbps")
        unusual = value > self.threshold and float(np.max(z)) >= 3.0
        drivers = sorted(
            [
                {
                    "metric": k,
                    "label": label,
                    "value": round(float(v), 3),
                    "baseline": round(float(b), 3),
                    "deviation": round(float(d), 2),
                    "unit": unit,
                }
                for k, label, unit, v, b, d in zip(
                    FEATURES, LABELS, UNITS, raw, self.center, z
                )
            ],
            key=lambda item: item["deviation"],
            reverse=True,
        )
        key = rules[0] if rules else drivers[0]["metric"]
        action, detail = RUNBOOKS[key]
        return {
            "score": round(value, 5),
            "threshold": round(self.threshold, 5),
            "candidate": bool(unusual or rules),
            "guardrails": rules,
            "drivers": drivers[:3],
            "action": action,
            "guidance": detail,
            "inference_ms": round((time.perf_counter() - started) * 1000, 3),
        }


@dataclass
class IncidentTracker:
    """Three consecutive candidates open an incident; three clean samples close it."""

    candidate_count: int = 0
    clear_count: int = 0
    active: dict | None = None
    incidents: list = field(default_factory=list)

    def update(self, item):
        if item["candidate"]:
            self.candidate_count += 1
            self.clear_count = 0
            if self.active is None and self.candidate_count >= 3:
                self.active = {
                    "id": max([x["id"] for x in self.incidents], default=0) + 1,
                    "opened_at": item["timestamp"],
                    "start_step": item.get("step"),
                    "status": "open",
                    "severity": "critical" if item["guardrails"] else "warning",
                    "title": item["action"],
                    "guidance": item["guidance"],
                    "drivers": item["drivers"],
                    "peak_score": item["score"],
                    "source": item["source"],
                    "closed_at": None,
                    "acknowledged": False,
                }
                self.incidents.append(self.active)
            if self.active:
                self.active["peak_score"] = max(
                    self.active["peak_score"], item["score"]
                )
                if item["guardrails"]:
                    self.active["severity"] = "critical"
        else:
            self.candidate_count = 0
            self.clear_count += 1
            if self.active and self.clear_count >= 3:
                self.active["closed_at"] = item["timestamp"]
                self.active["status"] = "resolved"
                self.active = None
        item["confirmed"] = self.active is not None
        return item


def normal_workload(n, seed):
    """Reproducible synthetic workload; never represented as production observations."""
    rng = np.random.default_rng(seed)
    x = np.arange(n)
    busy = 5 * np.sin(x / 13) + rng.normal(0, 2.5, n)
    cpu = np.clip(27 + busy, 1, 70)
    mem = np.clip(44 + busy * 0.18 + rng.normal(0, 1, n), 1, 80)
    disk = np.clip(39 + rng.normal(0, 0.35, n), 1, 80)
    network = np.clip(110 + busy * 4 + rng.normal(0, 12, n), 1, 500)
    load = np.clip(0.28 + busy * 0.011 + rng.normal(0, 0.025, n), 0.01, 1.5)
    io = np.clip(2 + busy * 0.025 + rng.normal(0, 0.25, n), 0, 10)
    return [
        dict(zip(FEATURES, map(float, values)))
        for values in zip(cpu, mem, disk, network, load, io)
    ]


def replay(scenario="compound", seed=2026, n=180):
    rows = normal_workload(n, seed)
    windows = [(40, 70), (105, 140)] if scenario != "normal" else []
    for i, row in enumerate(rows):
        row["step"] = i
        row["expected"] = any(a <= i < b for a, b in windows)
        if not row["expected"]:
            continue
        if scenario in ("compound", "cpu"):
            row["cpu_pct"] = 95.0 + (i % 3)
            row["load_per_cpu"] = 2.3 + (i % 5) * 0.1
        if scenario in ("compound", "memory"):
            row["memory_pct"] = 95.0 + (i % 3) * 0.5
        if scenario in ("compound", "network"):
            row["network_kbps"] = 2200.0 + (i % 5) * 120.0
        # The first compound episode stays below the critical guardrails, so the
        # learned multivariate model must identify this coordinated departure.
        if scenario == "compound" and 40 <= i < 70:
            row.update(
                cpu_pct=62.0 + i % 3,
                memory_pct=68.0 + i % 2,
                network_kbps=400.0 + i % 5,
                load_per_cpu=0.9,
                io_wait_pct=8.0,
            )
        if scenario == "disk":
            row["disk_pct"], row["io_wait_pct"] = 97.0, 42.0 + i % 4
    return rows


def evaluate(detector, scenarios=None):
    result = []
    for name in scenarios or SCENARIOS:
        tracker = IncidentTracker()
        outputs = []
        for row in replay(name, seed=4001):
            scored = {
                **row,
                **detector.score(row),
                "source": "simulation",
                "timestamp": f'replay:{row["step"]:03d}',
            }
            outputs.append(tracker.update(scored))
        tp = sum(r["confirmed"] and r["expected"] for r in outputs)
        fp = sum(r["confirmed"] and not r["expected"] for r in outputs)
        fn = sum(not r["confirmed"] and r["expected"] for r in outputs)
        tn = sum(not r["confirmed"] and not r["expected"] for r in outputs)
        starts = [
            i
            for i, r in enumerate(outputs)
            if r["expected"] and (i == 0 or not outputs[i - 1]["expected"])
        ]
        delays = []
        guardrail_events = 0
        for start in starts:
            end = next(
                (i for i in range(start, len(outputs)) if not outputs[i]["expected"]),
                len(outputs),
            )
            guardrail_events += int(
                any(outputs[i]["guardrails"] for i in range(start, end))
            )
            detected = next(
                (i for i in range(start, end) if outputs[i]["confirmed"]), None
            )
            if detected is not None:
                delays.append(detected - start)
        result.append(
            {
                "scenario": name,
                "title": SCENARIOS[name],
                "samples": len(outputs),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(tp / max(1, tp + fp), 4),
                "recall": round(tp / max(1, tp + fn), 4),
                "false_positive_rate": round(fp / max(1, fp + tn), 4),
                "detected_events": len(delays),
                "expected_events": len(starts),
                "guardrail_detected_events": guardrail_events,
                "detection_delay_samples": delays,
                "inference_p95_ms": round(
                    float(np.percentile([r["inference_ms"] for r in outputs], 95)), 3
                ),
            }
        )
    return {
        "dataset": "Held-out synthetic stress replay; not a production accuracy claim",
        "train_seed": 17,
        "calibration_seed": 31,
        "evaluation_seed": 4001,
        "confirmation_samples": 3,
        "recovery_samples": 3,
        "results": result,
    }
